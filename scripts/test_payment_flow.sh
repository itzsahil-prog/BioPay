#!/usr/bin/env bash
# scripts/test_payment_flow.sh
# End-to-end payment flow test:
#   1. Initiate payment  (Go gateway)
#   2. Verify face       (Python biometric service)
#   3. Verify voice      (Python biometric service)
#   4. Confirm payment   (Go gateway — authorized only if both pass)

set -euo pipefail

USER_ID="${1:-user-001}"
FACE_IMG="${2:-sample_face.jpg}"
VOICE_WAV="${3:-sample_voice.wav}"
AMOUNT="${4:-500.00}"
CURRENCY="${5:-USD}"

GW_URL="${GATEWAY_URL:-http://localhost:8080}"
BIO_URL="${BIOMETRIC_SERVICE_URL:-http://localhost:8001}"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; RESET='\033[0m'

divider() { echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; }
ok()  { echo -e "  ${GREEN}✓${RESET} $1"; }
err() { echo -e "  ${RED}✗${RESET} $1"; exit 1; }
info(){ echo -e "  ${CYAN}▶${RESET} $1"; }

divider
echo -e "${BOLD}  BioPay — End-to-End Payment Flow Test${RESET}"
divider
echo -e "  User     : ${YELLOW}$USER_ID${RESET}"
echo -e "  Amount   : ${YELLOW}$AMOUNT $CURRENCY${RESET}"
echo -e "  Gateway  : $GW_URL"
echo -e "  Biometric: $BIO_URL"
divider
echo ""

# ── STEP 1: Initiate payment (Go gateway) ──────────────────────────────────
echo -e "${BOLD}STEP 1 — Initiate Payment${RESET}"
INITIATE=$(curl -s -X POST "$GW_URL/payment/initiate" \
  -H "Content-Type: application/json" \
  -d "{
    \"user_id\": \"$USER_ID\",
    \"amount\": $AMOUNT,
    \"currency\": \"$CURRENCY\",
    \"recipient\": \"bob@example.com\",
    \"description\": \"Test payment\"
  }")

echo "  Raw: $INITIATE"

TXN_ID=$(echo "$INITIATE" | python3 -c "import sys,json; print(json.load(sys.stdin)['transaction_id'])")
SESSION_ID=$(echo "$INITIATE" | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
RISK_LEVEL=$(echo "$INITIATE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('message',''))" | grep -oP 'Risk: \K[A-Z]+' || echo "UNKNOWN")

ok "Transaction created: $TXN_ID"
ok "Biometric session  : $SESSION_ID"
info "Risk level         : $RISK_LEVEL"
echo ""

# ── STEP 2: Face verification (Python biometric service) ───────────────────
echo -e "${BOLD}STEP 2 — Face Verification${RESET}"
FACE_RESP=$(curl -s -X POST "$BIO_URL/verify/face" \
  -F "user_id=$USER_ID" \
  -F "session_id=$SESSION_ID" \
  -F "image=@$FACE_IMG;type=image/jpeg")

echo "  Raw: $FACE_RESP"

FACE_VERIFIED=$(echo "$FACE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['verified'])")
FACE_CONF=$(echo "$FACE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['confidence'])")
FACE_DIST=$(echo "$FACE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['distance'])")
FACE_LIVE=$(echo "$FACE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['liveness_score'])")
FACE_REASON=$(echo "$FACE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['reason'])")

if [ "$FACE_VERIFIED" = "True" ]; then
  ok "Face verified — confidence=${FACE_CONF} distance=${FACE_DIST} liveness=${FACE_LIVE}"
else
  echo -e "  ${YELLOW}⚠${RESET} Face NOT verified — reason=${FACE_REASON} dist=${FACE_DIST}"
fi
echo ""

# ── STEP 3: Voice verification (Python biometric service) ──────────────────
echo -e "${BOLD}STEP 3 — Voice Verification${RESET}"
VOICE_RESP=$(curl -s -X POST "$BIO_URL/verify/voice" \
  -F "user_id=$USER_ID" \
  -F "session_id=$SESSION_ID" \
  -F "audio=@$VOICE_WAV;type=audio/wav")

echo "  Raw: $VOICE_RESP"

VOICE_VERIFIED=$(echo "$VOICE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['verified'])")
VOICE_SIM=$(echo "$VOICE_RESP"    | python3 -c "import sys,json; print(json.load(sys.stdin)['similarity'])")
VOICE_REPLAY=$(echo "$VOICE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['replay_detected'])")
VOICE_REASON=$(echo "$VOICE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['reason'])")

if [ "$VOICE_VERIFIED" = "True" ]; then
  ok "Voice verified — similarity=${VOICE_SIM} replay=${VOICE_REPLAY}"
else
  echo -e "  ${YELLOW}⚠${RESET} Voice NOT verified — reason=${VOICE_REASON} replay=${VOICE_REPLAY}"
fi
echo ""

# ── STEP 4: Confirm payment (Go gateway) ───────────────────────────────────
echo -e "${BOLD}STEP 4 — Confirm Payment (Biometric Gate)${RESET}"
CONFIRM=$(curl -s -X POST "$GW_URL/payment/confirm" \
  -H "Content-Type: application/json" \
  -d "{
    \"transaction_id\": \"$TXN_ID\",
    \"session_id\": \"$SESSION_ID\",
    \"user_id\": \"$USER_ID\"
  }")

echo "  Raw: $CONFIRM"

AUTHORIZED=$(echo "$CONFIRM"  | python3 -c "import sys,json; print(json.load(sys.stdin)['authorized'])")
FINAL_STATUS=$(echo "$CONFIRM"| python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
RISK_SCORE=$(echo "$CONFIRM"  | python3 -c "import sys,json; print(json.load(sys.stdin)['risk_score'])")
RISK_LVL=$(echo "$CONFIRM"    | python3 -c "import sys,json; print(json.load(sys.stdin)['risk_level'])")
MSG=$(echo "$CONFIRM"         | python3 -c "import sys,json; print(json.load(sys.stdin)['message'])")

echo ""
divider
echo -e "${BOLD}  RESULT${RESET}"
divider
if [ "$AUTHORIZED" = "True" ]; then
  echo -e "  ${GREEN}${BOLD}✓ PAYMENT AUTHORIZED${RESET}"
else
  echo -e "  ${RED}${BOLD}✗ PAYMENT DECLINED${RESET}"
  DECLINE=$(echo "$CONFIRM" | python3 -c "import sys,json; print(json.load(sys.stdin).get('decline_reason',''))" 2>/dev/null || echo "")
  [ -n "$DECLINE" ] && echo -e "  Reason: ${RED}$DECLINE${RESET}"
fi
echo -e "  Status    : $FINAL_STATUS"
echo -e "  Risk Score: $RISK_SCORE/100 ($RISK_LVL)"
echo -e "  Face ✓    : $FACE_VERIFIED (conf=$FACE_CONF)"
echo -e "  Voice ✓   : $VOICE_VERIFIED (sim=$VOICE_SIM)"
echo -e "  Message   : $MSG"
divider
