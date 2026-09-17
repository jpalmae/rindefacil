"""Reportería para managers, finanzas y admins.

Scope por rol:
- manager: su equipo (subordinados directos) + él mismo
- finanzas/admin: empresa completa

Todas las funciones reciben el user y filtros (date_from/date_to ISO,
cost_center_id, category_id) y devuelven dicts serializables.
"""
from datetime import date, timedelta
from decimal import Decimal

from flask import current_app
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from app.extensions import db
from app.models.approval import ApprovalDecision
from app.models.category import Category
from app.models.cost_center import CostCenter
from app.models.expense import Expense, ExpenseStatus
from app.models.report import Report, ReportStatus
from app.models.user import User


def can_access(user):
    return user.is_admin or user.has_role("manager") or user.has_finance_report_access


def _scope_user_ids(user):
    """IDs de usuarios incluidos en el scope del viewer."""
    if user.is_admin or user.has_finance_report_access:
        return None  # None = toda la empresa
    # manager: subordinados directos + self
    ids = [u.id for u in User.query.filter_by(manager_id=user.id, is_active=True).all()]
    ids.append(user.id)
    return ids


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _apply_filters(query, user, filters):
    query = query.filter(Expense.company_id == user.company_id)

    ids = _scope_user_ids(user)
    if ids is not None:
        query = query.filter(Expense.user_id.in_(ids))

    date_from = _parse_date(filters.get("date_from"))
    if date_from:
        query = query.filter(Expense.date >= date_from)
    date_to = _parse_date(filters.get("date_to"))
    if date_to:
        query = query.filter(Expense.date < date_to + timedelta(days=1))

    cc = filters.get("cost_center_id")
    if cc:
        query = query.filter(Expense.cost_center_id == cc)
    cat = filters.get("category_id")
    if cat:
        query = query.filter(Expense.category_id == cat)
    return query


def _base_query(user, filters):
    return _apply_filters(Expense.query, user, filters)


def _money(value):
    return float(value or 0)


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

def get_kpis(user, filters):
    date_from = _parse_date(filters.get("date_from")) or date.today().replace(day=1)
    date_to = _parse_date(filters.get("date_to")) or date.today()

    base_filter = _apply_filters(db.session.query(Expense), user, filters)
    row = base_filter.with_entities(
        func.coalesce(func.sum(Expense.amount_clp), 0),
        func.count(Expense.id),
        func.count(func.distinct(Expense.user_id)),
    ).one()

    total = Decimal(row[0] or 0)
    count = row[1] or 0
    active_users = row[2] or 0
    avg_ticket = float(total / count) if count else 0

    # Período anterior (misma duración inmediatamente anterior)
    span_days = max((date_to - date_from).days, 1)
    prev_filters = dict(filters)
    prev_filters["date_from"] = (date_from - timedelta(days=span_days)).isoformat()
    prev_filters["date_to"] = (date_from - timedelta(days=1)).isoformat()
    prev_total = Decimal(
        _apply_filters(db.session.query(Expense), user, prev_filters).with_entities(
            func.coalesce(func.sum(Expense.amount_clp), 0)
        ).scalar() or 0
    )
    delta_pct = float((total - prev_total) / prev_total * 100) if prev_total else None

    # Por pagar (reports aprobados, reembolso, no pagados)
    scope_ids = _scope_user_ids(user)
    pay_q = Report.query.filter(
        Report.company_id == user.company_id,
        Report.status == ReportStatus.APPROVED,
        Report.settlement_type == "employee_reimbursement",
    )
    if scope_ids is not None:
        pay_q = pay_q.filter(Report.user_id.in_(scope_ids))
    to_pay_row = pay_q.with_entities(
        func.coalesce(func.sum(Report.total_amount), 0), func.count(Report.id)
    ).one()

    # Pendientes de revisión
    review_q = Report.query.filter(
        Report.company_id == user.company_id,
        Report.status == ReportStatus.UNDER_REVIEW,
    )
    if scope_ids is not None:
        review_q = review_q.filter(Report.user_id.in_(scope_ids))
    in_review = review_q.count()

    return {
        "total": _money(total),
        "count": count,
        "active_users": active_users,
        "avg_ticket": round(avg_ticket, 2),
        "delta_pct": round(delta_pct, 1) if delta_pct is not None else None,
        "to_pay_amount": _money(to_pay_row[0]),
        "to_pay_count": to_pay_row[1],
        "in_review": in_review,
        "base_currency": user.company.base_currency or "CLP",
    }


# ---------------------------------------------------------------------------
# Series y distribuciones
# ---------------------------------------------------------------------------

