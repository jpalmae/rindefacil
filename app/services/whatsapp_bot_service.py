"""Bot de WhatsApp para Rinde Fácil — máquina de estados por sesión.

Cada número de WhatsApp tiene una WhatsappSession con estado y datos de
borrador. El webhook (blueprint whatsapp_bot) despacha cada mensaje entrante
a handle_incoming_message().

Flujos:
- Vinculación: email → OTP por correo → cuenta vinculada
- Gastos: foto/texto → OCR → confirmación → ubicación → gasto creado
- Rendiciones: crear desde borradores, enviar a revisión
- Aprobaciones: listar pendientes, aprobar/rechazar/pedir antecedentes
"""
import logging
import re
import threading
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import current_app

from app.extensions import db
from app.models.category import Category
from app.models.company import Company
from app.models.expense import Expense, ExpenseCurrency, ExpenseStatus
from app.models.mfa_code import MfaCode
from app.models.report import Report, ReportSettlementType, ReportStatus
from app.models.user import User
from app.models.whatsapp import WhatsappSession
from app.services import kapso_service
from app.services.email_service import send_mfa_code_email
from app.services.ocr_service import extract_expense_data

logger = logging.getLogger(__name__)

MFA_PURPOSE_WHATSAPP = "whatsapp_link"

# Locks por teléfono para procesar mensajes secuencialmente por usuario
_phone_locks = {}
_phone_locks_guard = threading.Lock()


def _get_phone_lock(phone):
    with _phone_locks_guard:
        if phone not in _phone_locks:
            _phone_locks[phone] = threading.Lock()
        return _phone_locks[phone]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _fmt_amount(amount, currency):
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, TypeError):
        return str(amount)
    if currency == "CLP":
        return f"${int(value):,}".replace(",", ".")
    symbol = "S/" if currency == "PEN" else "US$"
    return f"{symbol} {value:,.2f}"


def _fmt_base(amount, base_currency):
    return _fmt_amount(amount, base_currency)


def _normalize_phone(raw):
    return re.sub(r"\D", "", str(raw or ""))


def _user_accounts_by_email(email):
    return (
        User.query
        .filter(db.func.lower(User.email) == email.strip().lower(), User.is_active.is_(True))
        .order_by(User.created_at.asc())
        .all()
    )


def _user_accounts_for_phone(phone):
    return (
        User.query
        .filter(User.phone == phone, User.is_active.is_(True))
        .order_by(User.created_at.asc())
        .all()
    )


def _get_session(phone):
    session = WhatsappSession.query.filter_by(phone=phone).first()
    if not session:
        session = WhatsappSession(phone=phone, state=WhatsappSession.STATE_IDLE, state_data={})
        db.session.add(session)
        db.session.commit()

    # Auto-vinculación: el teléfono ya está registrado en el perfil/por admin
    if not session.user_id:
        accounts = _user_accounts_for_phone(phone)
        if accounts:
            session.user_id = accounts[0].id
            session.active_company_id = accounts[0].company_id
            session.linked_at = datetime.now(timezone.utc)

    session.touch()
    db.session.commit()
    return session


def _set_state(session, state, **data):
    session.state = state
    session.state_data = data if data else {}
    session.touch()
    db.session.commit()


def _clear_state(session):
    _set_state(session, WhatsappSession.STATE_IDLE)


def _linked_user(session):
    if not session.user_id:
        return None
    user = User.query.get(session.user_id)
    if not user or not user.is_active:
        return None
    return user


def _user_accounts(session):
    """Todas las cuentas activas de la persona vinculada (multi-empresa)."""
    user = _linked_user(session)
    if not user:
        return []
    accounts = _user_accounts_by_email(user.email)
    return [u for u in accounts if u.phone == session.phone]


def _can_approve(user):
    return user.role in ("superadmin", "admin", "manager", "approver")


# ---------------------------------------------------------------------------
# Menú principal
# ---------------------------------------------------------------------------

MENU_ROW_EXPENSE = "menu_expense"
MENU_ROW_MY_EXPENSES = "menu_my_expenses"
MENU_ROW_REPORT = "menu_report"
MENU_ROW_MY_REPORTS = "menu_my_reports"
MENU_ROW_APPROVALS = "menu_approvals"
MENU_ROW_SWITCH_COMPANY = "menu_switch_company"
MENU_ROW_HELP = "menu_help"


