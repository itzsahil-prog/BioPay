// internal/middleware/biometric.go
// HTTP middleware that calls the Python biometric service to verify
// face + voice before permitting any payment confirmation.
package middleware

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"time"

	"biopay-gateway/internal/models"
)

const (
	defaultBioServiceURL = "http://localhost:8001"
	httpTimeout          = 15 * time.Second
)

// BiometricClient handles HTTP communication with the Python biometric service.
type BiometricClient struct {
	BaseURL    string
	httpClient *http.Client
	logger     *log.Logger
}

func NewBiometricClient(baseURL string, logger *log.Logger) *BiometricClient {
	if baseURL == "" {
		baseURL = defaultBioServiceURL
	}
	return &BiometricClient{
		BaseURL: baseURL,
		httpClient: &http.Client{
			Timeout: httpTimeout,
			Transport: &http.Transport{
				MaxIdleConns:    10,
				IdleConnTimeout: 30 * time.Second,
			},
		},
		logger: logger,
	}
}

// CreateSession creates a biometric verification session in the Python service.
func (c *BiometricClient) CreateSession(ctx context.Context, userID string, amount float64) (string, error) {
	body := fmt.Sprintf("user_id=%s&amount=%.4f", userID, amount)
	resp, err := c.post(ctx, "/session/create",
		"application/x-www-form-urlencoded", bytes.NewBufferString(body))
	if err != nil {
		return "", fmt.Errorf("create session: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("create session: HTTP %d", resp.StatusCode)
	}

	var result struct {
		SessionID string `json:"session_id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("create session decode: %w", err)
	}
	return result.SessionID, nil
}

// CheckAuthorization queries whether both face and voice have been verified
// for a given session. Called after the user has completed the biometric flow.
func (c *BiometricClient) CheckAuthorization(ctx context.Context, sessionID string) (*models.BiometricVerification, error) {
	url := fmt.Sprintf("%s/session/%s/authorize", c.BaseURL, sessionID)

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, nil)
	if err != nil {
		return nil, fmt.Errorf("build request: %w", err)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("authorize request: %w", err)
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)

	if resp.StatusCode == http.StatusNotFound {
		return nil, fmt.Errorf("session not found or expired")
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("authorize HTTP %d: %s", resp.StatusCode, string(body))
	}

	var result models.BiometricVerification
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("decode authorization response: %w", err)
	}

	c.logger.Printf("[biometric] session=%s authorized=%v face=%v(%.3f) voice=%v(%.3f)",
		sessionID, result.Authorized,
		result.FaceVerified, result.FaceConfidence,
		result.VoiceVerified, result.VoiceSimilarity,
	)

	return &result, nil
}

// GetSessionStatus fetches the current status of a session without finalising it.
func (c *BiometricClient) GetSessionStatus(ctx context.Context, sessionID string) (map[string]interface{}, error) {
	resp, err := c.get(ctx, fmt.Sprintf("/session/%s", sessionID))
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		return nil, fmt.Errorf("session not found")
	}

	var result map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

// HealthCheck pings the Python service.
func (c *BiometricClient) HealthCheck(ctx context.Context) error {
	resp, err := c.get(ctx, "/health")
	if err != nil {
		return fmt.Errorf("biometric service unreachable: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("biometric service unhealthy: HTTP %d", resp.StatusCode)
	}
	return nil
}

// ── Private HTTP helpers ──────────────────────────────────────────────────

func (c *BiometricClient) get(ctx context.Context, path string) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+path, nil)
	if err != nil {
		return nil, err
	}
	return c.httpClient.Do(req)
}

func (c *BiometricClient) post(ctx context.Context, path, contentType string, body io.Reader) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.BaseURL+path, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", contentType)
	return c.httpClient.Do(req)
}
