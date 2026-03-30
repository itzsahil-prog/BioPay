"""
storage/session_store.py
In-memory session store with TTL expiry.
Sessions are never written to disk — pure RAM only.
"""

import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict

logger = logging.getLogger("biopay.sessions")


@dataclass
class BiometricSession:
    session_id: str
    user_id: str
    amount: float
    created_at: float = field(default_factory=time.time)
    ttl: float = 300.0              # 5 minutes

    face_verified: bool = False
    face_confidence: float = 0.0
    face_distance: float = 1.0
    face_liveness: float = 0.0

    voice_verified: bool = False
    voice_similarity: float = 0.0
    replay_detected: bool = False

    authorized: bool = False
    status: str = "PENDING"         # PENDING | FACE_OK | VOICE_OK | AUTHORIZED | EXPIRED | DENIED

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl

    def to_dict(self) -> dict:
        return {
            "session_id":      self.session_id,
            "user_id":         self.user_id,
            "amount":          self.amount,
            "status":          self.status,
            "face_verified":   self.face_verified,
            "face_confidence": round(self.face_confidence, 4),
            "face_liveness":   round(self.face_liveness, 4),
            "voice_verified":  self.voice_verified,
            "voice_similarity": round(self.voice_similarity, 4),
            "replay_detected": self.replay_detected,
            "authorized":      self.authorized,
            "expires_in":      max(0, int(self.ttl - (time.time() - self.created_at))),
        }


class SessionStore:
    def __init__(self, ttl_seconds: float = 300):
        self._sessions: Dict[str, BiometricSession] = {}
        self._lock = threading.RLock()
        self._default_ttl = ttl_seconds
        # Background cleanup every 60s
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def create(self, session_id: str, user_id: str, amount: float) -> BiometricSession:
        s = BiometricSession(
            session_id=session_id,
            user_id=user_id,
            amount=amount,
            ttl=self._default_ttl,
        )
        with self._lock:
            self._sessions[session_id] = s
        return s

    def get(self, session_id: str) -> Optional[BiometricSession]:
        with self._lock:
            s = self._sessions.get(session_id)
            if s and s.is_expired():
                s.status = "EXPIRED"
                del self._sessions[session_id]
                return None
            return s

    def update(
        self,
        session_id: str,
        face_result=None,
        voice_result=None,
    ) -> Optional[BiometricSession]:
        with self._lock:
            s = self._sessions.get(session_id)
            if not s:
                logger.warning(f"Session not found: {session_id}")
                return None

            if face_result is not None:
                s.face_verified   = face_result.verified
                s.face_confidence = face_result.confidence
                s.face_distance   = face_result.distance
                s.face_liveness   = face_result.liveness_score

            if voice_result is not None:
                s.voice_verified  = voice_result.verified
                s.voice_similarity = voice_result.similarity
                s.replay_detected  = voice_result.replay_detected

            # Update status
            if s.face_verified and s.voice_verified:
                s.status = "BOTH_VERIFIED"
            elif s.face_verified:
                s.status = "FACE_OK"
            elif s.voice_verified:
                s.status = "VOICE_OK"
            else:
                s.status = "PENDING"

            return s

    def mark_authorized(self, session_id: str) -> None:
        with self._lock:
            s = self._sessions.get(session_id)
            if s:
                s.authorized = True
                s.status = "AUTHORIZED"

    def clear_all(self) -> None:
        with self._lock:
            self._sessions.clear()
        logger.info("All sessions cleared")

    def _cleanup_loop(self):
        while True:
            time.sleep(60)
            expired = []
            with self._lock:
                for sid, s in list(self._sessions.items()):
                    if s.is_expired():
                        expired.append(sid)
                for sid in expired:
                    del self._sessions[sid]
            if expired:
                logger.info(f"Session GC: removed {len(expired)} expired sessions")
