// internal/models/models.go
// Core domain models for the BioPay payment gateway.
package models

import "time"

// ─── User ────────────────────────────────────────────────────────────────────

type User struct {
	ID                string    `json:"id"`
	Email             string    `json:"email"`
	Phone             string    `json:"phone"`
	DeviceFingerprint string    `json:"device_fingerprint"`
	TrustScore        int       `json:"trust_score"` // 0–100; dynamic
	FaceRegistered    bool      `json:"face_registered"`
	VoiceRegistered   bool      `json:"voice_registered"`
	CreatedAt         time.Time `json:"created_at"`
}

// ─── Transaction ─────────────────────────────────────────────────────────────

type TransactionStatus string

const (
	TxnPending    TransactionStatus = "PENDING"
	TxnAuthorized TransactionStatus = "AUTHORIZED"
	TxnDeclined   TransactionStatus = "DECLINED"
	TxnFailed     TransactionStatus = "FAILED"
)

type Transaction struct {
	ID          string            `json:"id"`
	UserID      string            `json:"user_id"`
	Amount      float64           `json:"amount"`
	Currency    string            `json:"currency"`
	Recipient   string            `json:"recipient"`
	Description string            `json:"description"`
	Status      TransactionStatus `json:"status"`
	RiskScore   int               `json:"risk_score"`
	RiskLevel   string            `json:"risk_level"`
	RiskFactors []string          `json:"risk_factors"`
	SessionID   string            `json:"session_id,omitempty"`
	CreatedAt   time.Time         `json:"created_at"`
	UpdatedAt   time.Time         `json:"updated_at"`
}

// ─── Biometric session (from Python service) ─────────────────────────────────

type BiometricVerification struct {
	Authorized      bool     `json:"authorized"`
	SessionID       string   `json:"session_id"`
	UserID          string   `json:"user_id"`
	FaceVerified    bool     `json:"face_verified"`
	VoiceVerified   bool     `json:"voice_verified"`
	FaceConfidence  float64  `json:"face_confidence"`
	VoiceSimilarity float64  `json:"voice_similarity"`
	MissingFactors  []string `json:"missing_factors"`
}

// ─── Payment request / response ──────────────────────────────────────────────

type InitiatePaymentRequest struct {
	UserID      string  `json:"user_id"      binding:"required"`
	Amount      float64 `json:"amount"       binding:"required,gt=0"`
	Currency    string  `json:"currency"     binding:"required"`
	Recipient   string  `json:"recipient"    binding:"required"`
	Description string  `json:"description"`
}

type InitiatePaymentResponse struct {
	TransactionID string   `json:"transaction_id"`
	SessionID     string   `json:"session_id"`
	Status        string   `json:"status"`
	RequiredAuth  []string `json:"required_auth"`
	BiometricURL  string   `json:"biometric_service_url"`
	ExpiresIn     int      `json:"expires_in_seconds"`
	Message       string   `json:"message"`
}

type ConfirmPaymentRequest struct {
	TransactionID string `json:"transaction_id" binding:"required"`
	SessionID     string `json:"session_id"     binding:"required"`
	UserID        string `json:"user_id"        binding:"required"`
}

type ConfirmPaymentResponse struct {
	TransactionID string  `json:"transaction_id"`
	Status        string  `json:"status"`
	Authorized    bool    `json:"authorized"`
	RiskScore     int     `json:"risk_score"`
	RiskLevel     string  `json:"risk_level"`
	FaceConf      float64 `json:"face_confidence"`
	VoiceSim      float64 `json:"voice_similarity"`
	Message       string  `json:"message"`
	DeclineReason string  `json:"decline_reason,omitempty"`
}

// ─── Risk decision ────────────────────────────────────────────────────────────

type RiskDecision struct {
	Score        int      `json:"risk_score"`
	Level        string   `json:"level"`
	RequiredAuth []string `json:"required_auth"`
	Factors      []string `json:"risk_factors"`
	Block        bool     `json:"block"`
}
