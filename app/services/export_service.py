"""
app/services/export_service.py — CSV and PDF report generation.

Extracted from the large export/report block in main.py (~600 lines).
No route handling; returns raw bytes or string content.
"""
import logging
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Optional

from app.domain.detection import normalize_verification_status

logger = logging.getLogger(__name__)


# ── CSV export ─────────────────────────────────────────────────────────────────

def generate_detections_csv() -> str:
    """Generate a CSV string from detection_history.json (legacy export)."""
    detection_file = Path("detection_history.json")
    if not detection_file.exists():
        return "Disease Name,Confidence,Severity,Field ID,Detection Date,Location,Email,Source\n"

    import json
    with open(detection_file, "r") as f:
        all_detections = json.load(f)

    if not all_detections:
        return "Disease Name,Confidence,Severity,Field ID,Detection Date,Location,Email,Source\n"

    disease_stats: dict = defaultdict(lambda: {"count": 0, "high": 0, "medium": 0, "low": 0, "total_confidence": 0})
    for detection in all_detections:
        disease = detection.get("disease_name", "Unknown")
        confidence = float(detection.get("confidence", 0))
        severity = detection.get("severity", "low")
        disease_stats[disease]["count"] += 1
        disease_stats[disease]["total_confidence"] += confidence
        disease_stats[disease][severity if severity in ("high", "medium", "low") else "low"] += 1

    for disease in disease_stats:
        count = disease_stats[disease]["count"]
        if count > 0:
            disease_stats[disease]["avg_confidence"] = round(
                disease_stats[disease]["total_confidence"] / count, 4
            )

    buf = StringIO()
    buf.write("Disease Name,Confidence,Severity,Field ID,Detection Date,Location,Email,Source\n")
    for detection in all_detections:
        disease = detection.get("disease_name", "Unknown")
        confidence = float(detection.get("confidence", 0))
        severity = detection.get("severity", "low")
        location_str = ""
        if isinstance(detection.get("location"), dict):
            location_str = f"{detection['location'].get('lat', '')},{detection['location'].get('lng', '')}"
        buf.write(
            f"{disease},{confidence:.4f},{severity},"
            f"{detection.get('field_id', '')},{detection.get('detection_date', '')},"
            f"{location_str},{detection.get('email', '')},detection\n"
        )

    buf.write("\n=== INVENTORY SUMMARY ===\n\nDisease,Total Count,Avg Confidence,High,Medium,Low\n")
    total_detections = total_high = total_medium = total_low = total_conf = 0
    for disease in sorted(disease_stats, key=lambda x: disease_stats[x]["count"], reverse=True):
        stats = disease_stats[disease]
        buf.write(f"{disease},{stats['count']},{stats.get('avg_confidence', 0)},{stats['high']},{stats['medium']},{stats['low']}\n")
        total_detections += stats["count"]
        total_high += stats["high"]
        total_medium += stats["medium"]
        total_low += stats["low"]
        total_conf += stats["total_confidence"]

    avg = round(total_conf / total_detections, 4) if total_detections > 0 else 0
    buf.write(f"\nTOTAL,{total_detections},{avg},{total_high},{total_medium},{total_low}\n")
    return buf.getvalue()


# ── PDF report ─────────────────────────────────────────────────────────────────

