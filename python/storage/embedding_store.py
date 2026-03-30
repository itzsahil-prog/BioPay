"""
storage/embedding_store.py
Encrypted file-based embedding storage — NO DATABASE.

Layout on disk:
  data/embeddings/
  ├── face/
  │   └── <user_id>.face.enc     (AES-256-GCM encrypted numpy array)
  └── voice/
      └── <user_id>.voice.enc    (AES-256-GCM encrypted numpy array)

Master key is derived from BIOPAY_SECRET_KEY env var via PBKDF2-HMAC-SHA256.
Each file has its own random 12-byte nonce stored as a prefix.

File format (binary):
  [4 bytes: magic "BPEM"] [12 bytes: nonce] [N bytes: ciphertext]
"""

import gc
import hashlib
import hmac
import logging
import os
import struct
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("biopay.store")

MAGIC = b"BPEM"   # BioPay Embedding
NONCE_LEN = 12


class EmbeddingStore:
    """
    AES-256-GCM encrypted embedding store backed by the filesystem.
    Zero database dependency.
    """

    def __init__(self, base_dir: Path):
        self._face_dir  = base_dir / "face"
        self._voice_dir = base_dir / "voice"
        self._face_dir.mkdir(parents=True, exist_ok=True)
        self._voice_dir.mkdir(parents=True, exist_ok=True)
        self._master_key = self._derive_master_key()
        logger.info(f"EmbeddingStore initialized at {base_dir} (AES-256-GCM)")

    # ── Public API ────────────────────────────────────────────────────────
    def save_face_embedding(self, user_id: str, embedding: np.ndarray) -> None:
        path = self._face_path(user_id)
        self._save(path, embedding)
        logger.info(f"[store] Face embedding saved: user={user_id} dim={len(embedding)} path={path.name}")

    def load_face_embedding(self, user_id: str) -> Optional[np.ndarray]:
        path = self._face_path(user_id)
        if not path.exists():
            return None
        return self._load(path)

    def save_voice_embedding(self, user_id: str, embedding: np.ndarray) -> None:
        path = self._voice_path(user_id)
        self._save(path, embedding)
        logger.info(f"[store] Voice embedding saved: user={user_id} dim={len(embedding)} path={path.name}")

    def load_voice_embedding(self, user_id: str) -> Optional[np.ndarray]:
        path = self._voice_path(user_id)
        if not path.exists():
            return None
        return self._load(path)

    def has_face(self, user_id: str) -> bool:
        return self._face_path(user_id).exists()

    def has_voice(self, user_id: str) -> bool:
        return self._voice_path(user_id).exists()

    def delete_all(self, user_id: str) -> dict:
        deleted = {"face": False, "voice": False}
        fp = self._face_path(user_id)
        vp = self._voice_path(user_id)
        if fp.exists():
            self._secure_delete(fp)
            deleted["face"] = True
        if vp.exists():
            self._secure_delete(vp)
            deleted["voice"] = True
        logger.info(f"[store] Deleted embeddings for user={user_id}: {deleted}")
        return deleted

    def list_users(self) -> dict:
        face_ids  = {p.stem.split(".")[0] for p in self._face_dir.glob("*.face.enc")}
        voice_ids = {p.stem.split(".")[0] for p in self._voice_dir.glob("*.voice.enc")}
        return {
            "face_registered":  sorted(face_ids),
            "voice_registered": sorted(voice_ids),
            "both_registered":  sorted(face_ids & voice_ids),
        }

    # ── Internal serialisation ─────────────────────────────────────────
    def _save(self, path: Path, embedding: np.ndarray) -> None:
        """Serialize embedding to bytes, encrypt, write to disk."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        # Derive a per-file key to limit blast radius
        file_key = self._derive_file_key(path.name)
        nonce = os.urandom(NONCE_LEN)
        raw = embedding.astype(np.float32).tobytes()
        aesgcm = AESGCM(file_key)
        ct = aesgcm.encrypt(nonce, raw, None)

        payload = MAGIC + nonce + ct
        path.write_bytes(payload)

        # Wipe sensitive bytes from RAM
        self._wipe(bytearray(raw))
        self._wipe(bytearray(file_key))
        del raw, file_key, ct
        gc.collect()

    def _load(self, path: Path) -> Optional[np.ndarray]:
        """Read, decrypt, and deserialize an embedding file."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.exceptions import InvalidTag

        payload = path.read_bytes()
        if len(payload) < 4 + NONCE_LEN or payload[:4] != MAGIC:
            logger.error(f"Invalid embedding file: {path}")
            return None

        nonce = payload[4 : 4 + NONCE_LEN]
        ct    = payload[4 + NONCE_LEN :]
        file_key = self._derive_file_key(path.name)

        try:
            aesgcm = AESGCM(file_key)
            raw = aesgcm.decrypt(nonce, ct, None)
            embedding = np.frombuffer(raw, dtype=np.float32).copy()
            return embedding
        except InvalidTag:
            logger.error(f"AES-GCM authentication failed for {path} — file tampered?")
            return None
        except Exception as e:
            logger.error(f"Load error {path}: {e}")
            return None
        finally:
            self._wipe(bytearray(file_key))
            del file_key
            gc.collect()

    # ── Key derivation ─────────────────────────────────────────────────
    def _derive_master_key(self) -> bytes:
        secret = os.environ.get("BIOPAY_SECRET_KEY", "dev-insecure-change-in-production")
        if secret == "dev-insecure-change-in-production":
            logger.warning("⚠ Using default dev key. Set BIOPAY_SECRET_KEY in production!")
        return hashlib.pbkdf2_hmac(
            "sha256",
            secret.encode(),
            b"biopay-embedding-store-v1",
            iterations=200_000,
            dklen=32,
        )

    def _derive_file_key(self, filename: str) -> bytes:
        """Derive a unique 256-bit key per file using HMAC-SHA256."""
        return hmac.new(
            self._master_key,
            filename.encode(),
            hashlib.sha256,
        ).digest()

    # ── Helpers ────────────────────────────────────────────────────────
    def _face_path(self, user_id: str) -> Path:
        safe = self._safe_id(user_id)
        return self._face_dir / f"{safe}.face.enc"

    def _voice_path(self, user_id: str) -> Path:
        safe = self._safe_id(user_id)
        return self._voice_dir / f"{safe}.voice.enc"

    def _safe_id(self, user_id: str) -> str:
        """Hash the user_id so it never appears in plaintext on disk."""
        return hashlib.sha256(user_id.encode()).hexdigest()[:32]

    def _secure_delete(self, path: Path) -> None:
        """Overwrite file contents with zeros before deleting."""
        try:
            size = path.stat().st_size
            with path.open("wb") as f:
                f.write(b"\x00" * size)
            path.unlink()
        except Exception as e:
            logger.error(f"Secure delete failed for {path}: {e}")
            path.unlink(missing_ok=True)

    def _wipe(self, buf: bytearray) -> None:
        for i in range(len(buf)):
            buf[i] = 0
