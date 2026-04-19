"""
Advanced Real-Time Streaming with OpenVINO
Optimized for continuous inference from webcam, drone, or video streams
"""

import cv2
import numpy as np
import time
import threading
from collections import deque
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
from model import detector

class RealtimeDetector:
    """Optimized real-time detector with frame queue and threading"""
    
    def __init__(self, source=0, confidence=50, max_queue_size=2):
        """
        Initialize real-time detector
        
        Args:
            source: 0 for webcam, file path for video, or RTSP URL
            confidence: Detection confidence threshold (0-100)
            max_queue_size: Maximum frames to queue (for buffering)
        """
        self.source = source
        self.confidence = confidence
        self.cap = cv2.VideoCapture(source)
        self.frame_queue = deque(maxlen=max_queue_size)
        self.result_queue = deque(maxlen=1)
        self.running = False
        self.fps = 0
        self.frame_count = 0
        
        if not self.cap.isOpened():
            raise ValueError(f"Cannot open video source: {source}")
        
        # Get video properties
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        print(f"✅ Video source opened")
        print(f"   Resolution: {self.width}x{self.height}")
        print(f"   FPS: {self.fps}")
    
    def capture_frames(self):
        """Thread function: Continuously capture frames"""
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                print("⚠️  End of stream reached")
                break
            
            self.frame_queue.append(frame)
    
    def process_frames(self):
        """Thread function: Continuously process frames from queue"""
        inference_times = deque(maxlen=30)  # Keep last 30 inference times
        
        while self.running:
            try:
                if len(self.frame_queue) == 0:
                    time.sleep(0.001)
                    continue
                
                frame = self.frame_queue.popleft()
                self.frame_count += 1
                
                # Run inference
                start = time.time()
                result = detector.predict(frame.copy(), conf=self.confidence)
                inference_time = time.time() - start
                inference_times.append(inference_time)
                
                # Store result (only keeps latest)
                self.result_queue.append({
                    'image': result['image'],
                    'detections': result['detections'],
                    'inference_time': inference_time,
                    'frame_number': self.frame_count,
                    'avg_fps': 1 / (sum(inference_times) / len(inference_times))
                })
                
            except Exception as e:
                print(f"❌ Error processing frame: {e}")
    
    def get_latest_result(self):
        """Get the latest inference result"""
        if len(self.result_queue) > 0:
            return self.result_queue[0]
        return None
    
    def start(self):
        """Start capture and processing threads"""
        self.running = True
        
        capture_thread = threading.Thread(target=self.capture_frames, daemon=True)
        process_thread = threading.Thread(target=self.process_frames, daemon=True)
        
        capture_thread.start()
        process_thread.start()
        
        print("✅ Real-time detector started")
    
    def stop(self):
        """Stop all threads"""
        self.running = False
        self.cap.release()
        print("✅ Real-time detector stopped")
    
    def display_stream(self, window_name="OpenVINO Real-Time Detection"):
        """Display the real-time stream with detections"""
        while self.running:
            result = self.get_latest_result()
            
            if result:
                img = result['image'].copy()
                
                # Add stats overlay
                stats_text = [
                    f"FPS: {result['avg_fps']:.1f}",
                    f"Inference: {result['inference_time']*1000:.1f}ms",
                    f"Detections: {len(result['detections'])}",
                    f"Frame: {result['frame_number']}"
                ]
                
                y_offset = 30
                for text in stats_text:
                    cv2.putText(img, text, (10, y_offset),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                    y_offset += 25
                
                cv2.imshow(window_name, img)
                
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    self.stop()
                    break
                elif key == ord('s'):
                    filename = f"capture_{result['frame_number']}.jpg"
                    cv2.imwrite(filename, img)
                    print(f"📷 Saved: {filename}")
            else:
                time.sleep(0.01)
        
        cv2.destroyAllWindows()


def benchmark_performance():
    """Benchmark model performance on different resolutions"""
    print("\n" + "="*60)
    print("⚡ PERFORMANCE BENCHMARK")
    print("="*60)
    
    resolutions = [
        (640, 480),
        (1280, 720),
        (1920, 1080),
    ]
    
    for width, height in resolutions:
        print(f"\n📐 Testing {width}x{height}...")
        
        # Create dummy image
        image = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
        
        times = []
        for _ in range(10):
            start = time.time()
            detector.predict(image, conf=50)
            times.append(time.time() - start)
        
        avg_time = np.mean(times)
        fps = 1 / avg_time
        
        print(f"   Avg inference time: {avg_time*1000:.2f}ms")
        print(f"   FPS: {fps:.2f}")


def test_with_specific_device(device="CPU"):
    """Test inference with specific device (CPU/GPU/MYRIAD)"""
    print(f"\n✅ Testing with device: {device}")
    
    image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
    
    start = time.time()
    result = detector.predict(image, conf=50)
    elapsed = time.time() - start
    
    print(f"   Inference time: {elapsed*1000:.2f}ms")
    print(f"   FPS: {1/elapsed:.2f}")
    print(f"   Detections: {len(result['detections'])}")


def main():
    """Main function for real-time testing"""
    print("\n" + "="*60)
    print("🎯 ADVANCED REAL-TIME STREAMING TEST")
    print("="*60)
    
    try:
        # Option 1: Test with webcam
        print("\n1️⃣  Starting webcam stream...")
        detector_rt = RealtimeDetector(
            source=0,
            confidence=50,
            max_queue_size=2
        )
        detector_rt.start()
        detector_rt.display_stream()
        detector_rt.stop()
        
    except KeyboardInterrupt:
        print("\n⚠️  Interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    
    # Option 2: Performance benchmarking
    print("\n" + "="*60)
    print("Would you like to run performance benchmark? (y/n)")
    # Uncomment for interactive mode:
    # if input().strip().lower() == 'y':
    #     benchmark_performance()


if __name__ == "__main__":
    main()
