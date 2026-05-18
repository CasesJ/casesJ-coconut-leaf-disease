"""
Integration bridge between the detection system and ML recommendations.

Provides helpers to:
- Convert detection results from model.py to AIRecommendation objects
- Load/save historical data
- Manage inventory database
- Provide HTTP endpoints for recommendations
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

import pandas as pd
from ai_recommendations_ml import (
    RecommendationSystem,
    DetectionRecord,
    InventoryLog,
    AIRecommendation,
)

logger = logging.getLogger(__name__)


class InventoryManager:
    """
    Manage persistent inventory/treatment logs.
    
    Stores to JSON for portability and easy backup.
    """
    
    def __init__(self, inventory_file: str = "inventory_logs.json"):
        self.inventory_file = Path(inventory_file)
        self.logs: List[InventoryLog] = []
        self.load_from_file()
    
    def load_from_file(self):
        """Load inventory logs from JSON file"""
        if self.inventory_file.exists():
            try:
                with open(self.inventory_file, "r") as f:
                    data = json.load(f)
                
                self.logs = [
                    InventoryLog(**log_dict) for log_dict in data
                ]
                logger.info(f"✅ Loaded {len(self.logs)} inventory records")
            except Exception as e:
                logger.warning(f"⚠️  Could not load inventory: {e}")
                self.logs = []
        else:
            logger.info("📝 No existing inventory file. Starting fresh.")
            self.logs = []
    
    def save_to_file(self):
        """Save inventory logs to JSON file"""
        try:
            data = [log.to_dict() for log in self.logs]
            with open(self.inventory_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"✅ Saved {len(self.logs)} inventory records")
        except Exception as e:
            logger.error(f"❌ Failed to save inventory: {e}")
    
    def add_log(self, treatment_data: Dict[str, Any]) -> InventoryLog:
        """Add a new inventory log"""
        log = InventoryLog(
            treatment_name=treatment_data["treatment_name"],
            treatment_type=treatment_data["treatment_type"],
            cost=float(treatment_data["cost"]),
            application_date=treatment_data.get("application_date", datetime.utcnow().isoformat()),
            effectiveness_rating=float(treatment_data["effectiveness_rating"]),
            field_id=treatment_data["field_id"],
            disease_treated=treatment_data["disease_treated"],
            notes=treatment_data.get("notes"),
        )
        self.logs.append(log)
        self.save_to_file()
        return log
    
    def get_logs_by_disease(self, disease: str) -> List[InventoryLog]:
        """Get all logs for a specific disease"""
        return [log for log in self.logs if log.disease_treated.lower() == disease.lower()]
    
    def get_logs_by_field(self, field_id: str) -> List[InventoryLog]:
        """Get all logs for a specific field"""
        return [log for log in self.logs if log.field_id == field_id]
    
    def get_successful_treatments(self, min_rating: float = 4.0) -> List[InventoryLog]:
        """Get treatments with high effectiveness"""
        return [log for log in self.logs if log.effectiveness_rating >= min_rating]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get inventory statistics"""
        if not self.logs:
            return {"total_records": 0, "average_effectiveness": 0}
        
        df = pd.DataFrame([log.to_dict() for log in self.logs])
        
        return {
            "total_records": len(self.logs),
            "average_effectiveness": float(df["effectiveness_rating"].mean()),
            "average_cost": float(df["cost"].mean()),
            "by_type": df["treatment_type"].value_counts().to_dict(),
            "organic_percentage": float(
                (df["treatment_type"] == "organic").sum() / len(df) * 100
            ),
        }


class DetectionHistory:
    """
    Manage persistent detection history.
    
    Stores to JSON for analysis and training data.
    """
    
    def __init__(self, history_file: str = "detection_history.json"):
        self.history_file = Path(history_file)
        self.detections: List[DetectionRecord] = []
        self.load_from_file()
    
    def load_from_file(self):
        """Load detection history from JSON file"""
        if self.history_file.exists():
            try:
                with open(self.history_file, "r") as f:
                    data = json.load(f)
                
                self.detections = [
                    DetectionRecord(**det_dict) for det_dict in data
                ]
                logger.info(f"✅ Loaded {len(self.detections)} detection records")
            except Exception as e:
                logger.warning(f"⚠️  Could not load detection history: {e}")
                self.detections = []
    
    def save_to_file(self):
        """Save detection history to JSON file"""
        try:
            data = [det.to_dict() for det in self.detections]
            with open(self.history_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"✅ Saved {len(self.detections)} detection records")
        except Exception as e:
            logger.error(f"❌ Failed to save detection history: {e}")
    
    def add_detection(self, detection: DetectionRecord):
        """Add a detection to history"""
        self.detections.append(detection)
        self.save_to_file()
    
    def get_detections_by_field(self, field_id: str) -> List[DetectionRecord]:
        """Get detections for a specific field"""
        return [d for d in self.detections if d.field_id == field_id]
    
    def get_detections_by_disease(self, disease: str) -> List[DetectionRecord]:
        """Get detections for a specific disease"""
        return [d for d in self.detections if d.disease_name.lower() == disease.lower()]


