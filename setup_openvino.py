#!/usr/bin/env python3
"""
OpenVINO Quick Start Setup Script
Automates OpenVINO environment setup and verification
"""

import sys
import subprocess
import os
from pathlib import Path

def print_header(text):
    """Print formatted header"""
    print("\n" + "="*70)
    print(f"  {text}")
    print("="*70 + "\n")

def print_step(step, text):
    """Print step with number"""
    print(f"[{step}] {text}")

def check_python_version():
    """Verify Python version"""
    print_header("🐍 Checking Python Version")
    
    if sys.version_info < (3, 8):
        print("❌ Python 3.8+ required")
        print(f"   Current: Python {sys.version_info.major}.{sys.version_info.minor}")
        return False
    
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor} - OK")
    return True

def check_model_files():
    """Verify OpenVINO model files exist"""
    print_header("📁 Checking Model Files")
    
    required_files = [
        Path("best_openvino_model/best.xml"),
        Path("best_openvino_model/best.bin"),
        Path("best_openvino_model/metadata.yaml"),
    ]
    
    all_exist = True
    for file_path in required_files:
        if file_path.exists():
            size_mb = file_path.stat().st_size / (1024 * 1024)
            print(f"✅ {file_path} ({size_mb:.1f} MB)")
        else:
            print(f"❌ {file_path} NOT FOUND")
            all_exist = False
    
    return all_exist

def install_dependencies():
    """Install required packages"""
    print_header("📦 Installing Dependencies")
    
    # Check if requirements.txt exists
    req_file = Path("models/requirements.txt")
    if not req_file.exists():
        print(f"❌ {req_file} not found")
        return False
    
    print("Installing packages from requirements.txt...")
    print("(This may take a few minutes)\n")
    
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "-q", "-r", str(req_file)
        ])
        print("\n✅ Dependencies installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Installation failed: {e}")
        return False

def test_openvino_import():
    """Test if OpenVINO can be imported"""
    print_header("🔧 Testing OpenVINO Import")
    
    try:
        import openvino
        print(f"✅ OpenVINO imported successfully")
        print(f"   Version: {openvino.__version__}")
        return True
    except ImportError as e:
        print(f"❌ Failed to import OpenVINO: {e}")
        print("\n💡 Try running:")
        print("   pip install openvino>=2024.0.0")
        return False

def test_model_loading():
    """Test loading the model"""
    print_header("🤖 Testing Model Loading")
    
    try:
        from model import detector
        print("✅ Model loaded successfully")
        print(f"   Input shape: {detector.input_shape}")
        print(f"   Classes: {len(detector.class_names)}")
        
        # List classes
        print("\n   Available classes:")
        for class_id, class_name in detector.class_names.items():
            print(f"      {class_id}: {class_name}")
        
        return True
    except Exception as e:
        print(f"❌ Failed to load model: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_inference():
    """Test single inference"""
    print_header("⚡ Testing Inference")
    
    try:
        import cv2
        import numpy as np
        import time
        from model import detector
        
        # Create dummy image
        print("Creating test image...")
        dummy_image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        
        # Run inference
        print("Running inference...")
        start = time.time()
        result = detector.predict(dummy_image, conf=50)
        elapsed = time.time() - start
        
        print(f"✅ Inference successful")
        print(f"   Time: {elapsed*1000:.2f}ms")
        print(f"   FPS: {1/elapsed:.2f}")
        print(f"   Detections: {len(result['detections'])}")
        
        return True
    except Exception as e:
        print(f"❌ Inference test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def run_test_suite():
    """Run full test suite"""
    print_header("🧪 Running Full Test Suite")
    
    try:
        result = subprocess.run(
            [sys.executable, "test_openvino_realtime.py"],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        print(result.stdout)
        if result.returncode == 0:
            print("✅ Test suite passed")
            return True
        else:
            print("❌ Test suite failed")
            print(result.stderr)
            return False
    except subprocess.TimeoutExpired:
        print("⚠️  Test suite timed out")
        return False
    except Exception as e:
        print(f"❌ Error running test suite: {e}")
        return False

def display_next_steps():
    """Display next steps for user"""
    print_header("✨ Next Steps")
    
    print("🚀 Your OpenVINO setup is ready! Here's what to do next:\n")
    
    print("1️⃣  Start the FastAPI server:")
    print("   python main.py\n")
    
    print("2️⃣  Test real-time detection with webcam:")
    print("   python realtime_stream.py\n")
    
    print("3️⃣  Access the web interface:")
    print("   Open http://localhost:8000 in your browser\n")
    
    print("4️⃣  Use drone GPS integration:")
    print("   python drone_gps.py\n")
    
    print("📖 For more info, see OPENVINO_SETUP.md\n")

def main():
    """Main setup flow"""
    print_header("🎯 OpenVINO Quick Start Setup")
    
    steps = [
        ("Python Version Check", check_python_version),
        ("Model Files Check", check_model_files),
        ("Install Dependencies", install_dependencies),
        ("Test OpenVINO Import", test_openvino_import),
        ("Test Model Loading", test_model_loading),
        ("Test Inference", test_inference),
    ]
    
    results = {}
    step_num = 1
    
    for step_name, step_func in steps:
        print_step(step_num, step_name)
        results[step_name] = step_func()
        step_num += 1
    
    # Summary
    print_header("📊 Setup Summary")
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    print(f"Passed: {passed}/{total}\n")
    
    for step_name, result in results.items():
        status = "✅" if result else "❌"
        print(f"{status} {step_name}")
    
    if passed == total:
        print("\n" + "="*70)
        print("  🎉 Setup Complete! All tests passed!")
        print("="*70)
        display_next_steps()
        
        # Optionally run test suite
        print("\nWould you like to run the full test suite? (y/n)")
        try:
            response = input().strip().lower()
            if response == 'y':
                run_test_suite()
        except:
            pass
        
        return 0
    else:
        print("\n" + "="*70)
        print("  ⚠️  Setup Incomplete - Some checks failed")
        print("="*70)
        print("\n💡 Please fix the issues above and run this script again.\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
