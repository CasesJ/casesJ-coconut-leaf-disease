# 🔧 Complete Troubleshooting Guide

## Installation Issues

### Problem: "ModuleNotFoundError: No module named 'firebase_admin'"

**Solution:**
```bash
pip install -r requirements_hybrid.txt --upgrade
```

If still failing:
```bash
pip install firebase-admin python-jose python-dotenv pydantic
```

### Problem: "ModuleNotFoundError: No module named 'openvino'"

**Solution:**
```bash
pip install openvino opencv-python-headless numpy
```

### Problem: pip installation never finishes

**Solution:**
```bash
# Use specific Python 3.9+
python -m pip install --upgrade pip
pip install -r requirements_hybrid.txt -v
```

## Running the Application

### Problem: "Port 8000 already in use"

**Solution 1 - Use different port:**
```bash
python -m uvicorn main:app --reload --port 8001
```

**Solution 2 - Kill existing process:**
```bash
# Windows
netstat -ano | findstr :8000
taskkill /PID <PID> /F

# Mac/Linux
lsof -i :8000
kill -9 <PID>
```

### Problem: "Uvicorn not found"

**Solution:**
```bash
pip install uvicorn[standard]
python -m uvicorn main:app --reload
```

### Problem: "No module named 'model'"

**Solution:** Make sure you're in correct directory
```bash
cd c:\src\coconut-disease-detector
python -m uvicorn main:app --reload
```

## Frontend Issues

### Problem: Firebase SDK not loading

**Symptoms:**
- "firebase is not defined" in console
- Login button doesn't work
- Page errors on load

**Solution:**
1. Check internet connection
2. Check browser console (F12) for network errors
3. Verify Firebase<< script tags loaded in index.html
4. Try refreshing page

### Problem: Login modal appears but doesn't work

**Symptoms:**
- "firebase is not defined"
- "auth is not defined"
- Signup/login button does nothing

**Solution:**
```javascript
// In browser console, check:
firebase          // Should be defined
firebase.auth     // Should be function
auth              // Should be initialized
```

### Problem: "Email already exists" on signup

**Symptoms:**
- Same email tried multiple times
- Want to use that email anyway

**Solution:**
1. **Option 1**: Use different email
2. **Option 2**: Reset in Firebase Console:
   - Go to Authentication → Users
   - Find email → Click ⋮ → Reset Password
3. **Option 3**: Delete old account
   - Find email → Click ⋮ → Delete User

### Problem: Can type in login form but button doesn't work

**Symptoms:**
- Form accepts input
- Clicking submit does nothing
- No errors in console

**Solution:**
```javascript
// Check browser console
console.log(firebase)           // Check if firebase loaded
console.log(firebase.auth())    // Check auth exists
```

Try clear browser cache:
- Press Ctrl+Shift+Delete
- Clear all cache
- Refresh page

## Authentication Issues

### Problem: "Invalid token" when uploading image

**Symptoms:**
- Logged in but image upload fails
- "Invalid token: ..." error message

**Solution 1 - Token expired:**
```
Logout and login again
```

**Solution 2 - Check authorization header:**
```javascript
// In browser console:
const token = await firebase.auth().currentUser.getIdToken();
console.log("Token:", token);
```

**Solution 3 - Check network request:**
1. Press F12 → Network tab
2. Upload image
3. Look for `/detect/image` request
4. Check Headers → Authorization should have Bearer token

### Problem: Login works but detection features disabled

**Symptoms:**
- Can sign up/login
- All buttons/features are greyed out
- Upload area is disabled

**Solution:**
1. Check if actually logged in - should see email in top right
2. Try logging out and back in
3. Check browser console for errors (F12)
4. Try different browser/incognito mode

### Problem: "Auth failed: ..." error on WebSocket

**Symptoms:**
- Starting drone camera fails
- WebSocket error appears

**Solution:**
1. Check token is still valid (within 1 hour)
2. Logout and login again
3. Check browser console for full error message
4. Verify backend is running

## Backend Issues

### Problem: 'verify_token' not found

**Error:**
```
ModuleNotFoundError: cannot import name 'verify_token'
```

**Solution:**
1. Check firebase_config.py exists in project root
2. Check file paths are correct
3. Restart application:
   ```bash
   python -m uvicorn main:app --reload
   ```

### Problem: "No module named firebase_config"

**Solution:**
```bash
# Ensure file exists
dir firebase_config.py

# If missing, reinstall dependencies
pip install -r requirements_hybrid.txt
```

### Problem: Backend 401 Unauthorized error

**Symptoms:**
- Upload/detection returns 401
- Token not being accepted

**Solution 1 - Check service account:**
```bash
# If you have service-account-key.json:
echo $GOOGLE_APPLICATION_CREDENTIALS

# Should point to your key file
```

