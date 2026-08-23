"""
app/infrastructure/firebase/admin.py — Firebase Admin SDK initialisation.

Importing this module triggers the existing, well-tested initialisation
block in firebase_config.py exactly once before any Firebase call.
"""
# Side-effect import: initialises the Firebase Admin SDK.
import firebase_config  # noqa: F401