def build_executive_user_records_pdf(records: list[dict], email: str) -> bytes:
    """Render the authenticated user's records as an executive-grade PDF dashboard.

    Enhanced version with real analytics:
    - Source breakdown (upload vs drone)
    - Confidence distribution histogram
    - Verified vs pending donut chart
    - Diseased vs healthy stacked monthly chart
    - Day-of-week activity chart
    - Expert recommendations section
    - Top-5 confidence detections table
    - Enhanced UI with section dividers, color-coded tables, cover page
    """
    # All PDF-specific imports are kept local to avoid penalising startup time.
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415
    import matplotlib.patches as mpatches  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    from io import BytesIO as _BytesIO  # noqa: PLC0415
    import json as _json  # noqa: PLC0415

    from reportlab.lib import colors  # noqa: PLC0415
    from reportlab.lib.pagesizes import A4  # noqa: PLC0415
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # noqa: PLC0415
    from reportlab.lib.units import mm  # noqa: PLC0415
    from reportlab.platypus import (  # noqa: PLC0415
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, Flowable, Image, KeepTogether, HRFlowable,
    )
    from reportlab.pdfgen.canvas import Canvas  # noqa: PLC0415

    # ── palette ────────────────────────────────────────────────────────────────
    BRAND_DARK    = "#164D37"
    BRAND_MID     = "#41966E"
    BRAND_LIGHT   = "#D1FAE5"
    SLATE_900     = "#0F172A"
    SLATE_700     = "#334155"
    SLATE_400     = "#94A3B8"
    SLATE_100     = "#F1F5F9"
    SLATE_50      = "#F8FAFC"
    RED_BG        = "#FEF2F2"
    ORANGE_BG     = "#FFF7ED"
    YELLOW_BG     = "#FEFCE8"
    GREEN_BG      = "#F0FDF4"
    PAGE_W, PAGE_H = A4

    # ── helpers ──────────────────────────────────────────────────────────────

    def _title_case(value: Any) -> str:
        return str(value or "-").replace("_", " ").strip().title()

    def _safe(value: Any) -> str:
        return str(value or "-").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    detection_colors = {
        "healthy":              "#22C55E",
        "caterpillars":         "#F97316",
        "cercospora":           "#EC4899",
        "drying of leaflets":   "#2563EB",
        "leaf rot":             "#6366F1",
        "pestalotiopsis":       "#06B6D4",
        "bud root":             "#D97706",
        "bud rot":              "#D97706",
    }

    def _detection_color(disease: str) -> str:
        return detection_colors.get(str(disease or "").replace("_", " ").strip().lower(), "#64748B")

    def _severity_for_disease(disease: str) -> str:
        nd = disease.lower()
        if any(n in nd for n in ("lethal", "bud rot", "bud root")):
            return "Critical"
        if any(n in nd for n in ("blight", "pestalotiopsis")):
            return "Severe"
        if nd != "healthy":
            return "Moderate"
        return "Mild"

    def _severity_row_color(severity: str) -> colors.HexColor:
        return {
            "Critical": colors.HexColor(RED_BG),
            "Severe":   colors.HexColor(ORANGE_BG),
            "Moderate": colors.HexColor(YELLOW_BG),
            "Mild":     colors.HexColor(GREEN_BG),
        }.get(severity, colors.HexColor(SLATE_50))

    def _mapped_area(record: dict) -> Optional[str]:
        map_bounds = {
            "min_lat": 7.35140, "max_lat": 7.35295,
            "min_lng": 125.64055, "max_lng": 125.64215,
        }
        lat_step = (map_bounds["max_lat"] - map_bounds["min_lat"]) / 2
        lng_step = (map_bounds["max_lng"] - map_bounds["min_lng"]) / 2
        gps_data = record.get("gps_data") or record.get("gps") or {}
        if isinstance(gps_data, str):
            try:
                gps_data = _json.loads(gps_data)
            except Exception:
                gps_data = {}
        if not isinstance(gps_data, dict):
            return None
        lat = gps_data.get("latitude") or record.get("lat")
        lng = gps_data.get("longitude") or record.get("lng")
        if lat is None or lng is None:
            return None
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            return None
        if not (map_bounds["min_lat"] <= lat <= map_bounds["max_lat"]):
            return None
        if not (map_bounds["min_lng"] <= lng <= map_bounds["max_lng"]):
            return None
        row = 0 if lat >= map_bounds["min_lat"] + lat_step else 1
        col = 0 if lng < map_bounds["min_lng"] + lng_step else 1
        return f"Area {row * 2 + col + 1}"

    def _has_gps(record: dict) -> bool:
        gps_data = record.get("gps_data") or record.get("gps") or {}
        if isinstance(gps_data, str):
            try:
                gps_data = _json.loads(gps_data)
            except Exception:
                return False
        if isinstance(gps_data, dict):
            lat = gps_data.get("latitude") or record.get("lat")
            lng = gps_data.get("longitude") or record.get("lng")
            if lat is not None and lng is not None:
                try:
                    float(lat); float(lng)
                    return True
                except (TypeError, ValueError):
                    pass
        return bool(record.get("lat") and record.get("lng"))

    # ── expert recommendations ────────────────────────────────────────────────
    def _load_expert_recommendations() -> dict:
        recs_path = Path("expert_recommendations.json")
        if not recs_path.exists():
            return {}
        try:
            with open(recs_path, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            return {}

    expert_recs = _load_expert_recommendations()

    def _get_expert_rec(disease: str) -> Optional[dict]:
        key = str(disease or "").strip().lower().replace(" ", "_")
        return expert_recs.get(key)

    # ── data aggregation ─────────────────────────────────────────────────────
    disease_stats: dict = defaultdict(lambda: {"count": 0, "confidence": []})
    monthly_counts: dict = defaultdict(int)
    monthly_disease_counts: dict = defaultdict(lambda: defaultdict(int))
    monthly_healthy_counts: dict = defaultdict(int)
    monthly_diseased_counts: dict = defaultdict(int)
    severity_counts: dict = defaultdict(int)
    area_stats = {f"Area {i}": {"samples": 0, "severity": defaultdict(int)} for i in range(1, 5)}
    verified_records = 0
    pending_records = 0
    upload_records = 0
    drone_records = 0
    other_source_records = 0
    gps_records = 0
    no_gps_records = 0
    top_confidences: list = []
    weekday_counts: dict = defaultdict(int)  # 0=Mon … 6=Sun
    confidence_bands: dict = {f"{i*10}-{i*10+10}%": 0 for i in range(10)}  # 0-10%, 10-20% …
    top_detection_records: list = []  # individual detections with metadata

    for record in records:
        # Verification
        status = normalize_verification_status(record)
        if status == "verified":
            verified_records += 1
        else:
            pending_records += 1

        # Source
        source = str(record.get("source") or "upload").lower()
        if "drone" in source:
            drone_records += 1
        elif "upload" in source:
            upload_records += 1
        else:
            other_source_records += 1

        # GPS
        if _has_gps(record):
            gps_records += 1
        else:
            no_gps_records += 1

        # Timestamp
        ts = str(record.get("timestamp") or "")
        month_key = ts[:7] if len(ts) >= 7 else None
        if month_key:
            monthly_counts[month_key] += 1
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                weekday_counts[dt.weekday()] += 1
            except Exception:
                pass

        area = _mapped_area(record)
        has_disease_in_record = False

        for detection in record.get("detections") or []:
            disease = str(detection.get("class") or "Unknown")
            conf = float(detection.get("confidence") or 0)
            conf_pct = conf * 100

            disease_stats[disease]["count"] += 1
            disease_stats[disease]["confidence"].append(conf)
            severity = _severity_for_disease(disease)
            severity_counts[severity] += 1
            top_confidences.append(conf)

            # Confidence histogram
            band_idx = min(int(conf_pct / 10), 9)
            band_key = f"{band_idx*10}-{band_idx*10+10}%"
            confidence_bands[band_key] += 1

            # Top detection records (for top-5 table)
            top_detection_records.append({
                "disease":    disease,
                "confidence": conf,
                "timestamp":  ts,
                "source":     source,
                "record_id":  str(record.get("id") or ""),
            })

            if month_key:
                monthly_disease_counts[month_key][disease] += 1
                if disease.lower() != "healthy":
                    has_disease_in_record = True
                    monthly_diseased_counts[month_key] += 1
                else:
                    monthly_healthy_counts[month_key] += 1

            if area:
                area_stats[area]["samples"] += 1
                area_stats[area]["severity"][severity] += 1

    # Sort top detections by confidence descending
    top_detection_records.sort(key=lambda x: x["confidence"], reverse=True)
    top5_detections = top_detection_records[:5]

    total_records = len(records)
    total_detections = sum(d["count"] for d in disease_stats.values())
    healthy_count = disease_stats.get("Healthy", {}).get("count", 0) or disease_stats.get("healthy", {}).get("count", 0)
    disease_incidence = (
        (total_detections - healthy_count) / total_detections * 100
        if total_detections > 0 else 0
    )
    primary_disease = max(disease_stats, key=lambda k: disease_stats[k]["count"], default="None detected")
    avg_conf = (sum(top_confidences) / len(top_confidences) * 100) if top_confidences else 0

    # Month-over-month delta for KPI
    sorted_months = sorted(monthly_counts.keys())
    if len(sorted_months) >= 2:
        prev_month_count = monthly_counts[sorted_months[-2]]
        curr_month_count = monthly_counts[sorted_months[-1]]
        delta_pct = ((curr_month_count - prev_month_count) / prev_month_count * 100) if prev_month_count > 0 else 0
        delta_str = f"{'▲' if delta_pct >= 0 else '▼'} {abs(delta_pct):.0f}% vs prev month"
    else:
        delta_str = "First reporting period"

    gps_pct = (gps_records / total_records * 100) if total_records > 0 else 0

    # ── canvas class for page numbers ────────────────────────────────────────
    class _NumberedCanvas(Canvas):
        def __init__(self, *args, **kwargs):
            Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states: list = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.setStrokeColor(colors.HexColor("#E2E8F0"))
                self.line(16 * mm, 14 * mm, PAGE_W - 16 * mm, 14 * mm)
                self.setFillColor(colors.HexColor(SLATE_400))
                self.setFont("Helvetica", 7.5)
                self.drawString(16 * mm, 9 * mm, "Confidential · Coconut Leaf Disease Analytics · PCA-Aligned Guidance")
                self.drawRightString(
                    PAGE_W - 16 * mm, 9 * mm,
                    f"Generated {datetime.now(timezone.utc).astimezone().strftime('%d %b %Y %H:%M')} | Page {self._pageNumber} of {total_pages}"
                )
                Canvas.showPage(self)
            Canvas.save(self)

    # ── metric card flowable ──────────────────────────────────────────────────
    class _MetricCard(Flowable):
        def __init__(self, value: str, label: str, note: str, width: float,
                     height: float = 38 * mm, note_color: str = BRAND_MID,
                     accent: str = BRAND_MID):
            Flowable.__init__(self)
            self.value, self.label, self.note = value, label, note
            self.width, self.height = width, height
            self.note_color = note_color
            self.accent = accent

        def wrap(self, aw, ah):
            return self.width, self.height

        def draw(self):
            c = self.canv
            # Card background + border
            c.setFillColor(colors.white)
            c.setStrokeColor(colors.HexColor("#DDE5E0"))
            c.roundRect(0, 0, self.width, self.height, 3 * mm, fill=1, stroke=1)
            # Accent bar
            c.setFillColor(colors.HexColor(self.accent))
            c.roundRect(0, 0, 2.8 * mm, self.height, 2 * mm, fill=1, stroke=0)
            # Label
            c.setFillColor(colors.HexColor(SLATE_400))
            c.setFont("Helvetica", 7)
            c.drawString(6 * mm, self.height - 10 * mm, self.label.upper())
            # Value
            c.setFillColor(colors.HexColor(SLATE_900))
            font_size = 16 if len(self.value) > 12 else 19
            c.setFont("Helvetica-Bold", font_size)
            c.drawString(6 * mm, self.height - 19 * mm, self.value[:22])
            # Note
            c.setFillColor(colors.HexColor(self.note_color))
            c.setFont("Helvetica-Bold", 7)
            c.drawString(6 * mm, 5 * mm, self.note[:36])

    # ── status badge flowable ─────────────────────────────────────────────────
    class _StatusBadge(Flowable):
        def __init__(self, status_str: str):
            Flowable.__init__(self)
            self.status = status_str
            self.width, self.height = 30 * mm, 8 * mm

        def wrap(self, aw, ah):
            return self.width, self.height

        def draw(self):
            verified = self.status.lower() == "verified"
            self.canv.setFillColor(colors.HexColor("#D1FAE5" if verified else "#FEF3C7"))
            self.canv.roundRect(0, 0, self.width, self.height, 3 * mm, fill=1, stroke=0)
            self.canv.setFillColor(colors.HexColor("#059669" if verified else "#D97706"))
            self.canv.setFont("Helvetica-Bold", 6.7)
            self.canv.drawCentredString(self.width / 2, 2.8 * mm, self.status)

    # ── section divider flowable ──────────────────────────────────────────────
    class _SectionDivider(Flowable):
        def __init__(self, label: str, width: float, accent: str = BRAND_DARK):
            Flowable.__init__(self)
            self.label = label
            self.width = width
            self.accent = accent
            self.height = 10 * mm

        def wrap(self, aw, ah):
            return self.width, self.height

        def draw(self):
            c = self.canv
            # Filled accent bar
            c.setFillColor(colors.HexColor(self.accent))
            c.roundRect(0, 2 * mm, self.width, 7 * mm, 1.5 * mm, fill=1, stroke=0)
            # Label text
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(4 * mm, 4.5 * mm, self.label.upper())

    # ── recommendation card flowable ──────────────────────────────────────────
    class _RecommendationCard(Flowable):
        def __init__(self, disease: str, rec: dict, width: float, accent: str = "#164D37"):
            Flowable.__init__(self)
            self.disease = disease
            self.rec = rec
            self.width = width
            self.accent = accent
            self.height = 52 * mm

        def wrap(self, aw, ah):
            return self.width, self.height

        def draw(self):
            c = self.canv
            h = self.height
            w = self.width
            # Card border
            c.setFillColor(colors.HexColor("#F0FDF4"))
            c.setStrokeColor(colors.HexColor("#A7F3D0"))
            c.roundRect(0, 0, w, h, 2.5 * mm, fill=1, stroke=1)
            # Accent stripe
            c.setFillColor(colors.HexColor(self.accent))
            c.roundRect(0, 0, 3 * mm, h, 1.5 * mm, fill=1, stroke=0)
            # Disease title
            c.setFillColor(colors.HexColor(SLATE_900))
            c.setFont("Helvetica-Bold", 10)
            c.drawString(6 * mm, h - 9 * mm, _title_case(self.disease))
            # Severity badge inline
            sev = _severity_for_disease(self.disease)
            sev_clr = {"Critical": "#EF4444", "Severe": "#F97316", "Moderate": "#EAB308", "Mild": "#22C55E"}.get(sev, "#94A3B8")
            c.setFillColor(colors.HexColor(sev_clr))
            c.setFont("Helvetica-Bold", 7)
            badge_x = 6 * mm + c.stringWidth(_title_case(self.disease), "Helvetica-Bold", 10) + 3 * mm
            c.roundRect(badge_x, h - 10 * mm, 18 * mm, 5.5 * mm, 1 * mm, fill=1, stroke=0)
            c.setFillColor(colors.white)
            c.drawString(badge_x + 2 * mm, h - 8 * mm, sev)
            # Fertilizer
            c.setFillColor(colors.HexColor(SLATE_700))
            c.setFont("Helvetica-Bold", 7.5)
            c.drawString(6 * mm, h - 16 * mm, "Fertilizer:")
            c.setFont("Helvetica", 7)
            fert_text = str(self.rec.get("fertilizer") or "—")[:110]
            c.drawString(26 * mm, h - 16 * mm, fert_text)
            # Treatment
            c.setFont("Helvetica-Bold", 7.5)
            c.drawString(6 * mm, h - 22 * mm, "Treatment:")
            c.setFont("Helvetica", 7)
            treat_text = str(self.rec.get("treatment") or "—")[:110]
            c.drawString(26 * mm, h - 22 * mm, treat_text)
            # Prevention
            c.setFont("Helvetica-Bold", 7.5)
            c.drawString(6 * mm, h - 28 * mm, "Prevention:")
            c.setFont("Helvetica", 6.8)
            prevention = self.rec.get("prevention") or []
            if isinstance(prevention, list):
                for idx, item in enumerate(prevention[:3]):
                    y = h - (33 + idx * 5.5) * mm
                    c.drawString(8 * mm, y, f"• {str(item)[:110]}")
            # Note / updated
            note = str(self.rec.get("note") or "")[:100]
            if note:
                c.setFillColor(colors.HexColor(SLATE_400))
                c.setFont("Helvetica-Oblique", 6.5)
                c.drawString(6 * mm, 4 * mm, note)

    # ── chart helpers ─────────────────────────────────────────────────────────
    def _chart_image(chart_type: str, width_mm: float = 6.7, height_mm: float = 3.2) -> _BytesIO:
        dpi = 110
        fig, ax = plt.subplots(figsize=(width_mm, height_mm), dpi=dpi)
        fig.patch.set_facecolor("white")
        ax.set_facecolor(SLATE_50)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#E2E8F0")
        ax.spines["bottom"].set_color("#E2E8F0")
        ax.tick_params(colors=SLATE_700, labelsize=7.5)

        if chart_type == "disease":
            names = [n for n in list(disease_stats.keys())[:8]]
            counts = [disease_stats[n]["count"] for n in names]
            bar_colors = [_detection_color(n) for n in names]
            bars = ax.barh(names, counts, color=bar_colors, height=0.55, edgecolor="none")
            for bar, count in zip(bars, counts):
                ax.text(bar.get_width() + 0.15, bar.get_y() + bar.get_height() / 2,
                        str(count), va="center", fontsize=7.5, color=SLATE_700, fontweight="bold")
            ax.set_xlabel("Detections", fontsize=8, color=SLATE_400)
            ax.set_title("Disease Detection Frequency", fontsize=9, color=SLATE_900, pad=8, fontweight="bold")

        elif chart_type == "trend":
            # Mirror the dashboard's multi-series frequency chart: each line is
            # a detection class, so changes in the disease mix are visible at a
            # glance instead of being hidden in one all-records total.
            months = sorted(monthly_disease_counts.keys())[-12:]
            ranked_diseases = sorted(
                disease_stats,
                key=lambda disease: disease_stats[disease]["count"],
                reverse=True,
            )[:4]
            x = np.arange(len(months))

            for disease in ranked_diseases:
                values = [monthly_disease_counts[month].get(disease, 0) for month in months]
                color = _detection_color(disease)
                ax.plot(
                    x, values, label=_title_case(disease), color=color,
                    linewidth=1.8, marker="o", markersize=4.5,
                    markeredgecolor="white", markeredgewidth=0.8, zorder=3,
                )
                ax.fill_between(x, values, color=color, alpha=0.11, zorder=1)

            ax.set_xlim(-0.15, max(len(months) - 1, 0) + 0.15)
            ax.set_ylim(bottom=0)
            ax.set_xticks(x)
            ax.set_xticklabels(months, rotation=0, ha="center", fontsize=7)
            ax.set_ylabel("Detections", fontsize=8, color=SLATE_400)
            ax.set_title("Monthly Detection Frequency", fontsize=10, color=SLATE_900, pad=18, loc="left", fontweight="bold")
            ax.grid(axis="y", color="#E2E8F0", linewidth=0.6)
            ax.grid(axis="x", color="#F1F5F9", linewidth=0.45)
            if ranked_diseases:
                legend = ax.legend(
                    loc="upper center", bbox_to_anchor=(0.5, 1.12), ncol=min(4, len(ranked_diseases)),
                    fontsize=7.2, frameon=False, handlelength=2.4, columnspacing=1.2,
                )
                for handle in legend.legend_handles:
                    handle.set_linewidth(2.4)

        elif chart_type == "severity":
            sev_labels = ["Critical", "Severe", "Moderate", "Mild"]
            sev_vals = [severity_counts.get(s, 0) for s in sev_labels]
            sev_clrs = ["#EF4444", "#F97316", "#EAB308", "#22C55E"]
            bars = ax.bar(sev_labels, sev_vals, color=sev_clrs, edgecolor="none", width=0.5)
            for bar, val in zip(bars, sev_vals):
                if val > 0:
                    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
                            str(val), ha="center", fontsize=8, color=SLATE_700, fontweight="bold")
            ax.set_ylabel("Count", fontsize=8, color=SLATE_400)
            ax.set_title("Detection Severity Breakdown", fontsize=9, color=SLATE_900, pad=8, fontweight="bold")

        elif chart_type == "stacked_monthly":
            months = sorted(set(list(monthly_diseased_counts.keys()) + list(monthly_healthy_counts.keys())))[-10:]
            diseased_vals = [monthly_diseased_counts.get(m, 0) for m in months]
            healthy_vals = [monthly_healthy_counts.get(m, 0) for m in months]
            x = np.arange(len(months))
            width = 0.5
            ax.bar(x, diseased_vals, width, label="Diseased", color="#EF4444", edgecolor="none", alpha=0.85)
            ax.bar(x, healthy_vals, width, bottom=diseased_vals, label="Healthy", color="#22C55E", edgecolor="none", alpha=0.85)
            ax.set_xticks(x)
            ax.set_xticklabels(months, rotation=30, ha="right", fontsize=7)
            ax.set_ylabel("Detections", fontsize=8, color=SLATE_400)
            ax.set_title("Monthly Diseased vs Healthy", fontsize=9, color=SLATE_900, pad=8, fontweight="bold")
            ax.legend(fontsize=7, loc="upper left", framealpha=0.7)

        elif chart_type == "weekday_activity":
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            vals = [weekday_counts.get(i, 0) for i in range(7)]
            max_val = max(vals) if vals else 1
            bar_clrs = [BRAND_MID if v == max_val else "#A7F3D0" for v in vals]
            ax.bar(days, vals, color=bar_clrs, edgecolor="none", width=0.6)
            for i, v in enumerate(vals):
                if v > 0:
                    ax.text(i, v + 0.1, str(v), ha="center", fontsize=7.5, color=SLATE_700, fontweight="bold")
            ax.set_ylabel("Records", fontsize=8, color=SLATE_400)
            ax.set_title("Activity by Day of Week", fontsize=9, color=SLATE_900, pad=8, fontweight="bold")

        elif chart_type == "confidence_hist":
            bands = [f"{i*10}-{i*10+10}%" for i in range(10)]
            vals = [confidence_bands.get(b, 0) for b in bands]
            bar_clrs = ["#22C55E" if i >= 7 else "#EAB308" if i >= 4 else "#EF4444" for i in range(10)]
            ax.barh(bands, vals, color=bar_clrs, edgecolor="none", height=0.6)
            for i, v in enumerate(vals):
                if v > 0:
                    ax.text(v + 0.1, i, str(v), va="center", fontsize=7.5, color=SLATE_700, fontweight="bold")
            ax.set_xlabel("Detections", fontsize=8, color=SLATE_400)
            ax.set_title("Confidence Score Distribution", fontsize=9, color=SLATE_900, pad=8, fontweight="bold")

        elif chart_type == "donut_verification":
            ver_vals = [verified_records, pending_records]
            ver_labels = [f"Verified\n({verified_records})", f"Pending\n({pending_records})"]
            ver_clrs = ["#22C55E", "#F59E0B"]
            wedges, texts = ax.pie(
                ver_vals if sum(ver_vals) > 0 else [1, 0],
                labels=ver_labels,
                colors=ver_clrs,
                startangle=90,
                wedgeprops=dict(width=0.5, edgecolor="white"),
                textprops={"fontsize": 8, "color": SLATE_700},
            )
            ax.set_title("Verification Status", fontsize=9, color=SLATE_900, pad=8, fontweight="bold")

        elif chart_type == "donut_source":
            src_vals = [upload_records, drone_records]
            if other_source_records > 0:
                src_vals.append(other_source_records)
            src_labels_parts = [f"Upload\n({upload_records})", f"Drone\n({drone_records})"]
            if other_source_records > 0:
                src_labels_parts.append(f"Other\n({other_source_records})")
            src_clrs = [BRAND_MID, "#2563EB", "#94A3B8"]
            ax.pie(
                src_vals if sum(src_vals) > 0 else [1, 0],
                labels=src_labels_parts[:len(src_vals)],
                colors=src_clrs[:len(src_vals)],
                startangle=90,
                wedgeprops=dict(width=0.5, edgecolor="white"),
                textprops={"fontsize": 8, "color": SLATE_700},
            )
            ax.set_title("Record Source Breakdown", fontsize=9, color=SLATE_900, pad=8, fontweight="bold")

        plt.tight_layout(pad=0.5)
        buf = _BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", dpi=dpi)
        plt.close(fig)
        buf.seek(0)
        return buf

    # ── styles ────────────────────────────────────────────────────────────────
    buffer = _BytesIO()
    styles = getSampleStyleSheet()
    heading = ParagraphStyle(
        "heading", parent=styles["Heading2"],
        textColor=colors.HexColor(SLATE_900),
        spaceBefore=5 * mm, spaceAfter=2 * mm, fontSize=12, fontName="Helvetica-Bold"
    )
    subheading = ParagraphStyle(
        "subheading", parent=styles["Heading3"],
        textColor=colors.HexColor(SLATE_700),
        spaceBefore=3 * mm, spaceAfter=1.5 * mm, fontSize=10, fontName="Helvetica-Bold"
    )
    normal = ParagraphStyle(
        "normal", parent=styles["Normal"],
        fontSize=8.5, textColor=colors.HexColor(SLATE_700), leading=13
    )
    insight = ParagraphStyle(
        "insight", parent=styles["Normal"],
        fontSize=8.5, textColor=colors.HexColor("#92400E"), leading=13
    )
    small_grey = ParagraphStyle(
        "small_grey", parent=styles["Normal"],
        fontSize=7.5, textColor=colors.HexColor(SLATE_400), leading=12
    )

    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=28 * mm, bottomMargin=22 * mm,
        title="Coconut Leaf Disease Analytics Report",
    )

    CONTENT_W = PAGE_W - 32 * mm
    card_w = (CONTENT_W - 9 * mm) / 4

    # ── page templates ────────────────────────────────────────────────────────
    def first_page(canvas, doc):
        canvas.saveState()
        # Header band
        canvas.setFillColor(colors.HexColor(BRAND_DARK))
        canvas.rect(0, PAGE_H - 22 * mm, PAGE_W, 22 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(16 * mm, PAGE_H - 14 * mm, "Coconut Leaf Disease — Analytics Report")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(PAGE_W - 16 * mm, PAGE_H - 14 * mm, f"Prepared for {email or 'User'}")
        canvas.restoreState()

    def later_pages(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor(BRAND_DARK))
        canvas.rect(0, PAGE_H - 14 * mm, PAGE_W, 14 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(16 * mm, PAGE_H - 9 * mm, "Coconut Leaf Disease Analytics")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(PAGE_W - 16 * mm, PAGE_H - 9 * mm, f"{email or 'User'}")
        canvas.restoreState()

    # ── table style helpers ───────────────────────────────────────────────────
    BASE_TABLE_STYLE = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(SLATE_900)),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, 0), 8),
        ("LINEBELOW",  (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("FONTNAME",   (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE",   (0, 1), (-1, -1), 8),
    ]

    # ── narrative texts ───────────────────────────────────────────────────────
    primary_count = disease_stats.get(primary_disease, {}).get("count", 0)
    primary_avg_conf = (
        sum(disease_stats.get(primary_disease, {}).get("confidence", [])) /
        len(disease_stats.get(primary_disease, {}).get("confidence", [1])) * 100
        if disease_stats.get(primary_disease, {}).get("confidence") else 0
    )

    priority_text = (
        f"<b>Priority alert:</b> <b>{_safe(primary_disease)}</b> is the leading classified disease "
        f"with <b>{primary_count}</b> detections at <b>{primary_avg_conf:.1f}%</b> avg confidence. "
        f"Disease incidence stands at <b>{disease_incidence:.1f}%</b> of all detections across "
        f"<b>{total_records}</b> submitted records."
    )
    trend_narrative = (
        f"The monthly trend shows detection activity across the reporting period. "
        f"<b>{primary_disease}</b> remains the most frequent result. "
        f"Disease conditions represent <b>{disease_incidence:.1f}%</b> of all classified findings. "
        f"<b>{delta_str}</b> in submitted records."
    )
    coverage_text = (
        f"<b>{gps_records}</b> of <b>{total_records}</b> records have GPS coordinates "
        f"(<b>{gps_pct:.0f}%</b> coverage). "
        f"Records from drone source: <b>{drone_records}</b>. "
        f"Records from photo upload: <b>{upload_records}</b>."
    )

    # ── severity area table ───────────────────────────────────────────────────
    sev_rows = [["Area", "Samples", "Critical", "Severe", "Moderate", "Mild"]]
    for area_name, area_data in area_stats.items():
        sev_rows.append([
            area_name,
            str(area_data["samples"]),
            str(area_data["severity"].get("Critical", 0)),
            str(area_data["severity"].get("Severe", 0)),
            str(area_data["severity"].get("Moderate", 0)),
            str(area_data["severity"].get("Mild", 0)),
        ])
    severity_table = Table(
        sev_rows,
        colWidths=[35 * mm, 25 * mm, 28 * mm, 25 * mm, 30 * mm, 25 * mm]
    )
    sev_table_style = list(BASE_TABLE_STYLE) + [
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(SLATE_50)]),
    ]
    severity_table.setStyle(TableStyle(sev_table_style))

    # ── disease distribution table (color-coded severity rows) ────────────────
    dist_rows = [["Disease", "Detections", "Avg Confidence", "Severity"]]
    dist_row_styles = list(BASE_TABLE_STYLE)
    for row_idx, (disease, data) in enumerate(
        sorted(disease_stats.items(), key=lambda item: item[1]["count"], reverse=True), start=1
    ):
        avg = (sum(data["confidence"]) / len(data["confidence"]) * 100) if data["confidence"] else 0
        sev = _severity_for_disease(disease)
        dist_rows.append([
            _safe(disease)[:32],
            str(data["count"]),
            f"{avg:.1f}%",
            sev,
        ])
        row_bg = _severity_row_color(sev)
        dist_row_styles.append(("BACKGROUND", (0, row_idx), (-1, row_idx), row_bg))

    distribution_table = Table(
        dist_rows,
        colWidths=[68 * mm, 32 * mm, 42 * mm, 35 * mm]
    )
    distribution_table.setStyle(TableStyle(dist_row_styles))

    # ── top-5 detection records table ─────────────────────────────────────────
    top5_rows = [["#", "Disease", "Confidence", "Source", "Timestamp"]]
    for rank, det in enumerate(top5_detections, start=1):
        ts_display = str(det["timestamp"])[:19].replace("T", " ")
        top5_rows.append([
            str(rank),
            _safe(det["disease"])[:28],
            f"{det['confidence'] * 100:.1f}%",
            _title_case(det["source"]),
            ts_display,
        ])
    if len(top5_rows) == 1:
        top5_rows.append(["—", "No detections recorded", "—", "—", "—"])

    top5_table = Table(
        top5_rows,
        colWidths=[10 * mm, 55 * mm, 28 * mm, 28 * mm, 56 * mm]
    )
    top5_style = list(BASE_TABLE_STYLE) + [
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor(SLATE_50)]),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
    ]
    top5_table.setStyle(TableStyle(top5_style))

    # ── priority alert box ────────────────────────────────────────────────────
    priority_box = Table(
        [[Paragraph(priority_text, insight)]],
        colWidths=[CONTENT_W],
        style=[
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(ORANGE_BG)),
            ("BOX",        (0, 0), (-1, -1), 0.4, colors.HexColor("#F5D6B3")),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ],
    )

    coverage_box = Table(
        [[Paragraph(coverage_text, normal)]],
        colWidths=[CONTENT_W],
        style=[
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(BRAND_LIGHT)),
            ("BOX",        (0, 0), (-1, -1), 0.4, colors.HexColor("#6EE7B7")),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ],
    )

    # ── expert recommendations ────────────────────────────────────────────────
    detected_diseases_sorted = sorted(
        disease_stats.keys(),
        key=lambda k: disease_stats[k]["count"],
        reverse=True,
    )
    rec_cards = []
    for disease in detected_diseases_sorted[:6]:
        rec = _get_expert_rec(disease)
        if rec:
            rec_cards.append(_RecommendationCard(
                disease=disease,
                rec=rec,
                width=CONTENT_W,
                accent=_detection_color(disease),
            ))
            rec_cards.append(Spacer(1, 4 * mm))

    # ── 4 KPI cards ──────────────────────────────────────────────────────────
    kpi_row = Table(
        [[
            _MetricCard(str(total_records), "Detection Records",
                        delta_str, card_w,
                        accent=BRAND_DARK, note_color="#1D4ED8" if delta_str.startswith("▲") else "#DC2626"),
            _MetricCard(str(total_detections), "Total Detections",
                        f"{disease_incidence:.0f}% disease incidence", card_w,
                        accent="#EF4444", note_color="#991B1B"),
            _MetricCard(_title_case(primary_disease)[:16], "Leading Disease",
                        f"{primary_count} occurrence{'s' if primary_count != 1 else ''}", card_w,
                        accent="#F97316", note_color="#9A3412"),
            _MetricCard(f"{avg_conf:.1f}%", "Avg Confidence",
                        f"{gps_pct:.0f}% GPS coverage", card_w,
                        accent=BRAND_MID, note_color=BRAND_DARK),
        ]],
        colWidths=[card_w + 2.5 * mm] * 4,
    )

    # ── story ─────────────────────────────────────────────────────────────────
    # Page 1 — KPI + Disease Distribution
    story = [
        kpi_row,
        Spacer(1, 5 * mm),
        _SectionDivider("📊  Disease Distribution Overview", CONTENT_W),
        Spacer(1, 3 * mm),
        Image(_chart_image("disease"), width=CONTENT_W, height=80 * mm),
        Spacer(1, 5 * mm),
        distribution_table,
        PageBreak(),

        # Page 2 — Monthly Trend + Stacked Monthly
        _SectionDivider("📈  Monthly Detection Trend", CONTENT_W),
        Spacer(1, 3 * mm),
        Image(_chart_image("trend"), width=CONTENT_W, height=72 * mm),
        Spacer(1, 5 * mm),
        Paragraph(trend_narrative, normal),
        Spacer(1, 5 * mm),
        _SectionDivider("🌿  Diseased vs Healthy — Monthly Breakdown", CONTENT_W, accent=BRAND_MID),
        Spacer(1, 3 * mm),
        Image(_chart_image("stacked_monthly"), width=CONTENT_W, height=72 * mm),
        PageBreak(),

        # Page 3 — Severity + Weekday Activity
        _SectionDivider("⚠️  Severity Analysis by Map Area", CONTENT_W, accent="#B45309"),
        Spacer(1, 3 * mm),
        Image(_chart_image("severity"), width=CONTENT_W, height=55 * mm),
        Spacer(1, 4 * mm),
        severity_table,
        Spacer(1, 5 * mm),
        priority_box,
        Spacer(1, 5 * mm),
        _SectionDivider("📅  Detection Activity by Day of Week", CONTENT_W, accent=BRAND_DARK),
        Spacer(1, 3 * mm),
        Image(_chart_image("weekday_activity"), width=CONTENT_W, height=55 * mm),
        PageBreak(),

        # Page 4 — Verification / Source / Confidence
        _SectionDivider("✅  Verification & Source Breakdown", CONTENT_W, accent="#059669"),
        Spacer(1, 3 * mm),
        Table(
            [[
                Image(_chart_image("donut_verification", width_mm=3.5, height_mm=3.0), width=85 * mm, height=72 * mm),
                Image(_chart_image("donut_source",       width_mm=3.5, height_mm=3.0), width=85 * mm, height=72 * mm),
            ]],
            colWidths=[88 * mm, 88 * mm],
        ),
        Spacer(1, 4 * mm),
        coverage_box,
        Spacer(1, 5 * mm),
        _SectionDivider("📊  Confidence Score Distribution", CONTENT_W, accent="#7C3AED"),
        Spacer(1, 3 * mm),
        Image(_chart_image("confidence_hist"), width=CONTENT_W, height=70 * mm),
        PageBreak(),

        # Page 5 — Top-5 + GPS coverage stat
        _SectionDivider("🏆  Top 5 Highest-Confidence Detections", CONTENT_W, accent="#1D4ED8"),
        Spacer(1, 4 * mm),
        top5_table,
        Spacer(1, 6 * mm),
        Paragraph(
            f"<b>GPS Data Coverage:</b> {gps_records} of {total_records} records contain GPS coordinates "
            f"({gps_pct:.1f}%). Records without GPS are excluded from the map-area analysis above.",
            small_grey,
        ),
    ]

    # Page 6 — Expert Recommendations (only if we have some)
    if rec_cards:
        story.append(PageBreak())
        story.append(_SectionDivider("💡  Expert Treatment Recommendations", CONTENT_W, accent=BRAND_DARK))
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(
            "Guidance below is sourced from the system's expert recommendation database "
            "and is aligned with Philippine Coconut Authority (PCA) guidelines. "
            "Always confirm diagnosis and pesticide registration with your local agricultural office before treatment.",
            small_grey,
        ))
        story.append(Spacer(1, 4 * mm))
        story.extend(rec_cards)

    document.build(story, onFirstPage=first_page, onLaterPages=later_pages, canvasmaker=_NumberedCanvas)
    return buffer.getvalue()
