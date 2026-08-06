import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from uuid import UUID

import jwt
from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import (
    ApprovalDecision,
    ApprovalFlow,
    ApprovalStep,
    AuditLog,
    Category,
    Expense,
    ExpenseCurrency,
    ExpenseStatus,
    Policy,
    Report,
    ReportSettlementType,
    ReportStatus,
    User,
    UserApiKey,
    UserRole,
)
from app.services.location_service import evaluate_expense_integrity, reverse_geocode
from app.services.notification_service import (
    notify_approval_needed,
    notify_report_created,
    notify_report_approved,
    notify_report_info_requested,
    notify_report_paid,
    notify_report_rejected,
    notify_report_submitted,
)
from app.services.ocr_service import calculate_receipt_hash, extract_expense_data


api_bp = Blueprint("api", __name__)

ALLOWED_RECEIPT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".pdf"}
REVIEW_STATUSES = {ReportStatus.UNDER_REVIEW, "in_review"}
EDITABLE_REPORT_STATUSES = {ReportStatus.DRAFT, ReportStatus.NEEDS_INFO}
FINANCE_VISIBLE_STATUSES = {ReportStatus.APPROVED, ReportStatus.PAID}


def _ok(data=None, status=200):
    payload = {"ok": True}
    if data is not None:
        payload["data"] = data
    return jsonify(payload), status


def _error(message, status=400, code="bad_request", details=None):
    err = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return jsonify({"ok": False, "error": err}), status


def _is_admin_like(user):
    return user.role in {UserRole.SUPERADMIN, UserRole.ADMIN, UserRole.MANAGER}


def _parse_amount(value, currency=None):
    if value is None:
        return None

    if isinstance(value, Decimal):
        return value

    if isinstance(value, (int, float)):
        return Decimal(str(value))

    raw = str(value).strip()
    if not raw:
        return None

    cleaned = re.sub(r"[^\d,.\-]", "", raw)
    if not cleaned:
        return None

    negative = cleaned.startswith("-")
    cleaned = cleaned.replace("-", "")
    if not cleaned:
        return None

    if currency == ExpenseCurrency.CLP:
        last_separator = max(cleaned.rfind("."), cleaned.rfind(","))
        if last_separator != -1:
            right = cleaned[last_separator + 1:]
            left = cleaned[:last_separator]
            if right.isdigit() and len(right) <= 2:
                cleaned = left
        normalized = re.sub(r"[.,]", "", cleaned)
        if negative:
            normalized = f"-{normalized}"
        if not normalized or normalized == "-":
            return None
        try:
            return Decimal(normalized)
        except InvalidOperation:
            return None

    if "." in cleaned and "," in cleaned:
        last_dot = cleaned.rfind(".")
        last_comma = cleaned.rfind(",")
        decimal_sep = "." if last_dot > last_comma else ","
        thousand_sep = "," if decimal_sep == "." else "."
        normalized = cleaned.replace(thousand_sep, "").replace(decimal_sep, ".")
    elif cleaned.count(".") > 1 and "," not in cleaned:
        normalized = cleaned.replace(".", "")
    elif cleaned.count(",") > 1 and "." not in cleaned:
        normalized = cleaned.replace(",", "")
    elif "." in cleaned and "," not in cleaned:
        left, right = cleaned.split(".", 1)
        if len(right) == 3 and len(left) >= 1:
            normalized = f"{left}{right}"
        else:
            normalized = cleaned
    elif "," in cleaned and "." not in cleaned:
        left, right = cleaned.split(",", 1)
        if len(right) == 3 and len(left) >= 1:
            normalized = f"{left}{right}"
        else:
            normalized = cleaned.replace(",", ".")
    else:
        normalized = cleaned

    if negative:
        normalized = f"-{normalized}"

    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _decimal_as_text(value):
    amount = _parse_amount(value)
    if amount is None:
        return None

    normalized = amount.normalize()
    if normalized == normalized.to_integral():
        return str(int(normalized))
    return format(normalized, "f")


def _parse_date(value):
    if not value:
        return None

    if isinstance(value, date):
        return value

    raw = str(value).strip()
    if not raw:
        return None

    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse_datetime(value):
    if not value:
        return None

    if isinstance(value, datetime):
        return value

    raw = str(value).strip()
    if not raw:
        return None

    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_coordinate(value, min_value, max_value):
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        return None

    if parsed < Decimal(str(min_value)) or parsed > Decimal(str(max_value)):
        return None

    return parsed


def _parse_non_negative_decimal(value):
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        return None

    if parsed < 0:
        return None

    return parsed


def _normalize_currency(value):
    currency = (str(value).strip().upper() if value is not None else ExpenseCurrency.CLP) or ExpenseCurrency.CLP
    if currency not in ExpenseCurrency.CHOICES:
        return None
    return currency


def _compute_amount_clp(amount, currency, exchange_rate):
    if amount is None:
        return None
    if currency == ExpenseCurrency.CLP:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if exchange_rate is None or exchange_rate <= 0:
        return None
    return (amount * exchange_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _parse_time(value):
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    normalized = raw.replace(".", ":")
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(normalized, fmt).time()
        except ValueError:
            continue
    return None


def _data_dict():
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict(flat=True)


def _uuid_or_none(value):
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _ensure_auth_header():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    return auth_header.split(" ", 1)[1].strip()


def _serialize_user(user):
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "company_id": str(user.company_id),
        "is_active": bool(user.is_active),
        "permissions": {
            "can_view_approved_reports": bool(user.can_view_approved_reports),
            "can_mark_reimbursements_paid": bool(user.can_mark_reimbursements_paid),
            "has_finance_report_access": bool(user.has_finance_report_access),
        },
    }


def _serialize_expense(expense):
    return {
        "id": str(expense.id),
        "public_id": expense.public_id,
        "user_id": str(expense.user_id),
        "company_id": str(expense.company_id),
        "report_id": str(expense.report_id) if expense.report_id else None,
        "amount": _decimal_as_text(expense.amount),
        "currency": expense.currency,
        "exchange_rate": _decimal_as_text(expense.exchange_rate),
        "amount_clp": _decimal_as_text(expense.amount_clp),
        "date": expense.date.isoformat() if expense.date else None,
        "receipt_time": expense.receipt_time.isoformat() if expense.receipt_time else None,
        "merchant": expense.merchant,
        "client_partner": expense.client_partner,
        "description": expense.description,
        "status": expense.status,
        "category": {
            "id": str(expense.category.id),
            "name": expense.category.name,
        }
        if expense.category
        else None,
        "receipt_url": expense.receipt_url,
        "location": {
            "latitude": _decimal_as_text(expense.gps_latitude),
            "longitude": _decimal_as_text(expense.gps_longitude),
            "accuracy_m": _decimal_as_text(expense.gps_accuracy_m),
            "captured_at": expense.gps_captured_at.isoformat() if expense.gps_captured_at else None,
            "address": expense.gps_address,
        },
        "geo_validation": {
            "status": expense.gps_validation_status,
            "score": _decimal_as_text(expense.gps_validation_score),
            "reason": expense.gps_validation_reason,
            "meta": expense.gps_validation_meta or {},
        },
        "is_duplicate": bool(expense.is_duplicate),
        "duplicate_of_id": str(expense.duplicate_of_id) if expense.duplicate_of_id else None,
        "created_at": expense.created_at.isoformat() if expense.created_at else None,
        "updated_at": expense.updated_at.isoformat() if expense.updated_at else None,
    }


