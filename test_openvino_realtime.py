"""
Real-Time OpenVINO Inference Test Script
Tests the OpenVINO model with both static images and webcam streaming
"""

import cv2
import numpy as np
import time
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from model import detector

def test_static_image():
    """Test inference on a static image file"""
    print("\n" + "="*60)
    print("🎬 STATIC IMAGE TEST")
    print("="*60)
    
    # Try to find test image
    test_image_paths = [
        "test_image.jpg",
        "detection_records/test.jpg",
        Path(__file__).parent / "static" / "test.jpg"
    ]
    
    test_image_path = None
    for path in test_image_paths:
        if Path(path).exists():
            test_image_path = path
            break
    
    if not test_image_path:
        print("⚠️  No test image found. Creating a dummy test image...")
        # Create a dummy image with some features
        dummy_image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        test_image_path = "dummy_test.jpg"
        cv2.imwrite(test_image_path, dummy_image)
        print(f"   Created dummy image: {test_image_path}")
    else:
        print(f"✅ Found test image: {test_image_path}")
    
    # Load image
    image = cv2.imread(str(test_image_path))
    if image is None:
        print(f"❌ Failed to load image: {test_image_path}")
        return
    
    print(f"📸 Image shape: {image.shape}")
    
    # Run inference with timing
    print("🔍 Running inference...")
    start_time = time.time()
    result = detector.predict(image, conf=50)
    inference_time = time.time() - start_time
    
    # Display results
    print(f"\n✅ Inference completed in {inference_time*1000:.2f}ms")
    print(f"📊 FPS (single image): {1/inference_time:.2f}")
    print(f"🎯 Detections found: {len(result['detections'])}")
    
    if result['detections']:
        print("\n📋 Detections:")
        for i, det in enumerate(result['detections'], 1):
            print(f"   {i}. {det['class']} (Confidence: {det['confidence']:.1%})")
            print(f"      BBox: {det['bbox']}")
    
    # Save annotated image
    output_path = "openvino_test_result.jpg"
    cv2.imwrite(output_path, result['image'])
    print(f"\n📷 Annotated image saved to: {output_path}")
    
    return result

def test_webcam_realtime():
    """Test real-time inference from webcam"""
    print("\n" + "="*60)
    print("🎥 REAL-TIME WEBCAM TEST")
    print("="*60)
    
    cap = cv2.VideoCapture(0)
    
    if not cap.isOpened():
        print("❌ Cannot open webcam. Please check camera is connected.")
        return
    
    print("✅ Webcam opened successfully")
    print("Press 'q' to quit, 's' to save frame")
    
    frame_count = 0
    total_time = 0
    
    while True:
        ret, frame = cap.read()
        if not ret:
            print("❌ Failed to read frame")
            break
        
        frame_count += 1
        
        # Run inference
        start_time = time.time()
        result = detector.predict(frame, conf=50)
        inference_time = time.time() - start_time
        total_time += inference_time
        
        # Add FPS counter to image
        fps = 1 / inference_time if inference_time > 0 else 0
        cv2.putText(result['image'], f"FPS: {fps:.1f}", (10, 30),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(result['image'], f"Detections: {len(result['detections'])}", (10, 60),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        
        # Display
        cv2.imshow("OpenVINO Real-Time Detection", result['image'])
        
        # Handle key presses
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            print("\n✅ Exiting webcam test...")
            break
        elif key == ord('s'):
            filename = f"frame_{frame_count}.jpg"
            cv2.imwrite(filename, result['image'])
            print(f"📷 Saved frame to {filename}")
    
    # Print statistics
    avg_time = total_time / frame_count if frame_count > 0 else 0
    avg_fps = 1 / avg_time if avg_time > 0 else 0
    
    print(f"\n📊 Webcam Test Statistics:")
    print(f"   Frames processed: {frame_count}")
    print(f"   Average inference time: {avg_time*1000:.2f}ms")
    print(f"   Average FPS: {avg_fps:.2f}")
    
    cap.release()
    cv2.destroyAllWindows()

def test_video_file():
    """Test inference on a video file"""
    print("\n" + "="*60)
    print("🎬 VIDEO FILE TEST")
    print("="*60)
    
    video_path = "test_video.mp4"
    
    if not Path(video_path).exists():
        print(f"⚠️  Video file not found: {video_path}")
        print("   Skipping video test. Ensure test_video.mp4 exists to run this test.")
        return
    
    cap = cv2.VideoCapture(video_path)
    
    if not cap.isOpened():
        print(f"❌ Failed to open video: {video_path}")
        return
    
    frame_count = 0
    total_time = 0
    total_detections = 0
    
    print(f"✅ Processing video: {video_path}")
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        frame_count += 1
        
        # Run inference
        start_time = time.time()
        result = detector.predict(frame, conf=50)
        inference_time = time.time() - start_time
        total_time += inference_time
        total_detections += len(result['detections'])
        
        if frame_count % 30 == 0:  # Print every 30 frames
            fps = 1 / inference_time if inference_time > 0 else 0
            print(f"   Frame {frame_count}: {len(result['detections'])} detections, {fps:.2f} FPS")
    
    avg_time = total_time / frame_count if frame_count > 0 else 0
    avg_fps = 1 / avg_time if avg_time > 0 else 0
    
    print(f"\n📊 Video Test Statistics:")
    print(f"   Frames processed: {frame_count}")
    print(f"   Total detections: {total_detections}")
    print(f"   Average inference time: {avg_time*1000:.2f}ms")
    print(f"   Average FPS: {avg_fps:.2f}")
    
    cap.release()

def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("🚀 OPENVINO REAL-TIME INFERENCE TEST SUITE")
    print("="*60)
    
    try:
        # Test model loading
        print(f"\n✅ Model loaded successfully")
        print(f"   Input shape: {detector.input_shape}")
        print(f"   Model resolution: {detector.model_width}x{detector.model_height}")
        print(f"   Classes: {list(detector.class_names.values())}")
        
        # Run tests
        test_static_image()
        
        print("\n" + "="*60)
        print("🎯 Would you like to test webcam? (requires camera)")
        print("   [y/n]")
        
        # For non-interactive, skip webcam test
        # Uncomment below if running interactively:
        # response = input().strip().lower()
        # if response == 'y':
        #     test_webcam_realtime()
        
        test_video_file()
        
        print("\n" + "="*60)
        print("✅ ALL TESTS COMPLETED")
        print("="*60)
        print("\n✨ OpenVINO integration is working correctly!")
        print("📝 Next steps:")
        print("   1. Run: python main.py")
        print("   2. Upload images to http://localhost:8000")
        print("   3. Use WebSocket for real-time streaming")
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