def send_main_menu(session, greeting=None):
    user = _linked_user(session)
    if not user:
        return send_welcome_unlinked(session)

    accounts = _user_accounts(session)
    company_label = session.company.name if session.company else user.company.name

    rows = [
        {"id": MENU_ROW_EXPENSE, "title": "📷 Nuevo gasto", "description": "Sube una foto de tu boleta"},
        {"id": MENU_ROW_MY_EXPENSES, "title": "📋 Mis gastos", "description": "Gastos borrador y rechazados"},
        {"id": MENU_ROW_REPORT, "title": "📦 Nueva rendición", "description": "Junta gastos y envía a revisión"},
        {"id": MENU_ROW_MY_REPORTS, "title": "📊 Mis rendiciones", "description": "Estado de tus rendiciones"},
    ]
    if _can_approve(user):
        rows.append({
            "id": MENU_ROW_APPROVALS,
            "title": "✅ Pendientes por aprobar",
            "description": "Rendiciones que esperan tu decisión",
        })
    if len(accounts) > 1:
        rows.append({
            "id": MENU_ROW_SWITCH_COMPANY,
            "title": "🏢 Cambiar empresa",
            "description": f"Actual: {company_label}",
        })
    rows.append({"id": MENU_ROW_HELP, "title": "❓ Ayuda"})

    text = greeting or f"Hola {user.full_name.split()[0]} 👋\n¿Qué necesitas hacer en *{company_label}*?"
    return kapso_service.send_list(
        session.phone,
        text,
        "Ver opciones",
        [{"title": "Acciones", "rows": rows}],
        header="SixRinde",
        footer="Gestión de gastos",
    )


def send_welcome_unlinked(session):
    return kapso_service.send_buttons(
        session.phone,
        "Bienvenido a *SixRinde* 🤖\n\nGestiona tus gastos y rendiciones por WhatsApp.\n\nPara empezar, vincula tu cuenta con tu correo corporativo.",
        [("link_start", "Vincular mi cuenta"), ("help", "Más información")],
        header="SixRinde",
    )


HELP_TEXT = (
    "*SixRinde — Ayuda* 🤖\n\n"
    "Puedo ayudarte a:\n"
    "📷 *Nuevo gasto* — envía una foto de tu boleta y la leo por ti\n"
    "📋 *Mis gastos* — revisa tus gastos borrador\n"
    "📦 *Nueva rendición* — junta tus gastos y envíalos a aprobación\n"
    "📊 *Mis rendiciones* — consulta el estado\n"
    "✅ *Aprobaciones* — si eres aprobador, revisa pendientes\n\n"
    "Comandos rápidos: escribe *menu* para volver al menú o *cancelar* para salir de un flujo."
)


# ---------------------------------------------------------------------------
# Vinculación teléfono ↔ usuario
# ---------------------------------------------------------------------------

def _start_link(session):
    _set_state(session, WhatsappSession.STATE_LINK_EMAIL)
    return kapso_service.send_text(
        session.phone,
        "Vinculemos tu cuenta 📧\n\nEscribe el *correo* con el que ingresas a SixRinde y te enviaré un código de verificación.",
    )


def _handle_link_email(session, text):
    email = text.strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return kapso_service.send_text(session.phone, "Ese correo no parece válido. Intenta de nuevo (ej: nombre@empresa.com).")

    accounts = _user_accounts_by_email(email)
    if not accounts:
        return kapso_service.send_text(
            session.phone,
            "No encontré una cuenta activa con ese correo ❌\nVerifica que esté escrito correctamente o escríbelo otra vez.",
        )

    if len(accounts) > 1:
        companies = Company.query.filter(Company.id.in_([a.company_id for a in accounts])).all()
        by_id = {c.id: c for c in companies}
        rows = [
            {"id": f"linkco:{a.company_id}", "title": (by_id[a.company_id].name or "Empresa")[:24]}
            for a in accounts
        ]
        _set_state(session, WhatsappSession.STATE_LINK_EMAIL, email=email)
        return kapso_service.send_list(
            session.phone,
            "Tienes cuentas en varias empresas. ¿Cuál quieres usar?",
            "Elegir empresa",
            [{"title": "Empresas", "rows": rows}],
        )

    return _send_otp(session, accounts[0])


