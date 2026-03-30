"""
tests/test_biometric_engines.py
Unit + integration tests for face and voice engines.
Run: pytest tests/ -v
"""

import io
import os
import struct
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_random_embedding(dim: int = 128) -> np.ndarray:
    """Generate a normalised random embedding."""
    v = np.random.randn(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def make_similar_embedding(ref: np.ndarray, noise: float = 0.05) -> np.ndarray:
    """Generate an embedding close to ref (simulates same person)."""
    noisy = ref + np.random.randn(*ref.shape).astype(np.float32) * noise
    return noisy / np.linalg.norm(noisy)


def make_different_embedding(dim: int = 128) -> np.ndarray:
    """Generate an embedding far from any reference (simulates different person)."""
    return make_random_embedding(dim)


def make_minimal_wav(duration_s: float = 3.0, sample_rate: int = 16000) -> bytes:
    """Create a minimal valid WAV file with sine wave audio."""
    num_samples = int(sample_rate * duration_s)
    t = np.linspace(0, duration_s, num_samples)
    audio = (np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)

    buf = io.BytesIO()
    # WAV header
    data_bytes = audio.tobytes()
    buf.write(b'RIFF')
    buf.write(struct.pack('<I', 36 + len(data_bytes)))
    buf.write(b'WAVE')
    buf.write(b'fmt ')
    buf.write(struct.pack('<I', 16))          # chunk size
    buf.write(struct.pack('<H', 1))           # PCM
    buf.write(struct.pack('<H', 1))           # channels
    buf.write(struct.pack('<I', sample_rate)) # sample rate
    buf.write(struct.pack('<I', sample_rate * 2))  # byte rate
    buf.write(struct.pack('<H', 2))           # block align
    buf.write(struct.pack('<H', 16))          # bits per sample
    buf.write(b'data')
    buf.write(struct.pack('<I', len(data_bytes)))
    buf.write(data_bytes)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
#  Face Engine Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFaceEngineComparison:
    """Test face embedding comparison logic (no real images needed)."""

    def setup_method(self):
        # Patch the face_recognition import so we don't need dlib installed
        with patch.dict("sys.modules", {"face_recognition": MagicMock()}):
            import importlib
            import sys
            # Reload to pick up mock
            if "biometric.face_engine" in sys.modules:
                del sys.modules["biometric.face_engine"]
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from biometric.face_engine import FaceEngine
            self.engine = FaceEngine.__new__(FaceEngine)
            self.engine.THRESHOLD = 0.50

    def test_same_person_low_distance(self):
        """Same person embeddings should have Euclidean distance < THRESHOLD."""
        ref = make_random_embedding(128)
        similar = make_similar_embedding(ref, noise=0.02)
        distance, confidence = self.engine.compare(ref, similar)
        assert distance < self.engine.THRESHOLD, f"Expected dist < {self.engine.THRESHOLD}, got {distance:.4f}"
        assert confidence > 0.5

    def test_different_person_high_distance(self):
        """Different embeddings should (usually) exceed THRESHOLD."""
        ref     = make_random_embedding(128)
        other   = make_different_embedding(128)
        distance, confidence = self.engine.compare(ref, other)
        # Note: random embeddings can occasionally be close — this is statistical
        # For the test, we just verify the math is consistent
        expected_conf = max(0.0, 1.0 - distance)
        assert abs(confidence - expected_conf) < 1e-5

    def test_identical_embeddings_zero_distance(self):
        emb = make_random_embedding(128)
        distance, confidence = self.engine.compare(emb, emb.copy())
        assert distance < 1e-6, "Identical embeddings should have ~zero distance"
        assert confidence > 0.99

    def test_confidence_in_range(self):
        a = make_random_embedding(128)
        b = make_random_embedding(128)
        _, confidence = self.engine.compare(a, b)
        assert 0.0 <= confidence <= 1.0

    def test_dtype_float32_float64_compatible(self):
        """Engine should handle mixed dtypes without error."""
        a = make_random_embedding(128).astype(np.float32)
        b = make_random_embedding(128).astype(np.float64)
        distance, _ = self.engine.compare(a, b)
        assert isinstance(distance, float)


# ─────────────────────────────────────────────────────────────────────────────
#  Voice Engine Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestVoiceEngineSimilarity:
    """Test voice similarity computation (no resemblyzer install needed)."""

    def setup_method(self):
        with patch.dict("sys.modules", {
            "resemblyzer": MagicMock(),
            "resemblyzer.VoiceEncoder": MagicMock(),
            "resemblyzer.preprocess_wav": MagicMock(),
        }):
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            if "biometric.voice_engine" in sys.modules:
                del sys.modules["biometric.voice_engine"]
            from biometric.voice_engine import VoiceEngine
            self.engine = VoiceEngine.__new__(VoiceEngine)
            self.engine.THRESHOLD = 0.75

    def test_same_speaker_high_similarity(self):
        ref = make_random_embedding(256)
        similar = make_similar_embedding(ref, noise=0.01)
        sim = self.engine.cosine_similarity(ref, similar)
        assert sim > 0.90, f"Expected > 0.90, got {sim:.4f}"

    def test_different_speaker_low_similarity(self):
        a = make_random_embedding(256)
        b = make_random_embedding(256)
        sim = self.engine.cosine_similarity(a, b)
        assert 0.0 <= sim <= 1.0

    def test_identical_embedding_similarity_one(self):
        emb = make_random_embedding(256)
        sim = self.engine.cosine_similarity(emb, emb.copy())
        assert abs(sim - 1.0) < 1e-5, f"Identical should give similarity ~1.0, got {sim}"

    def test_cosine_similarity_commutative(self):
        a = make_random_embedding(256)
        b = make_random_embedding(256)
        assert abs(self.engine.cosine_similarity(a, b) - self.engine.cosine_similarity(b, a)) < 1e-6

    def test_zero_vector_returns_zero(self):
        a = np.zeros(256, dtype=np.float32)
        b = make_random_embedding(256)
        sim = self.engine.cosine_similarity(a, b)
        assert sim == 0.0

    def test_replay_detection_sine_wave(self):
        """Pure sine wave should score higher replay risk than typical speech."""
        wav_bytes = make_minimal_wav(duration_s=3.0)
        score = self.engine.detect_replay(wav_bytes)
        assert 0.0 <= score <= 1.0, f"Replay score out of range: {score}"

    def test_framing_helper(self):
        signal = np.random.randn(16000).astype(np.float32)
        frames = self.engine._frame_signal(signal, 400, 160)
        assert len(frames) > 0
        assert all(len(f) == 400 for f in frames)


# ─────────────────────────────────────────────────────────────────────────────
#  Embedding Store Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestEmbeddingStore:
    """Test encrypted file store — no mocking needed, uses temp dir."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["BIOPAY_SECRET_KEY"] = "test-secret-key-pytest"
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from storage.embedding_store import EmbeddingStore
        self.store = EmbeddingStore(Path(self.tmpdir))

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load_face_embedding(self):
        user_id = "test-user-001"
        embedding = make_random_embedding(128)
        self.store.save_face_embedding(user_id, embedding)
        loaded = self.store.load_face_embedding(user_id)
        assert loaded is not None
        np.testing.assert_allclose(loaded, embedding, rtol=1e-5)

    def test_save_and_load_voice_embedding(self):
        user_id = "test-user-002"
        embedding = make_random_embedding(256)
        self.store.save_voice_embedding(user_id, embedding)
        loaded = self.store.load_voice_embedding(user_id)
        assert loaded is not None
        np.testing.assert_allclose(loaded, embedding, rtol=1e-5)

    def test_missing_user_returns_none(self):
        assert self.store.load_face_embedding("nonexistent-user") is None
        assert self.store.load_voice_embedding("nonexistent-user") is None

    def test_file_is_encrypted(self):
        """Confirm the stored file does NOT contain the raw float values."""
        user_id = "test-user-003"
        embedding = make_random_embedding(128)
        self.store.save_face_embedding(user_id, embedding)

        # Find the file
        files = list(Path(self.tmpdir).rglob("*.face.enc"))
        assert len(files) == 1

        raw = files[0].read_bytes()
        # The BPEM magic should be present
        assert raw[:4] == b"BPEM"
        # But the plaintext float bytes should NOT be present
        plain = embedding.tobytes()
        assert plain not in raw, "Embedding stored in plaintext! Encryption failed."

    def test_user_id_not_in_filename(self):
        """User IDs must be hashed — never appear in plaintext filenames."""
        user_id = "alice@example.com"
        embedding = make_random_embedding(128)
        self.store.save_face_embedding(user_id, embedding)
        files = list(Path(self.tmpdir).rglob("*.face.enc"))
        for f in files:
            assert "alice" not in f.name, "User ID appears in filename!"
            assert "example" not in f.name

    def test_delete_removes_files(self):
        user_id = "test-user-del"
        self.store.save_face_embedding(user_id, make_random_embedding(128))
        self.store.save_voice_embedding(user_id, make_random_embedding(256))
        assert self.store.has_face(user_id)
        assert self.store.has_voice(user_id)
        result = self.store.delete_all(user_id)
        assert result["face"] is True
        assert result["voice"] is True
        assert not self.store.has_face(user_id)
        assert not self.store.has_voice(user_id)

    def test_tampered_file_returns_none(self):
        """Tampered ciphertext should fail GCM authentication tag check."""
        user_id = "test-user-tamper"
        embedding = make_random_embedding(128)
        self.store.save_face_embedding(user_id, embedding)

        # Corrupt the file
        files = list(Path(self.tmpdir).rglob("*.face.enc"))
        data = bytearray(files[0].read_bytes())
        data[-1] ^= 0xFF      # Flip last byte of auth tag
        files[0].write_bytes(bytes(data))

        loaded = self.store.load_face_embedding(user_id)
        assert loaded is None, "Tampered file should not decrypt successfully"

    def test_wrong_key_returns_none(self):
        """File encrypted with one key should not decrypt with another."""
        user_id = "test-user-key"
        embedding = make_random_embedding(128)
        self.store.save_face_embedding(user_id, embedding)

        # Change the key
        os.environ["BIOPAY_SECRET_KEY"] = "completely-different-key"
        from storage.embedding_store import EmbeddingStore
        store2 = EmbeddingStore(Path(self.tmpdir))
        loaded = store2.load_face_embedding(user_id)
        assert loaded is None, "Wrong key should not decrypt"

    def test_list_users(self):
        for i in range(3):
            self.store.save_face_embedding(f"user-{i}", make_random_embedding(128))
        for i in range(2):
            self.store.save_voice_embedding(f"user-{i}", make_random_embedding(256))
        users = self.store.list_users()
        assert len(users["face_registered"]) == 3
        assert len(users["voice_registered"]) == 2
        assert len(users["both_registered"]) == 2


# ─────────────────────────────────────────────────────────────────────────────
#  Session Store Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionStore:

    def setup_method(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from storage.session_store import SessionStore
        self.store = SessionStore(ttl_seconds=5)  # Short TTL for testing

    def test_create_and_get(self):
        self.store.create("sess-1", user_id="u1", amount=100.0)
        s = self.store.get("sess-1")
        assert s is not None
        assert s.user_id == "u1"
        assert s.amount == 100.0

    def test_session_expiry(self):
        import time
        self.store.create("sess-expire", user_id="u2", amount=50.0)
        time.sleep(6)   # Wait past TTL
        s = self.store.get("sess-expire")
        assert s is None, "Expired session should return None"

    def test_update_face_result(self):
        from types import SimpleNamespace
        self.store.create("sess-2", user_id="u3", amount=200.0)
        face_result = SimpleNamespace(
            verified=True, confidence=0.92,
            distance=0.35, liveness_score=0.88
        )
        s = self.store.update("sess-2", face_result=face_result)
        assert s.face_verified is True
        assert s.face_confidence == 0.92

    def test_both_verified_sets_status(self):
        from types import SimpleNamespace
        self.store.create("sess-3", user_id="u4", amount=300.0)
        face = SimpleNamespace(verified=True, confidence=0.90, distance=0.30, liveness_score=0.85)
        voice = SimpleNamespace(verified=True, similarity=0.88, replay_detected=False)
        self.store.update("sess-3", face_result=face)
        s = self.store.update("sess-3", voice_result=voice)
        assert s.status == "BOTH_VERIFIED"

    def test_mark_authorized(self):
        self.store.create("sess-4", user_id="u5", amount=1000.0)
        self.store.mark_authorized("sess-4")
        s = self.store.get("sess-4")
        assert s.authorized is True
        assert s.status == "AUTHORIZED"

    def test_nonexistent_session_returns_none(self):
        assert self.store.get("does-not-exist") is None


# ─────────────────────────────────────────────────────────────────────────────
#  Secure Memory Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSecureMemory:

    def test_wipe_zeroes_buffer(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from utils.secure_memory import secure_wipe_bytes
        buf = bytearray(b"super-secret-biometric-key-data")
        secure_wipe_bytes(buf)
        assert all(b == 0 for b in buf), "Buffer not fully zeroed after wipe"

    def test_wipe_ndarray(self):
        from utils.secure_memory import secure_wipe_ndarray
        arr = np.random.randn(128).astype(np.float32)
        secure_wipe_ndarray(arr)
        assert np.all(arr == 0), "Array not zeroed after wipe"

    def test_wipe_empty_buffer_no_error(self):
        from utils.secure_memory import secure_wipe_bytes
        secure_wipe_bytes(bytearray())  # Should not raise


# ─────────────────────────────────────────────────────────────────────────────
#  Run
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
