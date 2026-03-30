// cmd/server/main.go
// BioPay Payment Gateway — Go HTTP server.
// All payment confirmations are gated behind biometric verification
// from the Python service. No database — session state is in-memory.
package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"

	"biopay-gateway/internal/engine"
	"biopay-gateway/internal/middleware"
	"biopay-gateway/internal/models"
	"biopay-gateway/internal/security"
)

// ─── In-memory transaction store (no database) ───────────────────────────────

type TxnStore struct {
	mu   sync.RWMutex
	txns map[string]*models.Transaction
}

func newTxnStore() *TxnStore { return &TxnStore{txns: make(map[string]*models.Transaction)} }

func (s *TxnStore) Put(t *models.Transaction) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.txns[t.ID] = t
}

func (s *TxnStore) Get(id string) (*models.Transaction, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	t, ok := s.txns[id]
	return t, ok
}

func (s *TxnStore) Update(id string, fn func(*models.Transaction)) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	t, ok := s.txns[id]
	if !ok {
		return false
	}
	fn(t)
	t.UpdatedAt = time.Now()
	return true
}

// ─── Server ───────────────────────────────────────────────────────────────────

type Server struct {
	router     *gin.Engine
	txnStore   *TxnStore
	bioClient  *middleware.BiometricClient
	riskEngine *engine.RiskEngine
	logger     *log.Logger
	bioSvcURL  string
}

func NewServer(bioServiceURL string) *Server {
	logger := log.New(os.Stdout, "[biopay-gw] ", log.LstdFlags|log.Lmsgprefix)
	s := &Server{
		txnStore:   newTxnStore(),
		riskEngine: engine.NewRiskEngine(),
		logger:     logger,
		bioSvcURL:  bioServiceURL,
		bioClient:  middleware.NewBiometricClient(bioServiceURL, logger),
	}
	s.router = s.buildRouter()
	return s
}

func (s *Server) buildRouter() *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	r.Use(gin.Recovery())
	r.Use(s.requestLogger())
	r.Use(s.securityHeaders())

	// ── Public endpoints ──────────────────────────────────────────
	r.GET("/health", s.handleHealth)

	// ── Payment flow ──────────────────────────────────────────────
	pay := r.Group("/payment")
	{
		// Step 1: Initiate — creates a transaction + biometric session
		pay.POST("/initiate", s.handleInitiatePayment)

		// Step 2: Status — poll while user completes biometrics
		pay.GET("/status/:txn_id", s.handlePaymentStatus)

		// Step 3: Confirm — check biometric result, then execute payment
		pay.POST("/confirm", s.handleConfirmPayment)
	}

	// ── Biometric proxy (convenience) ─────────────────────────────
	bio := r.Group("/biometric")
	{
		bio.GET("/session/:session_id", s.handleGetBioSession)
	}

	return r
}

// ─── Handlers ────────────────────────────────────────────────────────────────

func (s *Server) handleHealth(c *gin.Context) {
	ctx, cancel := context.WithTimeout(c.Request.Context(), 3*time.Second)
	defer cancel()

	bioOK := s.bioClient.HealthCheck(ctx) == nil
	status := "healthy"
	if !bioOK {
		status = "degraded"
	}

	c.JSON(http.StatusOK, gin.H{
		"gateway_status":   status,
		"biometric_online": bioOK,
		"biometric_url":    s.bioSvcURL,
		"timestamp":        time.Now().UTC(),
	})
}

