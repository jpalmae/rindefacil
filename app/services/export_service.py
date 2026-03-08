import io
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from flask import send_file

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
        spaceAfter=20
    )
    
    # Title
    Story.append(Paragraph(f"Informe de Rendición: {report.title}", title_style))
    Story.append(Paragraph(f"<b>Generado por:</b> {report.user.full_name}", styles['Normal']))
    Story.append(Paragraph(f"<b>Fecha de Solicitud:</b> {report.created_at.strftime('%d/%m/%Y')}", styles['Normal']))
    Story.append(Paragraph(f"<b>Estado:</b> {report.status.upper()}", styles['Normal']))
    Story.append(Paragraph(f"<b>Monto Total:</b> ${report.total_amount:,.0f} {report.currency}", styles['Normal']))
    Story.append(Spacer(1, 20))
    
    # Table Header
    data = [['Fecha', 'Comercio', 'Categoría', 'Monto']]
    
    # Table Data
    for exp in report.expenses.order_by('date').all():
        cat_name = exp.category.name if exp.category else 'N/A'
        merchant = exp.merchant or 'S/N'
        data.append([
            exp.date.strftime('%d/%m/%Y'),
            merchant,
            cat_name,
            f"${exp.amount:,.0f}"
        ])
        
    data.append(['', '', 'TOTAL:', f"${report.total_amount:,.0f}"])
    
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
