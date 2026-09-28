"""Build a single Russian PDF handbook from the project's Markdown documentation."""
from pathlib import Path
import re
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table
from reportlab.platypus.tableofcontents import TableOfContents
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "ARM112_сопроводительная_документация.pdf"
DOCS = [
    ("README.md", "Обзор и быстрый запуск"),
    ("ARCHITECTURE.md", "Архитектура решения"),
    ("API.md", "API и форматы взаимодействия"),
    ("SECURITY.md", "Безопасность и разграничение доступа"),
    ("OPERATIONS.md", "Эксплуатация, резервное копирование и восстановление"),
    ("DDS_TRAINING.md", "Работа в кабинете ДДС"),
    ("STUDENT_GUIDE.md", "Руководство обучающегося"),
    ("SIP_PHONE_SETUP.md", "Настройка браузерной и аппаратной телефонии"),
    ("CALLS_AND_REPORTS.md", "Звонки, записи и отчётность"),
    ("GENERATION_COVERAGE.md", "Генерация сценариев и покрытие ЕКП"),
    ("PRONUNCIATION.md", "Произношение и TTS"),
    ("VALIDATION.md", "Проверки и валидация"),
    ("TRACEABILITY.md", "Матрица соответствия ТЗ"),
    ("UI.md", "Интерфейс и UX/UI"),
    ("RESOURCES.md", "Ресурсы и исходные материалы"),
]


def text(value: str) -> str:
    value = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = value.replace("**", "").replace("__", "")
    return escape(value).replace("\n", "<br/>")


def build_story(styles):
    story = [
        Spacer(1, 30 * mm),
        Paragraph("АРМ-112", styles["Title"]),
        Paragraph("Сопроводительная документация учебного тренажёра", styles["Cover"]),
        Spacer(1, 12 * mm),
        Paragraph("Проект dds · локальный учебный контур", styles["CoverSmall"]),
        Paragraph("Сформировано автоматически из актуальной документации репозитория", styles["CoverSmall"]),
        PageBreak(),
        Paragraph("Содержание", styles["Heading1"]),
        TableOfContents(),
        PageBreak(),
    ]
    for filename, title in DOCS:
        path = ROOT / "docs" / filename
        if not path.exists():
            continue
        story.append(Paragraph(title, styles["Heading1"]))
        story.append(Spacer(1, 2 * mm))
        lines = path.read_text(encoding="utf-8").splitlines()
        index = 0
        while index < len(lines):
            line = lines[index].rstrip()
            if not line:
                index += 1
                continue
            if line.startswith("```"):
                code = []
                index += 1
                while index < len(lines) and not lines[index].startswith("```"):
                    code.append(lines[index])
                    index += 1
                story.append(Paragraph(text("\n".join(code)), styles["CodeBlock"]))
                index += 1
                continue
            heading = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading:
                level = min(len(heading.group(1)) + 1, 4)
                story.append(Paragraph(text(heading.group(2)), styles[f"Heading{level}"]))
                index += 1
                continue
            if line.startswith("|") and index + 1 < len(lines) and lines[index + 1].startswith("|"):
                table_lines = []
                while index < len(lines) and lines[index].startswith("|"):
                    cells = [cell.strip() for cell in lines[index].strip("|").split("|")]
                    if not all(set(cell) <= set("-: ") for cell in cells):
                        table_lines.append([Paragraph(text(cell), styles["TableCell"]) for cell in cells])
                    index += 1
                if table_lines:
                    table = Table(table_lines, repeatRows=1, hAlign="LEFT")
                    table.setStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b8c2cc")),
                                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9eef3")),
                                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                                    ("RIGHTPADDING", (0, 0), (-1, -1), 4)])
                    story.extend([Spacer(1, 2 * mm), table, Spacer(1, 3 * mm)])
                continue
            if re.match(r"^\s*[-*+]\s+", line):
                line = "• " + re.sub(r"^\s*[-*+]\s+", "", line)
            elif re.match(r"^\s*\d+[.)]\s+", line):
                line = re.sub(r"^\s*", "", line)
            story.append(Paragraph(text(line), styles["BodyText"]))
            index += 1
        story.append(PageBreak())
    return story


def main() -> None:
    font = next((Path(item) for item in sys.argv[1:] if Path(item).exists()), None)
    if font is None:
        raise SystemExit("Russian TrueType font path is required")
    pdfmetrics.registerFont(TTFont("DocsSans", str(font)))
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = "DocsSans"
    styles.add(ParagraphStyle("Cover", parent=styles["Normal"], fontName="DocsSans", fontSize=18, leading=24, alignment=TA_CENTER, spaceAfter=8))
    styles.add(ParagraphStyle("CoverSmall", parent=styles["Normal"], fontName="DocsSans", fontSize=10, leading=15, alignment=TA_CENTER))
    styles.add(ParagraphStyle("CodeBlock", parent=styles["Code"], fontName="DocsSans", fontSize=7, leading=9, leftIndent=6, rightIndent=6, backColor=colors.HexColor("#f1f3f5")))
    styles.add(ParagraphStyle("TableCell", parent=styles["BodyText"], fontName="DocsSans", fontSize=7, leading=9))
    doc = SimpleDocTemplate(str(OUTPUT), pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm, topMargin=16 * mm, bottomMargin=16 * mm, title="АРМ-112 — сопроводительная документация", author="Проект dds")
    doc.multiBuild(build_story(styles))
    print(OUTPUT)


if __name__ == "__main__":
    main()
