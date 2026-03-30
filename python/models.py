"""
models.py — Pydantic request/response models
"""
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    PENDING       = "PENDING"
    FACE_OK       = "FACE_OK"
    VOICE_OK      = "VOICE_OK"
    BOTH_VERIFIED = "BOTH_VERIFIED"
    AUTHORIZED    = "AUTHORIZED"
    EXPIRED       = "EXPIRED"
    DENIED        = "DENIED"


class RegisterFaceRequest(BaseModel):
    user_id: str = Field(..., description="Unique user identifier")


class RegisterVoiceRequest(BaseModel):
    user_id: str = Field(..., description="Unique user identifier")


class VerifyFaceResponse(BaseModel):
    verified: bool
    confidence: float = Field(..., ge=0.0, le=1.0)
    distance: float   = Field(..., ge=0.0)
    liveness_score: float = Field(..., ge=0.0, le=1.0)
    reason: str


class VerifyVoiceResponse(BaseModel):
    verified: bool
    similarity: float = Field(..., ge=0.0, le=1.0)
    replay_detected: bool
    reason: str


class BiometricSession(BaseModel):
    session_id: str
    user_id: str
    amount: float
    status: SessionStatus
    face_verified: bool
    voice_verified: bool
    authorized: bool


class PaymentAuthRequest(BaseModel):
    session_id: str
    user_id: str
    amount: float
    currency: str = "USD"


class PaymentAuthResponse(BaseModel):
    authorized: bool
    session_id: str
    reason: str
    risk_score: Optional[int] = None
    risk_level: Optional[str] = None
