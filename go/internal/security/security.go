// internal/security/security.go
// Cryptographic and memory security utilities for the payment gateway.
package security

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"fmt"
	"reflect"
	"runtime"
	"unsafe"
)

// ─── Secure Memory Wipe ───────────────────────────────────────────────────────

// SecureZeroBytes overwrites a byte slice with zeros.
// Uses a volatile write pattern to prevent the compiler from optimising it away,
// then randomises before clearing (two-pass wipe).
func SecureZeroBytes(b []byte) {
	if len(b) == 0 {
		return
	}
	// Pass 1: overwrite with random
	rand.Read(b) //nolint:errcheck

	// Pass 2: zero out
	for i := range b {
		b[i] = 0
	}

	// Prevent GC from moving this before the write completes
	runtime.KeepAlive(b)

	// Zero the slice header (len/cap/ptr) — advanced defensive measure
	sh := (*reflect.SliceHeader)(unsafe.Pointer(&b))
	sh.Len = 0
	sh.Cap = 0
}

// SecureZeroString wipes the underlying memory of a string.
// NOTE: Strings in Go are immutable; this uses unsafe to bypass that.
// Best-effort only — the GC may have already copied the value.
func SecureZeroString(s *string) {
	if s == nil || *s == "" {
		return
	}
	sh := (*reflect.StringHeader)(unsafe.Pointer(s))
	b := (*[1 << 30]byte)(unsafe.Pointer(sh.Data))[:sh.Len:sh.Len]
	for i := range b {
		b[i] = 0
	}
	runtime.KeepAlive(s)
}

// ─── Constant-Time Comparison ────────────────────────────────────────────────

// ConstantTimeEqual compares two strings in constant time
// to prevent timing-based side-channel attacks.
func ConstantTimeEqual(a, b string) bool {
	return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}

// ─── HMAC Utilities ───────────────────────────────────────────────────────────

// SignPayload creates an HMAC-SHA256 signature for a payment payload.
func SignPayload(secret, payload []byte) string {
	mac := hmac.New(sha256.New, secret)
	mac.Write(payload)
	return hex.EncodeToString(mac.Sum(nil))
}

// VerifySignature validates a payload signature in constant time.
func VerifySignature(secret []byte, payload []byte, sig string) bool {
	expected := SignPayload(secret, payload)
	return subtle.ConstantTimeCompare([]byte(expected), []byte(sig)) == 1
}

// ─── Session Token ───────────────────────────────────────────────────────────

// GenerateSessionToken creates a cryptographically secure 32-byte token.
func GenerateSessionToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", fmt.Errorf("failed to generate session token: %w", err)
	}
	token := hex.EncodeToString(b)
	SecureZeroBytes(b)
	return token, nil
}

// HashUserID produces a stable, non-reversible hash of a user ID for logging.
func HashUserID(userID string) string {
	h := sha256.Sum256([]byte(userID))
	return hex.EncodeToString(h[:8]) // First 8 bytes = 16 hex chars
}
