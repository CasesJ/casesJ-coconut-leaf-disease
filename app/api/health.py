"""
app/api/health.py — Health check and utility endpoints.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    from model import detector  # noqa: PLC0415
    return {
        "status": "ok",
        "model": "YOLO26 v6",
        "backend": detector.backend,
        "weights": detector.active_model_path.name if detector.active_model_path else None,
    }


@router.get("/classes")
def get_classes():
    from model import detector  # noqa: PLC0415
    return {"classes": detector.class_names}


@router.get("/")
def root():
    return HTMLResponse(open("static/index.html", encoding="utf-8").read())


@router.get("/login/")
def login():
    return root()


@router.get("/favicon.ico")
async def favicon():
    favicon_svg = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>
  <text y='75' font-size='75'>🥥</text>
</svg>"""
    return Response(content=favicon_svg, media_type="image/svg+xml")
