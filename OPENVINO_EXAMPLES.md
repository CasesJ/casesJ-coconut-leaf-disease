"""
OpenVINO Integration Examples
Real-world usage patterns for different deployment scenarios
"""

# ============================================================================
# Example 1: Basic Single Image Inference
# ============================================================================

def example_1_single_image():
    """Process a single image file"""
    import cv2
    from model import detector
    
    # Load image
    image = cv2.imread("leaf_image.jpg")
    
    # Run inference
    result = detector.predict(image, conf=50)
    
    # Get results
    detections = result['detections']
    annotated_image = result['image']
    
    # Display and save
    cv2.imshow("Detections", annotated_image)
    cv2.imwrite("result.jpg", annotated_image)
    
    print(f"Found {len(detections)} detections:")
    for det in detections:
        print(f"  {det['class']}: {det['confidence']:.1%}")


# ============================================================================
# Example 2: Real-Time Webcam Stream
# ============================================================================

def example_2_webcam_realtime():
    """Live detection from webcam"""
    import cv2
    import time
    from model import detector
    
    cap = cv2.VideoCapture(0)
    fps_display = 0
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        # Inference
        start = time.time()
        result = detector.predict(frame, conf=50)
        elapsed = time.time() - start
        fps_display = 1 / elapsed
        
        # Add FPS to display
        cv2.putText(result['image'], f"FPS: {fps_display:.1f}",
                   (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        
        cv2.imshow("Live Detection", result['image'])
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    
    cap.release()
    cv2.destroyAllWindows()


# ============================================================================
# Example 3: Batch Processing Multiple Images
# ============================================================================

def example_3_batch_processing():
    """Process multiple images efficiently"""
    import cv2
    from pathlib import Path
    from model import detector
    import json
    
    # Get all JPG images in a directory
    image_dir = Path("images")
    image_files = list(image_dir.glob("*.jpg"))
    
    results_data = []
    
    for idx, image_path in enumerate(image_files, 1):
        print(f"Processing {idx}/{len(image_files)}: {image_path.name}")
        
        # Load and process
        image = cv2.imread(str(image_path))
        result = detector.predict(image, conf=50)
        
        # Store results
        results_data.append({
            "filename": image_path.name,
            "detections": result['detections'],
            "num_detections": len(result['detections'])
        })
        
        # Optionally save annotated image
        output_path = Path("results") / f"annotated_{image_path.name}"
        cv2.imwrite(str(output_path), result['image'])
    
    # Save results to JSON
    with open("detection_results.json", "w") as f:
        json.dump(results_data, f, indent=2)
    
    print(f"✅ Processed {len(image_files)} images")


# ============================================================================
# Example 4: Drone Integration with GPS Coordinates
# ============================================================================

def example_4_drone_integration():
    """Process drone footage with GPS tagging"""
    import cv2
    import asyncio
    from model import detector
    from drone_gps import get_current_drone_position
    from datetime import datetime
    import json
    
    async def process_drone_frame():
        """Process frame with drone position"""
        cap = cv2.VideoCapture("drone_footage.mp4")
        frame_num = 0
        detections_log = []
        
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            
            frame_num += 1
            
            # Get drone position (if available)
            drone_pos = await get_current_drone_position()
            
            # Run detection
            result = detector.predict(frame, conf=50)
            
            # Log detection with GPS coordinates
            if result['detections']:
                for det in result['detections']:
                    detections_log.append({
                        "frame": frame_num,
                        "timestamp": datetime.now().isoformat(),
                        "drone_position": drone_pos,
                        "detection": det
                    })
        
        # Save log
        with open("drone_detections_log.json", "w") as f:
            json.dump(detections_log, f, indent=2)
        
        cap.release()
    
    # Run async loop
    asyncio.run(process_drone_frame())


# ============================================================================
# Example 5: FastAPI Integration (from main.py)
# ============================================================================

def example_5_fastapi_endpoint():
    """
    FastAPI endpoint for detection (already in main.py)
    Showing how it's used:
    """
    
    # In main.py, the detector is imported:
    from model import detector
    
    # Example endpoint:
    # @app.post("/detect")
    # async def detect_disease(file: UploadFile = File(...)):
    #     contents = await file.read()
    #     nparr = np.frombuffer(contents, np.uint8)
    #     image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    #     
    #     result = detector.predict(image, conf=50)
    #     
    #     return {
    #         "detections": result['detections'],
    #         "image_base64": encode_image(result['image'])
    #     }


# ============================================================================
# Example 6: WebSocket Real-Time Streaming
# ============================================================================

def example_6_websocket_streaming():
    """
    Real-time streaming via WebSocket (for browser display)
    Already implemented in main.py
    """
    
    # Usage in client JavaScript:
    # const ws = new WebSocket('ws://localhost:8000/ws/detect');
    # const video = document.getElementById('video');
    # const canvas = document.getElementById('canvas');
    # const ctx = canvas.getContext('2d');
    #
    # ws.onmessage = (event) => {
    #     const imageData = JSON.parse(event.data);
    #     // Display annotated image and detections
    # };


# ============================================================================
# Example 7: Export Results with Recommendations
# ============================================================================

def example_7_disease_recommendations():
    """Get treatment recommendations for detected diseases"""
    import cv2
    from model import detector
    
    image = cv2.imread("disease_sample.jpg")
    result = detector.predict(image, conf=50)
    
    # Get recommendations for each detection
    recommendations = []
    for det in result['detections']:
        rec = detector.get_fertilizer_recommendation(
            det['class'],
            det['confidence']
        )
        recommendations.append(rec)
    
    # Display recommendations
    for rec in recommendations:
        print(f"\n🌴 Disease: {rec['disease']}")
        print(f"   Confidence: {rec['confidence']:.1f}%")
        print(f"   Treatment:\n   {rec['treatment']}")
        print(f"   Prevention:\n   {rec['prevention']}")
        print(f"   Fertilizer:\n   {rec['fertilizer']}")


# ============================================================================
# Example 8: Performance Optimization
# ============================================================================

def example_8_performance_tuning():
    """Optimize inference for production"""
    import cv2
    from model import detector
    
    # For maximum speed:
    # 1. Reduce input size (but model expects 640x640)
    # 2. Lower confidence threshold to skip processing low scores
    
    image = cv2.imread("image.jpg")
    
    # Fast mode - less accurate but faster
    fast_result = detector.predict(image, conf=75)  # Higher threshold = fewer detections
    
    # Accurate mode - slower but more detections
    accurate_result = detector.predict(image, conf=30)
    
    print(f"Fast mode: {len(fast_result['detections'])} detections")
    print(f"Accurate mode: {len(accurate_result['detections'])} detections")


# ============================================================================
# Example 9: Error Handling and Logging
# ============================================================================

def example_9_error_handling():
    """Robust error handling"""
    import cv2
    import logging
    from model import detector
    
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    def safe_detect(image_path):
        try:
            # Validate image
            image = cv2.imread(str(image_path))
            if image is None:
                logger.error(f"Failed to load image: {image_path}")
                return None
            
            if image.shape[0] < 100 or image.shape[1] < 100:
                logger.warning(f"Image too small: {image.shape}")
            
            # Run detection
            result = detector.predict(image, conf=50)
            logger.info(f"Detected {len(result['detections'])} objects")
            
            return result
            
        except Exception as e:
            logger.error(f"Detection failed: {e}", exc_info=True)
            return None
    
    # Usage
    result = safe_detect("test.jpg")


# ============================================================================
# Example 10: Comparison with Confidence Thresholds
# ============================================================================

def example_10_confidence_analysis():
    """Analyze detection performance at different confidence levels"""
    import cv2
    from model import detector
    
    image = cv2.imread("test.jpg")
    
    thresholds = [30, 50, 70, 90]
    
    print("\nConfidence Threshold Analysis:")
    print("-" * 40)
    
    for threshold in thresholds:
        result = detector.predict(image, conf=threshold)
        print(f"Threshold {threshold}%: {len(result['detections'])} detections")
        for det in result['detections']:
            print(f"  - {det['class']}: {det['confidence']:.1%}")


if __name__ == "__main__":
    # Run examples (uncomment to test)
    print("OpenVINO Integration Examples")
    print("=" * 50)
    print("\nAvailable examples:")
    print("1. Single image inference")
    print("2. Webcam real-time")
    print("3. Batch processing")
    print("4. Drone integration")
    print("5. FastAPI endpoint")
    print("6. WebSocket streaming")
    print("7. Disease recommendations")
    print("8. Performance tuning")
    print("9. Error handling")
    print("10. Confidence analysis")
    print("\nImport and run specific examples as needed")
