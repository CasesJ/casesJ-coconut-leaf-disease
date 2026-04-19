"""
OpenVINO Configuration
Central configuration for OpenVINO inference settings
"""

# Model Configuration
MODEL_CONFIG = {
    # Model paths
    "model_xml": "best_openvino_model/best.xml",
    "model_bin": "best_openvino_model/best.bin",
    
    # Inference device - Options: "CPU", "GPU", "MYRIAD", "AUTO"
    "device": "CPU",
    
    # Input resolution (YOLO11n standard)
    "input_width": 640,
    "input_height": 640,
    
    # Confidence thresholds
    "default_confidence": 50,  # 0-100
    "min_confidence": 25,
    "max_confidence": 95,
    
    # NMS (Non-Maximum Suppression) settings
    "nms_threshold": 0.45,
    "max_detections": 100,
    
    # Preprocessing
    "normalize_input": True,
    "input_range": (0.0, 1.0),  # Normalized range
}

# Class configuration
CLASS_CONFIG = {
    0: {
        "name": "Caterpillars",
        "color": (0, 60, 220),  # BGR - Red for disease
        "severity": "medium",
        "treatment_priority": 2
    },
    1: {
        "name": "Cercospora",
        "color": (0, 60, 220),
        "severity": "medium",
        "treatment_priority": 3
    },
    2: {
        "name": "Drying of Leaflets",
        "color": (0, 60, 220),
        "severity": "high",
        "treatment_priority": 1
    },
    3: {
        "name": "Healthy",
        "color": (0, 200, 100),  # BGR - Green for healthy
        "severity": "none",
        "treatment_priority": 0
    },
    4: {
        "name": "Pestalotiopsis",
        "color": (0, 60, 220),
        "severity": "medium",
        "treatment_priority": 4
    },
    5: {
        "name": "bud root",
        "color": (0, 60, 220),
        "severity": "critical",
        "treatment_priority": 0  # Highest priority
    }
}

# Real-time streaming configuration
STREAMING_CONFIG = {
    # Thread settings
    "num_capture_threads": 1,
    "num_inference_threads": 1,
    "max_queue_size": 2,
    
    # Performance monitoring
    "enable_fps_counter": True,
    "fps_averaging_window": 30,  # frames
    
    # Display settings
    "show_inference_time": True,
    "show_confidence": True,
    "font_scale": 0.7,
    "line_thickness": 2,
}

# Performance tuning
OPTIMIZATION_CONFIG = {
    # Input optimization
    "resize_on_input": True,
    "cache_preprocessing": False,
    
    # Inference optimization
    "async_mode": False,  # Experimental
    "batch_processing": False,
    "batch_size": 4,
    
    # Output optimization
    "use_nms": True,
    "skip_low_confidence": True,
    "low_confidence_threshold": 0.3,  # Skip predictions below this
}

# Logging configuration
LOGGING_CONFIG = {
    "log_level": "INFO",  # DEBUG, INFO, WARNING, ERROR
    "log_inference_time": True,
    "log_detections": True,
    "log_to_file": False,
    "log_file": "openvino_inference.log",
}

# Device-specific settings
DEVICE_SETTINGS = {
    "CPU": {
        "num_streams": 1,
        "num_threads": 4,
        "throughput_streams": "1",
    },
    "GPU": {
        "num_streams": 2,
        "num_threads": 8,
        "throughput_streams": "2",
        "required_plugin": "openvino-dev[gpu]",
    },
    "MYRIAD": {
        "num_streams": 1,
        "num_threads": 2,
        "required_plugin": "openvino-dev[myriad]",
    },
    "AUTO": {
        "num_streams": "AUTO",
        "num_threads": "AUTO",
    }
}

# Export configuration for different platforms
EXPORT_PROFILES = {
    "cpu_optimized": {
        "device": "CPU",
        "num_streams": 1,
        "precision": "FP32",
    },
    "gpu_optimized": {
        "device": "GPU",
        "num_streams": 2,
        "precision": "FP16",
    },
    "edge_device": {
        "device": "MYRIAD",
        "num_streams": 1,
        "precision": "FP16",
    },
    "balanced": {
        "device": "AUTO",
        "num_streams": "AUTO",
        "precision": "FP32",
    }
}


def get_config(profile="balanced"):
    """Get configuration for specified profile"""
    config = MODEL_CONFIG.copy()
    config.update(EXPORT_PROFILES.get(profile, EXPORT_PROFILES["balanced"]))
    return config


def update_device_config(device):
    """Update device-specific configuration"""
    if device in DEVICE_SETTINGS:
        return DEVICE_SETTINGS[device].copy()
    return DEVICE_SETTINGS["CPU"].copy()
