"""Operaciones del flujo de rendiciones — fuente canónica de la lógica.

Extraído del blueprint web para que web, API y bot de WhatsApp compartan
exactamente las mismas reglas (selección de flujo, pasos, notificaciones).

NOTA: el blueprint web y la API mantienen su propia copia inline por ahora;
consolidarlos sobre este servicio queda pendiente (ver TODO.md).
"""
from datetime import datetime
from decimal import Decimal

from flask import current_app

from app.extensions import db
from app.models.approval import ApprovalDecision, ApprovalFlow, ApprovalStep
from app.models.expense import Expense, ExpenseStatus
from app.models.report import Report, ReportStatus
from app.models.user import User
from app.services.notification_service import (
    notify_approval_needed,
    notify_report_approved,
    notify_report_info_requested,
    notify_report_rejected,
    notify_report_submitted,
)

REVIEW_STATUSES = [ReportStatus.UNDER_REVIEW, "submitted", "in_review"]
EDITABLE_REPORT_STATUSES = [ReportStatus.DRAFT, ReportStatus.NEEDS_INFO]


# ---------------------------------------------------------------------------
# Helpers de pasos
# ---------------------------------------------------------------------------

def is_user_current_step_approver(report, user, allow_admin_override=False):
    if report.company_id != user.company_id or report.status not in REVIEW_STATUSES:
        return False

    if not report.approval_flow_id:
        return user.has_role("manager") or (allow_admin_override and user.is_admin)

    current_step_obj, _ = resolve_active_step(report, persist=False)
    if not current_step_obj:
        return False

    if current_step_obj.approver_type == "role":
        return user.has_role(current_step_obj.approver_target)
    if current_step_obj.approver_type == "user":
        return str(user.id) == current_step_obj.approver_target
    if current_step_obj.approver_type == "manager":
        return report.user.manager_id == user.id
    if allow_admin_override and user.is_admin:
        return True
    return False


def _step_requires_missing_manager(report, step):
    return step and step.approver_type == "manager" and not report.user.manager_id


def resolve_active_step(report, persist=False):
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


def notify_step_if_needed(report, step):
    if step is None:
        return

    if step.approver_type == "role":
        potential_approvers = User.query.filter_by(
            company_id=report.company_id,
            role=step.approver_target,
        ).all()
        for approver in potential_approvers:
            notify_approval_needed(approver.id, report)
    elif step.approver_type == "user":
        notify_approval_needed(step.approver_target, report)
    elif step.approver_type == "manager" and report.user.manager_id:
        notify_approval_needed(report.user.manager_id, report)


def select_approval_flow(company_id, total_amount):
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

    eligible.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return eligible[0][2]


def recalculate_report_total(report_id):
    from sqlalchemy.sql import func
    total = db.session.query(
        func.coalesce(func.sum(Expense.amount_clp), Decimal("0"))
    ).filter(Expense.report_id == report_id).scalar()
    return total or Decimal("0")


# ---------------------------------------------------------------------------
# Operaciones de alto nivel (devuelven (ok, mensaje))
# ---------------------------------------------------------------------------

def submit_report(report, user, info_response_comment=None):
    """Envía (o reenvía) una rendición a revisión. Replica reports.submit."""
    if report.user_id != user.id or report.status not in EDITABLE_REPORT_STATUSES:
        return False, "Esta rendición no se puede enviar en su estado actual."

    try:
        is_resubmitting_info = report.status == ReportStatus.NEEDS_INFO

        if is_resubmitting_info:
            if not info_response_comment:
                return False, "Debes indicar qué antecedentes adicionales estás entregando."
            if not report.approval_flow_id or not report.current_step:
                return False, "La rendición no tiene un paso de aprobación válido para retomar."
            selected_flow = report.approval_flow
            db.session.add(ApprovalDecision(
                report_id=report.id,
                user_id=user.id,
                step_number=report.current_step,
                decision="info_submitted",
                comments=info_response_comment,
            ))
        else:
            selected_flow = select_approval_flow(user.company_id, report.total_amount)
            if not selected_flow or not selected_flow.steps:
                db.session.rollback()
                return False, ("No existe un flujo de aprobación activo para esta rendición. "
                               "Queda en borrador hasta que un administrador configure uno.")
            report.approval_flow_id = selected_flow.id
            report.current_step = 1

        report.status = ReportStatus.UNDER_REVIEW
        report.submitted_at = datetime.utcnow()
        for exp in report.expenses:
            exp.status = ExpenseStatus.SUBMITTED

        current_step_obj, skipped_steps = resolve_active_step(report, persist=True)

        if current_step_obj:
            notify_step_if_needed(report, current_step_obj)
            db.session.commit()
            notify_report_submitted(report)
            if is_resubmitting_info:
                return True, "Antecedentes adicionales reenviados al mismo aprobador ✅"
            return True, f"Rendición enviada a revisión (flujo: {selected_flow.name}) ✅"

        report.status = ReportStatus.APPROVED
        report.approved_at = datetime.utcnow()
        for exp in report.expenses:
            exp.status = ExpenseStatus.APPROVED
        db.session.commit()
        notify_report_approved(report)
        return True, "La rendición quedó aprobada automáticamente (no había aprobadores en el flujo) ✅"
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Error en submit_report %s", report.id)
        return False, f"Error al enviar: {exc}"