def _serialize_report(report, expense_count=None, include_expenses=False, include_decisions=False):
    data = {
        "id": str(report.id),
        "public_id": report.public_id,
        "company_id": str(report.company_id),
        "user": {
            "id": str(report.user.id),
            "full_name": report.user.full_name,
            "email": report.user.email,
        }
        if report.user
        else None,
        "title": report.title,
        "description": report.description,
        "status": report.status,
        "settlement_type": report.settlement_type,
        "settlement_type_label": report.settlement_type_label,
        "total_amount": _decimal_as_text(report.total_amount),
        "currency": report.currency,
        "approval_flow_id": str(report.approval_flow_id) if report.approval_flow_id else None,
        "current_step": report.current_step,
        "expense_count": expense_count if expense_count is not None else report.expenses.count(),
        "submitted_at": report.submitted_at.isoformat() if report.submitted_at else None,
        "approved_at": report.approved_at.isoformat() if report.approved_at else None,
        "paid_at": report.paid_at.isoformat() if report.paid_at else None,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "updated_at": report.updated_at.isoformat() if report.updated_at else None,
    }

    latest_info_request = next(
        (decision for decision in report.decisions if decision.decision == "info_requested"),
        None,
    ) if include_decisions else None
    if latest_info_request:
        data["latest_info_request"] = {
            "user_id": str(latest_info_request.user_id),
            "user_name": latest_info_request.user.full_name if latest_info_request.user else None,
            "step_number": latest_info_request.step_number,
            "comments": latest_info_request.comments,
            "decided_at": latest_info_request.decided_at.isoformat() if latest_info_request.decided_at else None,
        }

    if include_expenses:
        expenses = report.expenses.order_by(Expense.created_at.desc()).all()
        data["expenses"] = [_serialize_expense(exp) for exp in expenses]

    if include_decisions:
        data["decisions"] = [
            {
                "id": str(decision.id),
                "user_id": str(decision.user_id),
                "user_name": decision.user.full_name if decision.user else None,
                "step_number": decision.step_number,
                "decision": decision.decision,
                "comments": decision.comments,
                "decided_at": decision.decided_at.isoformat() if decision.decided_at else None,
            }
            for decision in report.decisions
        ]

    return data


def _save_receipt(file_storage, company_id):
    filename = secure_filename(file_storage.filename or "receipt")
    if not filename:
        filename = "receipt"

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_RECEIPT_EXTENSIONS:
        return None, None, _error(
            "Formato de comprobante no permitido. Usa PNG, JPG, JPEG, WEBP o PDF.",
            status=415,
            code="unsupported_media_type",
        )

    # Normalizar extensión según el contenido real (evita PDF con .png, etc.)
    from app.services.upload_service import detect_extension, normalize_filename
    detected_ext = detect_extension(file_storage)
    if detected_ext and detected_ext not in ALLOWED_RECEIPT_EXTENSIONS:
        return None, None, _error(
            "El contenido del archivo no es un formato permitido (PNG, JPG, JPEG, WEBP o PDF).",
            status=415,
            code="unsupported_media_type",
        )
    filename = normalize_filename(filename, detected_ext)

    unique_name = f"{company_id}_{uuid.uuid4().hex}_{filename}"
    file_path = os.path.join(current_app.config["UPLOAD_FOLDER"], unique_name)
    file_storage.save(file_path)
    return file_path, f"/static/uploads/{unique_name}", None


