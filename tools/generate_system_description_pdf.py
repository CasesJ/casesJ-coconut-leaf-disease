"""Create the plain-English system description PDF for CoconutAI.

The text in this report is based on the current application source, not on
unverified feature assumptions in the older system-description document.
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "CoconutAI_System_Description_Plain_English.pdf"


def paragraph(text: str, style: ParagraphStyle) -> Paragraph:
    """Build a paragraph while keeping the source text readable below."""
    return Paragraph(text, style)


def add_list(story: list, items: list[str], styles: dict, ordered: bool = False) -> None:
    for number, item in enumerate(items, start=1):
        marker = f"{number}." if ordered else "-"
        story.append(paragraph(f"<b>{marker}</b> {item}", styles["body"]))
        story.append(Spacer(1, 1.8 * mm))


def add_section(story: list, title: str, styles: dict) -> None:
    story.append(Spacer(1, 4 * mm))
    story.append(paragraph(title, styles["heading"]))
    story.append(HRFlowable(width="100%", thickness=0.55, color=colors.HexColor("#9CB6A4"), spaceBefore=1.5 * mm, spaceAfter=3 * mm))


def draw_page_number(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D7E2D8"))
    canvas.line(doc.leftMargin, 13 * mm, A4[0] - doc.rightMargin, 13 * mm)
    canvas.setFillColor(colors.HexColor("#4B6353"))
    canvas.setFont("Helvetica", 8)
    canvas.drawString(doc.leftMargin, 8.5 * mm, "CoconutAI System Description")
    canvas.drawRightString(A4[0] - doc.rightMargin, 8.5 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_pdf() -> Path:
    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=A4,
        leftMargin=19 * mm,
        rightMargin=19 * mm,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title="CoconutAI System Description - Plain English",
        author="Coconut Disease Detector project",
    )

    sample = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "ReportTitle", parent=sample["Title"], fontName="Helvetica-Bold",
            fontSize=22, leading=27, alignment=TA_CENTER,
            textColor=colors.HexColor("#163B27"), spaceAfter=3 * mm,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle", parent=sample["Normal"], fontName="Helvetica",
            fontSize=10.5, leading=14, alignment=TA_CENTER,
            textColor=colors.HexColor("#52665A"), spaceAfter=7 * mm,
        ),
        "heading": ParagraphStyle(
            "Heading", parent=sample["Heading2"], fontName="Helvetica-Bold",
            fontSize=14, leading=17, textColor=colors.HexColor("#163B27"),
            spaceBefore=0, spaceAfter=0,
        ),
        "subheading": ParagraphStyle(
            "Subheading", parent=sample["Heading3"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, textColor=colors.HexColor("#244C34"),
            spaceBefore=3 * mm, spaceAfter=1.5 * mm,
        ),
        "body": ParagraphStyle(
            "Body", parent=sample["BodyText"], fontName="Helvetica",
            fontSize=9.7, leading=14, textColor=colors.HexColor("#202822"),
            alignment=TA_LEFT, spaceAfter=2.5 * mm,
        ),
        "note": ParagraphStyle(
            "Note", parent=sample["BodyText"], fontName="Helvetica-Oblique",
            fontSize=8.7, leading=12, textColor=colors.HexColor("#415245"),
            spaceAfter=0,
        ),
        "table": ParagraphStyle(
            "Table", parent=sample["BodyText"], fontName="Helvetica",
            fontSize=8.8, leading=11.5, textColor=colors.HexColor("#202822"),
        ),
        "tablehead": ParagraphStyle(
            "TableHead", parent=sample["BodyText"], fontName="Helvetica-Bold",
            fontSize=8.8, leading=11, textColor=colors.white,
        ),
    }

    story: list = []
    story.append(Spacer(1, 6 * mm))
    story.append(paragraph("CoconutAI", styles["title"]))
    story.append(paragraph("Coconut Disease Detector - System Description", styles["subtitle"]))

    accuracy_note = Table(
        [[paragraph("<b>Plain-English version.</b> This document was rewritten from the current project source code. It only describes features found in the application. Where the code has a limit or an unfinished connection, that limit is stated clearly.", styles["note"])]],
        colWidths=[172 * mm],
    )
    accuracy_note.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EDF5EE")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#9CB6A4")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
    ]))
    story.append(accuracy_note)

    add_section(story, "1. System Description", styles)
    story.append(paragraph(
        "CoconutAI is a web application for detecting coconut leaf conditions from images. The application is also called the Coconut Disease Detector in the backend code. A signed-in user can upload a coconut image or use the live camera screen. The system runs an AI model and shows the detected labels, confidence scores, and marked image.",
        styles["body"],
    ))
    story.append(paragraph(
        "Uploaded detections are saved as records. An Expert can review these records and mark them as verified or pending. When an Expert verifies a record, the system creates a notification for the owner of that record. The user can then see the verified record and its saved recommendations.",
        styles["body"],
    ))
    story.append(paragraph(
        "The application also has a disease map. It shows saved records that have valid image GPS coordinates. In the web interface, the Farmer view has the map while the Expert view hides the Farmer navigation.",
        styles["body"],
    ))

    add_section(story, "2. Users and Access", styles)
    user_rows = [
        [paragraph("User", styles["tablehead"]), paragraph("What the current system lets the user do", styles["tablehead"])],
        [paragraph("Farmer", styles["table"]), paragraph("Sign in, upload images, use the live camera screen, view their detection records, see verification status, receive in-app notifications, view recommendations, use the map, and download a PDF report of their records.", styles["table"])],
        [paragraph("Expert", styles["table"]), paragraph("Sign in to the Expert view, see records from users, filter pending and verified records, mark a record verified or pending, and save expert recommendations for a selected record. The Expert view also has an audit log.", styles["table"])],
    ]
    users = Table(user_rows, colWidths=[31 * mm, 141 * mm], repeatRows=1)
    users.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#236A43")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9CABB")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F7FAF7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5 * mm),
    ]))
    story.append(users)
    story.append(Spacer(1, 3 * mm))
    story.append(paragraph(
        "The reviewed code has Farmer and Expert views. It does not show a separate Administrator role.",
        styles["note"],
    ))

    add_section(story, "3. How the System Works", styles)
    add_list(story, [
        "The user signs in with Firebase Authentication.",
        "The user uploads a JPG, PNG, or WEBP image. The browser can also send camera frames to the live detection screen.",
        "For an uploaded image, the server checks that it is a valid image, reads any available image metadata, and runs the active YOLO26 v6 model.",
        "The system returns the detected labels, confidence scores, boxes around detections, and an annotated image.",
        "A saved upload starts as <b>pending verification</b>. The AI score does not verify the result by itself.",
        "The system stores each detection record in the farmer's Firestore detections collection and stores new image evidence as authenticated Cloudinary assets. It does not fall back to local database storage.",
        "An Expert can review a record. When the Expert marks it verified, the system saves the verifier details, creates a recommendation snapshot, and creates an in-app notification for the record owner.",
        "The Farmer can view their records. The normal Farmer dashboard uses verified records for its main statistics.",
    ], styles, ordered=True)

    story.append(paragraph("Live camera and drone screen", styles["subheading"]))
    story.append(paragraph(
        "The project includes a live WebSocket detection endpoint. The browser camera sends frames to the server, and the system returns annotated frames. Higher-confidence detections from a live session are saved through the same Firestore and authenticated Cloudinary flow with GPS when available. This is a live camera feature; it does not prove that a physical drone is connected or flying.",
        styles["body"],
    ))

    add_section(story, "4. Detection Labels", styles)
    story.append(paragraph(
        "The label returned by an image is taken from the loaded <b>weights.pt</b> model. The exact list should therefore be checked from the running model before a final research paper claims a fixed class list.",
        styles["body"],
    ))
    story.append(paragraph(
        "The Expert recommendation editor currently lists: Caterpillars, Drying of Leaflets, Healthy, Pestalotiopsis, Bud Rot, and Stem Bleeding. The user interface highlights Healthy, Stem Bleeding, Pestalotiopsis, and Bud Rot on its map and filters.",
        styles["body"],
    ))
    story.append(paragraph(
        "Important: the current detector code filters out old labels named Cercospora and Leaf Rot. It also states that the loaded detection model must contain Stem Bleeding training examples before the detector itself can return Stem Bleeding. For this reason, this document does not claim that every label in the Expert editor is already an active model output.",
        styles["note"],
    ))

    add_section(story, "5. Recommendations and Verification", styles)
    story.append(paragraph(
        "The system can provide fertilizer, treatment, and prevention recommendations. The code first tries to use saved machine-learning recommendation files. If those files cannot be used, it uses built-in recommendation content. An Expert can also save a custom recommendation for one selected record.",
        styles["body"],
    ))
    story.append(paragraph(
        "The current Expert verification endpoint changes the record status between pending and verified. It does <b>not</b> include a field for changing the AI detection label. Therefore, this system description does not claim that the Expert can correct a predicted disease class.",
        styles["body"],
    ))

    add_section(story, "6. GPS and Map", styles)
    add_list(story, [
        "For an uploaded image, the server uses valid GPS data embedded in the image, including DJI EXIF data when available.",
        "If an uploaded image has no usable embedded GPS data, that upload is left unmapped. Browser or form coordinates are not used as a replacement for an uploaded image.",
        "The app can keep a farm center after a valid image GPS location is saved.",
        "The web map uses MapLibre GL. It shows location pins, a heatmap, and an overview map for records with location data.",
        "The live camera screen can use a current GPS position for live markers and drone-log entries when GPS is available.",
    ], styles)

    add_section(story, "7. Main Technologies Used", styles)
    technology_rows = [
        [paragraph("Technology", styles["tablehead"]), paragraph("Use in this project", styles["tablehead"])],
        [paragraph("Python and FastAPI", styles["table"]), paragraph("Backend web API, image upload, detection, records, reporting, and WebSocket streaming.", styles["table"])],
        [paragraph("YOLO26 v6 and Ultralytics", styles["table"]), paragraph("The active detector loads local weights.pt and uses the Ultralytics backend for normal predictions.", styles["table"])],
        [paragraph("OpenVINO", styles["table"]), paragraph("The project contains OpenVINO code and dependency support, but the active detector is set to the Ultralytics backend in the current model code.", styles["table"])],
        [paragraph("Firebase", styles["table"]), paragraph("Authentication and Firestore structured records.", styles["table"])],
        [paragraph("Cloudinary", styles["table"]), paragraph("Authenticated storage and private retrieval for new image evidence.", styles["table"])],
        [paragraph("HTML, CSS, and JavaScript", styles["table"]), paragraph("The browser interface for login, upload, records, recommendations, notifications, and live camera controls.", styles["table"])],
        [paragraph("MapLibre GL", styles["table"]), paragraph("The browser map, map pins, heatmap, and overview map.", styles["table"])],
        [paragraph("ReportLab", styles["table"]), paragraph("PDF report generation for a user's detection records.", styles["table"])],
    ]
    technologies = Table(technology_rows, colWidths=[47 * mm, 125 * mm], repeatRows=1)
    technologies.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#236A43")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9CABB")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F7FAF7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.2 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2 * mm),
    ]))
    story.append(technologies)

    add_section(story, "8. Input, Process, and Output", styles)
    ipo_rows = [
        [paragraph("Input", styles["tablehead"]), paragraph("Process", styles["tablehead"]), paragraph("Output", styles["tablehead"])],
        [paragraph("Signed-in user details; uploaded JPG, PNG, or WEBP images; live camera frames; image metadata; available GPS data; Expert status and recommendation details.", styles["table"]),
         paragraph("Authenticate the user; validate the image or stream frame; run AI detection; create annotated output; save records; allow Expert review; save a verification status and recommendation snapshot; build map data when coordinates exist.", styles["table"]),
         paragraph("Detected labels, confidence scores, marked images, saved records, pending or verified status, recommendations, notifications, GPS map pins, CSV exports, and a PDF report for the user's records.", styles["table"])],
    ]
    ipo = Table(ipo_rows, colWidths=[57.3 * mm, 57.3 * mm, 57.3 * mm], repeatRows=1)
    ipo.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#236A43")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9CABB")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F7FAF7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.6 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.6 * mm),
        ("TOPPADDING", (0, 0), (-1, -1), 2.6 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6 * mm),
    ]))
    story.append(ipo)

    add_section(story, "9. System Limits", styles)
    add_list(story, [
        "The AI result depends on the image quality, lighting, visible symptoms, training images, and model weights.",
        "The model can only return labels that exist in the loaded model. The exact model labels should be checked before they are listed in final academic documents.",
        "An AI result is not automatically verified. Uploaded records remain pending until an Expert verifies them.",
        "The current code lets an Expert verify a record and save recommendations, but it does not provide a way to edit the AI disease label.",
        "Mapping depends on valid GPS information. Uploaded images without usable embedded coordinates are not placed on the map.",
        "Record and image persistence depend on Firestore and Cloudinary availability. The application does not use SQLite or JSON backups as a runtime fallback.",
        "The live camera feature depends on camera permission, browser support, network connection, server capacity, and a valid signed-in session.",
        "Recommendations are generated from configured machine-learning files, built-in content, or an Expert's saved record recommendation. They are support information, not a substitute for professional agricultural advice.",
    ], styles)

    add_section(story, "10. Short Summary", styles)
    story.append(paragraph(
        "CoconutAI is a web-based coconut disease detection and record-management system. It uses a local YOLO26 v6 model to analyze uploaded images and live camera frames. Users can keep their detection records, view verified results, see available recommendations, receive verification notifications, and view GPS-tagged records on a map. Experts can review records, mark them verified or pending, and save recommendations for a selected record. The system supports decision-making, but its AI results and recommendations should be checked by a qualified agricultural professional.",
        styles["body"],
    ))

    doc.build(story, onFirstPage=draw_page_number, onLaterPages=draw_page_number)
    return OUTPUT_PATH


if __name__ == "__main__":
    output = build_pdf()
    print(output)