def _send_otp(session, user):
    # Invalidar códigos previos del mismo propósito
    MfaCode.query.filter_by(user_id=user.id, purpose=MFA_PURPOSE_WHATSAPP, consumed_at=None).update(
        {"consumed_at": datetime.now(timezone.utc)}
    )
    code, raw = MfaCode.build_for_user(user, MFA_PURPOSE_WHATSAPP)
    db.session.add(code)
    _set_state(session, WhatsappSession.STATE_LINK_OTP, email=user.email, user_id=str(user.id))
    send_mfa_code_email(user, user.company, raw, purpose=MFA_PURPOSE_WHATSAPP)
    return kapso_service.send_text(
        session.phone,
        f"Te envié un código de 6 dígitos a *{user.email}* 📬\n\nEscríbelo aquí para confirmar. (Vence en 10 minutos).",
    )


def _handle_link_otp(session, text):
    candidate_code = text.strip()
    if not re.fullmatch(r"\d{6}", candidate_code):
        if text.strip().lower() in ("cancelar", "cancel", "menu"):
            _clear_state(session)
            return send_main_menu(session)
        return kapso_service.send_text(session.phone, "Escribe el código de 6 dígitos que te llegó por correo (o *cancelar*).")

    user_id = session.state_data.get("user_id")
    user = User.query.get(user_id) if user_id else None
    if not user:
        _clear_state(session)
        return send_welcome_unlinked(session)

    code = (
        MfaCode.query
        .filter_by(user_id=user.id, purpose=MFA_PURPOSE_WHATSAPP, consumed_at=None)
        .order_by(MfaCode.created_at.desc())
        .first()
    )
    if not code or not code.matches(candidate_code):
        if code:
            code.attempts += 1
            db.session.commit()
        return kapso_service.send_text(session.phone, "Código incorrecto ❌ Intenta de nuevo.")

    if not code.is_valid:
        return kapso_service.send_text(session.phone, "El código expiró o superó los intentos. Escribe *vincular* para recibir uno nuevo.")

    code.consumed_at = datetime.now(timezone.utc)

    # Vincular: el teléfono queda en todas las cuentas activas con ese email
    accounts = _user_accounts_by_email(user.email)
    for account in accounts:
        account.phone = session.phone
    session.user_id = user.id
    session.active_company_id = user.company_id
    session.linked_at = datetime.now(timezone.utc)
    db.session.commit()

    _clear_state(session)
    return send_main_menu(session, greeting=f"Cuenta vinculada ✅\n\nBienvenido, *{user.full_name.split()[0]}*.")


def _handle_company_switch(session):
    accounts = _user_accounts(session)
    if len(accounts) < 2:
        return send_main_menu(session)
    companies = Company.query.filter(Company.id.in_([a.company_id for a in accounts])).all()
    by_id = {c.id: c for c in companies}
    rows = [
        {
            "id": f"swco:{a.company_id}",
            "title": (by_id[a.company_id].name or "Empresa")[:24],
            "description": "actual" if a.company_id == session.active_company_id else None,
        }
        for a in accounts
    ]
    return kapso_service.send_list(
        session.phone,
        "¿En qué empresa quieres trabajar?",
        "Cambiar empresa",
        [{"title": "Empresas", "rows": rows}],
    )


def _switch_company(session, company_id):
    accounts = _user_accounts(session)
    target = next((a for a in accounts if str(a.company_id) == str(company_id)), None)
    if not target:
        return send_main_menu(session)
    session.user_id = target.id
    session.active_company_id = target.company_id
    session.touch()
    db.session.commit()
    _clear_state(session)
    return send_main_menu(session, greeting=f"Ahora estás en *{target.company.name}* ✅")


# ---------------------------------------------------------------------------
# Entrada principal
# ---------------------------------------------------------------------------