def _extract_receipt_data_from_file(file_storage, user):
    filename = secure_filename(file_storage.filename or "receipt")
    if not filename:
        filename = "receipt"

    # Normalizar extensión según contenido (consistencia con _save_receipt).
    from app.services.upload_service import detect_extension, normalize_filename
    detected_ext = detect_extension(file_storage)
    filename = normalize_filename(filename, detected_ext)

    temp_dir = os.path.join(current_app.config["UPLOAD_FOLDER"], "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f"tmp_ocr_{user.id}_{uuid.uuid4().hex}_{filename}")
    file_storage.save(temp_path)

    try:
        data = extract_expense_data(temp_path) or {}
        if data.get("amount") is not None:
            normalized = _decimal_as_text(data.get("amount"))
            if normalized is not None:
                data["amount"] = normalized

        if data.get("category"):
            category = Category.query.filter(
                Category.company_id == user.company_id,
                Category.name.ilike(f"%{data['category']}%"),
            ).first()
            if category:
                data["category_id"] = str(category.id)

        return data
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                current_app.logger.warning("No se pudo eliminar archivo temporal OCR: %s", temp_path)


def _append_duplicate_flags(expense):
    if expense.id is not None and expense.duplicate_of_id == expense.id:
        expense.is_duplicate = False
        expense.duplicate_of_id = None

    if expense.receipt_url:
        local_path = os.path.join(current_app.root_path, expense.receipt_url.lstrip("/"))
        if os.path.exists(local_path):
            receipt_hash = calculate_receipt_hash(local_path)
            if receipt_hash:
                expense.receipt_hash = receipt_hash
                existing = Expense.query.filter(
                    Expense.company_id == expense.company_id,
                    Expense.receipt_hash == receipt_hash,
                    Expense.id != expense.id,
                ).first()
                if existing and existing.id != expense.id:
                    expense.is_duplicate = True
                    expense.duplicate_of_id = existing.id

    if not expense.is_duplicate:
        existing_data = Expense.query.filter(
            Expense.company_id == expense.company_id,
            Expense.amount == expense.amount,
            Expense.currency == expense.currency,
            Expense.date == expense.date,
            Expense.id != expense.id,
        ).first()
        if existing_data and existing_data.id != expense.id:
            expense.is_duplicate = True
            expense.duplicate_of_id = existing_data.id

    if expense.id is not None and expense.duplicate_of_id == expense.id:
        expense.is_duplicate = False
        expense.duplicate_of_id = None


def _policy_warnings_for_expense(expense):
    warnings = []
    company_policy = Policy.query.filter_by(company_id=expense.company_id, is_active=True).first()
    if not company_policy:
        return warnings

    rules = company_policy.rules or {}
    max_amount = rules.get("max_amount")
    if max_amount is not None:
        try:
            if expense.amount_clp > Decimal(str(max_amount)):
                warnings.append(
                    f"El monto equivalente en CLP excede el maximo permitido por politica ({_decimal_as_text(max_amount)})."
                )
        except (InvalidOperation, ValueError):
            pass

    return warnings


def _select_approval_flow(company_id, total_amount):
    flows = ApprovalFlow.query.filter_by(company_id=company_id, is_active=True).all()
    if not flows:
        return None

    eligible = []
    total = Decimal(str(total_amount or 0))
    for flow in flows:
        rules = flow.trigger_rules or {}
        min_amount = Decimal(str(rules.get("min_amount", 0) or 0))
        if total >= min_amount:
            eligible.append((min_amount, len(flow.steps), flow))

    if not eligible:
        return None

    eligible.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return eligible[0][2]


def _current_step(report):
    if not report.approval_flow_id or not report.current_step:
        return None

    return ApprovalStep.query.filter_by(
        flow_id=report.approval_flow_id,
        step_number=report.current_step,
    ).first()


def _step_requires_missing_manager(report, step):
    return step and step.approver_type == "manager" and not (report.user and report.user.manager_id)


def _resolve_active_step(report, persist=False):
    if not report.approval_flow_id:
        return None, []

    steps_by_number = {step.step_number: step for step in report.approval_flow.steps}
    current_number = report.current_step or 1
    skipped_steps = []

    while True:
        current_step_obj = steps_by_number.get(current_number)
        if not current_step_obj:
            if persist and current_number != report.current_step:
                report.current_step = current_number
            return None, skipped_steps

        if _step_requires_missing_manager(report, current_step_obj):
            skipped_steps.append(current_number)
            current_number += 1
            continue

        if persist and current_number != report.current_step:
            report.current_step = current_number
        return current_step_obj, skipped_steps


def _user_can_review_step(user, report, step):
    if step is None:
        return False

    if user.role in {UserRole.SUPERADMIN, UserRole.ADMIN}:
        return True

    if step.approver_type == "role":
        return user.has_role(step.approver_target)

    if step.approver_type == "user":
        return str(user.id) == str(step.approver_target)

    if step.approver_type == "manager":
        return report.user and report.user.manager_id == user.id

    return False


def _user_can_view_report(user, report):
    if report.company_id != user.company_id:
        return False
    if report.user_id == user.id or _is_admin_like(user):
        return True
    return user.has_finance_report_access and report.status in FINANCE_VISIBLE_STATUSES


def _user_can_mark_report_paid(user, report):
    return (
        report.company_id == user.company_id
        and user.can_process_reimbursements
        and report.status == ReportStatus.APPROVED
        and report.settlement_type == ReportSettlementType.EMPLOYEE_REIMBURSEMENT
    )


def _notify_step_if_needed(report, step):
    if step is None:
        return

    if step.approver_type == "role":
        approvers = User.query.filter_by(company_id=report.company_id, role=step.approver_target).all()
        for approver in approvers:
            notify_approval_needed(approver.id, report)
    elif step.approver_type == "user":
        notify_approval_needed(step.approver_target, report)
    elif step.approver_type == "manager" and report.user and report.user.manager_id:
        notify_approval_needed(report.user.manager_id, report)


def _audit(user, action, entity_type=None, entity_id=None, description=None, changes=None):
    entry = AuditLog(
        company_id=user.company_id,
        user_id=user.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        changes=changes,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string if request.user_agent else None,
    )
    db.session.add(entry)


def api_auth_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        token = _ensure_auth_header()
        if not token:
            return _error("Token Bearer requerido.", status=401, code="unauthorized")

        # API Key authentication (generated from profile): rfk_...
        if token.startswith("rfk_"):
            key_hash = UserApiKey.hash_raw_key(token)
            api_key = UserApiKey.query.filter_by(key_hash=key_hash).first()
            if not api_key or api_key.revoked_at is not None:
                return _error("API key invalida o revocada.", status=401, code="invalid_api_key")

            user = db.session.get(User, api_key.user_id)
            if not user or not user.is_active:
                return _error("Usuario no autorizado.", status=401, code="unauthorized")
            if user.company_id != api_key.company_id:
                return _error("API key invalida para la empresa del usuario.", status=401, code="invalid_api_key")

            api_key.last_used_at = datetime.utcnow()
            db.session.commit()

            g.api_user = user
            g.auth_type = "api_key"
            g.api_key_id = str(api_key.id)
            return view_func(*args, **kwargs)

        try:
            payload = jwt.decode(
                token,
                current_app.config["SECRET_KEY"],
                algorithms=["HS256"],
            )
        except jwt.ExpiredSignatureError:
            return _error("Token expirado.", status=401, code="token_expired")
        except jwt.InvalidTokenError:
            return _error("Token invalido.", status=401, code="invalid_token")

        user_id = _uuid_or_none(payload.get("sub"))
        if user_id is None:
            return _error("Token invalido.", status=401, code="invalid_token")

        user = db.session.get(User, user_id)
        if not user or not user.is_active:
            return _error("Usuario no autorizado.", status=401, code="unauthorized")

        g.api_user = user
        g.token_payload = payload
        g.auth_type = "jwt"
        return view_func(*args, **kwargs)

    return wrapped


@api_bp.route("/health", methods=["GET"])
def health():
    return _ok({"status": "ok", "service": "rinde-api"})


@api_bp.route("/auth/token", methods=["POST"])
def create_token():
    data = _data_dict()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return _error("Debes enviar email y password.", status=400, code="validation_error")

    user = User.query.filter(func.lower(User.email) == email).first()
    if not user or not user.check_password(password):
        return _error("Credenciales invalidas.", status=401, code="invalid_credentials")

    if not user.is_active:
        return _error("Usuario inactivo.", status=403, code="inactive_user")

    now = datetime.now(timezone.utc)
    expires_in_seconds = 60 * 60 * 12
    payload = {
        "sub": str(user.id),
        "company_id": str(user.company_id),
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
    }
    token = jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm="HS256")

    return _ok(
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": expires_in_seconds,
            "user": _serialize_user(user),
        }
    )


@api_bp.route("/me", methods=["GET"])
@api_auth_required
def me():
    return _ok(_serialize_user(g.api_user))


@api_bp.route("/categories", methods=["GET"])
@api_auth_required
def categories_list():
    categories = Category.query.filter_by(company_id=g.api_user.company_id, is_active=True).order_by(Category.name.asc()).all()
    return _ok([
        {
            "id": str(category.id),
            "name": category.name,
            "icon": category.icon,
            "account_code": category.account_code,
        }
        for category in categories
    ])


@api_bp.route("/expenses/analyze", methods=["POST"])
@api_auth_required
def analyze_expense_receipt():
    if "receipt" not in request.files:
        return _error("Debes adjuntar el archivo 'receipt'.", status=400, code="validation_error")

    file_storage = request.files["receipt"]
    if not file_storage or not file_storage.filename:
        return _error("Debes seleccionar una imagen o PDF.", status=400, code="validation_error")

    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_RECEIPT_EXTENSIONS:
        return _error(
            "Formato de comprobante no permitido. Usa PNG, JPG, JPEG, WEBP o PDF.",
            status=415,
            code="unsupported_media_type",
        )

    # Validar tipo real por contenido (no por extensión del filename).
    from app.services.upload_service import detect_extension
    detected_ext = detect_extension(file_storage)
    if detected_ext and detected_ext not in ALLOWED_RECEIPT_EXTENSIONS:
        return _error(
            "El contenido del archivo no es un formato permitido (PNG, JPG, JPEG, WEBP o PDF).",
            status=415,
            code="unsupported_media_type",
        )

    extracted = _extract_receipt_data_from_file(file_storage, g.api_user)
    if not extracted:
        return _error(
            "No se detectaron datos utiles en el comprobante.",
            status=422,
            code="ocr_no_data",
        )

    return _ok({"extracted": extracted})