def get_monthly_series(user, filters, months=12):
    date_to = _parse_date(filters.get("date_to")) or date.today()
    date_from = _parse_date(filters.get("date_from")) or (date_to - timedelta(days=365))

    q = _apply_filters(db.session.query(Expense), user, {**filters, "date_from": date_from.isoformat()})
    year_e = func.extract("year", Expense.date)
    month_e = func.extract("month", Expense.date)
    rows = (
        q.with_entities(
            year_e, month_e,
            func.coalesce(func.sum(Expense.amount_clp), 0),
            func.count(Expense.id),
        )
        .group_by(year_e, month_e)
        .order_by(year_e, month_e)
        .all()
    )
    return {
        "labels": [f"{int(r[0])}-{int(r[1]):02d}" for r in rows],
        "totals": [_money(r[2]) for r in rows],
        "counts": [r[3] for r in rows],
    }


def _group_by_dimension(user, filters, model, fk, name_field):
    q = _apply_filters(db.session.query(Expense), user, filters)
    rows = (
        q.join(model, fk == model.id)
        .with_entities(
            getattr(model, name_field),
            func.coalesce(func.sum(Expense.amount_clp), 0),
            func.count(Expense.id),
        )
        .group_by(getattr(model, name_field))
        .order_by(func.sum(Expense.amount_clp).desc())
        .all()
    )
    return [{"label": r[0], "total": _money(r[1]), "count": r[2]} for r in rows]


def get_by_category(user, filters):
    return _group_by_dimension(user, filters, Category, Expense.category_id, "name")


def get_by_cost_center(user, filters):
    return _group_by_dimension(user, filters, CostCenter, Expense.cost_center_id, "name")


def get_by_user(user, filters):
    q = _apply_filters(db.session.query(Expense), user, filters)
    rows = (
        q.join(User, Expense.user_id == User.id)
        .with_entities(
            User.full_name,
            func.coalesce(func.sum(Expense.amount_clp), 0),
            func.count(Expense.id),
        )
        .group_by(User.full_name)
        .order_by(func.sum(Expense.amount_clp).desc())
        .limit(10)
        .all()
    )
    return [{"label": r[0], "total": _money(r[1]), "count": r[2]} for r in rows]


def get_settlement_split(user, filters):
    q = _apply_filters(db.session.query(Expense), user, filters)
    rows = (
        q.with_entities(Expense.status, func.coalesce(func.sum(Expense.amount_clp), 0))
        .group_by(Expense.status)
        .all()
    )
    return {r[0]: _money(r[1]) for r in rows}


def get_by_status(user, filters):
    return get_settlement_split(user, filters)


# ---------------------------------------------------------------------------
# Ciclo de aprobación
# ---------------------------------------------------------------------------

def get_report_funnel(user, filters):
    scope_ids = _scope_user_ids(user)
    q = Report.query.filter(Report.company_id == user.company_id)
    if scope_ids is not None:
        q = q.filter(Report.user_id.in_(scope_ids))
    rows = q.with_entities(Report.status, func.count(Report.id)).group_by(Report.status).all()
    return {r[0]: r[1] for r in rows}


def get_approval_cycle(user, filters):
    scope_ids = _scope_user_ids(user)
    date_from = _parse_date(filters.get("date_from"))
    date_to = _parse_date(filters.get("date_to"))

    q = Report.query.filter(
        Report.company_id == user.company_id,
        Report.submitted_at.isnot(None),
    )
    if scope_ids is not None:
        q = q.filter(Report.user_id.in_(scope_ids))
    if date_from:
        q = q.filter(Report.submitted_at >= date_from)
    if date_to:
        q = q.filter(Report.submitted_at < date_to + timedelta(days=1))

    # Tiempo medio: submitted → última decisión relevante
    decision_sub = (
        db.session.query(
            ApprovalDecision.report_id,
            func.max(ApprovalDecision.decided_at).label("last_decision"),
        )
        .group_by(ApprovalDecision.report_id)
        .subquery()
    )
    rows = (
        q.join(decision_sub, decision_sub.c.report_id == Report.id)
        .with_entities(
            func.avg(
                func.extract("epoch", decision_sub.c.last_decision - Report.submitted_at) / 86400.0
            ),
            func.count(Report.id),
        )
        .first()
    )
    avg_days = round(float(rows[0] or 0), 1) if rows else 0

    # Tasa de rechazo
    statuses = get_report_funnel(user, filters)
    decided = statuses.get(ReportStatus.APPROVED, 0) + statuses.get(ReportStatus.REJECTED, 0) + statuses.get(ReportStatus.PAID, 0)
    rejected = statuses.get(ReportStatus.REJECTED, 0)
    rejection_rate = round(rejected / decided * 100, 1) if decided else 0

    return {
        "avg_days": avg_days,
        "rejection_rate": rejection_rate,
        "statuses": statuses,
    }


