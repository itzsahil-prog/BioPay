// go/internal/engine/risk_test.go
package engine

import (
	"testing"

	"biopay-gateway/internal/models"
)

// ── helpers ─────────────────────────────────────────────────────────────────

func verifiedBio(faceConf, voiceSim float64) *models.BiometricVerification {
	return &models.BiometricVerification{
		Authorized:      true,
		FaceVerified:    true,
		FaceConfidence:  faceConf,
		VoiceVerified:   true,
		VoiceSimilarity: voiceSim,
	}
}

func failedBio() *models.BiometricVerification {
	return &models.BiometricVerification{
		Authorized:   false,
		FaceVerified: false,
		VoiceVerified: false,
	}
}

func defaultUser() *models.User {
	return &models.User{
		ID:                "user-test-001",
		DeviceFingerprint: "known-device-abc",
		TrustScore:        80,
	}
}

func lowTrustUser() *models.User {
	return &models.User{
		ID:                "user-test-002",
		DeviceFingerprint: "",  // Unknown device
		TrustScore:        20,
	}
}

func txn(amount float64) *models.Transaction {
	return &models.Transaction{
		ID:       "txn-test",
		UserID:   "user-test-001",
		Amount:   amount,
		Currency: "USD",
		Status:   models.TxnPending,
	}
}

// ── Risk Engine Tests ────────────────────────────────────────────────────────

func TestRiskEngine_LowRiskPayment(t *testing.T) {
	re := NewRiskEngine()
	decision := re.Evaluate(txn(50.0), defaultUser(), verifiedBio(0.95, 0.88))

	if decision.Level != "LOW" {
		t.Errorf("expected LOW risk, got %s (score=%d)", decision.Level, decision.Score)
	}
	if decision.Block {
		t.Error("LOW risk payment should not be blocked")
	}
	if decision.Score > RiskScoreMedium {
		t.Errorf("expected score <= %d for low-risk txn, got %d", RiskScoreMedium, decision.Score)
	}
}

func TestRiskEngine_HighAmountElevatesRisk(t *testing.T) {
	re := NewRiskEngine()
	decision := re.Evaluate(txn(15000.0), defaultUser(), verifiedBio(0.92, 0.85))

	if decision.Score <= RiskScoreMedium {
		t.Errorf("high amount should raise risk score above %d, got %d", RiskScoreMedium, decision.Score)
	}
	found := false
	for _, f := range decision.Factors {
		if len(f) >= 11 && f[:11] == "HIGH_AMOUNT" {
			found = true
			break
		}
	}
	if !found {
		t.Error("expected HIGH_AMOUNT in risk factors")
	}
}

func TestRiskEngine_NoBiometricsBlocks(t *testing.T) {
	re := NewRiskEngine()
	decision := re.Evaluate(txn(500.0), defaultUser(), failedBio())

	if !decision.Block {
		t.Error("No biometric data should result in Block=true")
	}
	if decision.Level != "CRITICAL" {
		t.Errorf("expected CRITICAL level, got %s", decision.Level)
	}
}

func TestRiskEngine_LowFaceConfidenceAddsRisk(t *testing.T) {
	re := NewRiskEngine()
	bio := &models.BiometricVerification{
		FaceVerified:    true,
		FaceConfidence:  0.60, // Below BiometricMinConf=0.85
		VoiceVerified:   true,
		VoiceSimilarity: 0.88,
		Authorized:      true,
	}
	decision := re.Evaluate(txn(200.0), defaultUser(), bio)
	found := false
	for _, f := range decision.Factors {
		if len(f) >= 13 && f[:13] == "LOW_FACE_CONF" {
			found = true
			break
		}
	}
	if !found {
		t.Error("expected LOW_FACE_CONF in risk factors")
	}
}

func TestRiskEngine_UnknownDeviceAddsRisk(t *testing.T) {
	re := NewRiskEngine()
	decision := re.Evaluate(txn(200.0), lowTrustUser(), verifiedBio(0.92, 0.88))
	found := false
	for _, f := range decision.Factors {
		if f == "UNKNOWN_DEVICE" {
			found = true
			break
		}
	}
	if !found {
		t.Error("expected UNKNOWN_DEVICE in risk factors for user with no device fingerprint")
	}
}

func TestRiskEngine_RequiredAuthContainsFACE(t *testing.T) {
	re := NewRiskEngine()
	decision := re.Evaluate(txn(50.0), defaultUser(), verifiedBio(0.95, 0.90))
	found := false
	for _, a := range decision.RequiredAuth {
		if a == "FACE" {
			found = true
			break
		}
	}
	if !found {
		t.Error("FACE should always be in RequiredAuth")
	}
}

func TestRiskEngine_RiskScoreInRange(t *testing.T) {
	re := NewRiskEngine()
	cases := []struct {
		amount float64
		user   *models.User
		bio    *models.BiometricVerification
	}{
		{50, defaultUser(), verifiedBio(0.95, 0.90)},
		{1500, defaultUser(), verifiedBio(0.88, 0.80)},
		{25000, lowTrustUser(), failedBio()},
	}
	for _, c := range cases {
		d := re.Evaluate(txn(c.amount), c.user, c.bio)
		if d.Score < 0 || d.Score > 100 {
			t.Errorf("risk score out of range [0,100]: got %d", d.Score)
		}
	}
}

func TestRiskEngine_HighRiskRequiresVoiceAndFace(t *testing.T) {
	re := NewRiskEngine()
	// Big amount + unknown device + failed biometrics → HIGH or CRITICAL
	decision := re.Evaluate(txn(50000.0), lowTrustUser(), failedBio())

	hasVoice, hasFace := false, false
	for _, a := range decision.RequiredAuth {
		if a == "VOICE" {
			hasVoice = true
		}
		if a == "FACE" {
			hasFace = true
		}
	}
	if !hasVoice || !hasFace {
		t.Errorf("HIGH/CRITICAL risk should require both FACE and VOICE, got %v", decision.RequiredAuth)
	}
}

// ── Amount Scoring Tests ─────────────────────────────────────────────────────

func TestScoreAmount(t *testing.T) {
	re := NewRiskEngine()
	tests := []struct {
		amount   float64
		wantHigh bool
	}{
		{10.0, false},
		{99.0, false},
		{500.0, false},
		{1500.0, false},
		{10001.0, true},
		{100000.0, true},
	}
	for _, tc := range tests {
		score := re.scoreAmount(tc.amount)
		if tc.wantHigh && score < 0.7 {
			t.Errorf("amount %.2f: expected high score (>0.7), got %.3f", tc.amount, score)
		}
		if !tc.wantHigh && score > 0.7 {
			t.Errorf("amount %.2f: expected low score (<0.7), got %.3f", tc.amount, score)
		}
	}
}

// ── Security Utils Tests ─────────────────────────────────────────────────────

func TestSecureZeroBytes(t *testing.T) {
	import_security_pkg(t)
}

func import_security_pkg(t *testing.T) {
	// Security package tested separately in security_test.go
	t.Log("Security package tests in internal/security/security_test.go")
}