@api_bp.route("/expenses", methods=["GET"])
@api_auth_required
def expenses_list():
    user = g.api_user
    limit = request.args.get("limit", default=20, type=int) or 20
    offset = request.args.get("offset", default=0, type=int) or 0
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    query = Expense.query.options(selectinload(Expense.category)).order_by(Expense.created_at.desc())

    if _is_admin_like(user):
        query = query.filter(Expense.company_id == user.company_id)
    else:
        query = query.filter(Expense.user_id == user.id)

    status_filter = request.args.get("status")
    if status_filter:
        query = query.filter(Expense.status == status_filter)

    report_id = _uuid_or_none(request.args.get("report_id"))
    if report_id:
        query = query.filter(Expense.report_id == report_id)

    total = query.count()
    expenses = query.offset(offset).limit(limit).all()

    return _ok(
        {
            "items": [_serialize_expense(expense) for expense in expenses],
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
            },
        }
    )


@api_bp.route("/expenses", methods=["POST"])
@api_auth_required
def expenses_create():
    user = g.api_user
    data = _data_dict()

    analyze_receipt = str(data.get("analyze_receipt", "true")).lower() in {"1", "true", "yes", "on"}

    currency = _normalize_currency(data.get("currency"))
    amount = _parse_amount(data.get("amount"), currency=currency)
    exchange_rate = _parse_non_negative_decimal(data.get("exchange_rate"))
    merchant = (data.get("merchant") or "").strip() or None
    client_partner = (data.get("client_partner") or "").strip() or None
    description = (data.get("description") or "").strip()
    date_value = _parse_date(data.get("date"))
    receipt_time = _parse_time(data.get("receipt_time"))
    category_id = _uuid_or_none(data.get("category_id"))
    gps_latitude_raw = data.get("gps_latitude")
    gps_longitude_raw = data.get("gps_longitude")
    gps_accuracy_raw = data.get("gps_accuracy_m")
    gps_address_raw = data.get("gps_address")
    gps_address = str(gps_address_raw).strip() if gps_address_raw is not None else ""
    gps_address = gps_address or None
    gps_captured_at = _parse_datetime(data.get("gps_captured_at")) or datetime.now(timezone.utc)
    ocr_data = None

    receipt_file = request.files.get("receipt")
    receipt_path = None
    receipt_url = None

    if receipt_file and receipt_file.filename:
        receipt_path, receipt_url, file_error = _save_receipt(receipt_file, user.company_id)
        if file_error:
            return file_error

        if analyze_receipt:
            ocr_data = extract_expense_data(receipt_path) or {}
            if ocr_data:
                if amount is None and ocr_data.get("amount") is not None:
                    amount = _parse_amount(ocr_data.get("amount"), currency=currency)

                if not merchant:
                    merchant = (ocr_data.get("merchant") or "").strip() or None

                if not date_value:
                    date_value = _parse_date(ocr_data.get("date"))

                if receipt_time is None:
                    receipt_time = _parse_time(ocr_data.get("time"))

                if category_id is None and ocr_data.get("category"):
                    found_category = Category.query.filter(
                        Category.company_id == user.company_id,
                        Category.name.ilike(f"%{ocr_data['category']}%"),
                    ).first()
                    if found_category:
                        category_id = found_category.id

    if amount is None or amount <= 0:
        return _error("Monto invalido. Envia 'amount' o una boleta legible para OCR.", status=422, code="validation_error")

    if currency is None:
        return _error("Debes enviar una currency valida (CLP o USD).", status=422, code="validation_error")

    if currency == ExpenseCurrency.USD and (exchange_rate is None or exchange_rate <= 0):
        return _error(
            "Debes enviar exchange_rate valido para gastos en USD.",
            status=422,
            code="validation_error",
        )

    if currency == ExpenseCurrency.CLP:
        exchange_rate = Decimal("1")

    amount_clp = _compute_amount_clp(amount, currency, exchange_rate)
    if amount_clp is None or amount_clp <= 0:
        return _error("No fue posible calcular el monto en CLP.", status=422, code="validation_error")

    if not date_value:
        return _error("Fecha invalida. Usa formato YYYY-MM-DD.", status=422, code="validation_error")

    if not description:
        description = "Rendicion enviada por API"

    if len(description) < 15:
        return _error("La descripcion debe tener al menos 15 caracteres.", status=422, code="validation_error")

    if gps_latitude_raw is None or gps_longitude_raw is None:
        return _error(
            "Debes enviar gps_latitude y gps_longitude para crear un gasto.",
            status=422,
            code="validation_error",
        )

    gps_latitude = _parse_coordinate(gps_latitude_raw, -90, 90)
    gps_longitude = _parse_coordinate(gps_longitude_raw, -180, 180)
    gps_accuracy_m = _parse_non_negative_decimal(gps_accuracy_raw)

    if gps_latitude is None or gps_longitude is None:
        return _error(
            "Las coordenadas GPS no son validas.",
            status=422,
            code="validation_error",
        )

    resolved_address = gps_address
    if not resolved_address:
        geocode_result = reverse_geocode(float(gps_latitude), float(gps_longitude))
        resolved_address = geocode_result.get("display_name") if geocode_result else None
    if not resolved_address:
        resolved_address = f"Lat {gps_latitude}, Lon {gps_longitude}"

    geo_validation = evaluate_expense_integrity(
        merchant=merchant,
        address=resolved_address,
        accuracy_m=gps_accuracy_m,
        receipt_date=date_value,
        rendered_at=gps_captured_at,
        receipt_time=receipt_time,
        time_tolerance_minutes=20,
    )

    if category_id is not None:
        category = Category.query.filter_by(id=category_id, company_id=user.company_id).first()
        if not category:
            return _error("Categoria invalida para esta empresa.", status=422, code="validation_error")
    else:
        return _error(
            "Debes indicar una categoria (category_id). Si usas OCR, anade el campo tras detectarlo.",
            status=422,
            code="validation_error",
        )

    try:
        expense = Expense(
            user_id=user.id,
            company_id=user.company_id,
            amount=amount,
            currency=currency,
            exchange_rate=exchange_rate,
            amount_clp=amount_clp,
            merchant=merchant,
            client_partner=client_partner,
            date=date_value,
            receipt_time=receipt_time,
            category_id=category_id,
            description=description,
            status=ExpenseStatus.DRAFT,
            receipt_url=receipt_url,
            ocr_raw_data=ocr_data,
            gps_latitude=gps_latitude,
            gps_longitude=gps_longitude,
            gps_accuracy_m=gps_accuracy_m,
            gps_captured_at=gps_captured_at,
            gps_address=resolved_address,
            gps_validation_status=geo_validation["status"],
            gps_validation_score=geo_validation["score"],
            gps_validation_reason=geo_validation["reason"],
            gps_validation_meta={
                "matched_tokens": geo_validation.get("matched_tokens", []),
                "components": geo_validation.get("components", []),
            },
        )

        db.session.add(expense)
        db.session.flush()

        _append_duplicate_flags(expense)
        warnings = _policy_warnings_for_expense(expense)
        if geo_validation["status"] == "mismatch":
            if geo_validation.get("reason") == "receipt_date_mismatch":
                warnings.append("La fecha de la boleta no coincide con la fecha de rendicion.")
            elif geo_validation.get("reason") == "receipt_time_mismatch":
                warnings.append("La hora de la boleta no coincide con la hora de rendicion (margen 20 min).")
            elif geo_validation.get("reason") == "weekend_submission":
                warnings.append("Gasto en fin de semana: indicador de riesgo potencial.")
            elif geo_validation.get("reason") == "outside_business_hours":
                warnings.append("Gasto fuera del horario habitual (L-V 09:00 a 19:00).")
            else:
                warnings.append("Ubicacion GPS no coincide con el comercio informado.")
        elif geo_validation["status"] == "partial":
            if geo_validation.get("reason") == "weekend_submission":
                warnings.append("Gasto en fin de semana: indicador de riesgo potencial.")
            elif geo_validation.get("reason") == "outside_business_hours":
                warnings.append("Gasto fuera del horario habitual (L-V 09:00 a 19:00).")
            elif geo_validation.get("reason") == "outside_business_hours_near":
                warnings.append("Gasto levemente fuera del horario habitual (L-V 09:00 a 19:00).")
            else:
                warnings.append("Coincidencia parcial en validaciones de ubicacion/fecha/hora.")

        _audit(
            user,
            action="api_expense_created",
            entity_type="expense",
            entity_id=expense.id,
            description=f"Gasto creado via API por {_decimal_as_text(expense.amount)} {expense.currency} (CLP {_decimal_as_text(expense.amount_clp)})",
        )

        db.session.commit()

        return _ok(
            {
                "expense": _serialize_expense(expense),
                "warnings": warnings,
                "ocr": {"applied": bool(ocr_data), "raw": ocr_data},
            },
            status=201,
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Error creando gasto via API: %s", exc)
        return _error("No se pudo crear el gasto.", status=500, code="server_error")


@api_bp.route("/reports", methods=["GET"])
@api_auth_required
def reports_list():
    user = g.api_user
    limit = request.args.get("limit", default=20, type=int) or 20
    offset = request.args.get("offset", default=0, type=int) or 0
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    query = Report.query.options(joinedload(Report.user)).order_by(Report.created_at.desc())
    if _is_admin_like(user):
        query = query.filter(Report.company_id == user.company_id)
    elif user.has_finance_report_access:
        query = query.filter(
            Report.company_id == user.company_id,
            or_(
                Report.user_id == user.id,
                Report.status.in_(FINANCE_VISIBLE_STATUSES),
            ),
        )
    else:
        query = query.filter(Report.user_id == user.id)

    status_filter = request.args.get("status")
    if status_filter:
        query = query.filter(Report.status == status_filter)

    total = query.count()
    reports = query.offset(offset).limit(limit).all()

    report_ids = [report.id for report in reports]
    expense_counts = {}
    if report_ids:
        expense_counts = {
            report_id: count
            for report_id, count in db.session.query(Expense.report_id, func.count(Expense.id))
            .filter(Expense.report_id.in_(report_ids))
            .group_by(Expense.report_id)
            .all()
        }

    return _ok(
        {
            "items": [
                _serialize_report(report, expense_count=expense_counts.get(report.id, 0))
                for report in reports
            ],
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
            },
        }
    )


