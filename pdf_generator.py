"""
Генератор PDF з питаннями та відповідями
Використовує reportlab для створення гарно оформленого документу
"""
import os
from io import BytesIO

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak
    )
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


def generate_pdf(topic_title: str, questions: list) -> BytesIO:
    """
    Генерує PDF з питаннями та відповідями.
    
    questions — список sqlite3.Row з полями: question, answer, subtopic_title
    Повертає BytesIO об'єкт готового PDF
    """
    buffer = BytesIO()

    if not REPORTLAB_AVAILABLE:
        # Якщо reportlab не встановлено — повертаємо текстовий файл
        text = f"=== {topic_title} ===\n\n"
        for i, q in enumerate(questions, 1):
            sub = q["subtopic_title"] or "Загальне"
            text += f"[{sub}]\n"
            text += f"❓ {q['question']}\n"
            text += f"✅ {q['answer']}\n"
            text += "-" * 60 + "\n\n"
        buffer.write(text.encode("utf-8"))
        buffer.seek(0)
        return buffer

    # --- Стилі ---
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2*cm,
        leftMargin=2*cm,
        topMargin=2*cm,
        bottomMargin=2*cm,
    )

    styles = getSampleStyleSheet()

    style_title = ParagraphStyle(
        "CustomTitle",
        parent=styles["Title"],
        fontSize=20,
        textColor=colors.HexColor("#1e3a5f"),
        spaceAfter=6,
    )

    style_subtitle = ParagraphStyle(
        "SubTopic",
        parent=styles["Heading2"],
        fontSize=13,
        textColor=colors.HexColor("#2563eb"),
        spaceBefore=14,
        spaceAfter=4,
    )

    style_question = ParagraphStyle(
        "Question",
        parent=styles["Normal"],
        fontSize=11,
        textColor=colors.HexColor("#111827"),
        fontName="Helvetica-Bold",
        spaceBefore=10,
        spaceAfter=4,
        leading=16,
    )

    style_answer = ParagraphStyle(
        "Answer",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#374151"),
        leading=15,
        leftIndent=10,
        spaceAfter=8,
    )

    style_footer = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.grey,
        alignment=1,  # центр
    )

    # --- Збираємо контент ---
    story = []

    # Заголовок
    story.append(Paragraph(f"📚 {topic_title}", style_title))
    story.append(Paragraph(
        "Матеріали підготовлено командою Бухгалтерські курси | @buh_courses_bot",
        style_footer
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e5e7eb")))
    story.append(Spacer(1, 0.4*cm))

    # Групуємо питання за підтемами
    current_sub = None
    for i, q in enumerate(questions, 1):
        sub = q["subtopic_title"] or "Загальні питання"

        if sub != current_sub:
            current_sub = sub
            story.append(Spacer(1, 0.2*cm))
            story.append(Paragraph(f"▸ {sub}", style_subtitle))
            story.append(HRFlowable(
                width="100%", thickness=0.5,
                color=colors.HexColor("#bfdbfe"), spaceAfter=4
            ))

        story.append(Paragraph(f"❓  {q['question']}", style_question))
        story.append(Paragraph(q["answer"], style_answer))

    # Футер
    story.append(Spacer(1, 1*cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e5e7eb")))
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "© 2024 Бухгалтерські курси | Матеріал призначений лише для особистого використання",
        style_footer
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer
