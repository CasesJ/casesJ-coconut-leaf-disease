"""
app/schemas/auth.py — Authentication request and response shapes.
"""
from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    """Client sends the Firebase ID token received after a successful Firebase login."""
    token: str


class UserProfileRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    contact_number: str = Field(min_length=3, max_length=30)


class UserResponse(BaseModel):
    uid: str
    email: str
    role: str = "farmer"
    is_expert: bool = False
    message: str