@api_bp.route("/reports", methods=["POST"])
@api_auth_required
def reports_create():
    user = g.api_user
    data = _data_dict()

    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip() or None
    settlement_type = (data.get("settlement_type") or ReportSettlementType.EMPLOYEE_REIMBURSEMENT).strip()

    if request.is_json:
        expense_ids_raw = (request.get_json(silent=True) or {}).get("expense_ids") or []
    else:
        expense_ids_raw = request.form.getlist("expense_ids")
        if not expense_ids_raw and data.get("expense_ids"):
            expense_ids_raw = [item.strip() for item in str(data.get("expense_ids")).split(",") if item.strip()]

    if not title:
        return _error("Debes enviar 'title'.", status=422, code="validation_error")

    if not Report.is_valid_settlement_type(settlement_type):
        return _error("Debes enviar un settlement_type valido.", status=422, code="validation_error")

    if not expense_ids_raw:
        return _error("Debes enviar al menos un expense_id.", status=422, code="validation_error")

    expense_ids = []
    for raw_id in expense_ids_raw:
        parsed = _uuid_or_none(raw_id)
        if parsed:
            expense_ids.append(parsed)

    if not expense_ids:
        return _error("No se recibieron expense_ids validos.", status=422, code="validation_error")

    expenses = (
        Expense.query.filter(
            Expense.id.in_(expense_ids),
            Expense.user_id == user.id,
            Expense.report_id.is_(None),
            Expense.status.in_([ExpenseStatus.DRAFT, ExpenseStatus.REJECTED]),
        )
        .order_by(Expense.created_at.asc())
        .all()
    )

    if not expenses:
        return _error("No se encontraron gastos elegibles para rendicion.", status=422, code="validation_error")

    try:
        report = Report(
            company_id=user.company_id,
            user_id=user.id,
            title=title,
            description=description,
            status=ReportStatus.DRAFT,
            settlement_type=settlement_type,
        )
        db.session.add(report)
        db.session.flush()

        total = Decimal("0")
        for expense in expenses:
            expense.report_id = report.id
            total += Decimal(str(expense.amount_clp or expense.amount or 0))

        report.total_amount = total

        _audit(
            user,
            action="api_report_created",
            entity_type="report",
            entity_id=report.id,
            description=f"Rendicion creada via API con {len(expenses)} gastos",
        )

        db.session.commit()
        notify_report_created(report)

        return _ok({"report": _serialize_report(report, expense_count=len(expenses))}, status=201)
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Error creando rendicion via API: %s", exc)
        return _error("No se pudo crear la rendicion.", status=500, code="server_error")


@api_bp.route("/reports/pending-approvals", methods=["GET"])
@api_auth_required
def reports_pending_approvals():
    user = g.api_user
    reports = (
        Report.query.options(joinedload(Report.user))
        .filter(Report.company_id == user.company_id, Report.status.in_(list(REVIEW_STATUSES)))
        .order_by(Report.created_at.desc())
        .all()
    )

    actionable = []
    for report in reports:
        step, _ = _resolve_active_step(report, persist=False)
        if _user_can_review_step(user, report, step):
            actionable.append(
                {
                    **_serialize_report(report),
                    "current_step_detail": {
                        "step_number": step.step_number if step else None,
                        "approver_type": step.approver_type if step else None,
                        "approver_target": step.approver_target if step else None,
                    },
                }
            )

    return _ok({"items": actionable, "total": len(actionable)})


@api_bp.route("/reports/<uuid:report_id>", methods=["GET"])
@api_auth_required
def reports_detail(report_id):
    user = g.api_user
    report = (
        Report.query.options(
            joinedload(Report.user),
            joinedload(Report.approval_flow).selectinload(ApprovalFlow.steps),
            selectinload(Report.decisions).joinedload(ApprovalDecision.user),
        )
        .filter(Report.id == report_id)
        .first()
    )
    if not report:
        return _error("Rendicion no encontrada.", status=404, code="not_found")

    if not _user_can_view_report(user, report):
        return _error("No tienes permisos para ver esta rendicion.", status=403, code="forbidden")

    _resolve_active_step(report, persist=True)

    return _ok(
        {
            "report": _serialize_report(report, include_expenses=True, include_decisions=True),
        }
    )


