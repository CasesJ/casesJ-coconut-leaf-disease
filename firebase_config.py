import firebase_admin
from firebase_admin import credentials
from firebase_admin import auth as firebase_auth
import os
import base64
import json
from urllib import error as urlerror
from urllib import request as urlrequest
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
# In deployment, prefer the FIREBASE_SERVICE_ACCOUNT_JSON secret.  For local
# development, GOOGLE_APPLICATION_CREDENTIALS may point to an ignored file.

try:
    if not firebase_admin._apps:
        # Prefer the deployment secret so no credential file is required.
        service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
        cred_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        if service_account_json:
            try:
                service_account_info = json.loads(service_account_json)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "FIREBASE_SERVICE_ACCOUNT_JSON must contain valid service-account JSON"
                ) from exc
            cred = credentials.Certificate(service_account_info)
            firebase_admin.initialize_app(cred, {
                'databaseURL': 'https://coconut-leaf-disease-dcf9a-default-rtdb.firebaseio.com',
                'storageBucket': FIREBASE_STORAGE_BUCKET,
            })
            print("[OK] Firebase initialized with deployment secret")
        else:
            # If relative path, make it absolute
            if cred_path and not os.path.isabs(cred_path):
                cred_path = os.path.join(os.path.dirname(__file__), cred_path)
            if cred_path and os.path.exists(cred_path):
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred, {
                    'databaseURL': 'https://coconut-leaf-disease-dcf9a-default-rtdb.firebaseio.com',
                    'storageBucket': FIREBASE_STORAGE_BUCKET,
                })
                print("[OK] Firebase initialized with service account")
            else:
                # Token verification only needs the project ID and Google's public
                # signing certificates.  Do not leave an invalid credentials path
                # in the process environment: the Admin SDK would otherwise try
                # to load it later and turn a valid ID token into a FileNotFoundError.
                if cred_path:
                    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
                    print(
                        "[WARN] Configured Firebase service-account file was not found; "
                        "starting in token-verification mode"
                    )
                # Fallback - initialize with project ID only (limited functionality)
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


def _verify_token_with_firebase_auth_api(token: str) -> dict:
    """Verify an ID token through Firebase Auth when Admin credentials are absent.

    Firebase validates the submitted ID token server-side. The API key only
    identifies the Firebase project and is already used by the web client.
    """
    payload = json.dumps({"idToken": token}).encode("utf-8")
    endpoint = (
        "https://identitytoolkit.googleapis.com/v1/accounts:lookup?key="
        f"{FIREBASE_CONFIG['apiKey']}"
    )
    request = urlrequest.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urlerror.HTTPError as exc:
        raise Exception("Firebase rejected the ID token") from exc
    except urlerror.URLError as exc:
        raise Exception("Could not reach Firebase Auth to verify the ID token") from exc

    users = body.get("users") or []
    if not users or not users[0].get("localId"):
        raise Exception("Firebase did not return an identity for this token")
    user = users[0]
    return {
        "uid": user["localId"],
        "email": user.get("email"),
        "email_verified": user.get("emailVerified", False),
        "firebase": {"sign_in_provider": "firebase-auth-rest"},
    }

def verify_token(token: str):
    """
    Verify Firebase ID token
    Returns user data if valid, raises exception otherwise
    """
    try:
        # Firebase tokens can be minted a few seconds ahead of the local clock.
        # Keep a small tolerance while retaining all signature and expiry checks.
        decoded_token = firebase_auth.verify_id_token(token, clock_skew_seconds=60)
        return decoded_token
    except Exception as e:
        # Local development commonly has no Admin service account. Firebase's
        # lookup endpoint still validates the ID token server-side.
        if "default credentials were not found" in str(e).lower():
            try:
                decoded_token = _verify_token_with_firebase_auth_api(token)
                print("[INFO] Firebase ID token verified through Firebase Auth API")
                return decoded_token
            except Exception as fallback_error:
                raise Exception(
                    f"Invalid token: Firebase Auth verification failed: {fallback_error}"
                ) from e
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