class RecommendationPersistence:
    """
    Persist AI recommendations for audit trail and analysis.
    """
    
    def __init__(self, recommendations_file: str = "recommendations_log.jsonl"):
        self.recommendations_file = Path(recommendations_file)
    
    def save_recommendation(self, recommendation: AIRecommendation):
        """Append recommendation to log (JSONL format)"""
        try:
            with open(self.recommendations_file, "a") as f:
                f.write(recommendation.to_json() + "\n")
            logger.debug(f"✅ Saved recommendation to audit log")
        except Exception as e:
            logger.error(f"❌ Failed to save recommendation: {e}")
    
    def load_recommendations(self, limit: Optional[int] = None) -> List[AIRecommendation]:
        """Load recommendations from log"""
        if not self.recommendations_file.exists():
            return []
        
        recommendations = []
        with open(self.recommendations_file, "r") as f:
            for i, line in enumerate(f):
                if limit and i >= limit:
                    break
                try:
                    data = json.loads(line)
                    rec = AIRecommendation(**data)
                    recommendations.append(rec)
                except Exception as e:
                    logger.warning(f"⚠️  Could not parse recommendation: {e}")
        
        return recommendations


class IntegratedRecommendationSystem:
    """
    Complete integrated system combining:
    - ML recommendation engine
    - Inventory management
    - Detection history
    - Persistence
    
    This is the main interface for the application.
    """
    
    def __init__(
        self,
        model_dir: Optional[str] = "./models/ml_recommendations",
        inventory_file: str = "inventory_logs.json",
        history_file: str = "detection_history.json",
        recommendations_file: str = "recommendations_log.jsonl",
    ):
        self.rec_system = RecommendationSystem(model_dir=model_dir)
        self.inventory = InventoryManager(inventory_file)
        self.history = DetectionHistory(history_file)
        self.persistence = RecommendationPersistence(recommendations_file)
        
        # Sync inventory to recommendation system
        self.rec_system.inventory_logs = self.inventory.logs
    
    def detect_and_recommend(
        self,
        field_id: str,
        disease: str,
        confidence: float,
        severity: str = "medium",
        location: Optional[Dict] = None,
        sustainability_weight: float = 0.7,
    ) -> AIRecommendation:
        """
        Complete workflow: detection → recommendation → persist
        
        Args:
            field_id: Field identifier
            disease: Detected disease name
            confidence: Detection confidence (0-1)
            severity: "low", "medium", "high"
            location: Optional GPS coordinates
            sustainability_weight: Preference for eco-friendly methods
        
        Returns:
            AIRecommendation with full details
        """
        # Log detection
        detection = self.rec_system.log_detection(
            field_id, disease, confidence, severity, location
        )
        self.history.add_detection(detection)
        
        # Generate recommendation
        recommendation = self.rec_system.generate_ai_recommendations(detection, sustainability_weight)
        
        # Persist recommendation
        self.persistence.save_recommendation(recommendation)
        
        return recommendation
    
    def add_treatment_log(
        self,
        treatment_name: str,
        treatment_type: str,  # "organic", "chemical", "mechanical", "biological"
        cost: float,
        effectiveness_rating: float,  # 1-5
        field_id: str,
        disease_treated: str,
        application_date: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> InventoryLog:
        """
        Log a treatment action.
        
        Args:
            treatment_name: Name of treatment applied
            treatment_type: Category of treatment
            cost: Cost in currency units
            effectiveness_rating: Rating 1-5 of effectiveness
            field_id: Field where applied
            disease_treated: Disease it targeted
            application_date: When applied (default: now)
            notes: Additional notes
        
        Returns:
            InventoryLog record
        """
        treatment_data = {
            "treatment_name": treatment_name,
            "treatment_type": treatment_type,
            "cost": cost,
            "application_date": application_date or datetime.utcnow().isoformat(),
            "effectiveness_rating": effectiveness_rating,
            "field_id": field_id,
            "disease_treated": disease_treated,
            "notes": notes,
        }
        
        log = self.inventory.add_log(treatment_data)
        
        # Sync inventory to recommendation system
        self.rec_system.inventory_logs = self.inventory.logs
        
        return log
    
    def retrain_models(self) -> Dict[str, Any]:
        """
        Retrain ML models using all accumulated data.
        
        Returns:
            Training metrics
        """
        logger.info("🤖 Retraining models with accumulated data...")
        
        metrics = self.rec_system.train_models(
            self.history.detections,
            self.inventory.logs
        )
        
        return metrics
    
    def get_system_info(self) -> Dict[str, Any]:
        """Get comprehensive system information"""
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "recommendation_system": self.rec_system.get_system_status(),
            "inventory_stats": self.inventory.get_statistics(),
            "detection_history": len(self.history.detections),
            "recommendations_logged": len(self.persistence.load_recommendations()),
            "files": {
                "inventory": str(self.inventory.inventory_file),
                "detection_history": str(self.history.history_file),
                "recommendations_log": str(self.persistence.recommendations_file),
            },
        }


# ═══════════════════════════════════════════════════════════════════════════════
# EXAMPLE USAGE
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("🥥 Integrated Recommendation System - Demo")
    print("=" * 70 + "\n")
    
    # Initialize integrated system
    system = IntegratedRecommendationSystem()
    
    # Detect and recommend
    print("🎯 Detecting disease and generating recommendation...\n")
    rec = system.detect_and_recommend(
        field_id="field_1",
        disease="Cercospora",
        confidence=0.92,
        severity="high",
        location={"lat": 8.5241, "lng": 80.9262},  # Vellore, India
    )
    
    print(f"Recommendation:\n{rec.to_json()}\n")
    
    # Add treatment log
    print("📦 Logging treatment action...\n")
    system.add_treatment_log(
        treatment_name="Sulphur dust",
        treatment_type="organic",
        cost=45.00,
        effectiveness_rating=4.5,
        field_id="field_1",
        disease_treated="Cercospora",
        notes="Applied as preventive spray",
    )
    
    # Get system info
    print("📊 System Information:")
    print(json.dumps(system.get_system_info(), indent=2))
    
    print("\n" + "=" * 70)
    print("✅ Demo Complete")
    print("=" * 70)
