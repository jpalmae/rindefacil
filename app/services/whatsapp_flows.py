"""Flujos conversacionales del bot de WhatsApp (gastos, rendiciones, aprobaciones)."""
import logging
import os
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import current_app

from app.extensions import db
from app.models.category import Category
from app.models.expense import Expense, ExpenseCurrency, ExpenseStatus
from app.models.report import Report, ReportSettlementType, ReportStatus
from app.models.whatsapp import WhatsappSession
from app.services import kapso_service
from app.services.exchange_rate_service import resolve_amount_in_base
from app.services.location_service import evaluate_expense_integrity, reverse_geocode
from app.services.ocr_service import calculate_receipt_hash, extract_expense_data
from app.services.report_workflow_service import (
    approve_report,
    pending_reports_for_approver,
    reject_report,
    request_report_info,
    submit_report,
)
from app.services.whatsapp_bot_service import (
    MENU_ROW_APPROVALS,
    MENU_ROW_EXPENSE,
    MENU_ROW_MY_EXPENSES,
    MENU_ROW_MY_REPORTS,
    MENU_ROW_REPORT,
    _can_approve,
    _clear_state,
    _fmt_amount,
    _set_state,
    send_main_menu,
)
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}

# Estados de flujo
EXP_OCR_CONFIRM = "exp_ocr_confirm"
EXP_EDIT_FIELD = "exp_edit_field"
EXP_AWAIT_LOCATION = "exp_await_location"
REP_TITLE = "rep_title"
REP_SETTLEMENT = "rep_settlement"
REP_CONFIRM = "rep_confirm"
APPR_REASON = "appr_reason"

REPORT_STATUS_LABELS = {
    ReportStatus.DRAFT: "📝 Borrador",
    ReportStatus.UNDER_REVIEW: "🕒 En revisión",
    ReportStatus.APPROVED: "✅ Aprobada",
    ReportStatus.REJECTED: "❌ Rechazada",
    ReportStatus.NEEDS_INFO: "ℹ️ Requiere antecedentes",
    ReportStatus.PAID: "💰 Pagada",
}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def handle_action(session, user, action_id, title):
    """Botones/listas del menú principal y de flujos."""
    if action_id == MENU_ROW_EXPENSE:
        return start_expense(session, user)
    if action_id == MENU_ROW_MY_EXPENSES:
        return show_my_expenses(session, user)
    if action_id == MENU_ROW_REPORT:
        return start_report(session, user)
    if action_id == MENU_ROW_MY_REPORTS:
        return show_my_reports(session, user)
    if action_id == MENU_ROW_APPROVALS:
        return show_pending_approvals(session, user)

    if action_id == "exp_confirm_ok":
        return ask_location(session, user)
    if action_id == "exp_confirm_edit":
        return ask_which_field(session, user)
    if action_id == "exp_confirm_cancel":
        _clear_state(session)
        return send_main_menu(session, greeting="Gasto descartado.")
    if action_id.startswith("exp_edit:"):
        return ask_field_value(session, user, action_id.split(":", 1)[1])
    if action_id.startswith("exp_editcat:"):
        category_id = action_id.split(":", 1)[1]
        category = Category.query.get(category_id)
        d = session.state_data.get("draft") or {}
        if category and category.company_id == user.company_id:
            d["category"] = category.name
            _set_state(session, EXP_OCR_CONFIRM, draft=d)
            return show_ocr_confirmation(session, user)
        return ask_which_field(session, user)
    if action_id.startswith("exp_loc_skip"):
        return None  # GPS obligatorio: no se ofrece skip
    if action_id == "rep_settle_reimburse":
        session.state_data["settlement_type"] = ReportSettlementType.EMPLOYEE_REIMBURSEMENT
        return confirm_report(session, user)
    if action_id == "rep_settle_card":
        session.state_data["settlement_type"] = ReportSettlementType.CORPORATE_CARD
        return confirm_report(session, user)
    if action_id == "rep_confirm_create":
        return create_report(session, user)
    if action_id == "rep_confirm_submit":
        return create_report(session, user, submit=True)
    if action_id == "rep_confirm_cancel":
        _clear_state(session)
        return send_main_menu(session, greeting="Rendición descartada.")
    if action_id.startswith("appr_open:"):
        return show_approval_detail(session, user, action_id.split(":", 1)[1])
    if action_id.startswith("appr_act:"):
        _, action, report_id = action_id.split(":", 2)
        return start_approval_action(session, user, action, report_id)
    if action_id == "appr_done":
        return show_pending_approvals(session, user)

    return send_main_menu(session)


