# Firebase Admin Commands Reference

## Managing Users from Firebase Console

### View All Users
1. Go to https://console.firebase.google.com/
2. Select your project
3. Click **Authentication** in left sidebar
4. View list of all registered users

### Reset User Password
1. Find user in authentication list
2. Click the 3-dot menu (⋮) next to user email
3. Select **Reset Password**
4. User receives email with password reset link

### Delete User Account
1. Find user in authentication list
2. Click the 3-dot menu (⋮) next to user email
3. Select **Delete User**
⚠️ This cannot be undone!

### Disable/Enable User
1. Find user in authentication list
2. Click the 3-dot menu (⋮) next to user email
3. Toggle **Disable User** or **Enable User**
Disabled users cannot log in but account isn't deleted

## Viewing User Activity

### Authentication Logs
1. Go to **Authentication** tab
2. Click **Logs** tab at top
3. See signup/login/error activity
Helpful for debugging issues!

### Email Verification Status
- Users can see unverified emails with icon
- Useful for limiting features to verified users

## Advanced: Python Backend Operations

### Get User by Email
```python
from firebase_config import get_user_by_email

user = get_user_by_email("user@example.com")
print(user.uid)      # User's unique ID
print(user.email)    # User's email
print(user.display_name)  # Display name if set
```

### Delete User by UID
```python
from firebase_config import delete_user

delete_user("specific_user_uid_here")
# Removes user from Firebase
```

### Verify Token
```python
from firebase_config import verify_token

try:
    decoded = verify_token(id_token)
    print(decoded['uid'])      # User ID
    print(decoded['email'])    # User email
    print(decoded['aud'])      # Audience
except Exception as e:
    print(f"Invalid token: {e}")
```

## Email Customization

### Custom Signup Email
1. Go to **Authentication → Templates**
2. Find email template you want to customize
3. Click pencil icon to edit
Options:
- Verification email
- Password reset email
- Email change confirmation

### Add Custom Reply-To Email
1. Go to **Authentication → Templates**
2. Change "From" address field
3. Can use same domain as project

## Security Best Practices

✅ **DO:**
- Enable MFA in production
- Monitor authentication logs
- Delete inactive accounts after 90 days
- Use HTTPS always
- Rotate service account keys quarterly

❌ **DON'T:**
- Share Firebase API key (it's public, that's OK)
- Commit service account key to Git
- Disable password requirements
- Store tokens in localStorage (browser does it automatically)
- Send passwords in plain text

## Backup User Data

### Export User List
```python
# Firebase Console → Authentication tab
# Click "Download" button to export CSV
# Contains: email, creation date, last signin date
```

### Script to Backup User Data
```python
import firebase_admin
from firebase_admin import auth

# List all users
page = auth.list_users()
for user in page.iterate_all():
    print(f"{user.email} - Created: {user.user_metadata.creation_time}")
```

## Monitoring & Analytics

### Key Metrics to Track
- **Total Users**: Authentication → Users tab
- **Signup Rate**: Compare daily new users
- **Login Activity**: Check Logs tab
- **Error Rate**: Monitor failed auth attempts

### Set Up Alerts
1. Go to **Authentication → Logs**
2. Look for errors like:
   - "too_many_login_attempts"
   - "invalid_email"
   - "weak_password"

## Troubleshooting Common Issues

### "User disabled" error
- User was manually disabled in Console
- Solution: Re-enable user or create new account

### "Email already exists"
- Multiple signup attempts with same email
- Solution: User should login instead

### "Too many failed login attempts"
- User tried wrong password multiple times
- Solution: Reset password via email

### "Operation not allowed"
- Email/password auth not enabled
- Solution: Enable in Firebase Console → Auth → Sign-in methods

## Cost Considerations

Firebase Authentication is **FREE** for:
- ✅ Email/password auth
- ✅ Up to 50,000 token verifications/month
- ✅ First 100 users free
- ✅ Standard SMS (limited)

Cost kicks in for:
- 💰 High volume SMS OTP
- 💰 Enterprise features
- 💰 Advanced analytics

## Environment-Specific Config

### Development
```env
ENVIRONMENT=development
GOOGLE_APPLICATION_CREDENTIALS=./service-account-key.json
```

### Production
```env
ENVIRONMENT=production
GOOGLE_APPLICATION_CREDENTIALS=/secure/path/service-account-key.json
DEBUG=False
```

## Debugging in Browser

Press `F12` and check Console for:
```javascript
// Firebase errors appear here
// Example:
// "Error: Too many login attempts..."
// "Error: Invalid email..."
```

Also check Network tab:
- Look for `/auth/verify-token` requests
- Check if Bearer token is sent correctly
- Monitor WebSocket connection to `/detect/stream`

## Integration with Backend

### Send Email After Signup
```python
from firebase_config import get_user_by_email

# In your signup endpoint
user = get_user_by_email(email)
# Send welcome email here
send_email(user.email, "Welcome to CoconutAI!")
```

### Log User Activity
```python
from firebase_config import verify_token

@app.post("/detect/image")
async def detect_image(decoded: dict = Depends(verify_firebase_token)):
    user_uid = decoded['uid']
    user_email = decoded['email']
    
    # Log to database
    log_user_activity(user_uid, "Image detection", user_email)
```

## Useful Links

- [Firebase Console](https://console.firebase.google.com/)
- [Firebase Auth Docs](https://firebase.google.com/docs/auth)
- [Firebase Admin SDK](https://firebase.google.com/docs/database/admin/start)
- [Email Configuration](https://firebase.google.com/docs/auth/custom-email-handler)

