import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, List
from io import BytesIO

from PIL import Image
import piexif


@dataclass
class EXIFGPS:
    latitude: float
    longitude: float
    altitude: float
    accuracy: Optional[float]
    source: str


class DroneGPS:
    def __init__(self, drone_ip: Optional[str] = None, port: Optional[int] = None, use_simulation: bool = False):
        self.drone_ip = drone_ip
        self.port = port
        self.use_simulation = use_simulation
        self.connected = False
        self._history = deque(maxlen=2000)
        self._lock = asyncio.Lock()

    async def connect(self) -> bool:
        try:
            # Minimal connection logic: simulation mode seeds a default position.
            if self.use_simulation:
                self.connected = True
                # seed a sensible default (Davao center)
                self.add_position(7.0731, 125.6123, alt=0.0, accuracy=100.0, source="sim")
                return True

            # For real drones, connection logic would be implemented here.
            # For now, mark as connected to allow downstream functionality.
            self.connected = True
            return True
        except Exception:
            self.connected = False
            return False

    async def disconnect(self) -> None:
        self.connected = False

    def get_position_history(self, last_n: int = 100) -> List[dict]:
        return list(self._history)[-last_n:]

    def add_position(self, lat: float, lng: float, alt: float = 0.0, accuracy: float = 99999.0, source: str = "drone") -> None:
        entry = {
            "lat": float(lat),
            "lng": float(lng),
            "alt": float(alt),
            "accuracy": float(accuracy) if accuracy is not None else None,
            "timestamp": time.time(),
            "source": source,
        }
        self._history.append(entry)

    def current_position(self) -> Optional[dict]:
        if not self._history:
            return None
        return self._history[-1]

    def extract_gps_from_image(self, contents: bytes, filename: str, fallback_coords: Optional[dict] = None) -> Optional[EXIFGPS]:
        """Attempt to extract GPS EXIF from image bytes using piexif/Pillow.

        Returns an EXIFGPS on success, or a fallback EXIFGPS when fallback_coords provided.
        Returns None if no GPS info and no fallback provided.
        """
        try:
            img = Image.open(BytesIO(contents))
            exif_bytes = img.info.get("exif")
            if not exif_bytes:
                if fallback_coords:
                    return EXIFGPS(fallback_coords["lat"], fallback_coords["lng"], fallback_coords.get("alt", 0.0), fallback_coords.get("accuracy", None), "browser_fallback")
                return None

            exif = piexif.load(exif_bytes)
            gps_ifd = exif.get("GPS", {})
            if not gps_ifd:
                if fallback_coords:
                    return EXIFGPS(fallback_coords["lat"], fallback_coords["lng"], fallback_coords.get("alt", 0.0), fallback_coords.get("accuracy", None), "browser_fallback")
                return None

            def _rat_to_float(rat):
                return float(rat[0]) / float(rat[1]) if rat[1] != 0 else 0.0

            def _to_deg(rational_tuple):
                d = _rat_to_float(rational_tuple[0])
                m = _rat_to_float(rational_tuple[1])
                s = _rat_to_float(rational_tuple[2])
                return d + (m / 60.0) + (s / 3600.0)

            lat = None
            lng = None
            alt = 0.0
            accuracy = None

            lat_ref = gps_ifd.get(piexif.GPSIFD.GPSLatitudeRef)
            lat_val = gps_ifd.get(piexif.GPSIFD.GPSLatitude)
            lon_ref = gps_ifd.get(piexif.GPSIFD.GPSLongitudeRef)
            lon_val = gps_ifd.get(piexif.GPSIFD.GPSLongitude)

            if lat_val and lat_ref and lon_val and lon_ref:
                lat = _to_deg(lat_val)
                try:
                    if isinstance(lat_ref, bytes):
                        lat_ref = lat_ref.decode("utf-8")
                    if lat_ref and lat_ref.upper() == "S":
                        lat = -lat
                except Exception:
                    pass

                lng = _to_deg(lon_val)
                try:
                    if isinstance(lon_ref, bytes):
                        lon_ref = lon_ref.decode("utf-8")
                    if lon_ref and lon_ref.upper() == "W":
                        lng = -lng
                except Exception:
                    pass

            alt_val = gps_ifd.get(piexif.GPSIFD.GPSAltitude)
            if alt_val:
                try:
                    alt = _rat_to_float(alt_val)
                except Exception:
                    alt = 0.0

            dop = gps_ifd.get(piexif.GPSIFD.GPSDOP)
            if dop:
                try:
                    accuracy = _rat_to_float(dop)
                except Exception:
                    accuracy = None

            if lat is None or lng is None:
                if fallback_coords:
                    return EXIFGPS(fallback_coords["lat"], fallback_coords["lng"], fallback_coords.get("alt", 0.0), fallback_coords.get("accuracy", None), "browser_fallback")
                return None

            gps = EXIFGPS(latitude=lat, longitude=lng, altitude=alt, accuracy=accuracy, source="exif")
            # store in history
            try:
                self.add_position(lat, lng, alt=alt, accuracy=accuracy if accuracy is not None else 0.0, source="exif")
            except Exception:
                pass

            return gps
        except Exception:
            if fallback_coords:
                return EXIFGPS(fallback_coords["lat"], fallback_coords["lng"], fallback_coords.get("alt", 0.0), fallback_coords.get("accuracy", None), "browser_fallback")
            return None


# Module-level singleton
_DRONE_GPS: Optional[DroneGPS] = None


def init_drone_gps(drone_ip: Optional[str] = None, port: Optional[int] = None, use_simulation: bool = False) -> DroneGPS:
    global _DRONE_GPS
    if _DRONE_GPS is None:
        _DRONE_GPS = DroneGPS(drone_ip=drone_ip, port=port, use_simulation=use_simulation)
    return _DRONE_GPS


def get_drone_gps() -> Optional[DroneGPS]:
    return _DRONE_GPS


def get_current_drone_position() -> Optional[dict]:
    if _DRONE_GPS:
        return _DRONE_GPS.current_position()
    return None