def handle_text_state(session, user, text):
    """Entrada de texto cuando hay un flujo activo."""
    state = session.state
    if state == EXP_EDIT_FIELD:
        return receive_field_value(session, user, text)
    if state == REP_TITLE:
        return receive_report_title(session, user, text)
    if state == APPR_REASON:
        return receive_approval_reason(session, user, text)
    if state in (EXP_OCR_CONFIRM, EXP_AWAIT_LOCATION, REP_SETTLEMENT, REP_CONFIRM):
        return kapso_service.send_text(session.phone, "Usa los botones de arriba para continuar, o escribe *cancelar*.")
    # idle sin flujo: interpretar como posible gasto rápido no soportado
    return send_main_menu(session)


# ---------------------------------------------------------------------------
# Gastos: foto → OCR → confirmar → ubicación → crear
# ---------------------------------------------------------------------------

def handle_image(session, message):
    """Recibe una foto (o PDF) y arranca el flujo de gasto con OCR."""
    from app.services.whatsapp_bot_service import _linked_user

    user = _linked_user(session)
    if not user:
        from app.services.whatsapp_bot_service import send_welcome_unlinked
        return send_welcome_unlinked(session)

    media_data = ((message.get("kapso") or {}).get("media_data")) or {}
    media_url = media_data.get("url") or (message.get("kapso") or {}).get("media_url")
    if not media_url:
        return kapso_service.send_text(session.phone, "No pude descargar el archivo 😕 Intenta enviarlo de nuevo.")

    kapso_service.send_text(session.phone, "📷 Foto recibida. Leyendo la boleta…")

    try:
        content = kapso_service.download_media(media_url)
    except Exception:
        logger.exception("Error descargando media WhatsApp")
        return kapso_service.send_text(session.phone, "No pude descargar la imagen 😕 Envíala nuevamente.")

    ext = os.path.splitext(media_data.get("filename") or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        ext = ".jpg"
    unique_name = f"{user.company_id}_{uuid.uuid4().hex}_wa{ext}"
    file_path = os.path.join(current_app.config["UPLOAD_FOLDER"], unique_name)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "wb") as fh:
        fh.write(content)
    receipt_url = f"/static/uploads/{unique_name}"

    ocr = extract_expense_data(file_path) or {}

    draft = {
        "receipt_path": file_path,
        "receipt_url": receipt_url,
        "amount": ocr.get("amount"),
        "currency": (ocr.get("currency") or "").upper() or None,
        "merchant": ocr.get("merchant"),
        "date": ocr.get("date"),
        "time": ocr.get("time"),
        "category": ocr.get("category"),
        "description": None,
    }

    if draft["currency"] not in (user.company.allowed_expense_currencies or []):
        draft["currency"] = (user.company.base_currency or "CLP")

    _set_state(session, EXP_OCR_CONFIRM, draft=draft)
    return show_ocr_confirmation(session, user)


def _resolve_category(user, name):
    if not name:
        return None
    return Category.query.filter(
        Category.company_id == user.company_id,
        Category.is_active.is_(True),
        Category.name.ilike(f"%{name.strip()}%"),
    ).first()


