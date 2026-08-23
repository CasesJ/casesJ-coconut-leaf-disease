"""
app/api/drone.py — Drone GPS position and history endpoints.
"""
from fastapi import APIRouter

router = APIRouter(prefix="/drone", tags=["drone"])


@router.get("/gps")
async def get_drone_gps_position():
    """Get current drone GPS position."""
    from drone_gps import get_current_drone_position  # noqa: PLC0415
    position = get_current_drone_position()
    if position:
        return {"status": "connected", "gps": position, "source": "drone"}
    return {"status": "no_connection", "gps": None, "source": "none",
            "message": "Drone GPS not available. Ensure drone is connected."}


@router.get("/gps/history")
async def get_drone_gps_history(last_n: int = 100):
    """Get recent drone GPS position history."""
    from drone_gps import get_drone_gps  # noqa: PLC0415
    drone_gps = get_drone_gps()
    if drone_gps:
        history = drone_gps.get_position_history(last_n)
        return {"status": "ok", "count": len(history), "history": history}
    return {"status": "error", "count": 0, "history": []}
