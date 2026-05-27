# 🏗️ System Architecture Diagram

## Before: Legacy Rule-Based System
```
Detection
   ↓
Hardcoded IF-THEN Rules
   ↓
Single Static Recommendation
   ↓
Never Improves
   ✗ Same advice for all farms
   ✗ Doesn't learn from outcomes
   ✗ Can't adapt to new diseases
```

## After: ML-Driven System
```
┌─────────────────────────────────────────────────────────────────┐
│                    DISEASE DETECTION                            │
│  (from model.py - OpenVINO)                                     │
│  Input: disease, confidence (0-1), severity, field_id, location │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│              DETECTION & INVENTORY LOGS                         │
│  (Unified Data Pipeline)                                        │
│  - DetectionRecord: disease_name, confidence, severity          │
│  - InventoryLog: treatment_name, type, cost, effectiveness      │
└────────────────────┬────────────────────────────────────────────┘
                     │
                     ↓
┌─────────────────────────────────────────────────────────────────┐
│              FEATURE ENGINEERING                                │
│  FeatureEngineer processes raw data:                            │
│  - Encode disease names                                         │
│  - Compute historical effectiveness metrics                     │
│  - Calculate treatment type distribution                        │
│                                                                 │
│  Output: Feature Matrix X (samples × 11 features)              │
└────────────────────┬────────────────────────────────────────────┘
                     │
         ┌───────────┼───────────┐
         │           │           │
         ↓           ↓           ↓
      ┌──────┐  ┌──────┐  ┌──────┐
      │ ≥ 5  │  │      │  │      │
      │data? │  │ NO   │  │ YES  │
      └──┬───┘  │      │  │      │
         │      └──┬───┘  └──┬───┘
         │         │        │
         │         ↓        ↓
         │    ┌──────────────────────────────┐
         │    │   ML Models Not Available    │
         │    │   Use Cold-Start Baselines   │
         │    └──────────────────────────────┘
         │
         ↓
    ┌─────────────────────────────────────────────────────┐
    │         ML RECOMMENDATION ENGINE                     │
    │  (scikit-learn Multi-Output Classification)         │
    │                                                      │
    │  3 Independent Classifiers:                         │
    │  ┌─────────────────────────────────────────────┐   │
    │  │ Treatment Classifier                        │   │
    │  │ → Predicts best remedy from historical data │   │
    │  └─────────────────────────────────────────────┘   │
    │                                                      │
    │  ┌─────────────────────────────────────────────┐   │
    │  │ Fertilizer Classifier                       │   │
    │  │ → Predicts optimal fertilizer strategy      │   │
    │  └─────────────────────────────────────────────┘   │
    │                                                      │
    │  ┌─────────────────────────────────────────────┐   │
    │  │ Preventive Measures Classifier              │   │
    │  │ → Predicts recommended practices            │   │
    │  └─────────────────────────────────────────────┘   │
    │                                                      │
    │  Model Type: RandomForest or GradientBoosting      │
    │  Training: Supervised learning on historical data  │
    │  Evaluation: Accuracy scoring on validation set    │
    │                                                      │
    └────────────┬────────────────────────────────────────┘
                 │
                 ↓
    ┌──────────────────────────────────────────┐
    │  SUSTAINABILITY CONSTRAINTS              │
    │  Score treatments 0-1 (eco-friendly)     │
    │                                          │
    │  Base Scores:                            │
    │  - Organic       → 0.95 ✅               │
    │  - Biological    → 0.90 ✅               │
    │  - Mechanical    → 0.85 ✅               │
    │  - Chemical      → 0.40 ⚠️                │
    │                                          │
    │  Result: Sustainability Score (0-1)     │
    └────────────┬─────────────────────────────┘
                 │
                 ↓
    ┌──────────────────────────────────────────────────┐
    │  AI RECOMMENDATION (JSON)                        │
    │  ├─ Disease: "Cercospora"                       │
    │  ├─ Confidence: 0.92                            │
    │  │                                              │
    │  ├─ Treatment: "Sulphur dust"                   │
    │  ├─ Treatment Type: "organic"                   │
    │  ├─ Treatment Confidence: 0.85                  │
    │  │                                              │
    │  ├─ Fertilizer: "Phosphorus-rich"              │
    │  ├─ Fertilizer Type: "phosphorus-rich"         │
    │  ├─ Fertilizer Confidence: 0.80                │
    │  │                                              │
    │  ├─ Preventive Measures: [                      │
    │  │   "Improve air circulation",                │
    │  │   "Reduce leaf wetness",                    │
    │  │   "Rotate fungicides"                       │
    │  │ ]                                            │
    │  ├─ Preventive Confidence: 0.82                │
    │  │                                              │
    │  ├─ Sustainability Score: 0.95 (Highly eco)    │
    │  ├─ Organic Alternative: null                  │
    │  │                                              │
    │  └─ Is Cold-Start: false (ML-driven)           │
    └────────────┬─────────────────────────────────────┘
                 │
                 ↓
    ┌──────────────────────────────────────────┐
    │  RESPONSE TO USER                        │
    │  - Structured JSON/Dict                  │
    │  - Confidence scores                     │
    │  - Sustainability rating                 │
    │  - Actionable recommendations            │
    │  - Ready for UI/mobile display           │
    └────────────┬─────────────────────────────┘
                 │
                 ↓
    ┌──────────────────────────────────────────┐
    │  PERSISTENCE & MONITORING                │
    │  - Log recommendation (JSONL)            │
    │  - Track accuracy                        │
    │  - Accumulate training data              │
    │  - Trigger periodic retraining           │
    └──────────────────────────────────────────┘
```