def show_ocr_confirmation(session, user):
    d = session.state_data.get("draft") or {}
    category = _resolve_category(user, d.get("category") or "")

    lines = ["*Leí esto de tu boleta* 👇"]
    lines.append(f"💵 Monto: {_fmt_amount(d.get('amount') or 0, d.get('currency') or user.company.base_currency)}")
    if d.get("merchant"):
        lines.append(f"🏪 Comercio: {d['merchant']}")
    if d.get("date"):
        lines.append(f"📅 Fecha: {d['date']}")
    if d.get("time"):
        lines.append(f"🕐 Hora: {d['time']}")
    if category:
        lines.append(f"🏷️ Categoría: {category.name}")

    return kapso_service.send_buttons(
        session.phone,
        "\n".join(lines),
        [
            ("exp_confirm_ok", "✅ Correcto"),
            ("exp_confirm_edit", "✏️ Editar"),
            ("exp_confirm_cancel", "❌ Cancelar"),
        ],
        header="Nuevo gasto",
        footer="Confirma para continuar",
    )


def ask_which_field(session, user):
    _set_state(session, EXP_EDIT_FIELD, draft=session.state_data.get("draft") or {})
    rows = [
        {"id": "exp_edit:amount", "title": "Monto"},
        {"id": "exp_edit:currency", "title": "Moneda"},
        {"id": "exp_edit:date", "title": "Fecha"},
        {"id": "exp_edit:category", "title": "Categoría"},
        {"id": "exp_edit:description", "title": "Motivo"},
    ]
    return kapso_service.send_list(
        session.phone, "¿Qué quieres corregir?", "Elegir campo",
        [{"title": "Campos", "rows": rows}],
    )


def ask_field_value(session, user, field):
    _set_state(session, EXP_EDIT_FIELD, draft=session.state_data.get("draft") or {}, field=field)
    prompts = {
        "amount": "Escribe el monto total (solo números, ej: 12990 o 1250.50):",
        "currency": "Escribe la moneda: CLP, USD o PEN:",
        "date": "Escribe la fecha (DD/MM/AAAA):",
        "category": None,  # se maneja con lista abajo
        "description": "Describe el motivo del gasto (mínimo 15 caracteres):",
    }
    if field == "category":
        categories = (
            Category.query
            .filter_by(company_id=user.company_id, is_active=True)
            .order_by(Category.name)
            .limit(10)
            .all()
        )
        rows = [{"id": f"exp_editcat:{c.id}", "title": c.name[:24]} for c in categories]
        _set_state(session, EXP_EDIT_FIELD, draft=session.state_data.get("draft") or {}, field="__await")
        # las respuestas de lista llegan por handle_action con id exp_editcat:
        session.state_data["field"] = "category"
        db.session.commit()
        return kapso_service.send_list(session.phone, "Elige la categoría:", "Elegir", [{"title": "Categorías", "rows": rows}])

    return kapso_service.send_text(session.phone, prompts[field])


def receive_field_value(session, user, text):
    d = session.state_data.get("draft") or {}
    field = session.state_data.get("field")
    text = (text or "").strip()

    if field == "amount":
        try:
            value = Decimal(text.replace(".", "").replace(",", ".")) if ("," in text) else Decimal(text)
            if value <= 0:
                raise InvalidOperation
            d["amount"] = str(value)
        except (InvalidOperation, ValueError):
            return kapso_service.send_text(session.phone, "Monto no válido. Escribe solo números (ej: 12990):")
    elif field == "currency":
        cur = text.upper()
        if cur not in (user.company.allowed_expense_currencies or []):
            return kapso_service.send_text(session.phone, f"Moneda no permitida. Usa: {', '.join(user.company.allowed_expense_currencies)}")
        d["currency"] = cur
    elif field == "date":
        parsed = _parse_date_text(text)
        if not parsed:
            return kapso_service.send_text(session.phone, "Fecha no válida. Usa DD/MM/AAAA (ej: 07/09/2026):")
        d["date"] = parsed
    elif field == "description":
        if len(text) < 15:
            return kapso_service.send_text(session.phone, f"El motivo debe tener al menos 15 caracteres (llevas {len(text)}):")
        d["description"] = text

    _set_state(session, EXP_OCR_CONFIRM, draft=d)
    return show_ocr_confirmation(session, user)


def _parse_date_text(text):
    text = text.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return None