// handleInitiatePayment — Step 1 of the payment flow.
// Creates a transaction record + a biometric session in the Python service.
func (s *Server) handleInitiatePayment(c *gin.Context) {
	var req models.InitiatePaymentRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// Preliminary risk assessment (pre-biometric)
	user := s.mockUser(req.UserID)   // in prod: fetch from user service
	txnForRisk := &models.Transaction{
		Amount:   req.Amount,
		Currency: req.Currency,
		UserID:   req.UserID,
	}

	risk := s.riskEngine.Evaluate(txnForRisk, user, nil)

	// Create biometric session in Python service
	ctx, cancel := context.WithTimeout(c.Request.Context(), 10*time.Second)
	defer cancel()

	sessionID, err := s.bioClient.CreateSession(ctx, req.UserID, req.Amount)
	if err != nil {
		s.logger.Printf("ERROR: create bio session: %v", err)
		c.JSON(http.StatusServiceUnavailable, gin.H{
			"error":   "biometric_service_unavailable",
			"message": "Cannot create biometric session. Ensure the Python service is running.",
		})
		return
	}

	// Create transaction record
	txnID := uuid.NewString()
	txn := &models.Transaction{
		ID:          txnID,
		UserID:      req.UserID,
		Amount:      req.Amount,
		Currency:    req.Currency,
		Recipient:   req.Recipient,
		Description: req.Description,
		Status:      models.TxnPending,
		RiskScore:   risk.Score,
		RiskLevel:   risk.Level,
		RiskFactors: risk.Factors,
		SessionID:   sessionID,
		CreatedAt:   time.Now(),
		UpdatedAt:   time.Now(),
	}
	s.txnStore.Put(txn)

	s.logger.Printf("INITIATE txn=%s user=%s amount=%.2f risk=%d(%s) session=%s",
		txnID, security.HashUserID(req.UserID), req.Amount, risk.Score, risk.Level, sessionID)

	c.JSON(http.StatusOK, models.InitiatePaymentResponse{
		TransactionID: txnID,
		SessionID:     sessionID,
		Status:        "PENDING_BIOMETRIC",
		RequiredAuth:  risk.RequiredAuth,
		BiometricURL:  s.bioSvcURL,
		ExpiresIn:     300,
		Message: fmt.Sprintf(
			"Payment of %.2f %s initiated. Complete face + voice verification to confirm. Risk: %s (%d/100)",
			req.Amount, req.Currency, risk.Level, risk.Score,
		),
	})
}

// handleConfirmPayment — Step 3 of the payment flow.
// Calls Python service to check biometric authorization, runs final risk score,
// then AUTHORIZED or DECLINED.
func (s *Server) handleConfirmPayment(c *gin.Context) {
	var req models.ConfirmPaymentRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	// Fetch transaction
	txn, ok := s.txnStore.Get(req.TransactionID)
	if !ok {
		c.JSON(http.StatusNotFound, gin.H{"error": "transaction_not_found"})
		return
	}
	if txn.UserID != req.UserID {
		c.JSON(http.StatusForbidden, gin.H{"error": "user_mismatch"})
		return
	}
	if txn.Status != models.TxnPending {
		c.JSON(http.StatusConflict, gin.H{
			"error":  "transaction_not_pending",
			"status": txn.Status,
		})
		return
	}
	if txn.SessionID != req.SessionID {
		c.JSON(http.StatusBadRequest, gin.H{"error": "session_mismatch"})
		return
	}

	// ── KEY GATE: Check biometric authorization from Python service ───────
	ctx, cancel := context.WithTimeout(c.Request.Context(), 10*time.Second)
	defer cancel()

	bio, err := s.bioClient.CheckAuthorization(ctx, req.SessionID)
	if err != nil {
		s.logger.Printf("ERROR: check biometric auth: %v", err)
		c.JSON(http.StatusServiceUnavailable, gin.H{
			"error":   "biometric_check_failed",
			"message": err.Error(),
		})
		return
	}

	// Final risk evaluation (with biometric data)
	user := s.mockUser(req.UserID)
	risk := s.riskEngine.Evaluate(txn, user, bio)

	authorized := bio.Authorized && !risk.Block

	var newStatus models.TransactionStatus
	var declineReason string

	if authorized {
		newStatus = models.TxnAuthorized
	} else {
		newStatus = models.TxnDeclined
		if risk.Block {
			declineReason = fmt.Sprintf("Risk score %d exceeds threshold (%d). Factors: %v",
				risk.Score, engine.RiskScoreBlock, risk.Factors)
		} else if !bio.FaceVerified {
			declineReason = "Face biometric not verified"
		} else if !bio.VoiceVerified {
			declineReason = "Voice biometric not verified"
		}
	}

	// Update transaction
	s.txnStore.Update(req.TransactionID, func(t *models.Transaction) {
		t.Status      = newStatus
		t.RiskScore   = risk.Score
		t.RiskLevel   = risk.Level
		t.RiskFactors = risk.Factors
	})

	s.logger.Printf("CONFIRM txn=%s user=%s authorized=%v risk=%d(%s) face=%v(%.3f) voice=%v(%.3f)",
		req.TransactionID, security.HashUserID(req.UserID),
		authorized, risk.Score, risk.Level,
		bio.FaceVerified, bio.FaceConfidence,
		bio.VoiceVerified, bio.VoiceSimilarity,
	)

	resp := models.ConfirmPaymentResponse{
		TransactionID: req.TransactionID,
		Status:        string(newStatus),
		Authorized:    authorized,
		RiskScore:     risk.Score,
		RiskLevel:     risk.Level,
		FaceConf:      bio.FaceConfidence,
		VoiceSim:      bio.VoiceSimilarity,
		DeclineReason: declineReason,
	}

	if authorized {
		resp.Message = fmt.Sprintf(
			"Payment of %.2f %s AUTHORIZED. Both face and voice verified.",
			txn.Amount, txn.Currency,
		)
		c.JSON(http.StatusOK, resp)
	} else {
		resp.Message = "Payment DECLINED. Biometric verification failed or risk too high."
		c.JSON(http.StatusForbidden, resp)
	}
}

