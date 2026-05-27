"""
Drone GPS Module - Post-Flight Approach (Fixed & Enhanced)
Extracts GPS coordinates from geotagged drone photos via EXIF metadata.
Falls back to high-accuracy browser geolocation if image metadata is stripped.
Users upload images after flight, coordinates are extracted and saved to database.
This is the standard approach used in commercial drone agriculture (DroneDeploy, WebODM).
"""

import asyncio
import json
from typing import Optional, Dict
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


@dataclass
class DronePosition:
    """Drone GPS position data"""
    latitude: float
    longitude: float
    altitude: float  # meters
    timestamp: str
    accuracy: float = 5.0  # meters
    source: str = "drone_exif"  # Tracks metadata vs. browser fallback
    
    def to_dict(self) -> Dict:
        return {
            "lat": self.latitude,
            "lng": self.longitude,
            "altitude": self.altitude,
            "timestamp": self.timestamp,
            "accuracy": self.accuracy,
            "source": self.source
        }


class DroneGPS:
    """
    POST-FLIGHT GPS EXTRACTION (Fixed & Enhanced)
    Extracts GPS from drone EXIF, falls back to high-accuracy browser telemetry.
    """
    
    def __init__(self, drone_ip: str = None, port: int = None, use_simulation: bool = False):
        """Initialize GPS extractor for drone photos"""
        self.is_connected = True 
        self.current_position: Optional[DronePosition] = None
        self.position_history = []
        self.max_history = 500
        
    async def connect(self) -> bool:
        """
        Connect (no-op for post-flight approach)
        GPS data comes from:
        1. Uploaded drone images with EXIF GPS metadata
        2. Browser geolocation (when image has no GPS)
        """
        print("[INFO] 📸 POST-FLIGHT GPS MODE: Real location extraction enabled")
        print("[INFO] ✅ Using: Image EXIF metadata (drone) + Browser geolocation")
        print("[INFO] Waiting for geotagged drone images to be uploaded...")
        self.is_connected = True
        return True
    
    def extract_gps_from_image(self, image_data: bytes, filename: str = "unknown", fallback_coords: Optional[Dict] = None) -> Optional[DronePosition]:
        """
        Extract GPS coordinates from image EXIF data.
        
        Args:
            image_data: Raw image bytes from drone photo
            filename: Original filename for reference
            fallback_coords: High-accuracy browser GPS dictionary passed from frontend
                            {'lat': float, 'lng': float, 'accuracy': float}
            
        Returns:
            DronePosition with latitude, longitude, altitude, timestamp and source tracking
        """
        if not HAVE_PIL:
            print("[ERROR] ❌ PIL/Pillow not installed. Run: pip install pillow")
            return self._apply_fallback(fallback_coords, "missing_library")

        try:
            image = Image.open(BytesIO(image_data))
            exif_data = image._getexif()
            
            if not exif_data:
                print(f"[WARN] No EXIF data in {filename}. Image was likely stripped.")
                return self._apply_fallback(fallback_coords, "exif_stripped")
            
            # Extract GPSInfo dictionary safely
            gps_data = None
            for tag_id, value in exif_data.items():
                if TAGS.get(tag_id) == "GPSInfo":
                    gps_data = value
                    break
            
            if not gps_data:
                print(f"[WARN] No GPS block inside EXIF for {filename}")
                return self._apply_fallback(fallback_coords, "no_gps_tags")
            
            # ✅ FIX: Safe conversion of PIL IFDRational / tuple structures to floats
            try:
                lat_dms = [float(x) for x in gps_data.get(2, (0, 0, 0))]
                lng_dms = [float(x) for x in gps_data.get(4, (0, 0, 0))]
                
                lat_ref = gps_data.get(1, 'N')
                lng_ref = gps_data.get(3, 'E')
                
                # ✅ FIX: Proper direction handling
                lat = self._dms_to_decimal(lat_dms, lat_ref in ['S', 'W'])
                lng = self._dms_to_decimal(lng_dms, lng_ref in ['S', 'W'])
                
                # Safely parse altitude fraction
                raw_alt = gps_data.get(6, 0.0)
                alt = float(raw_alt) if not isinstance(raw_alt, tuple) else float(raw_alt[0]) / float(raw_alt[1])
            except Exception as parse_err:
                print(f"[ERROR] Malformed GPS coordinates structure: {parse_err}")
                return self._apply_fallback(fallback_coords, "corrupted_metadata")
            
            # ✅ FIX: Package valid position and SAVE TO STATE
            position = DronePosition(
                latitude=lat,
                longitude=lng,
                altitude=alt,
                timestamp=exif_data.get("DateTime", datetime.now().isoformat()),
                accuracy=5.0,
                source="drone_exif"
            )
            
            # ✅ CRITICAL FIX: State is now properly saved
            self.current_position = position
            self._store_history()
            
            print(f"[OK] ✅ GPS extracted from EXIF: {lat:.6f}, {lng:.6f}, alt={alt:.1f}m")
            return position
            
        except Exception as e:
            print(f"[ERROR] Critical failure processing {filename}: {str(e)}")
            return self._apply_fallback(fallback_coords, "system_error")
            
    def _apply_fallback(self, fallback_coords: Optional[Dict], reason: str) -> Optional[DronePosition]:
        """Injects high-accuracy browser coordinates if the file is stripped."""
        if fallback_coords and "lat" in fallback_coords and "lng" in fallback_coords:
            fallback = DronePosition(
                latitude=float(fallback_coords["lat"]),
                longitude=float(fallback_coords["lng"]),
                altitude=float(fallback_coords.get("altitude", 0.0)),
                timestamp=datetime.now().isoformat(),
                accuracy=float(fallback_coords.get("accuracy", 10.0)),
                source=f"browser_fallback_{reason}"
            )
            # ✅ Save fallback position to state
            self.current_position = fallback
            self._store_history()
            print(f"[INFO] 🛰️ Fallback used ({reason}). Browser GPS Saved: {fallback.latitude:.6f}, {fallback.longitude:.6f}, accuracy={fallback.accuracy:.1f}m")
            return fallback
        
        print("[WARN] ❌ No EXIF data and no high-accuracy fallback data was provided.")
        return None

    @staticmethod
    def _dms_to_decimal(dms_list, is_negative):
        """Convert degrees/minutes/seconds to decimal degrees"""
        try:
            decimal = dms_list[0] + (dms_list[1] / 60.0) + (dms_list[2] / 3600.0)
            return -decimal if is_negative else decimal
        except:
            return 0.0

    def _store_history(self):
        """Store position in history"""
        if self.current_position:
            if len(self.position_history) >= self.max_history:
                self.position_history.pop(0)
            self.position_history.append(self.current_position)

    async def get_position(self) -> Optional[DronePosition]:
        """Get current drone position"""
        return self.current_position
    
    def get_position_sync(self) -> Optional[Dict]:
        """Get current drone position (synchronous)"""
        return self.current_position.to_dict() if self.current_position else None
    
    def get_position_history(self, last_n: int = 100) -> list:
        """Get all extracted GPS coordinates from photos"""
        return [p.to_dict() for p in self.position_history[-last_n:]]
    
    async def disconnect(self):
        """Disconnect (no-op for post-flight approach)"""
        self.is_connected = False
        print("[OK] GPS extraction stopped")


# Global drone GPS instance
_drone_gps: Optional[DroneGPS] = None


def init_drone_gps(drone_ip: str = "192.168.1.1", port: int = 8889, use_simulation: bool = False) -> DroneGPS:
    """Initialize global drone GPS instance"""
    global _drone_gps
    _drone_gps = DroneGPS(drone_ip=drone_ip, port=port, use_simulation=use_simulation)
    return _drone_gps


def get_drone_gps() -> Optional[DroneGPS]:
    """Get global drone GPS instance"""
    return _drone_gps


def get_current_drone_position() -> Optional[Dict]:
    """Get current drone position (convenient function)"""
    if _drone_gps:
        return _drone_gps.get_position_sync()
    return None