@api_bp.route("/reports/<uuid:report_id>/submit", methods=["POST"])
@api_auth_required
def reports_submit(report_id):
    user = g.api_user
    data = _data_dict()
    report = Report.query.options(joinedload(Report.user)).filter(Report.id == report_id).first()
    if not report:
        return _error("Rendicion no encontrada.", status=404, code="not_found")

    if report.company_id != user.company_id:
        return _error("No tienes permisos para enviar esta rendicion.", status=403, code="forbidden")

    if report.user_id != user.id and not _is_admin_like(user):
        return _error("Solo el solicitante o admin puede enviar esta rendicion.", status=403, code="forbidden")

    if report.status not in EDITABLE_REPORT_STATUSES:
        return _error(
            "Solo se pueden enviar rendiciones en borrador o con antecedentes solicitados.",
            status=409,
            code="invalid_state",
        )

    try:
        response_comment = (data.get("response_comment") or data.get("comment") or "").strip()
        is_resubmitting_info = report.status == ReportStatus.NEEDS_INFO
        if is_resubmitting_info:
            if not response_comment:
                return _error(
                    "Debes indicar qué antecedentes adicionales estás entregando en 'response_comment'.",
                    status=422,
                    code="validation_error",
                )
            if not report.approval_flow_id or not report.current_step:
                return _error(
                    "La rendicion no tiene un paso de aprobacion valido para retomar la revision.",
                    status=409,
                    code="invalid_state",
                )
            decision = ApprovalDecision(
                report_id=report.id,
                user_id=user.id,
                step_number=report.current_step,
                decision="info_submitted",
                comments=response_comment,
            )
            db.session.add(decision)
            selected_flow = report.approval_flow
        else:
            selected_flow = _select_approval_flow(report.company_id, report.total_amount)
            if not selected_flow or not selected_flow.steps:
                db.session.rollback()
                return _error(
                    "No existe un flujo de aprobacion activo para esta rendicion. Se mantiene en borrador.",
                    status=409,
                    code="no_approval_flow",
                )

            report.approval_flow_id = selected_flow.id
            report.current_step = 1
        report.status = ReportStatus.UNDER_REVIEW
        report.submitted_at = datetime.utcnow()

        for expense in report.expenses:
            expense.status = ExpenseStatus.SUBMITTED

        step, skipped_steps = _resolve_active_step(report, persist=True)
        if step:
            _notify_step_if_needed(report, step)
            action_msg = (
                "Antecedentes adicionales reenviados al mismo aprobador."
                if is_resubmitting_info
                else f"Rendicion enviada a flujo '{selected_flow.name}'."
            )
            if skipped_steps and not is_resubmitting_info:
                action_msg += " Se omitió el paso de manager porque el solicitante no tiene manager asignado."
        else:
            report.status = ReportStatus.APPROVED
            report.approved_at = datetime.utcnow()
            for expense in report.expenses:
                expense.status = ExpenseStatus.APPROVED
            action_msg = "La rendicion quedo aprobada automaticamente porque no habia aprobadores disponibles en el flujo."

        _audit(
            user,
            action="api_report_resubmitted_with_info" if is_resubmitting_info else "api_report_submitted",
            entity_type="report",
            entity_id=report.id,
            description=action_msg,
        )

        db.session.commit()
        if step:
            notify_report_submitted(report)
        else:
            notify_report_approved(report)

        return _ok({"message": action_msg, "report": _serialize_report(report)})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Error enviando rendicion via API: %s", exc)
        return _error("No se pudo enviar la rendicion.", status=500, code="server_error")


@api_bp.route("/reports/<uuid:report_id>/approve", methods=["POST"])
@api_auth_required
def reports_approve(report_id):
    user = g.api_user
    data = _data_dict()
    comment = (data.get("comment") or "").strip()

    report = Report.query.options(joinedload(Report.user)).filter(Report.id == report_id).first()
    if not report:
        return _error("Rendicion no encontrada.", status=404, code="not_found")

    if report.company_id != user.company_id:
        return _error("No tienes permisos para aprobar esta rendicion.", status=403, code="forbidden")

    if report.status in {ReportStatus.APPROVED, ReportStatus.REJECTED, ReportStatus.PAID}:
        return _error("La rendicion ya fue resuelta.", status=409, code="invalid_state")

    if report.status not in REVIEW_STATUSES:
        return _error("La rendicion no esta actualmente en revision.", status=409, code="invalid_state")

    if not report.approval_flow_id:
        if not _is_admin_like(user):
            return _error("No tienes permisos para aprobar esta rendicion.", status=403, code="forbidden")

        try:
            report.status = ReportStatus.APPROVED
            report.approved_at = datetime.utcnow()
            for expense in report.expenses:
                expense.status = ExpenseStatus.APPROVED

            _audit(
                user,
                action="api_report_approved",
                entity_type="report",
                entity_id=report.id,
                description="Rendicion aprobada sin flujo.",
            )
            db.session.commit()
            notify_report_approved(report)
            return _ok({"message": "Rendicion aprobada.", "report": _serialize_report(report)})
        except Exception as exc:
            db.session.rollback()
            current_app.logger.error("Error aprobando rendicion via API: %s", exc)
            return _error("No se pudo aprobar la rendicion.", status=500, code="server_error")

    step, _ = _resolve_active_step(report, persist=True)
    if not _user_can_review_step(user, report, step):
        return _error("No eres el aprobador designado para este paso.", status=403, code="forbidden")

    existing = ApprovalDecision.query.filter_by(
        report_id=report.id,
        user_id=user.id,
        step_number=report.current_step,
        decision="approved",
    ).first()
    if existing:
        return _ok({"message": "Aprobacion ya registrada previamente.", "report": _serialize_report(report)})

    try:
        decision = ApprovalDecision(
            report_id=report.id,
            user_id=user.id,
            step_number=report.current_step,
            decision="approved",
            comments=comment,
        )
        db.session.add(decision)

        report.current_step += 1
        next_step, skipped_steps = _resolve_active_step(report, persist=True)

        if next_step:
            report.status = ReportStatus.UNDER_REVIEW
            _notify_step_if_needed(report, next_step)
            message = (
                "Paso aprobado. Se omitió un paso de manager sin asignación y se notificó al siguiente aprobador."
                if skipped_steps
                else "Paso aprobado. Se notifico al siguiente aprobador."
            )
        else:
            report.status = ReportStatus.APPROVED
            report.approved_at = datetime.utcnow()
            for expense in report.expenses:
                expense.status = ExpenseStatus.APPROVED
            notify_report_approved(report)
            message = "Aprobacion final completada."

        _audit(
            user,
            action="api_report_approved",
            entity_type="report",
            entity_id=report.id,
            description=message,
            changes={"step": report.current_step},
        )
        db.session.commit()

        return _ok({"message": message, "report": _serialize_report(report)})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Error aprobando paso via API: %s", exc)
        return _error("No se pudo aprobar la rendicion.", status=500, code="server_error")


