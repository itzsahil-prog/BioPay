#!/usr/bin/env bash
# scripts/register_user.sh
# Register a user's face (image) and voice (audio) with the biometric service.
# Usage: ./scripts/register_user.sh <user_id> <face_image_path> <voice_wav_path>

set -euo pipefail

USER_ID="${1:-user-001}"
FACE_IMG="${2:-sample_face.jpg}"
VOICE_WAV="${3:-sample_voice.wav}"
BIO_URL="${BIOMETRIC_SERVICE_URL:-http://localhost:8001}"

echo "━━━ BioPay User Registration ━━━"
echo "  User ID   : $USER_ID"
echo "  Face image: $FACE_IMG"
echo "  Voice WAV : $VOICE_WAV"
echo "  Service   : $BIO_URL"
echo ""

# 1. Register face
echo "▶ [1/2] Registering face embedding..."
FACE_RESP=$(curl -s -X POST "$BIO_URL/register/face" \
  -F "user_id=$USER_ID" \
  -F "image=@$FACE_IMG;type=image/jpeg")

echo "  Response: $FACE_RESP"

FACE_STATUS=$(echo "$FACE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','error'))" 2>/dev/null || echo "error")
if [ "$FACE_STATUS" != "registered" ]; then
  echo "  ✗ Face registration failed!"
  exit 1
fi
echo "  ✓ Face registered (128-D embedding encrypted to disk)"

echo ""

# 2. Register voice
echo "▶ [2/2] Registering voice embedding..."
VOICE_RESP=$(curl -s -X POST "$BIO_URL/register/voice" \
  -F "user_id=$USER_ID" \
  -F "audio=@$VOICE_WAV;type=audio/wav")

echo "  Response: $VOICE_RESP"

VOICE_STATUS=$(echo "$VOICE_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','error'))" 2>/dev/null || echo "error")
if [ "$VOICE_STATUS" != "registered" ]; then
  echo "  ✗ Voice registration failed!"
  exit 1
fi
echo "  ✓ Voice registered (256-D d-vector encrypted to disk)"

echo ""
echo "━━━ Registration Complete ━━━"
echo "  User $USER_ID is now enrolled."
echo "  Face + voice embeddings stored as AES-256-GCM encrypted files."
echo "  No database used."
