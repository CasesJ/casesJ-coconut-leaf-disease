import firebase_admin
from firebase_admin import db
import os
from dotenv import load_dotenv

load_dotenv()

# Import firebase config to initialize
from firebase_config import FIREBASE_CONFIG

print("🔍 Testing Firebase Database Connection...\n")

try:
    # Try to access the database
    ref = db.reference('test_connection')
    test_data = {
        'timestamp': '2024-04-04T00:00:00',
        'message': 'Test write - this should appear in Firebase'
    }
    
    print(f"📝 Writing test data...")
    push_result = ref.push(test_data)
    print(f"✅ Test data written successfully!")
    print(f"   Key: {push_result.key}")
    print(f"   Path: test_connection/{push_result.key}")
    
    # Try to read it back
    print(f"\n📖 Reading test data back...")
    read_data = ref.get()
    print(f"✅ Test data read successfully!")
    print(f"   Data: {read_data.val()}")
    
    # Clean up
    push_result.delete()
    print(f"\n🗑️  Test data cleaned up")
    
except Exception as e:
    print(f"❌ Firebase Database Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*60)
print("📌 SUMMARY:")
print("="*60)
print(f"Firebase Config: {FIREBASE_CONFIG.get('projectId')}")
print(f"Database URL: https://coconut-leaf-disease-dcf9a-default-rtdb.firebaseio.com")
print(f"Service Account Path: {os.getenv('GOOGLE_APPLICATION_CREDENTIALS')}")
