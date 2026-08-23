"""
app/schemas/auth.py — Authentication request and response shapes.
"""
from pydantic import BaseModel


class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    """Client sends the Firebase ID token received after a successful Firebase login."""
    token: str


class UserResponse(BaseModel):
    uid: str
    email: str
    role: str = "farmer"
    is_expert: bool = False
    message: str
