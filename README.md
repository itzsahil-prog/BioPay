# BioPay — Biometric Security Layer Before Payment

**Face + Voice verification gate built in Python (FastAPI) + Go (Gin).**  
Zero database. Embeddings stored as AES-256-GCM encrypted files only.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          PAYMENT FLOW                               │
│                                                                     │
│  Client                Go Gateway :8080          Python Svc :8001  │
│    │                        │                         │             │
│    │── POST /payment/initiate ──>                     │             │
│    │                        │── POST /session/create ─>            │
│    │<── {txn_id, session_id} ──                       │             │
│    │                        │                         │             │
│    │    ┌──────────────────────────────────────────────────────┐   │
│    │    │   BIOMETRIC VERIFICATION WINDOW (5 min)              │   │
│    │    │                                                       │   │
│    │────│──── POST /verify/face  (image upload) ──────────────>│   │
│    │    │         [dlib ResNet-34: 128-D embedding compare]     │   │
│    │<───│──── {verified, confidence, distance, liveness} ──────│   │
│    │    │                                                       │   │
│    │────│──── POST /verify/voice (audio upload) ───────────────>│   │
│    │    │         [GE2E d-vector: 256-D cosine similarity]      │   │
│    │<───│──── {verified, similarity, replay_detected} ──────────│   │
│    │    └──────────────────────────────────────────────────────┘   │
│    │                        │                         │             │
│    │── POST /payment/confirm ──>                      │             │
│    │                        │── POST /session/authorize ─>         │
│    │                        │<── {authorized, face✓, voice✓} ──   │
│    │                        │                                       │
│    │                    [Risk Engine]                               │
│    │                        │  Score: 0–100                        │
│    │                        │  Factors: amount, bio, device, time  │
│    │                        │                                       │
│    │<── 200 AUTHORIZED  ───── (risk<80 AND face✓ AND voice✓)       │
│    │    OR                                                          │
│    │<── 403 DECLINED    ─────                                       │
└─────────────────────────────────────────────────────────────────────┘
```

## Storage — No Database

```
data/embeddings/
├── face/
│   └── <sha256(user_id)[:32]>.face.enc   ← AES-256-GCM encrypted float32[128]
└── voice/
    └── <sha256(user_id)[:32]>.voice.enc  ← AES-256-GCM encrypted float32[256]
```

**File format** (`BPEM`):
```
[4 bytes: magic "BPEM"]
[12 bytes: random nonce]
[N bytes: AES-256-GCM ciphertext + 16-byte auth tag]
```

- Master key: derived via PBKDF2-HMAC-SHA256 (200,000 iterations) from `BIOPAY_SECRET_KEY`
- Per-file key: HMAC-SHA256(master_key, filename) — limits blast radius
- User IDs are SHA-256 hashed in filenames — never stored in plaintext
- GCM authentication tag detects any file tampering

---

## Quick Start

### 1. Run with Docker Compose

```bash
# Set a strong secret key
export BIOPAY_SECRET_KEY="your-32-char-production-key-here"

docker compose up --build
```

Services start at:
- **Python biometric**: `http://localhost:8001`
- **Go gateway**: `http://localhost:8080`

---

### 2. Install manually

**Python service:**
```bash
cd python

# Ubuntu — required for dlib (face_recognition)
sudo apt-get install -y cmake libopenblas-dev liblapack-dev

pip install -r requirements.txt
BIOPAY_SECRET_KEY="your-secret" python main.py
```

**Go gateway:**
```bash
cd go
go mod tidy
BIOMETRIC_SERVICE_URL=http://localhost:8001 go run ./cmd/server
```

---

## API Reference

### Python Biometric Service (`:8001`)

#### Register Face
```http
POST /register/face
Content-Type: multipart/form-data

user_id=alice
image=<JPEG/PNG file>
```
```json
{
  "status": "registered",
  "user_id": "alice",
  "embedding_dim": 128,
  "face_confidence": 0.9421,
  "storage": "encrypted_file",
  "message": "Face embedding saved. Original image discarded."
}
```

#### Register Voice
```http
POST /register/voice
Content-Type: multipart/form-data

user_id=alice
audio=<WAV file, ≥3 seconds>
```

#### Verify Face
```http
POST /verify/face
Content-Type: multipart/form-data

user_id=alice
session_id=<uuid>
image=<JPEG/PNG>
```
```json
{
  "verified": true,
  "confidence": 0.8934,
  "distance": 0.3241,
  "liveness_score": 0.7821,
  "reason": "match"
}
```

#### Verify Voice
```http
POST /verify/voice
Content-Type: multipart/form-data

user_id=alice
session_id=<uuid>
audio=<WAV>
```
```json
{
  "verified": true,
  "similarity": 0.8821,
  "replay_detected": false,
  "reason": "match"
}
```

---

### Go Payment Gateway (`:8080`)

