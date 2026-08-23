"""
app/infrastructure/ml/detector.py — ML detector adapter.

Provides a stable import path for the CoconutDiseaseDetector singleton.
The actual class and its singleton live in model.py (unchanged).
"""
from model import CoconutDiseaseDetector, detector  # noqa: F401

__all__ = ["CoconutDiseaseDetector", "detector"]
