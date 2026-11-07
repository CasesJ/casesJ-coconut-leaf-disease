"""
app/schemas/auth.py — Authentication request and response shapes.
"""
from typing import Dict

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    """Client sends the Firebase ID token received after a successful Firebase login."""
    token: str


class RecoveryVerificationRequest(BaseModel):
    """Details checked before a Firebase reset email can be requested."""

    email: str = Field(min_length=3, max_length=320)
    mobile_number: str = Field(
        min_length=11,
        max_length=11,
        pattern=r"^\d{11}$",
        description="An 11-digit mobile number",
    )


class UserProfileRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    contact_number: str = Field(
        min_length=11,
        max_length=11,
        pattern=r"^\d{11}$",
        description="An 11-digit mobile number",
    )


class UserResponse(BaseModel):
    uid: str
    email: str
    role: str = "farmer"
    is_expert: bool = False
    features: Dict[str, bool] = Field(default_factory=dict)
    message: str
