// internal/security/security_test.go
package security

import (
	"strings"
	"testing"
)

func TestSecureZeroBytes_WipesData(t *testing.T) {
	sensitive := []byte("super-secret-biometric-embedding-key")
	original := make([]byte, len(sensitive))
	copy(original, sensitive)

	SecureZeroBytes(sensitive)

	for i, b := range sensitive {
		if b != 0 {
			t.Errorf("byte %d not zeroed after SecureZeroBytes (got 0x%x)", i, b)
		}
	}
}

func TestSecureZeroBytes_EmptySlice(t *testing.T) {
	// Should not panic
	SecureZeroBytes([]byte{})
	SecureZeroBytes(nil)
}

func TestConstantTimeEqual_Match(t *testing.T) {
	if !ConstantTimeEqual("correct-session-token", "correct-session-token") {
		t.Error("ConstantTimeEqual: should return true for identical strings")
	}
}

func TestConstantTimeEqual_NoMatch(t *testing.T) {
	if ConstantTimeEqual("token-a", "token-b") {
		t.Error("ConstantTimeEqual: should return false for different strings")
	}
}

func TestConstantTimeEqual_EmptyStrings(t *testing.T) {
	if !ConstantTimeEqual("", "") {
		t.Error("Two empty strings should be equal")
	}
	if ConstantTimeEqual("", "non-empty") {
		t.Error("Empty vs non-empty should not be equal")
	}
}

func TestSignAndVerifyPayload(t *testing.T) {
	secret  := []byte("payment-signing-key-256bit-secret!")
	payload := []byte(`{"transaction_id":"txn-001","amount":500.00,"user_id":"user-001"}`)

	sig := SignPayload(secret, payload)
	if sig == "" {
		t.Fatal("SignPayload returned empty signature")
	}
	if len(sig) != 64 { // SHA-256 hex = 64 chars
		t.Errorf("expected 64-char hex signature, got %d chars", len(sig))
	}

	if !VerifySignature(secret, payload, sig) {
		t.Error("VerifySignature should return true for correct signature")
	}

	// Tampered payload
	tampered := []byte(`{"transaction_id":"txn-001","amount":9999.99,"user_id":"user-001"}`)
	if VerifySignature(secret, tampered, sig) {
		t.Error("VerifySignature should return false for tampered payload")
	}

	// Wrong key
	wrongKey := []byte("wrong-key")
	if VerifySignature(wrongKey, payload, sig) {
		t.Error("VerifySignature should return false for wrong key")
	}
}

func TestGenerateSessionToken(t *testing.T) {
	token, err := GenerateSessionToken()
	if err != nil {
		t.Fatalf("GenerateSessionToken failed: %v", err)
	}
	if len(token) != 64 { // 32 bytes = 64 hex chars
		t.Errorf("expected 64-char token, got %d", len(token))
	}

	// Two tokens should be unique
	token2, _ := GenerateSessionToken()
	if token == token2 {
		t.Error("Two generated tokens should not be identical")
	}
}

func TestHashUserID(t *testing.T) {
	hash := HashUserID("alice@example.com")
	if len(hash) != 16 {
		t.Errorf("expected 16-char hash, got %d", len(hash))
	}
	// Should not contain the original user ID
	if strings.Contains(hash, "alice") {
		t.Error("Hash should not contain original user ID")
	}
	// Deterministic
	if HashUserID("alice@example.com") != hash {
		t.Error("HashUserID should be deterministic")
	}
	// Different inputs → different hashes
	if HashUserID("bob@example.com") == hash {
		t.Error("Different user IDs should produce different hashes")
	}
}
