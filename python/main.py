"""
BioPay — Biometric Security Layer
Python FastAPI Service: Face + Voice verification before payment
Embeddings stored as encrypted .npy files — NO DATABASE
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from biometric.face_engine import FaceEngine
from biometric.voice_engine import VoiceEngine
from storage.embedding_store import EmbeddingStore
from storage.session_store import SessionStore
from models import (
    RegisterFaceRequest, RegisterVoiceRequest,
    VerifyFaceResponse, VerifyVoiceResponse,
    BiometricSession, SessionStatus,
    PaymentAuthRequest, PaymentAuthResponse,
)
from utils.secure_memory import secure_wipe_bytes
from utils.logger import setup_logger

# ─────────────────────────────────────────────────────────────────────────────
#  STARTUP / SHUTDOWN
# ─────────────────────────────────────────────────────────────────────────────
logger = setup_logger("biopay.main")

face_engine: FaceEngine = None
voice_engine: VoiceEngine = None
embedding_store: EmbeddingStore = None
session_store: SessionStore = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global face_engine, voice_engine, embedding_store, session_store

    logger.info("━━━ BioPay Biometric Service Starting ━━━")
    embedding_store = EmbeddingStore(base_dir=Path("data/embeddings"))
    session_store = SessionStore(ttl_seconds=300)   # 5-min session window

    logger.info("Loading face recognition model (dlib ResNet)...")
    face_engine = FaceEngine()

    logger.info("Loading voice encoder model (resemblyzer GE2E)...")
    voice_engine = VoiceEngine()

    logger.info("✓ All models loaded. Service ready.")
    yield

    # Shutdown — wipe any in-memory state
    logger.info("Shutting down. Wiping in-memory buffers...")
    if face_engine:
        face_engine.cleanup()
    if voice_engine:
        voice_engine.cleanup()
    session_store.clear_all()


app = FastAPI(
    title="BioPay Biometric Security Layer",
    version="2.0.0",
    description="Face + Voice biometric gate before payment. No database — embeddings stored as encrypted files.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
#  HEALTH
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    return {
        "status": "online",
        "face_model": face_engine.model_name if face_engine else "not_loaded",
        "voice_model": voice_engine.model_name if voice_engine else "not_loaded",
        "storage_backend": "encrypted_file_store",
        "database": "none",
        "timestamp": int(time.time()),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  REGISTRATION ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/register/face", tags=["Registration"])
async def register_face(
    user_id: str = Form(...),
    image: UploadFile = File(..., description="Clear frontal face photo (JPEG/PNG)"),
):
    """
    Register a user's face by uploading a photo.
    Extracts a 128-D ResNet embedding and stores it as an encrypted .npy file.
    The original image is NEVER persisted — only the embedding.
    """
    raw_bytes = None
    embedding = None
    try:
        raw_bytes = await image.read()
        logger.info(f"[register/face] user={user_id} bytes={len(raw_bytes)}")

        embedding, face_meta = face_engine.extract_embedding(raw_bytes)

        if embedding is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "no_face_detected",
                    "message": "No face detected in the uploaded image. "
                               "Ensure good lighting and a clear frontal view.",
                },
            )

        embedding_store.save_face_embedding(user_id, embedding)

        return {
            "status": "registered",
            "user_id": user_id,
            "embedding_dim": len(embedding),
            "face_confidence": round(face_meta["detection_confidence"], 4),
            "storage": "encrypted_file",
            "message": "Face embedding saved. Original image discarded.",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[register/face] ERROR: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
    finally:
        # Secure wipe — raw image bytes and embedding cleared from RAM
        if raw_bytes:
            secure_wipe_bytes(bytearray(raw_bytes))
        await image.close()


@app.post("/register/voice", tags=["Registration"])
async def register_voice(
    user_id: str = Form(...),
    audio: UploadFile = File(..., description="WAV audio of user speaking passphrase (≥3 seconds)"),
):
    """
    Register a user's voice by uploading a WAV recording.
    Extracts a 256-D d-vector speaker embedding using GE2E (resemblyzer).
    The audio file is NEVER persisted — only the embedding.
    """
    raw_bytes = None
    embedding = None
    try:
        raw_bytes = await audio.read()
        logger.info(f"[register/voice] user={user_id} bytes={len(raw_bytes)}")

        embedding, voice_meta = voice_engine.extract_embedding(raw_bytes)

        if embedding is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "voice_extraction_failed",
                    "message": "Could not extract a voice embedding. "
                               "Ensure the audio is clear, WAV format, ≥3 seconds.",
                },
            )

        embedding_store.save_voice_embedding(user_id, embedding)

        return {
            "status": "registered",
            "user_id": user_id,
            "embedding_dim": len(embedding),
            "duration_seconds": round(voice_meta.get("duration", 0), 2),
            "storage": "encrypted_file",
            "message": "Voice embedding saved. Original audio discarded.",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[register/voice] ERROR: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
    finally:
        if raw_bytes:
            secure_wipe_bytes(bytearray(raw_bytes))
        await audio.close()


# ─────────────────────────────────────────────────────────────────────────────
#  VERIFICATION ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/verify/face", response_model=VerifyFaceResponse, tags=["Verification"])
async def verify_face(
    user_id: str = Form(...),
    session_id: str = Form(...),
    image: UploadFile = File(...),
):
    """
    Verify face against registered embedding.
    Returns match confidence and updates the session.
    """
    raw_bytes = None
    live_embedding = None
    try:
        # 1. Load registered embedding
        stored = embedding_store.load_face_embedding(user_id)
        if stored is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_registered", "message": f"No face profile for user {user_id}"}
            )

        # 2. Extract live embedding from uploaded photo
        raw_bytes = await image.read()
        live_embedding, face_meta = face_engine.extract_embedding(raw_bytes)

        if live_embedding is None:
            result = VerifyFaceResponse(
                verified=False,
                confidence=0.0,
                distance=1.0,
                liveness_score=0.0,
                reason="no_face_detected",
            )
            session_store.update(session_id, face_result=result)
            return result

        # 3. Compute euclidean distance against stored embedding
        distance, confidence = face_engine.compare(live_embedding, stored)

        # 4. Liveness estimation (texture/frequency analysis on the image)
        liveness = face_engine.estimate_liveness(raw_bytes, face_meta)

        verified = (distance < face_engine.THRESHOLD) and (liveness > 0.50)

        result = VerifyFaceResponse(
            verified=verified,
            confidence=round(confidence, 4),
            distance=round(distance, 4),
            liveness_score=round(liveness, 4),
            reason="match" if verified else ("liveness_failed" if distance < face_engine.THRESHOLD else "distance_too_large"),
        )

        session_store.update(session_id, face_result=result)
        logger.info(f"[verify/face] user={user_id} dist={distance:.4f} conf={confidence:.4f} live={liveness:.4f} → {verified}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[verify/face] ERROR: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if raw_bytes:
            secure_wipe_bytes(bytearray(raw_bytes))
        if live_embedding is not None:
            secure_wipe_bytes(live_embedding.tobytes())
        await image.close()


@app.post("/verify/voice", response_model=VerifyVoiceResponse, tags=["Verification"])
async def verify_voice(
    user_id: str = Form(...),
    session_id: str = Form(...),
    audio: UploadFile = File(...),
):
    """
    Verify voice against registered speaker embedding.
    Uses cosine similarity between d-vectors.
    """
    raw_bytes = None
    live_embedding = None
    try:
        stored = embedding_store.load_voice_embedding(user_id)
        if stored is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_registered", "message": f"No voice profile for user {user_id}"}
            )

        raw_bytes = await audio.read()
        live_embedding, voice_meta = voice_engine.extract_embedding(raw_bytes)

        if live_embedding is None:
            result = VerifyVoiceResponse(
                verified=False, similarity=0.0,
                replay_detected=False, reason="extraction_failed"
            )
            session_store.update(session_id, voice_result=result)
            return result

        # Replay attack detection — checks for spectral anomalies
        replay_score = voice_engine.detect_replay(raw_bytes)
        replay_detected = replay_score > 0.75

        if replay_detected:
            logger.warning(f"[verify/voice] REPLAY ATTACK DETECTED user={user_id} score={replay_score:.3f}")
            result = VerifyVoiceResponse(
                verified=False, similarity=0.0,
                replay_detected=True, reason="replay_attack_detected"
            )
            session_store.update(session_id, voice_result=result)
            return result

        similarity = voice_engine.cosine_similarity(live_embedding, stored)
        verified = similarity > voice_engine.THRESHOLD

        result = VerifyVoiceResponse(
            verified=verified,
            similarity=round(float(similarity), 4),
            replay_detected=False,
            reason="match" if verified else "similarity_below_threshold",
        )

        session_store.update(session_id, voice_result=result)
        logger.info(f"[verify/voice] user={user_id} sim={similarity:.4f} → {verified}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[verify/voice] ERROR: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if raw_bytes:
            secure_wipe_bytes(bytearray(raw_bytes))
        await audio.close()


# ─────────────────────────────────────────────────────────────────────────────
#  SESSION MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/session/create", tags=["Session"])
def create_session(user_id: str = Form(...), amount: float = Form(...)):
    """Create a biometric verification session before a payment attempt."""
    session_id = str(uuid.uuid4())
    session_store.create(session_id, user_id=user_id, amount=amount)
    logger.info(f"[session] created={session_id} user={user_id} amount={amount}")
    return {"session_id": session_id, "expires_in": 300, "required": ["FACE", "VOICE"]}


@app.get("/session/{session_id}", tags=["Session"])
def get_session(session_id: str):
    """Get current session verification status."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return session.to_dict()


