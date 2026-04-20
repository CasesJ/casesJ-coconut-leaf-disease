import firebase_admin
from firebase_admin import credentials
from firebase_admin import auth as firebase_auth
import os
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
                'databaseURL': 'https://coconut-leaf-disease-dcf9a-default-rtdb.firebaseio.com'
            })
            print("[OK] Firebase initialized with service account")
        else:
            # Fallback - initialize with project ID only (limited functionality)
            print(f"[WARN] Service account not found at {cred_path}, using project ID only")
            options = {
                'projectId': FIREBASE_CONFIG['projectId'],
                'databaseURL': 'https://coconut-leaf-disease-dcf9a-default-rtdb.firebaseio.com'
            }
            firebase_admin.initialize_app(options=options)
            print("[WARN] Firebase initialized with project ID (read-only mode)")
except ValueError as e:
    # App already initialized
    print(f"[INFO] Firebase already initialized: {e}")
except Exception as e:
    print(f"[ERROR] Firebase init error: {e}")
    raise

def verify_token(token: str):
    """
    Verify Firebase ID token
    Returns user data if valid, raises exception otherwise
    """
    try:
        decoded_token = firebase_auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
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

def delete_user(uid: str):
    """Delete user"""
    try:
        firebase_auth.delete_user(uid)
        return True
    except Exception as e:
        raise Exception(f"Error deleting user: {str(e)}")
