"""
Drone GPS Module
Connects to drone for real-time GPS tracking via network or serial connection
Runs in simulation mode if hardware unavailable (generates realistic GPS traces)
"""

import asyncio
import json
import random
from typing import Optional, Dict, Tuple
from dataclasses import dataclass
from datetime import datetime
import math

try:
    import serial
    HAVE_SERIAL = True
except ImportError:
    HAVE_SERIAL = False


@dataclass
class DronePosition:
    """Drone GPS position data"""
    latitude: float
    longitude: float
    altitude: float  # meters
    timestamp: str
    accuracy: float = 5.0  # meters
    
    def to_dict(self) -> Dict:
        return {
            "lat": self.latitude,
            "lng": self.longitude,
            "altitude": self.altitude,
            "timestamp": self.timestamp,
            "accuracy": self.accuracy
        }


class DroneGPS:
    """Manages drone GPS connection and data streaming"""
    
    def __init__(self, drone_ip: str = "192.168.1.1", port: int = 8889, use_simulation: bool = False):
        """
        Initialize drone GPS connection
        
        Args:
            drone_ip: IP address of drone (for network-based telemetry)
            port: Port for drone communication
            use_simulation: Force simulation mode (for testing without hardware)
        """
        self.drone_ip = drone_ip
        self.port = port
        self.is_connected = False
        self.current_position: Optional[DronePosition] = None
        self.position_history = []
        self.max_history = 500
        self.use_simulation = use_simulation
        self.simulation_task = None
        
        # Simulation parameters - Using Panabo, Davao, Philippines
        self.sim_lat = 7.30806   # Panabo, Davao latitude
        self.sim_lng = 125.68417  # Panabo, Davao longitude
        self.sim_alt = 50.0  # Default altitude in meters (above ground)
        self.sim_frame = 0
        
    async def connect(self) -> bool:
        """Connect to drone GPS"""
        try:
            # Try network connection first
            if not self.use_simulation:
                connected = await self._try_network_connection()
                if connected:
                    return True
            
            # Fallback to simulation mode
            print("⚠️  No drone hardware detected. Running in simulation mode.")
            print("💡 To use real drone GPS:")
            print("   - Option 1: Connect via network (Wi-Fi/Ethernet)")
            print("   - Option 2: Connect via serial port (USB/UART)")
            print("   - Option 3: Manually implement telemetry parsing")
            
            self.is_connected = True
            self.simulation_task = asyncio.create_task(self._simulation_loop())
            return True
            
        except Exception as e:
            print(f"❌ Connection error: {str(e)}")
            return False
    
    async def _try_network_connection(self) -> bool:
        """Try to connect via network (UDP/TCP)"""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2)
            
            # Try to ping drone
            sock.sendto(b"ping", (self.drone_ip, self.port))
            data, _ = sock.recvfrom(1024)
            sock.close()
            
            print(f"✅ Connected to drone at {self.drone_ip}:{self.port}")
            asyncio.create_task(self._network_gps_loop())
            return True
        except Exception as e:
            return False
    
    async def _network_gps_loop(self):
        """Receive GPS data via network"""
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("0.0.0.0", self.port + 1))
            
            while self.is_connected:
                try:
                    data, _ = sock.recvfrom(1024)
                    # Parse GPS data (expect JSON format: {"lat": ..., "lng": ..., "alt": ...})
                    gps_data = json.loads(data.decode())
                    self.current_position = DronePosition(
                        latitude=gps_data.get("lat", 0.0),
                        longitude=gps_data.get("lng", 0.0),
                        altitude=gps_data.get("alt", 0.0),
                        timestamp=datetime.now().isoformat(),
                        accuracy=gps_data.get("accuracy", 5.0)
                    )
                    self._store_history()
                except Exception:
                    pass
                
                await asyncio.sleep(0.2)
        except Exception as e:
            print(f"Network GPS error: {str(e)}")
    
    async def _simulation_loop(self):
        """Simulate realistic drone GPS movements (circular flight pattern)"""
        while self.is_connected:
            try:
                # Simulate circular flight pattern
                self.sim_frame += 1
                progress = (self.sim_frame % 300) / 300.0  # Complete circle every 300 frames
                
                # Circular path with radius ~0.002 degrees (~200m at equator)
                angle = progress * 2 * math.pi
                offset_lat = 0.002 * math.cos(angle)
                offset_lng = 0.002 * math.sin(angle)
                
                # Slight altitude variation
                altitude_var = 5 * math.sin(progress * 2 * math.pi)
                
                self.current_position = DronePosition(
                    latitude=self.sim_lat + offset_lat + random.gauss(0, 0.0001),
                    longitude=self.sim_lng + offset_lng + random.gauss(0, 0.0001),
                    altitude=self.sim_alt + 15 + altitude_var + random.gauss(0, 0.5),
                    timestamp=datetime.now().isoformat(),
                    accuracy=5.0 + random.gauss(0, 1.0)
                )
                
                self._store_history()
                await asyncio.sleep(0.2)  # 5 Hz update rate
                
            except Exception as e:
                print(f"⚠️  Simulation error: {str(e)}")
                await asyncio.sleep(1)
    
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
        if self.current_position:
            return self.current_position.to_dict()
        return None
    
    def get_position_history(self, last_n: int = 100) -> list:
        """Get last N position records"""
        return [p.to_dict() for p in self.position_history[-last_n:]]
    
    async def disconnect(self):
        """Disconnect from drone"""
        self.is_connected = False
        if self.simulation_task:
            self.simulation_task.cancel()
        print("🛑 Drone GPS disconnected")


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

