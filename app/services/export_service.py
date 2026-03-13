import io
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from flask import current_app, send_file
from PIL import Image as PILImage


def _report_status_label(report):
    labels = {
        'draft': 'BORRADOR',
        'under_review': 'EN REVISION',
        'in_review': 'EN REVISION',
        'needs_info': 'ANTECEDENTES SOLICITADOS',
        'approved': 'APROBADO',
        'rejected': 'RECHAZADO',
        'paid': 'PAGADO',
    }
    return labels.get(report.status, (report.status or '').upper())


def _resolve_brand_logo_path(report):
    settings = (report.company.settings or {}) if report.company else {}
    logo_url = settings.get('brand_logo_url') or None
    if not logo_url or not logo_url.startswith('/static/'):
        return None

    static_relative = logo_url.replace('/static/', '', 1)
    logo_path = os.path.join(current_app.static_folder, static_relative)
    if not os.path.exists(logo_path):
        return None
    return logo_path


def _brand_logo_flowable(report, max_width=120, max_height=44):
    logo_path = _resolve_brand_logo_path(report)
    if not logo_path:
        return None

    try:
        with PILImage.open(logo_path) as pil_img:
            normalized = pil_img.convert('RGBA')
            width, height = normalized.size
            if not width or not height:
                return None

            scale = min(max_width / float(width), max_height / float(height), 1.0)
            target_w = max(width * scale, 1)
            target_h = max(height * scale, 1)

            buffer = io.BytesIO()
            normalized.save(buffer, format='PNG')
            buffer.seek(0)

        logo = Image(buffer, width=target_w, height=target_h)
        logo.hAlign = 'RIGHT'
        # Keep the buffer alive until the document is built.
        logo._image_buffer = buffer
        return logo
    except Exception:
        return None

def generate_report_pdf(report):
    """
    Genera un archivo PDF con el detalle del informe de rendición y sus comprobantes.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    Story = []
    styles = getSampleStyleSheet()
    
    # Custom Styles
    title_style = ParagraphStyle(
        'MainTitle',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=colors.HexColor('#4F46E5'),
        spaceAfter=8
    )

    header_title = Paragraph(f"Rendicion: {report.title}", title_style)
    brand_logo = _brand_logo_flowable(report)
    if brand_logo:
        header = Table(
            [[header_title, brand_logo]],
            colWidths=[doc.width - 130, 130],
        )
        header.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        Story.append(header)
    else:
        Story.append(header_title)

    Story.append(Paragraph(f"<b>Generado por:</b> {report.user.full_name}", styles['Normal']))
    Story.append(Paragraph(f"<b>Fecha de Solicitud:</b> {report.created_at.strftime('%d/%m/%Y')}", styles['Normal']))
    Story.append(Paragraph(f"<b>Estado:</b> {_report_status_label(report)}", styles['Normal']))
    Story.append(Paragraph(f"<b>Tipo:</b> {report.settlement_type_label}", styles['Normal']))
    Story.append(Paragraph(f"<b>Monto Total (CLP):</b> ${report.total_amount:,.0f} {report.currency}", styles['Normal']))
    Story.append(Spacer(1, 20))
    
    # Table Header
    data = [['Fecha', 'Comercio', 'Categoría', 'Monto']]
    
    # Table Data
    for exp in report.expenses.order_by('date').all():
        cat_name = exp.category.name if exp.category else 'N/A'
        merchant = exp.merchant or ('Vehículo particular' if exp.is_mileage else 'S/N')
        if exp.is_mileage and exp.distance_km is not None:
            merchant = f"{merchant} ({exp.distance_km:,.2f} km)"
        data.append([
            exp.date.strftime('%d/%m/%Y'),
            merchant,
            cat_name,
            f"USD {exp.amount:,.2f} (CLP ${exp.amount_clp:,.0f})" if exp.currency == 'USD' else f"${exp.amount:,.0f}"
        ])
        
    data.append(['', '', 'TOTAL CLP:', f"${report.total_amount:,.0f}"])
    
    # Stylize Table
    t = Table(data, colWidths=[80, 200, 150, 80])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F8FAFC')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#6B7280')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('GRID', (0, 0), (-1, -2), 1, colors.HexColor('#E2E8F0')),
        ('FONTNAME', (-2, -1), (-1, -1), 'Helvetica-Bold'), # Total row bold
        ('LINEABOVE', (0, -1), (-1, -1), 1.5, colors.HexColor('#4F46E5')), # Line above total
    ]))
    
    Story.append(t)
    doc.build(Story)
    
    buffer.seek(0)
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"Rendicion_{report.id}.pdf",
        mimetype='application/pdf'
    )
