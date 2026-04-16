# Firebase Setup Guide for CoconutAI

## Overview
This application now includes Firebase email authentication. Users must log in before accessing the disease detection features.

## Step 1: Firebase Project Already Created ✓
Your Firebase project is already set up:
- **Project ID**: coconut-leaf-disease-dcf9a
- **Auth Domain**: coconut-leaf-disease-dcf9a.firebaseapp.com

## Step 2: Enable Email Authentication in Firebase Console

1. Go to [Firebase Console](https://console.firebase.google.com/)
2. Select your project "coconut-leaf-disease"
3. Navigate to **Build → Authentication**
4. Click **Get started** (if not already done)
5. Under **Sign-in method**, click **Email/Password**
6. Enable **Email/Password** authentication
7. Save

## Step 3: Backend Setup (Python)

### Install Dependencies
```bash
pip install -r models/requirements.txt
```

### For Production (Recommended)
You need to download a Firebase Admin SDK service account key:

1. In Firebase Console, go to **Project Settings**
2. Click **Service Accounts** tab
3. Click **Generate New Private Key**
4. Save the JSON file as `service-account-key.json` in your project root

### Environment Setup
Create a `.env` file in your project root:
```env
GOOGLE_APPLICATION_CREDENTIALS=./service-account-key.json
```

For local testing without the service account key, the app will still work but token verification will be limited. For production, always use the service account key.

## Step 4: Run the Application

```bash
cd c:\src\coconut-disease-detector
pip install -r models/requirements.txt
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Then open: `http://localhost:8000/`

## Step 5: Test Authentication

### Create a Test Account
1. Click **Login** button in the app
2. Click **Sign Up** link
3. Enter email and password (min 6 characters)
4. Click **Sign Up**
5. You should be logged in automatically

### Login
1. Click **Logout** to test
2. Click **Login** button
3. Enter your credentials
4. Click **Login**

## Features Added

✅ **Sign Up** - Create account with email and password
✅ **Login** - Authenticate with email and password  
✅ **Protected Endpoints** - Disease detection requires authentication
✅ **User Info** - Shows logged-in user email in header
✅ **Logout** - Sign out and return to login screen
✅ **Token Management** - Automatic token refresh handled by Firebase

## API Endpoints

### Authentication
- `POST /auth/verify-token` - Verify Firebase ID token
- `GET /auth/user` - Get current user (requires token)
- `POST /auth/logout` - Logout endpoint

### Detection (Protected - requires Bearer token)
- `POST /detect/image` - Upload image for detection
- `WS /detect/stream?token=<token>` - WebSocket for live drone feed

### Public
- `GET /health` - Health check
- `GET /classes` - Get detection classes

## Frontend Implementation

The HTML file now includes:
- Firebase SDK integration
- Login/Sign Up modal
- Automatic auth state management
- Token injection in API requests
- User badge in header

## Troubleshooting

### "Invalid token" error
- Make sure you're logged in
- Token might have expired - try logging out and back in
- Check browser console for errors (F12)

### "Email already exists"
- Use a different email or reset password in Firebase Console

### WebSocket connection fails
- Ensure token is valid
- Check if you're authenticated
- Verify WebSocket is using the token parameter

### Can't access detection features when not logged in
- This is by design! All detection features require authentication
- Click the Login button and create an account first

## Security Notes

⚠️ **Important**: 
- Never commit `service-account-key.json` to version control
- Add it to `.gitignore`
- Keep your Firebase config (using public API key) is safe to expose
- Tokens are stored in browser's session storage
- Tokens auto-expire after 1 hour - Firebase SDK handles refresh

## Next Steps

1. Download service account key for production
2. Set up `.env` file with credentials
3. Deploy to production using proper environment variables
4. Monitor authentication in Firebase Console → Logs

## Support

For Firebase authentication issues:
- Check [Firebase Docs](https://firebase.google.com/docs/auth)
- Review [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
- Check browser console for detailed error messages
