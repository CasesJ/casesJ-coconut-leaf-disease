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

    This is a direct extraction of the build_executive_user_records_pdf() function
    that previously lived in main.py. No behaviour changes.
    """
    # All PDF-specific imports are kept local to avoid penalising startup time.
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415
    from io import BytesIO as _BytesIO  # noqa: PLC0415

    from reportlab.lib import colors  # noqa: PLC0415
    from reportlab.lib.pagesizes import A4  # noqa: PLC0415
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # noqa: PLC0415
    from reportlab.lib.units import mm  # noqa: PLC0415
    from reportlab.platypus import (  # noqa: PLC0415
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, Flowable, Image, KeepTogether,
    )
    from reportlab.pdfgen.canvas import Canvas  # noqa: PLC0415

    # ── helpers ──────────────────────────────────────────────────────────────

    def _title_case(value: Any) -> str:
        return str(value or "-").replace("_", " ").strip().title()

    def _safe(value: Any) -> str:
        return str(value or "-").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    detection_colors = {
        "healthy": "#22C55E",
        "caterpillars": "#F97316",
        "cercospora": "#EC4899",
        "drying of leaflets": "#2563EB",
        "leaf rot": "#2563EB",
        "pestalotiopsis": "#06B6D4",
        "bud root": "#D4D800",
        "bud rot": "#D4D800",
    }

    def _detection_color(disease: str) -> str:
        return detection_colors.get(str(disease or "").replace("_", " ").strip().lower(), "#64748B")

    # ── data aggregation ─────────────────────────────────────────────────────
    disease_stats: dict = defaultdict(lambda: {"count": 0, "confidence": []})
    monthly_counts: dict = defaultdict(int)
    monthly_disease_counts: dict = defaultdict(lambda: defaultdict(int))
    severity_counts: dict = defaultdict(int)
    map_bounds = {"min_lat": 7.35140, "max_lat": 7.35295, "min_lng": 125.64055, "max_lng": 125.64215}
    lat_step = (map_bounds["max_lat"] - map_bounds["min_lat"]) / 2
    lng_step = (map_bounds["max_lng"] - map_bounds["min_lng"]) / 2
    area_stats = {f"Area {i}": {"samples": 0, "severity": defaultdict(int)} for i in range(1, 5)}
    verified_records = 0
    top_confidences: list = []

    def _severity_for_disease(disease: str) -> str:
        nd = disease.lower()
        if any(n in nd for n in ("lethal", "bud rot", "bud root")):
            return "Critical"
        if any(n in nd for n in ("blight", "pestalotiopsis")):
            return "Severe"
        if nd != "healthy":
            return "Moderate"
        return "Mild"

    def _mapped_area(record: dict) -> Optional[str]:
        import json as _json  # noqa: PLC0415
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

    for record in records:
        if normalize_verification_status(record) == "verified":
            verified_records += 1
        if record.get("timestamp"):
            monthly_counts[str(record["timestamp"])[:7]] += 1
        area = _mapped_area(record)
        for detection in record.get("detections") or []:
            disease = str(detection.get("class") or "Unknown")
            conf = float(detection.get("confidence") or 0)
            disease_stats[disease]["count"] += 1
            disease_stats[disease]["confidence"].append(conf)
            severity = _severity_for_disease(disease)
            severity_counts[severity] += 1
            top_confidences.append(conf)
            if record.get("timestamp"):
                monthly_disease_counts[str(record["timestamp"])[:7]][disease] += 1
            if area:
                area_stats[area]["samples"] += 1
                area_stats[area]["severity"][severity] += 1

    total_detections = sum(d["count"] for d in disease_stats.values())
    disease_incidence = (
        (total_detections - disease_stats.get("Healthy", {}).get("count", 0)) / total_detections * 100
        if total_detections > 0 else 0
    )
    primary_disease = max(disease_stats, key=lambda k: disease_stats[k]["count"], default="None detected")
    avg_conf = (sum(top_confidences) / len(top_confidences) * 100) if top_confidences else 0

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
                self.line(16 * mm, 14 * mm, A4[0] - 16 * mm, 14 * mm)
                self.setFillColor(colors.HexColor("#64748B"))
                self.setFont("Helvetica", 7.5)
                self.drawString(16 * mm, 9 * mm, "Confidential - Coconut Leaf Disease Analytics")
                self.drawRightString(
                    A4[0] - 16 * mm, 9 * mm,
                    f"Generated {datetime.now(timezone.utc).astimezone().strftime('%d %b %Y %H:%M')} | Page {self._pageNumber} of {total_pages}"
                )
                Canvas.showPage(self)
            Canvas.save(self)

    # ── metric card flowable ──────────────────────────────────────────────────
    class _MetricCard(Flowable):
        def __init__(self, value: str, label: str, note: str, width: float, height: float = 35 * mm, note_color: str = "#2F6E54"):
            Flowable.__init__(self)
            self.value, self.label, self.note = value, label, note
            self.width, self.height, self.note_color = width, height, note_color

        def wrap(self, aw, ah):
            return self.width, self.height

        def draw(self):
            self.canv.setFillColor(colors.white)
            self.canv.setStrokeColor(colors.HexColor("#DDE5E0"))
            self.canv.roundRect(0, 0, self.width, self.height, 3 * mm, fill=1, stroke=1)
            self.canv.setFillColor(colors.HexColor("#41966E"))
            self.canv.roundRect(0, 0, 2.6 * mm, self.height, 2 * mm, fill=1, stroke=0)
            self.canv.setFillColor(colors.HexColor("#64748B"))
            self.canv.setFont("Helvetica", 7.4)
            self.canv.drawString(6 * mm, 25 * mm, self.label.upper())
            self.canv.setFillColor(colors.HexColor("#20252B"))
            self.canv.setFont("Helvetica-Bold", 18)
            self.canv.drawString(6 * mm, 14.5 * mm, self.value[:20])
            self.canv.setFillColor(colors.HexColor(self.note_color))
            self.canv.setFont("Helvetica-Bold", 7.2)
            self.canv.drawString(6 * mm, 5.2 * mm, self.note[:30])

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

    # ── chart helpers ─────────────────────────────────────────────────────────
    def _chart_image(chart_type: str) -> _BytesIO:
        fig, ax = plt.subplots(figsize=(6.7, 3.2), dpi=110)
        fig.patch.set_facecolor("white")
        ax.set_facecolor("#F8FAFC")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        if chart_type == "disease":
            names = list(disease_stats.keys())[:8]
            counts = [disease_stats[n]["count"] for n in names]
            bar_colors = [_detection_color(n) for n in names]
            bars = ax.barh(names, counts, color=bar_colors, height=0.55, edgecolor="none")
            for bar, count in zip(bars, counts):
                ax.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height() / 2,
                        str(count), va="center", fontsize=8, color="#374151")
            ax.set_xlabel("Detections", fontsize=9, color="#64748B")
        elif chart_type == "trend":
            months = sorted(monthly_counts.keys())[-12:]
            counts = [monthly_counts[m] for m in months]
            ax.plot(months, counts, marker="o", color="#22C55E", linewidth=2, markersize=5)
            ax.fill_between(months, counts, alpha=0.12, color="#22C55E")
            ax.set_ylabel("Records", fontsize=9, color="#64748B")
            plt.xticks(rotation=30, ha="right", fontsize=7.5)
        elif chart_type == "severity":
            sev_labels = list(severity_counts.keys())
            sev_vals = list(severity_counts.values())
            sev_clrs = {"Critical": "#EF4444", "Severe": "#F97316", "Moderate": "#EAB308", "Mild": "#22C55E"}
            clrs = [sev_clrs.get(s, "#64748B") for s in sev_labels]
            ax.bar(sev_labels, sev_vals, color=clrs, edgecolor="none", width=0.5)
            ax.set_ylabel("Count", fontsize=9, color="#64748B")

        plt.tight_layout(pad=0.4)
        buf = _BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", dpi=110)
        plt.close(fig)
        buf.seek(0)
        return buf

    # ── document layout ───────────────────────────────────────────────────────
    buffer = _BytesIO()
    styles = getSampleStyleSheet()
    heading = ParagraphStyle("heading", parent=styles["Heading2"], textColor=colors.HexColor("#1E293B"), spaceBefore=6 * mm, spaceAfter=2 * mm, fontSize=13)
    normal = ParagraphStyle("normal", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#374151"), leading=14)
    insight = ParagraphStyle("insight", parent=styles["Normal"], fontSize=8.5, textColor=colors.HexColor("#92400E"), leading=13)

    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=28 * mm, bottomMargin=22 * mm,
        title="Coconut Leaf Disease Analytics Report",
    )
    card_w = (A4[0] - 32 * mm - 8 * mm) / 4

    def first_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#164D37"))
        canvas.rect(0, A4[1] - 22 * mm, A4[0], 22 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawString(16 * mm, A4[1] - 14 * mm, "Coconut Leaf Disease — Analytics Report")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(A4[0] - 16 * mm, A4[1] - 14 * mm, f"Prepared for {email or 'User'}")
        canvas.restoreState()

    def later_pages(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#164D37"))
        canvas.rect(0, A4[1] - 14 * mm, A4[0], 14 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(16 * mm, A4[1] - 9 * mm, "Coconut Leaf Disease Analytics")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(A4[0] - 16 * mm, A4[1] - 9 * mm, f"{email or 'User'}")
        canvas.restoreState()

    priority_text = (
        f"<b>Priority alert:</b> {primary_disease} is the leading classified disease "
        f"with {disease_stats.get(primary_disease, {}).get('count', 0)} detections. "
        f"Disease incidence stands at {disease_incidence:.1f}% of all detections."
    )
    trend_narrative = (
        f"The monthly mix highlights the leading classified conditions across the available "
        f"reporting period. {primary_disease} remains the most frequent result, with disease "
        f"conditions representing {disease_incidence:.1f}% of all classified findings."
    )

    # Build severity area table rows
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
    severity_table = Table(sev_rows, colWidths=[35 * mm, 25 * mm, 28 * mm, 25 * mm, 30 * mm, 25 * mm])
    severity_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    # Disease distribution table
    dist_rows = [["Disease", "Detections", "Avg Confidence", "Severity", "Color"]]
    for disease, data in sorted(disease_stats.items(), key=lambda item: item[1]["count"], reverse=True):
        avg = (sum(data["confidence"]) / len(data["confidence"]) * 100) if data["confidence"] else 0
        dist_rows.append([
            _safe(disease)[:30],
            str(data["count"]),
            f"{avg:.1f}%",
            _severity_for_disease(disease),
            _detection_color(disease),
        ])
    distribution_table = Table(dist_rows, colWidths=[55 * mm, 28 * mm, 38 * mm, 30 * mm, 22 * mm])
    distribution_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    priority_box = Table(
        [[Paragraph(priority_text, insight)]],
        colWidths=[175 * mm],
        style=[
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF7ED")),
            ("BOX", (0, 0), (-1, -1), 0.3, colors.HexColor("#F5D6B3")),
        ],
    )

    story = [
        Table(
            [[
                _MetricCard(str(len(records)), "Detection Records", f"{verified_records} verified", card_w),
                _MetricCard(str(total_detections), "Total Detections", f"{disease_incidence:.0f}% disease incidence", card_w),
                _MetricCard(primary_disease[:16], "Leading Disease", f"{disease_stats.get(primary_disease, {}).get('count', 0)} occurrences", card_w),
                _MetricCard(f"{avg_conf:.1f}%", "Avg Confidence", "Model confidence score", card_w),
            ]],
            colWidths=[card_w + 2 * mm] * 4,
        ),
        Spacer(1, 6 * mm),
        Paragraph("Disease Distribution", heading),
        Spacer(1, 4 * mm),
        Image(_chart_image("disease"), width=171 * mm, height=83 * mm),
        Spacer(1, 6 * mm),
        PageBreak(),
        Paragraph("Monthly Detection Trend", heading),
        Spacer(1, 6 * mm),
        Image(_chart_image("trend"), width=171 * mm, height=83 * mm),
        Spacer(1, 7 * mm),
        Paragraph(trend_narrative, normal),
        PageBreak(),
        Paragraph("Severity by Map Area", heading),
        Spacer(1, 3 * mm),
        Image(_chart_image("severity"), width=171 * mm, height=55 * mm),
        Spacer(1, 4 * mm),
        severity_table,
        Spacer(1, 5 * mm),
        priority_box,
        PageBreak(),
        Paragraph("Disease distribution", heading),
        Spacer(1, 7 * mm),
        distribution_table,
    ]

    document.build(story, onFirstPage=first_page, onLaterPages=later_pages, canvasmaker=_NumberedCanvas)
    return buffer.getvalue()