**Solution 2 - Verify token format:**
Check network request headers:
```
Authorization: Bearer <long_token_string>
```

**Solution 3 - Check if token expired:**
Tokens last 1 hour. After that:
- Firebase SDK auto-refreshes
- If stuck, logout/login

### Problem: WebSocket "Token not provided" error

**Symptoms:**
- Drone camera won't start
- WebSocket connection fails

**Solution:**
Check URL includes token:
```
ws://localhost:8000/detect/stream?token=<token_here>
```

In browser console:
```javascript
const token = await firebase.auth().currentUser.getIdToken();
console.log("Token passed:", token);
```

## CORS & Network Issues

### Problem: "CORS error" in console

**Symptoms:**
- Network requests blocked
- "Access-Control-Allow-Origin" error

**Solution:**
This shouldn't happen with local development, but if it does:

Check if backend is running on same localhost:
```
Frontend: http://localhost:3000
Backend: http://localhost:8000
```

### Problem: "Network error" on image upload

**Symptoms:**
- Upload fails before even reaching server
- "NetworkError when attempting to fetch"

**Solution:**
1. Check backend is running
2. Check URL is correct: `http://localhost:8000`
3. Try different network:
   - Disconnect VPN if using
   - Try mobile hotspot
4. Check firewall isn't blocking port 8000

## Performance Issues

### Problem: App very slow

**Symptoms:**
- Page takes long to load
- Buttons are sluggish
- Socket connection slow

**Solution:**

1. **Clear cache:**
   ```
   Press F12 → Storage → Clear all
   ```

2. **Check internet speed:**
   ```
   speedtest.net
   ```

3. **Monitor network:**
   ```
   F12 → Network → Check file sizes
   ```

4. **Check CPU/Memory:**
   - Windows: Ctrl+Shift+Esc
   - Mac: Cmd+Space → Activity Monitor
   - Stop other apps using resources

### Problem: Camera feed is choppy

**Symptoms:**
- Drone feed stutters
- Low frame rate

**Solution:**
1. Reduce video quality resolution
2. Close other tabs/apps
3. Check internet connectivity
4. Try different browser
5. Reduce lighting (sometimes helps camera)

## YOLO Model Issues

### Problem: Model not found error

**Symptoms:**
- "yolo11n.pt not found"
- Detection fails

**Solution:**
1. Check file exists:
   ```bash
   dir yolo11n.pt
   dir models/best.pt
   ```

2. If missing, download from Ultralytics:
   ```bash
   pip install ultralytics
   python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
   ```

### Problem: Detection results are always wrong

**Symptoms:**
- Wrong classifications
- Extremely low confidence

**Solution:**
1. Model might not be trained on coconut diseases
2. Try with different images
3. Check lighting - may need better camera angle
4. Model might need retraining with your custom data

## Chrome DevTools Debugging

### Open Developer Console
```
Windows: F12 or Ctrl+Shift+I
Mac: Cmd+Shift+I
```

### Check Network Requests
1. Click Network tab
2. Perform action (login, upload, etc.)
3. Look for red (failed) requests
4. Click request → Response tab to see error

### Check Console Errors
1. Click Console tab
2. Look for red error messages
3. Click to expand for details
4. Copy full error message for research

### Monitor Local Storage
1. Click Storage tab
2. Click Local Storage
3. Can see Firebase session data
4. Useful for debugging auth issues

## When All Else Fails

### Nuclear Option - Fresh Start
```bash
# 1. Stop application (Ctrl+C)
# 2. Clear pip cache
pip cache purge

# 3. Reinstall all dependencies
pip install -r models/requirements.txt --force-reinstall

# 4. Clear browser cache
# Open incognito window (Ctrl+Shift+N)

# 5. Start fresh
python -m uvicorn main:app --reload
```

### Get Help
1. **Check logs**: Look at terminal output when errors occur
2. **Browser console**: F12 → Console tab
3. **Network tab**: See exactly what requests are failing
4. **Firebase Console**: Check Authentication logs for server-side errors

### Collect Debug Info
When asking for help, provide:
```
1. Full error message from console
2. Screenshot of console errors (F12)
3. Network request details (F12 → Network)
4. Python traceback from terminal
5. Browser version: Edge, Chrome, Firefox?
6. Steps to reproduce issue
7. Your firebase project ID
```

## Success Indicators

✅ **Working Correctly:**
- Login button visible
- Can sign up with email
- Can login with email
- User email shows in header
- Can upload images
- Can start drone camera
- Export CSV works
- No errors in console

🎉 **If you see all this, you're good to go!**

---

Still stuck? 
- Check START_HERE.md for quick start
- Check HYBRID_STORAGE_README.md for overview
- Check browser console (always!)
- Try with different email/password
- Restart application completely