def handle_incoming_message(payload):
    """Procesa un payload whatsapp.message.received de Kapso."""
    message = payload.get("message") or {}
    phone = _normalize_phone(message.get("from") or (payload.get("conversation") or {}).get("phone_number"))
    if not phone:
        logger.warning("Webhook WhatsApp sin teléfono: %s", payload)
        return

    lock = _get_phone_lock(phone)
    with lock:
        session = _get_session(phone)
        msg_type = message.get("type")

        try:
            if msg_type == "text":
                _handle_text(session, (message.get("text") or {}).get("body") or "")
            elif msg_type == "interactive":
                _handle_interactive(session, message.get("interactive") or {})
            elif msg_type == "location":
                _handle_location(session, message.get("location") or {})
            elif msg_type == "image":
                from app.services.whatsapp_flows import handle_image
                handle_image(session, message)
            elif msg_type == "button":
                _handle_text(session, (message.get("button") or {}).get("text") or "")
            elif msg_type == "audio":
                kapso_service.send_text(session.phone, "Por ahora no proceso audios 🎤 Envíame una foto de la boleta o escribe *menu*.")
            elif msg_type in ("video", "sticker", "contacts"):
                kapso_service.send_text(session.phone, "No puedo procesar ese tipo de mensaje. Envía una foto de la boleta o escribe *menu*.")
            else:
                kapso_service.send_text(session.phone, "No entendí ese mensaje 🤔 Escribe *menu* para ver opciones.")
        except Exception:
            logger.exception("Error procesando mensaje WhatsApp de %s", phone)
            db.session.rollback()
            try:
                kapso_service.send_text(session.phone, "Ocurrió un error inesperado 😕 Intenta de nuevo en un momento o escribe *menu*.")
            except Exception:
                logger.exception("Error notificando fallo por WhatsApp a %s", phone)


def _handle_text(session, text):
    text = (text or "").strip()
    low = text.lower()

    if not text:
        return

    # Comandos globales
    if low in ("menu", "inicio", "hola", "empezar"):
        _clear_state(session)
        return send_main_menu(session)
    if low == "ayuda":
        _clear_state(session)
        return kapso_service.send_text(session.phone, HELP_TEXT)
    if low in ("cancelar", "cancel", "salir"):
        _clear_state(session)
        return kapso_service.send_text(session.phone, "Flujo cancelado. Escribe *menu* para volver al menú principal.")
    if low in ("vincular", "vincular cuenta"):
        return _start_link(session)
    if low == "empresa":
        return _handle_company_switch(session)

    # Despacho por estado
    state = session.state

    if state == WhatsappSession.STATE_LINK_EMAIL:
        return _handle_link_email(session, text)
    if state == WhatsappSession.STATE_LINK_OTP:
        return _handle_link_otp(session, text)

    user = _linked_user(session)
    if not user:
        return _start_link(session)

    # Estados de flujos (gastos, rendiciones, aprobaciones)
    from app.services import whatsapp_flows
    return whatsapp_flows.handle_text_state(session, user, text)


def _handle_interactive(session, interactive):
    itype = interactive.get("type")
    if itype == "button_reply":
        button_id = (interactive.get("button_reply") or {}).get("id", "")
        title = (interactive.get("button_reply") or {}).get("title", "")
    elif itype == "list_reply":
        button_id = (interactive.get("list_reply") or {}).get("id", "")
        title = (interactive.get("list_reply") or {}).get("title", "")
    else:
        return

    # Botones globales
    if button_id == "help":
        return kapso_service.send_text(session.phone, HELP_TEXT)
    if button_id == "link_start":
        return _start_link(session)
    if button_id.startswith("linkco:"):
        company_id = button_id.split(":", 1)[1]
        account = next(
            (a for a in _user_accounts_by_email(session.state_data.get("email") or "")
             if str(a.company_id) == company_id),
            None,
        )
        if account:
            return _send_otp(session, account)
        return _start_link(session)
    if button_id.startswith("swco:"):
        return _switch_company(session, button_id.split(":", 1)[1])

    user = _linked_user(session)
    if not user:
        return _start_link(session)

    if button_id == MENU_ROW_HELP:
        return kapso_service.send_text(session.phone, HELP_TEXT)
    if button_id == MENU_ROW_SWITCH_COMPANY:
        return _handle_company_switch(session)

    from app.services import whatsapp_flows
    return whatsapp_flows.handle_action(session, user, button_id, title)


def _handle_location(session, location):
    user = _linked_user(session)
    if not user:
        return send_welcome_unlinked(session)
    from app.services import whatsapp_flows
    return whatsapp_flows.handle_location(session, user, location)
