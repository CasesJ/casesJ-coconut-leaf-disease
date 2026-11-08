"""
app/api/health.py — Health check and utility endpoints.
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from app.core.config import INFERENCE_BACKEND

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """Return process health without initializing the inference model.

    Render uses this route while deciding whether the web process is ready.
    Importing ``model`` here constructs and compiles the OpenVINO singleton,
    which turns a lightweight readiness probe into an inference startup path.
    """
    weights = "weights.xml" if INFERENCE_BACKEND == "openvino" else "weights.pt"
    return {
        "status": "ok",
        "model": "YOLO26 v6",
        "backend": INFERENCE_BACKEND,
        "weights": weights,
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