def ask_location(session, user):
    d = session.state_data.get("draft") or {}

    # Validaciones previas: monto, fecha y motivo
    try:
        amount = Decimal(str(d.get("amount") or ""))
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        _set_state(session, EXP_EDIT_FIELD, draft=d, field="amount")
        return kapso_service.send_text(session.phone, "No tengo un monto válido. Escríbelo (solo números):")

    if not d.get("date"):
        _set_state(session, EXP_EDIT_FIELD, draft=d, field="date")
        return kapso_service.send_text(session.phone, "No tengo la fecha. Escríbela (DD/MM/AAAA):")

    if not d.get("description"):
        _set_state(session, EXP_EDIT_FIELD, draft=d, field="description")
        return kapso_service.send_text(session.phone, "Falta el motivo del gasto (mínimo 15 caracteres):")

    _set_state(session, EXP_AWAIT_LOCATION, draft=d)
    return kapso_service.send_location_request(
        session.phone,
        "Por política de la empresa necesito tu ubicación al momento del gasto 📍\nToca el botón para compartirla.",
    )


def handle_location(session, user, location):
    if session.state != EXP_AWAIT_LOCATION:
        return send_main_menu(session)

    d = session.state_data.get("draft") or {}
    d["latitude"] = location.get("latitude")
    d["longitude"] = location.get("longitude")
    d["gps_address"] = (location.get("address") or location.get("name") or "").strip() or None
    _set_state(session, EXP_AWAIT_LOCATION, draft=d)
    return create_expense(session, user)


def create_expense(session, user):
    d = session.state_data.get("draft") or {}
    phone = session.phone

    try:
        amount = Decimal(str(d["amount"]))
    except (InvalidOperation, KeyError):
        return kapso_service.send_text(phone, "Error interno con el monto. Escribe *menu* para empezar de nuevo.")

    currency = d.get("currency") or user.company.base_currency or "CLP"
    base_currency = user.company.base_currency or ExpenseCurrency.CLP
    expense_date = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            expense_date = datetime.strptime(d.get("date"), fmt).date()
            break
        except (ValueError, TypeError):
            continue
    if not expense_date:
        return kapso_service.send_text(phone, "Error interno con la fecha. Escribe *menu*.")

    category = _resolve_category(user, d.get("category") or "")
    if not category:
        categories = Category.query.filter_by(company_id=user.company_id, is_active=True).order_by(Category.name).limit(10).all()
        rows = [{"id": f"exp_editcat:{c.id}", "title": c.name[:24]} for c in categories]
        _set_state(session, EXP_EDIT_FIELD, draft=d, field="category")
        return kapso_service.send_list(phone, "Elige una categoría para el gasto:", "Elegir", [{"title": "Categorías", "rows": rows}])

    receipt_time = None
    if d.get("time"):
        try:
            receipt_time = datetime.strptime(d["time"], "%H:%M").time()
        except ValueError:
            receipt_time = None

    amount_base, exchange_rate = resolve_amount_in_base(user.company, currency, amount)
    if amount_base is None:
        return kapso_service.send_text(
            phone,
            f"No pude obtener el tipo de cambio {currency}→{base_currency} 😕 Intenta más tarde o registra el gasto en la web.",
        )

    gps_lat = Decimal(str(d.get("latitude")))
    gps_lon = Decimal(str(d.get("longitude")))
    gps_address = d.get("gps_address")
    if not gps_address:
        geocode = reverse_geocode(float(gps_lat), float(gps_lon))
        gps_address = (geocode or {}).get("display_name") or f"Lat {gps_lat}, Lon {gps_lon}"

    geo_validation = evaluate_expense_integrity(
        merchant=d.get("merchant"),
        address=gps_address,
        receipt_date=expense_date,
        receipt_time=receipt_time,
        tz_name=user.company.timezone,
    )

    expense = Expense(
        user_id=user.id,
        company_id=user.company_id,
        status=ExpenseStatus.DRAFT,
        amount=amount,
        currency=currency,
        exchange_rate=exchange_rate,
        amount_clp=amount_base,
        merchant=d.get("merchant"),
        date=expense_date,
        receipt_time=receipt_time,
        category_id=category.id,
        description=d.get("description"),
        receipt_url=d.get("receipt_url"),
        gps_latitude=gps_lat.quantize(Decimal("0.0000001")),
        gps_longitude=gps_lon.quantize(Decimal("0.0000001")),
        gps_captured_at=datetime.now(timezone.utc),
        gps_address=gps_address,
        gps_validation_status=geo_validation["status"],
        gps_validation_score=geo_validation["score"],
        gps_validation_reason=geo_validation["reason"],
        gps_validation_meta={
            "matched_tokens": geo_validation.get("matched_tokens", []),
            "components": geo_validation.get("components", []),
            "source": "whatsapp",
        },
    )

    receipt_path = d.get("receipt_path")
    if receipt_path and os.path.exists(receipt_path):
        expense.receipt_hash = calculate_receipt_hash(receipt_path)
        if expense.receipt_hash:
            dup = Expense.query.filter(
                Expense.company_id == user.company_id,
                Expense.receipt_hash == expense.receipt_hash,
            ).first()
            if dup:
                expense.is_duplicate = True
                expense.duplicate_of_id = dup.id

    if not expense.is_duplicate:
        same = Expense.query.filter(
            Expense.company_id == user.company_id,
            Expense.amount == amount,
            Expense.currency == currency,
            Expense.date == expense_date,
            Expense.user_id == user.id,
        ).first()
        if same:
            expense.is_duplicate = True
            expense.duplicate_of_id = same.id

    db.session.add(expense)
    db.session.commit()

    warnings = ""
    if expense.is_duplicate:
        warnings = "\n\n⚠️ *Atención:* este comprobante parece duplicado de uno anterior."

    _clear_state(session)
    total_line = f"{_fmt_amount(amount, currency)}"
    if currency != base_currency:
        total_line += f" (≈ {_fmt_amount(amount_base, base_currency)})"
    kapso_service.send_text(
        phone,
        f"✅ *Gasto creado*\n\n{total_line} — {category.name}{warnings}\n\nSigue agregando gastos o escribe *menu*.",
    )
    return start_expense(session, user, new_hint=True)


