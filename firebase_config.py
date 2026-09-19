import firebase_admin
from firebase_admin import credentials
from firebase_admin import auth as firebase_auth
import os
import base64
import json
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# Firebase configuration
FIREBASE_CONFIG = {
    "apiKey": "AIzaSyCYPyhuhLGuKLColorKYU6gZs3-ASMo0ck",
    "authDomain": "coconut-leaf-disease-dcf9a.firebaseapp.com",
    "projectId": "coconut-leaf-disease-dcf9a",
    "storageBucket": "coconut-leaf-disease-dcf9a.firebasestorage.app",
    "messagingSenderId": "87937530464",
    "appId": "1:87937530464:web:3dfb3cfb5f7c689a522105",
    "measurementId": "G-4NNZLME6MB"
}

FIREBASE_STORAGE_BUCKET = os.getenv(
    "FIREBASE_STORAGE_BUCKET", FIREBASE_CONFIG["storageBucket"]
).strip()

ALLOW_UNVERIFIED_FIREBASE_TOKENS = os.getenv(
    "ALLOW_UNVERIFIED_FIREBASE_TOKENS",
    "false",
).strip().lower() in {"1", "true", "yes", "on"}

# Initialize Firebase Admin SDK
# For local testing without service account, we'll use basic setup
# In production, you should download service account key from Firebase Console
# and set GOOGLE_APPLICATION_CREDENTIALS environment variable

try:
    if not firebase_admin._apps:
        # Try to initialize with service account if available
        cred_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        
        # If relative path, make it absolute
        if cred_path and not os.path.isabs(cred_path):
            cred_path = os.path.join(os.path.dirname(__file__), cred_path)
            print(f"[CONFIG] Using service account: {cred_path}")
        
        if cred_path and os.path.exists(cred_path):
            print(f"[OK] Service account found: {cred_path}")
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred, {
                'databaseURL': 'https://coconut-leaf-disease-dcf9a-default-rtdb.firebaseio.com',
                'storageBucket': FIREBASE_STORAGE_BUCKET,
            })
            print("[OK] Firebase initialized with service account")
        else:
            # Fallback - initialize with project ID only (limited functionality)
            print(f"[WARN] Service account not found at {cred_path}, using project ID only")
            options = {
                'projectId': FIREBASE_CONFIG['projectId'],
                'databaseURL': 'https://coconut-leaf-disease-dcf9a-default-rtdb.firebaseio.com',
                'storageBucket': FIREBASE_STORAGE_BUCKET,
            }
            firebase_admin.initialize_app(options=options)
            print("[WARN] Firebase initialized with project ID (read-only mode)")
except ValueError as e:
    # App already initialized
    print(f"[INFO] Firebase already initialized: {e}")
except Exception as e:
    print(f"[ERROR] Firebase init error: {e}")
    raise


def _decode_base64url(data: str) -> bytes:
    """Decode a base64url segment with missing padding handled."""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _decode_unverified_jwt(token: str) -> dict:
    """
    Decode a Firebase JWT without signature verification.

    This is a local-development fallback for environments where Google cert
    fetches are blocked. It still validates the token structure and expiry.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise Exception("Token is not a valid JWT")

        header_raw, payload_raw, _signature = parts
        header = json.loads(_decode_base64url(header_raw).decode("utf-8"))
        payload = json.loads(_decode_base64url(payload_raw).decode("utf-8"))

        exp = payload.get("exp")
        if exp is not None:
            now_ts = int(datetime.now(timezone.utc).timestamp())
            if int(exp) < now_ts:
                raise Exception("Token has expired")

        user_id = payload.get("user_id") or payload.get("sub") or payload.get("uid")
        if not user_id:
            raise Exception("Token payload missing user id")

        payload["uid"] = user_id
        payload["token_header"] = header
        payload["token_verified_offline"] = True
        return payload
    except Exception as e:
        raise Exception(f"Offline token decode failed: {str(e)}")

def verify_token(token: str):
    """
    Verify Firebase ID token
    Returns user data if valid, raises exception otherwise
    """
    try:
        decoded_token = firebase_auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        if ALLOW_UNVERIFIED_FIREBASE_TOKENS:
            try:
                decoded_token = _decode_unverified_jwt(token)
                print("[WARN] Using offline JWT decode fallback for Firebase token verification")
                return decoded_token
            except Exception as fallback_error:
                raise Exception(f"Invalid token: {str(e)}; fallback failed: {str(fallback_error)}")
        raise Exception(f"Invalid token: {str(e)}")

def get_user_by_email(email: str):
    """Get user by email"""
    try:
        user = firebase_auth.get_user_by_email(email)
        return user
    except firebase_auth.UserNotFoundError:
        return None
    except Exception as e:
        raise Exception(f"Error fetching user: {str(e)}")

def create_user(email: str, password: str):
    """Create new user with email and password"""
    try:
        user = firebase_auth.create_user(
            email=email,
            password=password,
        )
        return user
    except firebase_auth.EmailAlreadyExistsError:
        raise Exception("Email already exists")
    except Exception as e:
        raise Exception(f"Error creating user: {str(e)}")

def ensure_user_account(email: str, password: str | None = None, custom_claims: dict | None = None):
    """
    Create or update a Firebase Auth user and optionally attach custom claims.

    Returns a tuple of (user, created_new_user).
    """
    try:
        user = get_user_by_email(email)
        created_new_user = False

        if user:
            update_kwargs = {
                "email": email,
                "email_verified": True,
            }
            if password:
                update_kwargs["password"] = password
            user = firebase_auth.update_user(user.uid, **update_kwargs)
        else:
            if not password:
                raise Exception("Password is required when creating a new user")
            user = firebase_auth.create_user(
                email=email,
                password=password,
                email_verified=True,
            )
            created_new_user = True

        if custom_claims:
            firebase_auth.set_custom_user_claims(user.uid, custom_claims)

        return user, created_new_user
    except Exception as e:
        raise Exception(f"Error ensuring user account: {str(e)}")

def delete_user(uid: str):
    """Delete user"""
    try:
        firebase_auth.delete_user(uid)
        return True
    except Exception as e:
        raise Exception(f"Error deleting user: {str(e)}")