def approve_report(report, user, comment=""):
    """Aprueba el paso actual. Replica reports.approve."""
    if report.status not in REVIEW_STATUSES:
        return False, "La rendición no está actualmente en revisión."

    if not report.approval_flow_id:
        if not user.is_admin:
            return False, "No tienes permiso para aprobar esta rendición."
        try:
            report.status = ReportStatus.APPROVED
            report.approved_at = datetime.utcnow()
            for exp in report.expenses:
                exp.status = ExpenseStatus.APPROVED
            db.session.commit()
            return True, "Rendición aprobada ✅"
        except Exception as exc:
            db.session.rollback()
            return False, f"Error: {exc}"

    current_step, _skipped = resolve_active_step(report, persist=True)
    if not current_step:
        return False, "No existen aprobadores disponibles para el paso actual."

    if not is_user_current_step_approver(report, user, allow_admin_override=False):
        return False, "No eres el aprobador designado para este paso."

    try:
        db.session.add(ApprovalDecision(
            report_id=report.id,
            user_id=user.id,
            step_number=report.current_step,
            decision="approved",
            comments=comment,
        ))

        report.current_step += 1
        next_step, _skipped2 = resolve_active_step(report, persist=True)
        if next_step:
            notify_step_if_needed(report, next_step)
            db.session.commit()
            return True, "Paso aprobado. La rendición avanza al siguiente nivel ✅"

        report.status = ReportStatus.APPROVED
        report.approved_at = datetime.utcnow()
        for exp in report.expenses:
            exp.status = ExpenseStatus.APPROVED
        db.session.commit()
        notify_report_approved(report)
        return True, "Aprobación final completada ✅"
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Error en approve_report %s", report.id)
        return False, f"Error: {exc}"


def reject_report(report, user, reason):
    """Rechaza la rendición completa. Replica reports.reject."""
    if report.status not in REVIEW_STATUSES:
        return False, "La rendición no está actualmente en revisión."
    if not reason or not reason.strip():
        return False, "Debes indicar un motivo de rechazo."
    reason = reason.strip()

    try:
        db.session.add(ApprovalDecision(
            report_id=report.id,
            user_id=user.id,
            step_number=report.current_step,
            decision="rejected",
            comments=reason,
        ))
        report.status = ReportStatus.REJECTED
        for exp in report.expenses:
            exp.status = ExpenseStatus.REJECTED
        db.session.commit()
        notify_report_rejected(report, reason)
        return True, "Rendición rechazada. Se notificó al solicitante ✅"
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Error en reject_report %s", report.id)
        return False, f"Error: {exc}"


def request_report_info(report, user, reason):
    """Pide antecedentes adicionales. Replica reports.request_info."""
    if report.status not in REVIEW_STATUSES:
        return False, "La rendición no está actualmente en revisión."
    if not reason or not reason.strip():
        return False, "Debes indicar qué antecedentes adicionales solicitas."
    reason = reason.strip()

    if report.approval_flow_id:
        if not is_user_current_step_approver(report, user, allow_admin_override=False):
            return False, "No eres el aprobador designado para este paso."
    elif not user.is_admin:
        return False, "No tienes permiso."

    try:
        db.session.add(ApprovalDecision(
            report_id=report.id,
            user_id=user.id,
            step_number=report.current_step,
            decision="info_requested",
            comments=reason,
        ))
        report.status = ReportStatus.NEEDS_INFO
        for exp in report.expenses:
            exp.status = ExpenseStatus.DRAFT
        db.session.commit()
        notify_report_info_requested(report, reason)
        return True, "Se solicitaron antecedentes al solicitante ✅"
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Error en request_report_info %s", report.id)
        return False, f"Error: {exc}"


def pending_reports_for_approver(user, limit=10):
    """Rendiciones en revisión que este usuario puede accionar ahora."""
    candidates = (
        Report.query
        .filter(
            Report.company_id == user.company_id,
            Report.status == ReportStatus.UNDER_REVIEW,
            Report.user_id != user.id,
        )
        .order_by(Report.submitted_at.asc())
        .limit(50)
        .all()
    )
    return [r for r in candidates if is_user_current_step_approver(r, user)][:limit]