def start_expense(session, user, new_hint=False):
    _clear_state(session)
    return kapso_service.send_text(
        session.phone,
        "Envíame la *foto de la boleta* 📷 y la leo por ti.\n\n(También puedes enviar un PDF).",
    )


def show_my_expenses(session, user):
    expenses = (
        Expense.query
        .filter(
            Expense.user_id == user.id,
            Expense.status.in_([ExpenseStatus.DRAFT, ExpenseStatus.REJECTED]),
            Expense.report_id.is_(None),
        )
        .order_by(Expense.date.desc())
        .limit(10)
        .all()
    )
    if not expenses:
        return kapso_service.send_text(session.phone, "No tienes gastos borrador 📭 Envíame una foto de boleta para crear uno.")

    base = user.company.base_currency or "CLP"
    lines = ["*Tus gastos disponibles* (borrador/rechazados):", ""]
    total = Decimal("0")
    for exp in expenses:
        total += exp.amount_clp or 0
        cat = exp.category.name if exp.category else "Sin categoría"
        lines.append(f"• {_fmt_amount(exp.amount, exp.currency)} — {cat} — {exp.date.strftime('%d/%m')}")
    lines.append("")
    lines.append(f"*Total: {_fmt_amount(total, base)}*")
    lines.append("")
    lines.append("Para rendirlos: *menu* → 📦 Nueva rendición")
    return kapso_service.send_text(session.phone, "\n".join(lines))


# ---------------------------------------------------------------------------
# Rendiciones
# ---------------------------------------------------------------------------

def start_report(session, user):
    expenses = (
        Expense.query
        .filter(
            Expense.user_id == user.id,
            Expense.status.in_([ExpenseStatus.DRAFT, ExpenseStatus.REJECTED]),
            Expense.report_id.is_(None),
        )
        .order_by(Expense.date.desc())
        .all()
    )
    if not expenses:
        return kapso_service.send_text(
            session.phone,
            "No tienes gastos borrador para rendir 📭 Primero crea gastos enviándome fotos de tus boletas.",
        )

    _set_state(session, REP_TITLE, expense_ids=[str(e.id) for e in expenses])
    total = sum((e.amount_clp or Decimal("0") for e in expenses), Decimal("0"))
    return kapso_service.send_text(
        session.phone,
        f"Tienes *{len(expenses)} gastos* por un total de {_fmt_amount(total, user.company.base_currency or 'CLP')}.\n\n"
        "Escribe un *título* para la rendición (ej: Gastos visita cliente Antofagasta):",
    )