@app.post("/session/{session_id}/authorize", tags=["Session"])
def authorize_payment(session_id: str):
    """
    Final authorization check — called by the Go payment gateway.
    Returns AUTHORIZED only if both face AND voice are verified.
    """
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session expired or not found")

    authorized = session.face_verified and session.voice_verified

    if authorized:
        session_store.mark_authorized(session_id)
        logger.info(f"[authorize] AUTHORIZED session={session_id} user={session.user_id}")
    else:
        missing = []
        if not session.face_verified:
            missing.append("FACE")
        if not session.voice_verified:
            missing.append("VOICE")
        logger.warning(f"[authorize] DENIED session={session_id} missing={missing}")

    return {
        "authorized": authorized,
        "session_id": session_id,
        "user_id": session.user_id,
        "face_verified": session.face_verified,
        "voice_verified": session.voice_verified,
        "face_confidence": session.face_confidence,
        "voice_similarity": session.voice_similarity,
        "missing_factors": [] if authorized else missing,
    }


@app.delete("/register/{user_id}", tags=["Registration"])
def delete_profile(user_id: str):
    """Permanently delete a user's biometric embeddings."""
    deleted = embedding_store.delete_all(user_id)
    return {"deleted": deleted, "user_id": user_id}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=False, log_level="info")