func (s *Server) handlePaymentStatus(c *gin.Context) {
	txnID := c.Param("txn_id")
	txn, ok := s.txnStore.Get(txnID)
	if !ok {
		c.JSON(http.StatusNotFound, gin.H{"error": "transaction_not_found"})
		return
	}

	// Optionally fetch live bio session status
	var bioStatus map[string]interface{}
	if txn.SessionID != "" {
		ctx, cancel := context.WithTimeout(c.Request.Context(), 5*time.Second)
		defer cancel()
		bioStatus, _ = s.bioClient.GetSessionStatus(ctx, txn.SessionID)
	}

	c.JSON(http.StatusOK, gin.H{
		"transaction":       txn,
		"biometric_session": bioStatus,
	})
}

func (s *Server) handleGetBioSession(c *gin.Context) {
	sessionID := c.Param("session_id")
	ctx, cancel := context.WithTimeout(c.Request.Context(), 5*time.Second)
	defer cancel()
	result, err := s.bioClient.GetSessionStatus(ctx, sessionID)
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, result)
}

// ─── Middleware ───────────────────────────────────────────────────────────────

func (s *Server) requestLogger() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		s.logger.Printf("%s %s → %d (%v)",
			c.Request.Method, c.Request.URL.Path,
			c.Writer.Status(), time.Since(start))
	}
}

func (s *Server) securityHeaders() gin.HandlerFunc {
	return func(c *gin.Context) {
		c.Header("X-Content-Type-Options", "nosniff")
		c.Header("X-Frame-Options", "DENY")
		c.Header("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
		c.Header("Cache-Control", "no-store")
		c.Next()
	}
}

// ─── Mock helpers (replace with real user service in production) ──────────────

func (s *Server) mockUser(userID string) *models.User {
	return &models.User{
		ID:                userID,
		DeviceFingerprint: "device-abc-123",
		TrustScore:        70,
		FaceRegistered:    true,
		VoiceRegistered:   true,
	}
}

// ─── Entry Point ─────────────────────────────────────────────────────────────

func main() {
	bioSvcURL := os.Getenv("BIOMETRIC_SERVICE_URL")
	if bioSvcURL == "" {
		bioSvcURL = "http://localhost:8001"
	}

	port := os.Getenv("GATEWAY_PORT")
	if port == "" {
		port = "8080"
	}

	srv := NewServer(bioSvcURL)

	httpSrv := &http.Server{
		Addr:         ":" + port,
		Handler:      srv.router,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 30 * time.Second,
		IdleTimeout:  120 * time.Second,
	}

	go func() {
		fmt.Printf(`
╔══════════════════════════════════════════════════╗
║         BioPay Payment Gateway (Go)              ║
║  Biometric gate: Face + Voice required           ║
╠══════════════════════════════════════════════════╣
║  Gateway:   http://localhost:%s              ║
║  Biometric: %s        ║
╚══════════════════════════════════════════════════╝
`, port, bioSvcURL)
		if err := httpSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("Server error: %v", err)
		}
	}()

	// Graceful shutdown
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Println("Shutting down gateway...")
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	if err := httpSrv.Shutdown(ctx); err != nil {
		log.Printf("Forced shutdown: %v", err)
	}
	log.Println("Gateway stopped.")
}