def receive_report_title(session, user, text):
    title = (text or "").strip()
    if len(title) < 5:
        return kapso_service.send_text(session.phone, "El título es muy corto (mínimo 5 caracteres). Escribe otro:")

    data = dict(session.state_data or {})
    data["title"] = title
    _set_state(session, REP_SETTLEMENT, **data)
    return kapso_service.send_buttons(
        session.phone,
        "¿Cómo se pagarán estos gastos?",
        [
            ("rep_settle_reimburse", "💸 Reembolso a mí"),
            ("rep_settle_card", "💳 Tarjeta corporativa"),
        ],
        header="Tipo de liquidación",
    )


def confirm_report(session, user):
    data = session.state_data or {}
    ids = data.get("expense_ids") or []
    expenses = Expense.query.filter(Expense.id.in_(ids)).all() if ids else []
    total = sum((e.amount_clp or Decimal("0") for e in expenses), Decimal("0"))
    settle = "Reembolso" if data.get("settlement_type") == ReportSettlementType.EMPLOYEE_REIMBURSEMENT else "Tarjeta corporativa"
    _set_state(session, REP_CONFIRM, **data)

    return kapso_service.send_buttons(
        session.phone,
        f"*{data.get('title')}*\n{len(expenses)} gastos — {_fmt_amount(total, user.company.base_currency or 'CLP')}\nTipo: {settle}\n\n¿Qué hago?",
        [
            ("rep_confirm_create", "💾 Crear borrador"),
            ("rep_confirm_submit", "🚀 Crear y enviar"),
            ("rep_confirm_cancel", "❌ Cancelar"),
        ],
    )


def create_report(session, user, submit=False):
    data = session.state_data or {}
    ids = data.get("expense_ids") or []
    expenses = Expense.query.filter(Expense.id.in_(ids)).all() if ids else []
    if not expenses:
        _clear_state(session)
        return kapso_service.send_text(session.phone, "Los gastos ya no están disponibles. Escribe *menu*.")

    report = Report(
        user_id=user.id,
        company_id=user.company_id,
        title=data.get("title"),
        settlement_type=data.get("settlement_type") or ReportSettlementType.EMPLOYEE_REIMBURSEMENT,
        status=ReportStatus.DRAFT,
        total_amount=sum((e.amount_clp or Decimal("0") for e in expenses), Decimal("0")),
    )
    db.session.add(report)
    db.session.flush()
    for exp in expenses:
        exp.report_id = report.id
        if exp.status == ExpenseStatus.REJECTED:
            exp.status = ExpenseStatus.DRAFT
    db.session.commit()

    _clear_state(session)
    msg = f"💾 Rendición *{report.public_id}* creada como borrador."
    if submit:
        ok, result = submit_report(report, user)
        msg = f"💾 Rendición *{report.public_id}* creada.\n\n{result}"
    kapso_service.send_text(session.phone, msg)
    return send_main_menu(session)


def show_my_reports(session, user):
    reports = (
        Report.query
        .filter_by(user_id=user.id)
        .order_by(Report.created_at.desc())
        .limit(8)
        .all()
    )
    if not reports:
        return kapso_service.send_text(session.phone, "No tienes rendiciones todavía 📭 Crea una desde *menu* → 📦 Nueva rendición.")

    lines = ["*Tus rendiciones:*", ""]
    for rep in reports:
        label = REPORT_STATUS_LABELS.get(rep.status, rep.status)
        lines.append(f"{label} — {rep.title[:30]} — {_fmt_amount(rep.total_amount, user.company.base_currency or 'CLP')}")
        if rep.status == ReportStatus.NEEDS_INFO:
            lines.append("   ↳ responde antecedentes desde la web o escribe *menu*")
    return kapso_service.send_text(session.phone, "\n".join(lines))


