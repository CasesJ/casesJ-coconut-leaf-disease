# Drone GPS Integration Guide

## Overview
The system includes real-time drone GPS tracking that works in three modes:
1. **Simulation Mode** (default) - Generates realistic circular flight patterns for testing
2. **Network Mode** - Receives GPS data via Wi-Fi/Ethernet UDP packets
3. **Serial Mode** - Reads GPS data from USB/UART serial port

## Current Status
✅ Running in **Simulation Mode** - The app generates realistic GPS coordinates automatically without hardware.

## To Use Real Drone Hardware

### Option 1: Network Connection (Wi-Fi/Ethernet)
Best for: DJI drones, telemetry modules with network adapter

**Setup:**
1. Connect drone to laptop via Wi-Fi
2. Drone sends GPS as JSON via UDP to port 8890:
   ```json
   {"lat": 7.30806, "lng": 125.68417, "alt": 25.0, "accuracy": 5.0}
   ```
3. App automatically receives and maps the data

### Option 2: Serial Connection (USB/UART)
Best for: Pixhawk, Hobby drones, custom telemetry boards

**Setup:**
1. Install `pyserial` (already in requirements):
   ```bash
   pip install pyserial
   ```
2. Update `drone_gps.py` with your serial parser:
   ```python
   # In _network_gps_loop, add serial port reading:
   import serial
   ser = serial.Serial('COM3', 115200)  # Adjust COM port and baud rate
   data = ser.readline()  # Parse your telemetry format
   ```

### Option 3: DJI Official SDK
Best for: DJI Air, Mini, Mavic series

**Setup:**
1. Get DJI SDK from: https://developer.dji.com/
2. Replace `drone_gps.py` with DJI SDK implementation
3. Or use community packages like:
   ```bash
   pip install dji-flightsdk  # Community package (use with caution)
   ```

## How to Test

### Test Simulated GPS
The system is already running simulated GPS. Open the drone camera:
1. Click "Start Drone Camera"
2. Watch the GPS coordinates update (circular flight pattern)
3. Disease detections will be placed on the map with simulated GPS

### Test with Real Hardware
Edit `main.py` startup event:
```python
@app.on_event("startup")
async def startup_event():
    drone_gps = init_drone_gps(
        drone_ip="192.168.1.1",  # Your drone's IP
        port=8889,
        use_simulation=False  # Enable real mode
    )
```

## GPS Data Format
The WebSocket sends GPS in this format:
```json
{
  "detections": [...],
  "gps": {
    "lat": 7.30806,
    "lng": 125.68417,
    "altitude": 25.5,
    "timestamp": "2025-04-02T12:34:56.789Z",
    "accuracy": 5.0
  }
}
```

## Troubleshooting

**No pins appearing on map?**
- Check if GPS data is being sent in WebSocket response
- Open DevTools → Network → WS → check messages
- Verify drone is connected and sending coordinates

**Connection timeout?**
- Ensure drone is on same network as laptop
- Check firewall ports (8889, 8890)
- Verify drone IP address in code

**Want to send GPS from external source?**
- Send JSON to UDP port 8890:
  ```bash
  echo '{"lat":7.30806,"lng":125.68417,"alt":25}' | nc -u localhost 8890
  ```

## Files
- `drone_gps.py` - Main GPS module
- `main.py` - FastAPI integration (startup/shutdown events)
- `static/index.html` - Frontend WebSocket handler