def get_approver_workload(user):
    """Pendientes por aprobador (empresa; solo para finanzas/admin/manager de flujo)."""
    pending = (
        Report.query
        .filter(
            Report.company_id == user.company_id,
            Report.status == ReportStatus.UNDER_REVIEW,
        )
        .all()
    )
    from app.services.report_workflow_service import resolve_active_step

    workload = {}
    for report in pending:
        step, _ = resolve_active_step(report, persist=False)
        if not step:
            continue
        if step.approver_type == "user":
            approver = db.session.get(User, step.approver_target) if _is_uuid(step.approver_target) else None
            label = approver.full_name if approver else "Usuario eliminado"
        elif step.approver_type == "manager":
            label = f"Manager de {report.user.full_name.split()[0]}" if report.user else "Manager (sin asignar)"
        else:
            label = f"Rol: {step.approver_target}"
        workload[label] = workload.get(label, 0) + 1
    return dict(sorted(workload.items(), key=lambda kv: -kv[1]))


def _is_uuid(value):
    try:
        PGUUID(as_uuid=True)
        import uuid as _uuid
        _uuid.UUID(str(value))
        return True
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Canales y patrones
# ---------------------------------------------------------------------------

def get_channels(user, filters):
    q = _base_query(user, filters)
    rows = (
        q.with_entities(
            func.coalesce(
                func.nullif(Expense.gps_validation_meta.op("->>")("source"), ""), "web"
            ),
            func.count(Expense.id),
        )
        .group_by(1)
        .all()
    )
    return {r[0]: r[1] for r in rows}


def get_weekday_distribution(user, filters):
    q = _base_query(user, filters)
    rows = (
        q.with_entities(
            func.extract("dow", Expense.date),
            func.coalesce(func.sum(Expense.amount_clp), 0),
            func.count(Expense.id),
        )
        .group_by(func.extract("dow", Expense.date))
        .all()
    )
    labels = ["Dom", "Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"]
    by_day = {int(r[0]): (r[1], r[2]) for r in rows}
    return {
        "labels": labels,
        "totals": [_money(by_day.get(i, (0, 0))[0]) for i in range(7)],
        "counts": [by_day.get(i, (0, 0))[1] for i in range(7)],
    }


def get_top_merchants(user, filters, limit=8):
    q = _base_query(user, filters)
    rows = (
        q.filter(Expense.merchant.isnot(None))
        .with_entities(
            Expense.merchant,
            func.coalesce(func.sum(Expense.amount_clp), 0),
            func.count(Expense.id),
        )
        .group_by(Expense.merchant)
        .order_by(func.sum(Expense.amount_clp).desc())
        .limit(limit)
        .all()
    )
    return [{"label": r[0], "total": _money(r[1]), "count": r[2]} for r in rows]


# ---------------------------------------------------------------------------
# Cumplimiento
# ---------------------------------------------------------------------------

def get_compliance(user, filters):
    q = _base_query(user, filters)
    total_count = q.count()

    duplicates = q.filter(Expense.is_duplicate.is_(True)).count()
    no_receipt = q.filter(
        db.or_(Expense.receipt_url.is_(None), Expense.receipt_url == "")
    ).count()
    gps_issues = q.filter(
        Expense.gps_validation_status.in_(["mismatch"])
    ).count()
    weekend = q.filter(
        func.extract("dow", Expense.date).in_([0, 6])
    ).count()

    return {
        "total": total_count,
        "duplicates": duplicates,
        "no_receipt": no_receipt,
        "gps_issues": gps_issues,
        "weekend": weekend,
    }


# ---------------------------------------------------------------------------
# Dataset para export CSV
# ---------------------------------------------------------------------------

def export_rows(user, filters):
    q = _base_query(user, filters).order_by(Expense.date.desc())
    expenses = q.all()
    headers = [
        "fecha", "usuario", "categoria", "centro_costo", "descripcion", "comercio",
        "monto", "moneda", "monto_base", "estado", "origen", "rendicion", "gps_validacion",
    ]
    rows = []
    for e in expenses:
        rows.append([
            e.date.isoformat() if e.date else "",
            e.user.full_name if e.user else "",
            e.category.name if e.category else "",
            e.cost_center.name if e.cost_center else "",
            (e.description or "")[:200],
            e.merchant or "",
            str(e.amount),
            e.currency,
            str(e.amount_clp),
            e.status,
            (e.gps_validation_meta or {}).get("source", "web"),
            e.report_id or "",
            e.gps_validation_status or "",
        ])
    return headers, rows