# ---------------------------------------------------------------------------
# Aprobaciones
# ---------------------------------------------------------------------------

def show_pending_approvals(session, user):
    if not _can_approve(user):
        return kapso_service.send_text(session.phone, "No tienes permisos de aprobación 👤")

    pending = pending_reports_for_approver(user)
    if not pending:
        return kapso_service.send_text(session.phone, "No tienes rendiciones pendientes de aprobación 🎉")

    rows = []
    for rep in pending[:8]:
        requester = rep.user.full_name.split()[0] if rep.user else "?"
        rows.append({
            "id": f"appr_open:{rep.id}",
            "title": f"{rep.public_id} — {requester}",
            "description": f"{_fmt_amount(rep.total_amount, rep.company.base_currency or 'CLP')} — {rep.title[:30]}",
        })
    _clear_state(session)
    return kapso_service.send_list(
        session.phone,
        f"Tienes *{len(pending)} rendiciones* esperando tu decisión:",
        "Revisar",
        [{"title": "Pendientes", "rows": rows}],
    )


def show_approval_detail(session, user, report_id):
    report = Report.query.get(report_id)
    if not report or report.company_id != user.company_id:
        return kapso_service.send_text(session.phone, "La rendición ya no está disponible. Escribe *menu*.")

    requester = report.user.full_name if report.user else "?"
    lines = [
        f"*{report.title}*",
        f"👤 {requester}",
        f"🧾 {report.public_id} — {REPORT_STATUS_LABELS.get(report.status, report.status)}",
        f"💵 Total: {_fmt_amount(report.total_amount, report.company.base_currency or 'CLP')}",
        "",
        "*Gastos incluidos:*",
    ]
    for exp in report.expenses[:10]:
        cat = exp.category.name if exp.category else "—"
        lines.append(f"• {_fmt_amount(exp.amount, exp.currency)} — {cat} — {exp.date.strftime('%d/%m')}")
    if len(report.expenses) > 10:
        lines.append(f"… y {len(report.expenses) - 10} más")

    _set_state(session, APPR_REASON, action=None, report_id=str(report.id))
    return kapso_service.send_buttons(
        session.phone,
        "\n".join(lines),
        [
            ("appr_act:approve:" + str(report.id), "✅ Aprobar"),
            ("appr_act:reject:" + str(report.id), "❌ Rechazar"),
            ("appr_act:info:" + str(report.id), "ℹ️ Pedir antecedentes"),
            ("appr_done", "⏭️ Siguiente"),
        ],
        header="Revisar rendición",
    )


def start_approval_action(session, user, action, report_id):
    if action == "approve":
        report = Report.query.get(report_id)
        ok, msg = approve_report(report, user) if report else (False, "Rendición no encontrada.")
        _clear_state(session)
        kapso_service.send_text(session.phone, msg)
        return show_pending_approvals(session, user)

    prompts = {
        "reject": "Escribe el *motivo del rechazo* (quedará registrado y se notificará al solicitante):",
        "info": "Escribe *qué antecedentes adicionales* necesitas (se notificará al solicitante):",
    }
    _set_state(session, APPR_REASON, action=action, report_id=report_id)
    return kapso_service.send_text(session.phone, prompts[action])


def receive_approval_reason(session, user, text):
    data = session.state_data or {}
    action = data.get("action")
    report_id = data.get("report_id")
    reason = (text or "").strip()

    if not action or not report_id:
        _clear_state(session)
        return send_main_menu(session)
    if len(reason) < 5:
        return kapso_service.send_text(session.phone, "Sé un poco más específico (mínimo 5 caracteres):")

    report = Report.query.get(report_id)
    _clear_state(session)
    if not report:
        return kapso_service.send_text(session.phone, "La rendición ya no está disponible.")

    if action == "reject":
        ok, msg = reject_report(report, user, reason)
    elif action == "info":
        ok, msg = request_report_info(report, user, reason)
    else:
        ok, msg = False, "Acción desconocida."

    kapso_service.send_text(session.phone, msg)
    return show_pending_approvals(session, user)
