"""
biometric/face_engine.py
Real face embedding extraction using face_recognition (dlib ResNet-34).
128-D embedding per face. Euclidean distance comparison.
Liveness estimation via Laplacian variance + frequency analysis.
"""

import io
import logging
import numpy as np
from typing import Optional, Tuple
import ctypes
import gc

logger = logging.getLogger("biopay.face")


class FaceEngine:
    """
    Wraps face_recognition (dlib) for:
      - Face detection
      - 128-D embedding extraction
      - Euclidean distance comparison
      - Basic liveness estimation
    """

    THRESHOLD = 0.50        # Euclidean distance threshold (0 = same, 1 = different)
    model_name = "dlib_resnet_v1_face_recognition"

    def __init__(self):
        try:
            import face_recognition
            self._fr = face_recognition
            self._model = "small"   # 'large' for higher accuracy but slower
            logger.info(f"FaceEngine loaded: model={self.model_name} threshold={self.THRESHOLD}")
        except ImportError:
            raise RuntimeError(
                "face_recognition not installed. Run: pip install face_recognition"
            )

    def extract_embedding(
        self, image_bytes: bytes
    ) -> Tuple[Optional[np.ndarray], dict]:
        """
        Extract a 128-D face embedding from raw image bytes.

        Returns:
            (embedding: np.ndarray[128] | None, meta: dict)
        """
        import PIL.Image
        img = None
        rgb_array = None
        try:
            img = PIL.Image.open(io.BytesIO(image_bytes)).convert("RGB")
            rgb_array = np.array(img)

            # Detect face locations
            locations = self._fr.face_locations(rgb_array, model="hog")
            if not locations:
                logger.debug("No face locations found")
                return None, {"detection_confidence": 0.0}

            # Use the largest face (primary subject)
            largest = self._largest_face(locations)

            # Extract 128-D embedding
            encodings = self._fr.face_encodings(
                rgb_array,
                known_face_locations=[largest],
                num_jitters=1,    # 1=fast, 10=accurate
                model=self._model,
            )

            if not encodings:
                logger.debug("Encoding failed for detected face")
                return None, {"detection_confidence": 0.0}

            embedding = encodings[0].astype(np.float32)
            top, right, bottom, left = largest
            face_h = bottom - top
            img_h = rgb_array.shape[0]
            detection_conf = min(1.0, face_h / img_h * 2.5)

            meta = {
                "detection_confidence": float(detection_conf),
                "face_location": largest,
                "image_shape": rgb_array.shape,
                "num_faces_found": len(locations),
            }

            return embedding, meta

        finally:
            # Wipe sensitive data from memory
            if rgb_array is not None:
                rgb_array.fill(0)
                del rgb_array
            if img is not None:
                del img
            gc.collect()

    def compare(
        self, live: np.ndarray, stored: np.ndarray
    ) -> Tuple[float, float]:
        """
        Compare two embeddings.

        Returns:
            (distance: float, confidence: float)
            distance: Euclidean distance [0, ∞)  — lower = more similar
            confidence: [0.0, 1.0]              — higher = more confident match
        """
        distance = float(np.linalg.norm(live.astype(np.float64) - stored.astype(np.float64)))
        # Map distance to confidence: 0.0 dist → 1.0 conf, 1.0 dist → 0.0 conf
        confidence = max(0.0, 1.0 - (distance / 1.0))
        return distance, confidence

    def estimate_liveness(self, image_bytes: bytes, face_meta: dict) -> float:
        """
        Estimate liveness probability using:
          1. Laplacian variance (blur = printed photo indicator)
          2. Colour diversity (flat colour planes = screen capture)
          3. High-frequency edge ratio

        Returns liveness score [0.0, 1.0].
        A genuine live face photo should score > 0.50.
        """
        import PIL.Image
        try:
            img = PIL.Image.open(io.BytesIO(image_bytes)).convert("RGB")
            gray = np.array(img.convert("L"), dtype=np.float64)

            # 1. Laplacian variance — measures sharpness/focus
            lap_var = self._laplacian_variance(gray)
            # Genuine faces tend to have lap_var > 100
            lap_score = min(1.0, lap_var / 500.0)

            # 2. Colour channel standard deviation — screens look flatter
            rgb = np.array(img, dtype=np.float64)
            color_std = float(np.mean([rgb[:,:,c].std() for c in range(3)]))
            color_score = min(1.0, color_std / 60.0)

            # 3. High-frequency ratio
            fft = np.fft.fft2(gray)
            fft_shift = np.fft.fftshift(fft)
            magnitude = np.abs(fft_shift)
            h, w = magnitude.shape
            center_h, center_w = h // 2, w // 2
            radius = min(h, w) // 8
            y, x = np.ogrid[:h, :w]
            mask = ((y - center_h)**2 + (x - center_w)**2) > radius**2
            hf_ratio = float(magnitude[mask].sum() / (magnitude.sum() + 1e-9))
            hf_score = min(1.0, hf_ratio * 3.0)

            liveness = (lap_score * 0.5) + (color_score * 0.3) + (hf_score * 0.2)
            logger.debug(
                f"Liveness: lap={lap_score:.3f} color={color_score:.3f} hf={hf_score:.3f} → {liveness:.3f}"
            )

            del img, gray, rgb
            gc.collect()
            return float(liveness)

        except Exception as e:
            logger.error(f"Liveness estimation error: {e}")
            return 0.5  # Neutral on error

    # ── Private helpers ────────────────────────────────────────────────────
    def _laplacian_variance(self, gray: np.ndarray) -> float:
        """Compute Laplacian variance of a grayscale image."""
        kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
        from scipy.ndimage import convolve
        laplacian = convolve(gray, kernel)
        return float(laplacian.var())

    def _largest_face(self, locations: list) -> tuple:
        """Return the bounding box of the largest detected face."""
        def area(loc):
            top, right, bottom, left = loc
            return (bottom - top) * (right - left)
        return max(locations, key=area)

    def cleanup(self):
        """Wipe internal state."""
        self._fr = None
        gc.collect()
        logger.info("FaceEngine cleaned up")