#### Step 1 — Initiate Payment
```http
POST /payment/initiate
Content-Type: application/json

{
  "user_id": "alice",
  "amount": 500.00,
  "currency": "USD",
  "recipient": "bob@example.com",
  "description": "Invoice #1234"
}
```
```json
{
  "transaction_id": "a1b2c3d4-...",
  "session_id": "e5f6g7h8-...",
  "status": "PENDING_BIOMETRIC",
  "required_auth": ["FACE", "VOICE"],
  "biometric_service_url": "http://localhost:8001",
  "expires_in_seconds": 300,
  "message": "Payment of 500.00 USD initiated. Risk: LOW (12/100)"
}
```

#### Step 2 — Complete biometrics via Python service  
_(use session_id from step 1)_

#### Step 3 — Confirm Payment
```http
POST /payment/confirm
Content-Type: application/json

{
  "transaction_id": "a1b2c3d4-...",
  "session_id": "e5f6g7h8-...",
  "user_id": "alice"
}
```
```json
{
  "transaction_id": "a1b2c3d4-...",
  "status": "AUTHORIZED",
  "authorized": true,
  "risk_score": 12,
  "risk_level": "LOW",
  "face_confidence": 0.8934,
  "voice_similarity": 0.8821,
  "message": "Payment of 500.00 USD AUTHORIZED. Both face and voice verified."
}
```

---

## Full Test Flow

```bash
# 1. Register a user
./scripts/register_user.sh user-001 /path/to/face.jpg /path/to/voice.wav

# 2. Run full payment flow
./scripts/test_payment_flow.sh user-001 /path/to/face.jpg /path/to/voice.wav 500.00

# 3. Python unit tests
cd python && pytest tests/ -v

# 4. Go tests
cd go && go test ./...
```

---

## Risk Engine

The Go gateway computes a **risk score (0–100)** combining:

| Factor | Weight | Triggers |
|--------|--------|----------|
| Transaction amount | 35% | >$1K medium, >$10K high |
| Biometric confidence | 30% | Face conf <0.85, voice sim <0.75 |
| Device fingerprint | 15% | Unknown device |
| Time of day | 10% | 00:00–05:00 UTC |
| Velocity | 10% | High amount + low trust score |

**Decision matrix:**

| Score | Level | Auth Required | Block? |
|-------|-------|---------------|--------|
| 0–34 | LOW | FACE | No |
| 35–59 | MEDIUM | FACE | No |
| 60–79 | HIGH | FACE + VOICE | No |
| 80–100 | HIGH | FACE + VOICE + OTP | **Yes** |
| any | CRITICAL | All | **Yes** if bio=failed |

---

## Security Properties

- **No raw biometrics on disk** — only embeddings, immediately after extraction the image/audio is wiped from RAM
- **AES-256-GCM** with per-file derived keys — tamper-evident (auth tag fails)  
- **User IDs hashed** in filenames — SHA-256, never stored plaintext  
- **Secure memory wipe** — both Python (`ctypes.memset`) and Go (`SecureZeroBytes`) overwrite sensitive buffers  
- **Session TTL** — biometric sessions expire after 5 minutes  
- **Replay attack detection** — spectral flatness + entropy analysis on voice  
- **Liveness estimation** — Laplacian variance + frequency analysis on face images  
- **Constant-time comparison** in Go for HMAC verification (prevents timing attacks)

---

## Environment Variables

| Variable | Service | Default | Description |
|----------|---------|---------|-------------|
| `BIOPAY_SECRET_KEY` | Python | `dev-insecure-...` | Master key for embedding encryption |
| `BIOMETRIC_SERVICE_URL` | Go | `http://localhost:8001` | Python service URL |
| `GATEWAY_PORT` | Go | `8080` | Gateway listen port |

---

## Project Structure

```
biopay/
├── python/                     ← FastAPI biometric service
│   ├── main.py                 ← API endpoints
│   ├── biometric/
│   │   ├── face_engine.py      ← dlib ResNet-34 128-D embeddings
│   │   └── voice_engine.py     ← GE2E d-vector 256-D + replay detection
│   ├── storage/
│   │   ├── embedding_store.py  ← AES-256-GCM encrypted file store
│   │   └── session_store.py    ← In-memory session store (TTL)
│   ├── utils/
│   │   └── secure_memory.py    ← ctypes memory wipe
│   ├── tests/
│   │   └── test_biometric_engines.py
│   ├── requirements.txt
│   └── Dockerfile
├── go/                         ← Gin payment gateway
│   ├── cmd/server/main.go      ← Server, handlers, TxnStore
│   ├── internal/
│   │   ├── engine/risk.go      ← Risk scoring engine
│   │   ├── middleware/biometric.go  ← Python service HTTP client
│   │   ├── models/models.go    ← Domain types
│   │   └── security/security.go    ← SecureZeroBytes, HMAC, tokens
│   ├── go.mod
│   └── Dockerfile
├── scripts/
│   ├── register_user.sh        ← Register face + voice via curl
│   └── test_payment_flow.sh    ← Full end-to-end test
├── data/embeddings/            ← Encrypted .enc files (not in git)
│   ├── face/
│   └── voice/
└── docker-compose.yml
```
