// internal/engine/risk.go
// Payment risk scoring engine.
// Combines transaction signals, biometric confidence, and behavioural factors.
package engine

import (
	"fmt"
	"math"
	"time"

	"biopay-gateway/internal/models"
)

// Factor weights (must sum to 1.0)
const (
	WeightAmount      = 0.35
	WeightBiometric   = 0.30
	WeightDevice      = 0.15
	WeightVelocity    = 0.10
	WeightTime        = 0.10
)

// Thresholds
const (
	AmountHighRisk   = 10_000.0 // $10,000
	AmountMediumRisk = 1_000.0  // $1,000
	AmountLowRisk    = 100.0

	BiometricMinConf    = 0.85
	BiometricWarnConf   = 0.70
	VoiceMinSimilarity  = 0.75

	RiskScoreBlock  = 80
	RiskScoreHigh   = 60
	RiskScoreMedium = 35
)

// RiskEngine scores payment transactions.
type RiskEngine struct{}

func NewRiskEngine() *RiskEngine {
	return &RiskEngine{}
}

// Evaluate computes a full risk assessment for a payment.
func (re *RiskEngine) Evaluate(
	txn *models.Transaction,
	user *models.User,
	bio *models.BiometricVerification,
) models.RiskDecision {

	score := 0.0
	factors := []string{}

	// ── 1. Amount risk ────────────────────────────────────────────────────
	amountRisk := re.scoreAmount(txn.Amount)
	score += amountRisk * WeightAmount * 100
	if amountRisk > 0.7 {
		factors = append(factors, fmt.Sprintf("HIGH_AMOUNT:%.2f", txn.Amount))
	} else if amountRisk > 0.3 {
		factors = append(factors, fmt.Sprintf("ELEVATED_AMOUNT:%.2f", txn.Amount))
	}

	// ── 2. Biometric confidence ───────────────────────────────────────────
	bioRisk, bioFactors := re.scoreBiometric(bio)
	score += bioRisk * WeightBiometric * 100
	factors = append(factors, bioFactors...)

	// ── 3. Device trust ───────────────────────────────────────────────────
	deviceRisk := re.scoreDevice(user)
	score += deviceRisk * WeightDevice * 100
	if deviceRisk > 0.5 {
		factors = append(factors, "UNKNOWN_DEVICE")
	}

	// ── 4. Time-of-day risk ───────────────────────────────────────────────
	timeRisk := re.scoreTimeOfDay()
	score += timeRisk * WeightTime * 100
	if timeRisk > 0.6 {
		factors = append(factors, "ODD_HOURS_TRANSACTION")
	}

	// ── 5. Velocity (simplified — would use Redis in production) ──────────
	velocityRisk := re.scoreVelocity(user, txn.Amount)
	score += velocityRisk * WeightVelocity * 100
	if velocityRisk > 0.5 {
		factors = append(factors, "HIGH_TRANSACTION_VELOCITY")
	}

	finalScore := int(math.Min(100, math.Max(0, score)))

	level, requiredAuth, block := re.classify(finalScore, bio)

	return models.RiskDecision{
		Score:        finalScore,
		Level:        level,
		RequiredAuth: requiredAuth,
		Factors:      factors,
		Block:        block,
	}
}

// ── Scoring sub-functions ──────────────────────────────────────────────────

func (re *RiskEngine) scoreAmount(amount float64) float64 {
	switch {
	case amount >= AmountHighRisk:
		return 1.0
	case amount >= AmountMediumRisk:
		return 0.5 + (amount-AmountMediumRisk)/(AmountHighRisk-AmountMediumRisk)*0.5
	case amount >= AmountLowRisk:
		return 0.1 + (amount-AmountLowRisk)/(AmountMediumRisk-AmountLowRisk)*0.4
	default:
		return 0.05
	}
}

func (re *RiskEngine) scoreBiometric(bio *models.BiometricVerification) (float64, []string) {
	if bio == nil {
		return 1.0, []string{"NO_BIOMETRIC_DATA"}
	}

	risk := 0.0
	factors := []string{}

	if !bio.FaceVerified {
		risk += 0.6
		factors = append(factors, "FACE_NOT_VERIFIED")
	} else if bio.FaceConfidence < BiometricMinConf {
		risk += 0.3
		factors = append(factors, fmt.Sprintf("LOW_FACE_CONF:%.3f", bio.FaceConfidence))
	} else if bio.FaceConfidence < BiometricWarnConf {
		risk += 0.15
		factors = append(factors, fmt.Sprintf("WARN_FACE_CONF:%.3f", bio.FaceConfidence))
	}

	if !bio.VoiceVerified {
		risk += 0.4
		factors = append(factors, "VOICE_NOT_VERIFIED")
	} else if bio.VoiceSimilarity < VoiceMinSimilarity {
		risk += 0.2
		factors = append(factors, fmt.Sprintf("LOW_VOICE_SIM:%.3f", bio.VoiceSimilarity))
	}

	return math.Min(1.0, risk), factors
}

func (re *RiskEngine) scoreDevice(user *models.User) float64 {
	if user.DeviceFingerprint == "" {
		return 0.8
	}
	return 0.0
}

func (re *RiskEngine) scoreTimeOfDay() float64 {
	hour := time.Now().UTC().Hour()
	// High risk: 00:00–05:00 UTC
	if hour >= 0 && hour < 5 {
		return 0.7
	}
	return 0.0
}

func (re *RiskEngine) scoreVelocity(user *models.User, amount float64) float64 {
	// Simplified: in production, check Redis for recent transaction count
	// For now: high amounts from low-trust users are flagged
	if user.TrustScore < 30 && amount > 500 {
		return 0.8
	}
	if user.TrustScore < 60 && amount > 5000 {
		return 0.6
	}
	return 0.0
}

// ── Classification ────────────────────────────────────────────────────────

func (re *RiskEngine) classify(score int, bio *models.BiometricVerification) (string, []string, bool) {
	// Hard block conditions
	if bio != nil && !bio.FaceVerified && !bio.VoiceVerified {
		return "CRITICAL", []string{"FACE", "VOICE", "OTP"}, true
	}
	if score >= RiskScoreBlock {
		return "HIGH", []string{"FACE", "VOICE", "OTP"}, true
	}
	if score >= RiskScoreHigh {
		return "HIGH", []string{"FACE", "VOICE"}, false
	}
	if score >= RiskScoreMedium {
		return "MEDIUM", []string{"FACE"}, false
	}
	return "LOW", []string{"FACE"}, false
}
