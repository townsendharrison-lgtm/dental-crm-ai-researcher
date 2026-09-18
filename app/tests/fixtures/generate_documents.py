"""Rebuild deterministic synthetic admissions fixtures; never use student data."""
import io
from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

ROOT = Path(__file__).parent


def write_pdf(name, title, elements):
    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(str(ROOT / name), pagesize=letter, title=title,
                                 topMargin=48, bottomMargin=48)
    story = [Paragraph("SYNTHETIC TEST FIXTURE - NOT A REAL SCHOOL", styles["Normal"]),
             Spacer(1, 18), Paragraph(title, styles["Title"]), Spacer(1, 18)]
    for element in elements:
        if isinstance(element, list):
            table = Table(element, colWidths=[285, 150])
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5edf5")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#b6c4d1")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]))
            story.append(table)
        else:
            story.append(Paragraph(element, styles["BodyText"]))
        story.append(Spacer(1, 16))
    document.build(story)


def main():
    write_pdf("class_profile.pdf", "Example Dental College - Class Profile", [
        "Entering class: 2026. These invented figures exist solely to test document extraction.",
        [["Class profile measure", "Value"], ["Average overall GPA", "3.7"],
         ["Average science GPA", "3.6"], ["Entering class size", "100 students"]],
        "These are class averages, not minimum admission requirements.",
        "This document does not state DAT scores, shadowing hours, or application deadlines.",
    ])
    write_pdf("mission_values.pdf", "Example Dental College - Mission and Values", [
        "Mission", "Our mission is to improve oral health through service to underserved communities.",
        "Learning environment", "We value collaboration, ethical practice, and lifelong learning.",
        "Applicants are encouraged to describe experiences that demonstrate compassion and teamwork.",
        "This page provides no numeric GPA, DAT, or shadowing requirements.",
    ])
    image = Image.new("RGB", (1500, 700), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 100), "SYNTHETIC SCANNED TEST FIXTURE", fill="black", font_size=42)
    draw.text((80, 230), "Average overall GPA: 3.7", fill="black", font_size=54)
    canvas = Canvas(str(ROOT / "scanned_profile.pdf"), pagesize=letter)
    canvas.drawImage(ImageReader(image), 36, 430, width=540, height=252)
    canvas.save()


if __name__ == "__main__":
    main()