## Cold-Start Fallback Mechanism
```
New Farm (No Historical Data)
         ↓
    Check Data Volume
         ↓
    < 5 records?
    ├─ YES → Use ColdStartBaselines
    │        ├─ 9 agronomic recommendations
    │        ├─ By disease type
    │        ├─ Sustainability-optimized
    │        └─ Based on agricultural best practices
    │
    └─ NO  → Train ML Models
            ├─ RandomForest/GradientBoosting
            ├─ Cross-validate
            ├─ Save models
            └─ Use ML predictions going forward
```

## Integration with Existing System
```
┌──────────────────────────────────────┐
│  USER UPLOADS LEAF IMAGE             │
└────────────┬─────────────────────────┘
             │
             ↓
┌──────────────────────────────────────┐
│  main_hybrid.py                      │
│  (FastAPI Application)               │
└────────────┬─────────────────────────┘
             │
             ↓
┌──────────────────────────────────────┐
│  model.py                            │
│  OpenVINO Inference Engine           │
│  → Returns: disease, confidence      │
└────────────┬─────────────────────────┘
             │
             ├─ Save to hybrid_storage/
             │  (offline-first local DB)
             │
             ↓
┌──────────────────────────────────────┐
│  [NEW] RECOMMENDATION SYSTEM         │
│                                      │
│  detect_and_recommend()              │
│  ├─ Logs detection                   │
│  ├─ Loads inventory history          │
│  ├─ Generates AI recommendation      │
│  └─ Returns: Treatment + Fertilizer  │
│            + Preventive Measures     │
│            + Sustainability Score    │
└────────────┬─────────────────────────┘
             │
             ├─ Save to local JSON files
             │  (inventory_logs.json)
             │  (detection_history.json)
             │  (recommendations_log.jsonl)
             │
             ├─ Train/retrain models
             │  (when enough data)
             │
             ↓
┌──────────────────────────────────────┐
│  RESPONSE TO USER                    │
│                                      │
│  {                                   │
│    "detections": [...],              │
│    "ai_recommendation": {            │
│      "treatment": "...",             │
│      "fertilizer": "...",            │
│      "preventive_measures": [...],   │
│      "sustainability_score": 0.95    │
│    }                                 │
│  }                                   │
└──────────────────────────────────────┘
```

## Data Flow for Model Training
```
Historical Detections
├─ disease_name: "Cercospora"
├─ confidence: 0.92
├─ severity: "high"
├─ field_id: "field_1"
└─ detection_date: "2026-05-18"
        ↓
        │
        ├────────────────────────┐
        │                        │
Historical Inventory Logs      Feature
├─ treatment_name: "Sulph"    Engineering
├─ treatment_type: "organic"       ↓
├─ effectiveness: 4.5         [13 Features]
├─ cost: 45.0                      │
└─ application_date: "2026-05"     │
        │                         │
        └────────────────────────┘
                    ↓
        ┌──────────────────────┐
        │  Feature Matrix X    │
        │  Shape: (N, 13)      │
        │                      │
        │ N = samples          │
        │ 13 = features        │
        └──────────────────────┘
                    ↓
        ┌──────────────────────┐
        │  Target Labels y     │
        │  3 Independent y     │
        │                      │
        │ y1: Treatment names  │
        │ y2: Fertilizer type  │
        │ y3: Preventive list  │
        └──────────────────────┘
                    ↓
        ┌──────────────────────────────┐
        │  Train-Test Split (80/20)    │
        └───────────┬──────────────────┘
                    │
        ┌───────────┴──────────────┐
        │                          │
        ↓                          ↓
    X_train, y1_train         X_test, y1_test
    X_train, y2_train         X_test, y2_test
    X_train, y3_train         X_test, y3_test
        │                          │
        ↓                          ↓
    ┌─────────────────┐       ┌─────────────────┐
    │ Train Classifiers│       │ Evaluate Models │
    │                 │       │ (Accuracy Score)│
    │ RandomForest ✓  │       │                 │
    │ GradientBoost ✓ │       │ Cross-Validate  │
    └────────┬────────┘       └────────┬────────┘
             │                         │
             └───────────┬─────────────┘
                        ↓
            ┌──────────────────────────┐
            │  Save Models (pickle)    │
            │ + Feature Encoder        │
            │ + Label Encoders         │
            │ + Scaler                 │
            │                          │
            │  → models/ml_recommend   │
            └──────────────────────────┘
```

## Decision Tree: Cold-Start vs ML-Driven
```
Detection Received
        │
        ↓
Are models already trained?
        │
    ┌───┴────┐
   NO        YES
    │         │
    ↓         ↓
  Do we   Sufficient data
  have    (≥5 detections
  data?   AND ≥5 treatments)?
    │         │
    ├─ NO ────┴─ NO
    │            ↓
    │        ┌──────────────────────┐
    │        │ Use ColdStartBaselines│
    │        │ (is_cold_start=true) │
    │        └──────────────────────┘
    │
    └─ YES
        │
        ↓
    ┌──────────────────────────────┐
    │ Try ML Prediction            │
    │                              │
    │ Success?                     │
    ├─ YES → Return ML prediction  │
    │        (is_cold_start=false) │
    │                              │
    └─ NO  → Fallback to Baselines │
             (is_cold_start=true)  │
             
    Result: Always returns
            recommendation
            (never fails)
```

---

**This architecture ensures:**
- ✅ Seamless transition from fallback to ML-driven
- ✅ No single point of failure
- ✅ Continuous improvement with more data
- ✅ Sustainable farming practices prioritized
- ✅ Production-ready error handling