@api_bp.route("/reports/<uuid:report_id>/reject", methods=["POST"])
@api_auth_required
def reports_reject(report_id):
    user = g.api_user
    data = _data_dict()
    reason = (data.get("reason") or data.get("comment") or "").strip()

    if not reason:
        return _error("Debes enviar un motivo de rechazo en 'reason'.", status=422, code="validation_error")

    report = Report.query.options(joinedload(Report.user)).filter(Report.id == report_id).first()
    if not report:
        return _error("Rendicion no encontrada.", status=404, code="not_found")

    if report.company_id != user.company_id:
        return _error("No tienes permisos para rechazar esta rendicion.", status=403, code="forbidden")

    if report.status in {ReportStatus.APPROVED, ReportStatus.REJECTED, ReportStatus.PAID}:
        return _error("La rendicion ya fue resuelta.", status=409, code="invalid_state")

    if report.status not in REVIEW_STATUSES:
        return _error("La rendicion no esta actualmente en revision.", status=409, code="invalid_state")

    if report.approval_flow_id:
        step, _ = _resolve_active_step(report, persist=True)
        if not _user_can_review_step(user, report, step):
            return _error("No eres el aprobador designado para este paso.", status=403, code="forbidden")
    elif not _is_admin_like(user):
        return _error("No tienes permisos para rechazar esta rendicion.", status=403, code="forbidden")

    existing = ApprovalDecision.query.filter_by(
        report_id=report.id,
        user_id=user.id,
        step_number=report.current_step,
        decision="rejected",
    ).first()
    if existing:
        return _ok({"message": "Rechazo ya registrado previamente.", "report": _serialize_report(report)})

    try:
        decision = ApprovalDecision(
            report_id=report.id,
            user_id=user.id,
            step_number=report.current_step,
            decision="rejected",
            comments=reason,
        )
        db.session.add(decision)

        report.status = ReportStatus.REJECTED
        for expense in report.expenses:
            expense.status = ExpenseStatus.REJECTED

        notify_report_rejected(report, reason)

        _audit(
            user,
            action="api_report_rejected",
            entity_type="report",
            entity_id=report.id,
            description=f"Rendicion rechazada: {reason}",
        )

        db.session.commit()

        return _ok({"message": "Rendicion rechazada.", "report": _serialize_report(report)})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Error rechazando rendicion via API: %s", exc)
        return _error("No se pudo rechazar la rendicion.", status=500, code="server_error")


