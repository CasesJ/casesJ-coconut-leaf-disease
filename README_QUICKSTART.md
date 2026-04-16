# CoconutAI - Quick Start Guide

## What's New? 🎉
Your application now has **Firebase Email Authentication**! Users must sign up and log in before using the disease detection features.

## Quick Start (5 Minutes)

### 1. Install Dependencies
```bash
pip install -r models/requirements.txt
```

### 2. Enable Authentication in Firebase Console
- Go to https://console.firebase.google.com/
- Select your project
- Go to **Authentication → Sign-in method → Email/Password**
- Click **Enable** and Save

### 3. Run the App
```bash
python -m uvicorn main:app --reload
```

Open: `http://localhost:8000/`

### 4. Test It Out
1. Click the **Login** button in the top right
2. Click **Sign Up** to create an account
3. Enter your email and password (min 6 characters)
4. After signup, you're automatically logged in!
5. Now you can upload images and use the drone camera

## Key Features

| Feature | Status |
|---------|--------|
| Email Sign Up | ✅ Working |
| Email Login | ✅ Working |
| Protected Detection | ✅ Requires login |
| User Badge | ✅ Shows in header |
| Logout | ✅ Available |
| Auto Token Refresh | ✅ Automatic |
| Live Drone Feed | ✅ Protected |

## File Structure

```
coconut-disease-detector/
├── main.py                    # Backend with FastAPI + Auth endpoints
├── firebase_config.py         # Firebase configuration (NEW!)
├── static/
│   └── index.html            # Frontend with Firebase SDK + Auth UI (UPDATED!)
├── models/
│   ├── best.pt
│   └── requirements.txt       # Updated with firebase-admin (UPDATED!)
├── FIREBASE_SETUP.md         # Detailed setup guide
├── .env.example              # Environment template
└── README_QUICKSTART.md      # This file
```

## API Changes

### Backend Endpoints (Protected)
```
POST /detect/image          - Requires Bearer token
WS /detect/stream?token=... - Requires token parameter
```

### New Auth Endpoints
```
POST /auth/verify-token     - Verify Firebase token
GET  /auth/user             - Get logged-in user
POST /auth/logout           - Logout
```

## Common Tasks

### Create Admin Test Account
```
Email: test@example.com
Password: password123
```
Then use this to test the app.

### Reset a User's Password
1. Go to Firebase Console → Authentication
2. Find user in list
3. Click menu → Reset password
4. User gets email with reset link

### View Authentication Logs
1. Firebase Console → Authentication
2. Logs tab shows signup/login attempts
3. Check for suspicious activity

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Email already exists" | Use different email for signup |
| "Invalid token" error | Log out and back in |
| Camera not working | Check browser camera permissions |
| Can't access detection | Make sure you're logged in |
| App won't start | Run `pip install -r models/requirements.txt` |

## Next Steps

1. ✅ **Now**: Test signup/login locally
2. 📝 **Soon**: Add user profiles to store detection history
3. 🗄️ **Later**: Connect to Firestore database to save detections
4. 📊 **Future**: Add analytics dashboard

## Need Help?

- **Firebase Docs**: https://firebase.google.com/docs/auth
- **FastAPI Security**: https://fastapi.tiangolo.com/tutorial/security/
- **Check Console**: Press F12 in browser to see detailed errors

## Production Deployment

When deploying to production:
1. Download service account key from Firebase
2. Set environment variable: `GOOGLE_APPLICATION_CREDENTIALS`
3. Use HTTPS for security
4. Enable CORS if needed

---

**Happy disease detecting!** 🥥🔍