@api_bp.route("/reports/<uuid:report_id>/request-info", methods=["POST"])
@api_auth_required
def reports_request_info(report_id):
    user = g.api_user
    data = _data_dict()
    reason = (data.get("reason") or data.get("comment") or "").strip()

    if not reason:
        return _error(
            "Debes enviar el detalle de los antecedentes solicitados en 'reason'.",
            status=422,
            code="validation_error",
        )

    report = Report.query.options(joinedload(Report.user)).filter(Report.id == report_id).first()
    if not report:
        return _error("Rendicion no encontrada.", status=404, code="not_found")

    if report.company_id != user.company_id:
        return _error("No tienes permisos para solicitar antecedentes en esta rendicion.", status=403, code="forbidden")

    if report.status not in REVIEW_STATUSES:
        return _error("La rendicion no esta actualmente en revision.", status=409, code="invalid_state")

    if report.approval_flow_id:
        step, _ = _resolve_active_step(report, persist=True)
        if not _user_can_review_step(user, report, step):
            return _error("No eres el aprobador designado para este paso.", status=403, code="forbidden")
    elif not _is_admin_like(user):
        return _error("No tienes permisos para solicitar antecedentes.", status=403, code="forbidden")

    try:
        decision = ApprovalDecision(
            report_id=report.id,
            user_id=user.id,
            step_number=report.current_step,
            decision="info_requested",
            comments=reason,
        )
        db.session.add(decision)

        report.status = ReportStatus.NEEDS_INFO
        for expense in report.expenses:
            expense.status = ExpenseStatus.DRAFT

        _audit(
            user,
            action="api_report_info_requested",
            entity_type="report",
            entity_id=report.id,
            description=f"Antecedentes adicionales solicitados: {reason}",
        )

        db.session.commit()
        notify_report_info_requested(report, reason)

        return _ok(
            {
                "message": "Se solicitaron antecedentes adicionales al solicitante.",
                "report": _serialize_report(report, include_decisions=True),
            }
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Error solicitando antecedentes via API: %s", exc)
        return _error("No se pudieron solicitar antecedentes.", status=500, code="server_error")


@api_bp.route("/reports/<uuid:report_id>/mark-paid", methods=["POST"])
@api_auth_required
def reports_mark_paid(report_id):
    user = g.api_user
    report = Report.query.options(joinedload(Report.user)).filter(Report.id == report_id).first()
    if not report:
        return _error("Rendicion no encontrada.", status=404, code="not_found")

    if not _user_can_mark_report_paid(user, report):
        return _error("No tienes permisos para marcar esta rendicion como pagada.", status=403, code="forbidden")

    try:
        report.status = ReportStatus.PAID
        report.paid_at = datetime.utcnow()
        for expense in report.expenses:
            expense.status = ExpenseStatus.PAID

        _audit(
            user,
            action="api_report_paid",
            entity_type="report",
            entity_id=report.id,
            description="Rendicion marcada como pagada.",
        )

        db.session.commit()
        notify_report_paid(report)
        return _ok({"message": "Rendicion marcada como pagada.", "report": _serialize_report(report)})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Error marcando rendicion como pagada via API: %s", exc)
        return _error("No se pudo marcar la rendicion como pagada.", status=500, code="server_error")


# ---------------------------------------------------------------------------
# CRUD faltante: detalle, editar y eliminar gastos; eliminar rendiciones;
# quitar gasto de rendición. Misma lógica que la web.
# ---------------------------------------------------------------------------

def _can_api_user_modify_expense(user, expense):
    """True si el usuario puede editar/eliminar el gasto (mismo criterio que web)."""
    if expense.company_id != user.company_id:
        return False
    if expense.user_id != user.id and not _is_admin_like(user):
        return False
    if expense.status != ExpenseStatus.DRAFT:
        return False
    if expense.report_id and expense.report and expense.report.status != ReportStatus.DRAFT:
        return False
    return True


def _can_api_user_manage_report(user, report):
    """True si el usuario puede administrar la rendición (delete/remove expense)."""
    if report.company_id != user.company_id:
        return False
    return report.user_id == user.id or _is_admin_like(user)


@api_bp.route("/expenses/<uuid:expense_id>", methods=["GET"])
@api_auth_required
def expense_detail(expense_id):
    user = g.api_user
    expense = Expense.query.options(selectinload(Expense.category)).filter_by(id=expense_id).first()
    if not expense or expense.company_id != user.company_id:
        return _error("Gasto no encontrado.", status=404, code="not_found")
    if expense.user_id != user.id and not _is_admin_like(user):
        return _error("Gasto no encontrado.", status=404, code="not_found")
    return _ok({"expense": _serialize_expense(expense)})


@api_bp.route("/expenses/<uuid:expense_id>", methods=["PUT"])
@api_auth_required
def expense_update(expense_id):
    user = g.api_user
    data = request.get_json(silent=True) or {}
    expense = Expense.query.options(selectinload(Expense.category)).filter_by(id=expense_id).first()
    if not expense or expense.company_id != user.company_id:
        return _error("Gasto no encontrado.", status=404, code="not_found")
    if not _can_api_user_modify_expense(user, expense):
        return _error(
            "No puedes editar este gasto. Solo se pueden editar gastos en borrador que no estén en una rendición enviada.",
            status=403,
            code="forbidden",
        )

    changes = {}

    # Campos editables
    if "description" in data:
        expense.description = (data.get("description") or "").strip()
        changes["description"] = True

    if "merchant" in data:
        expense.merchant = (data.get("merchant") or "").strip() or None
        changes["merchant"] = True

    if "amount" in data or "currency" in data:
        currency = data.get("currency")
        if currency and currency in {ExpenseCurrency.CLP, ExpenseCurrency.USD}:
            expense.currency = currency
            changes["currency"] = True
        else:
            currency = expense.currency

        amount_raw = data.get("amount")
        if amount_raw is not None:
            amount = _parse_amount(amount_raw, currency=currency)
            if amount is None or amount <= 0:
                return _error("Monto invalido.", status=422, code="validation_error")
            expense.amount = amount
            changes["amount"] = True

            if currency == ExpenseCurrency.USD:
                exchange_rate = _parse_amount(data.get("exchange_rate")) if data.get("exchange_rate") else expense.exchange_rate
                if exchange_rate and exchange_rate > 0:
                    expense.exchange_rate = exchange_rate
                    changes["exchange_rate"] = True
                amount_clp = (amount * exchange_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if exchange_rate else None
                expense.amount_clp = amount_clp or amount
            else:
                expense.exchange_rate = None
                expense.amount_clp = amount

    if "date" in data:
        date_str = (data.get("date") or "").strip()
        if date_str:
            try:
                if "/" in date_str:
                    parts = date_str.replace("-", "/").split("/")
                    expense.date = date(int(parts[2]), int(parts[1]), int(parts[0]))
                else:
                    expense.date = date.fromisoformat(date_str)
                changes["date"] = True
            except (ValueError, IndexError):
                return _error("Formato de fecha invalido. Usa YYYY-MM-DD o DD/MM/YYYY.", status=422, code="validation_error")

    if "category_id" in data:
        category_id = _uuid_or_none(data.get("category_id"))
        if category_id:
            category = Category.query.filter_by(id=category_id, company_id=user.company_id).first()
            if not category:
                return _error("Categoria invalida para esta empresa.", status=422, code="validation_error")
            expense.category_id = category.id
        else:
            return _error("Debes indicar una categoria valida.", status=422, code="validation_error")
        changes["category_id"] = True

    # Si el gasto está en una rendición borrador, recalcular el total
    if expense.report_id and changes:
        report = expense.report
        if report and report.status == ReportStatus.DRAFT:
            report.total_amount = db.session.query(
                func.coalesce(func.sum(Expense.amount_clp), Decimal("0"))
            ).filter(
                Expense.report_id == report.id
            ).scalar() or Decimal("0")

    try:
        _audit(
            user,
            action="api_expense_updated",
            entity_type="expense",
            entity_id=expense.id,
            description=f"Gasto editado via API. Campos: {', '.join(changes.keys()) if changes else 'sin cambios'}.",
            changes={k: True for k in changes} if changes else None,
        )
        db.session.commit()
        return _ok({"expense": _serialize_expense(expense), "updated_fields": list(changes.keys())})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Error editando gasto via API: %s", exc)
        return _error("No se pudo editar el gasto.", status=500, code="server_error")


@api_bp.route("/expenses/<uuid:expense_id>", methods=["DELETE"])
@api_auth_required
def expense_delete(expense_id):
    user = g.api_user
    expense = Expense.query.filter_by(id=expense_id).first()
    if not expense or expense.company_id != user.company_id:
        return _error("Gasto no encontrado.", status=404, code="not_found")
    if not _can_api_user_modify_expense(user, expense):
        return _error(
            "No puedes eliminar este gasto. Solo se pueden eliminar gastos en borrador que no estén en una rendición.",
            status=403,
            code="forbidden",
        )

    try:
        expense_id_str = str(expense.id)
        db.session.delete(expense)
        _audit(
            user,
            action="api_expense_deleted",
            entity_type="expense",
            entity_id=None,
            description=f"Gasto {expense_id_str} eliminado via API.",
        )
        db.session.commit()
        return _ok({"message": "Gasto eliminado.", "id": expense_id_str})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Error eliminando gasto via API: %s", exc)
        return _error("No se pudo eliminar el gasto.", status=500, code="server_error")


@api_bp.route("/reports/<uuid:report_id>", methods=["DELETE"])
@api_auth_required
def report_delete(report_id):
    user = g.api_user
    report = Report.query.options(selectinload(Report.expenses)).filter_by(id=report_id).first()
    if not report or report.company_id != user.company_id:
        return _error("Rendicion no encontrada.", status=404, code="not_found")
    if not _can_api_user_manage_report(user, report) or report.status not in EDITABLE_REPORT_STATUSES:
        return _error(
            "Solo puedes eliminar rendiciones en borrador o con antecedentes solicitados, y que sean tuyas.",
            status=403,
            code="forbidden",
        )

    try:
        detached = 0
        for expense in report.expenses.all():
            expense.report_id = None
            expense.status = ExpenseStatus.DRAFT
            detached += 1

        report_title = report.title
        report_id_str = str(report.id)
        db.session.delete(report)
        _audit(
            user,
            action="api_report_deleted",
            entity_type="report",
            entity_id=None,
            description=f"Rendicion '{report_title}' eliminada via API. {detached} gasto(s) volvieron a borrador.",
        )
        db.session.commit()
        return _ok({"message": "Rendicion eliminada.", "id": report_id_str, "detached_expenses": detached})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Error eliminando rendicion via API: %s", exc)
        return _error("No se pudo eliminar la rendicion.", status=500, code="server_error")


@api_bp.route("/reports/<uuid:report_id>/remove-expense", methods=["POST"])
@api_auth_required
def report_remove_expense(report_id):
    user = g.api_user
    data = request.get_json(silent=True) or {}
    expense_id = _uuid_or_none(data.get("expense_id"))
    if not expense_id:
        return _error("Debes indicar expense_id.", status=422, code="validation_error")

    report = Report.query.filter_by(id=report_id).first()
    if not report or report.company_id != user.company_id:
        return _error("Rendicion no encontrada.", status=404, code="not_found")
    if not _can_api_user_manage_report(user, report) or report.status not in EDITABLE_REPORT_STATUSES:
        return _error(
            "Solo puedes modificar rendiciones en borrador o con antecedentes solicitados.",
            status=403,
            code="forbidden",
        )

    expense = Expense.query.filter_by(id=expense_id, report_id=report.id).first()
    if not expense:
        return _error("El gasto no pertenece a esta rendicion.", status=404, code="not_found")

    try:
        if expense.duplicate_of_id == expense.id:
            expense.is_duplicate = False
            expense.duplicate_of_id = None
        expense.report_id = None
        expense.status = ExpenseStatus.DRAFT

        # Recalcular total de la rendición
        report.total_amount = db.session.query(
            func.coalesce(func.sum(Expense.amount_clp), Decimal("0"))
        ).filter(
            Expense.report_id == report.id
        ).scalar() or Decimal("0")

        _audit(
            user,
            action="api_report_expense_removed",
            entity_type="report",
            entity_id=report.id,
            description=f"Gasto {expense.id} quitado de rendicion '{report.title}' via API.",
        )
        db.session.commit()
        return _ok({
            "message": "Gasto quitado de la rendicion.",
            "report": _serialize_report(report, include_expenses=True),
        })
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error("Error quitando gasto de rendicion via API: %s", exc)
        return _error("No se pudo quitar el gasto de la rendicion.", status=500, code="server_error")
