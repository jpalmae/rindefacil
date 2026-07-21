from decimal import Decimal

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models.report import Report, ReportSettlementType, ReportStatus
from app.models.expense import Expense, ExpenseStatus
from app.models.approval import ApprovalFlow, ApprovalStep, ApprovalDecision
from datetime import datetime, timedelta
from uuid import UUID
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload, selectinload
from app.services.export_service import generate_report_pdf
from app.services.notification_service import (
    notify_report_created,
    notify_approval_needed,
    notify_report_approved,
    notify_report_info_requested,
    notify_report_paid,
    notify_report_rejected,
    notify_report_submitted,
)
from app.models import User
from app.services.audit_service import log_action

reports_bp = Blueprint('reports', __name__)
REVIEW_STATUSES = {ReportStatus.UNDER_REVIEW, 'in_review'}
EDITABLE_REPORT_STATUSES = {ReportStatus.DRAFT, ReportStatus.NEEDS_INFO}
FINANCE_VISIBLE_STATUSES = {ReportStatus.APPROVED, ReportStatus.PAID}


def _can_manage_draft_report(report):
    return report.company_id == current_user.company_id and (
        report.user_id == current_user.id or current_user.is_admin
    )


def _can_view_report(report):
    if report.company_id != current_user.company_id:
        return False
    if report.user_id == current_user.id or current_user.is_admin:
        return True
    if _can_user_approve_report(report):
        # Manager/approver del paso actual del flujo puede ver la rendición
        # que tiene pendiente de aprobar, pero no cualquier otra.
        return True
    return current_user.has_finance_report_access and report.status in FINANCE_VISIBLE_STATUSES


def _can_mark_report_paid(report):
    return (
        report.company_id == current_user.company_id
        and current_user.can_process_reimbursements
        and report.status == ReportStatus.APPROVED
        and report.settlement_type == ReportSettlementType.EMPLOYEE_REIMBURSEMENT
    )


def _is_user_current_step_approver(report, user=None, allow_admin_override=False):
    user = user or current_user
    if report.company_id != user.company_id or report.status not in REVIEW_STATUSES:
        return False

    if not report.approval_flow_id:
        return user.has_role('manager') or (allow_admin_override and user.has_role('admin'))

    current_step_obj, _ = _resolve_active_step(report, persist=False)
    if not current_step_obj:
        return False

    if current_step_obj.approver_type == 'role':
        return user.has_role(current_step_obj.approver_target)
    if current_step_obj.approver_type == 'user':
        return str(user.id) == current_step_obj.approver_target
    if current_step_obj.approver_type == 'manager':
        return report.user.manager_id == user.id
    if allow_admin_override and user.has_role('admin'):
        return True
    return False


def _can_user_approve_report(report, user=None):
    return _is_user_current_step_approver(report, user=user, allow_admin_override=False)


def _step_requires_missing_manager(report, step):
    return step and step.approver_type == 'manager' and not report.user.manager_id


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


def _notify_step_if_needed(report, step):
    if step is None:
        return

    if step.approver_type == 'role':
        potential_approvers = User.query.filter_by(
            company_id=report.company_id,
            role=step.approver_target,
        ).all()
        for approver in potential_approvers:
            notify_approval_needed(approver.id, report)
    elif step.approver_type == 'user':
        notify_approval_needed(step.approver_target, report)
    elif step.approver_type == 'manager' and report.user.manager_id:
        notify_approval_needed(report.user.manager_id, report)


def _recalculate_report_total(report_id):
    total = db.session.query(
        func.coalesce(func.sum(Expense.amount_clp), Decimal("0"))
    ).filter(
        Expense.report_id == report_id
    ).scalar()
    return total or Decimal("0")


def _select_approval_flow(company_id, total_amount):
    flows = ApprovalFlow.query.filter_by(company_id=company_id, is_active=True).all()
    if not flows:
        return None

    eligible = []
    total = Decimal(str(total_amount or 0))
    for flow in flows:
        rules = flow.trigger_rules or {}
        min_amount = Decimal(str(rules.get('min_amount', 0) or 0))
        if total >= min_amount:
            eligible.append((min_amount, len(flow.steps), flow))

    if not eligible:
        return None

    eligible.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return eligible[0][2]

@reports_bp.route('/')
@login_required
def index():
    scope = (request.args.get('scope') or '').strip().lower()
    payment_filter = (request.args.get('payment_filter') or 'all').strip().lower()

    # Filtros avanzados (solo admin/finanzas los ve en UI, pero aplicarlos
    # globalmente no rompe el aislamiento: los branches de manager/employee
    # ya acotan por user_id, así que cualquier filtro externo no filtra datos
    # que no deberían ver).
    date_from = (request.args.get('date_from') or '').strip()
    date_to = (request.args.get('date_to') or '').strip()
    user_filter = (request.args.get('user_id') or '').strip()
    status_filter = (request.args.get('status') or '').strip().lower()
    settlement_filter = (request.args.get('settlement_type') or '').strip().lower()

    can_use_advanced_filters = current_user.is_admin or current_user.has_finance_report_access

    base_query = Report.query.options(
        joinedload(Report.user),
        joinedload(Report.approval_flow).selectinload(ApprovalFlow.steps),
    )

    # Aplicar filtros al base_query (encadenan con los filtros de cada branch).
    if date_from:
        try:
            base_query = base_query.filter(Report.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
        except ValueError:
            date_from = ''
    if date_to:
        try:
            # Fin del día inclusive
            base_query = base_query.filter(Report.created_at < datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            date_to = ''
    if can_use_advanced_filters and user_filter:
        try:
            base_query = base_query.filter(Report.user_id == UUID(user_filter))
        except (ValueError, AttributeError):
            user_filter = ''
    if status_filter and status_filter in {'draft', 'submitted', 'in_review', 'under_review', 'needs_info', 'approved', 'rejected', 'paid'}:
        # Normalizar in_review -> under_review (alias histórico)
        if status_filter == 'in_review':
            status_filter = 'under_review'
        base_query = base_query.filter(Report.status == status_filter)
    else:
        status_filter = ''
    if settlement_filter and settlement_filter in {'employee_reimbursement', 'corporate_card'}:
        base_query = base_query.filter(Report.settlement_type == settlement_filter)
    else:
        settlement_filter = ''

    has_active_filters = bool(date_from or date_to or user_filter or status_filter or settlement_filter)

    report_views = {}
    default_scope = 'mine'

    if current_user.is_admin:
        # Admins: todas las rendiciones de la empresa.
        company_reports = base_query.filter_by(company_id=current_user.company_id).order_by(Report.created_at.desc()).all()
        report_views = {
            'pending': [report for report in company_reports if _can_user_approve_report(report)],
            'mine': [report for report in company_reports if report.user_id == current_user.id],
            'all': company_reports,
        }
        if current_user.has_finance_report_access:
            report_views['finance'] = [
                report for report in company_reports
                if report.user_id == current_user.id or report.status in FINANCE_VISIBLE_STATUSES
            ]
        default_scope = 'pending' if report_views['pending'] else 'mine'
    elif current_user.has_finance_report_access:
        # Perfil Finanzas (no admin): propias + aprobadas/pagadas de la empresa.
        finance_reports = base_query.filter(
            Report.company_id == current_user.company_id,
            or_(
                Report.user_id == current_user.id,
                Report.status.in_(FINANCE_VISIBLE_STATUSES),
            ),
        ).order_by(Report.created_at.desc()).all()
        report_views = {
            'mine': [report for report in finance_reports if report.user_id == current_user.id],
            'finance': finance_reports,
        }
        default_scope = 'finance'
    elif current_user.has_role('manager'):
        # Manager (no admin, no finanzas): propias + rendiciones donde es
        # aprobador del paso actual del flujo (por rol, usuario o como jefe
        # jerárquico del solicitante). NO ve todas las de la empresa.
        candidates = base_query.filter(
            Report.company_id == current_user.company_id,
            or_(
                Report.user_id == current_user.id,
                Report.status.in_(REVIEW_STATUSES),
            ),
        ).order_by(Report.created_at.desc()).all()
        report_views = {
            'mine': [report for report in candidates if report.user_id == current_user.id],
            'pending': [report for report in candidates if _can_user_approve_report(report)],
        }
        default_scope = 'pending' if report_views['pending'] else 'mine'
    else:
        own_reports = base_query.filter_by(user_id=current_user.id).order_by(Report.created_at.desc()).all()
        report_views = {'mine': own_reports}

    current_scope = scope if scope in report_views else default_scope
    reports = report_views.get(current_scope, [])

    mine_reports = report_views.get('mine', [])
    mine_payment_views = {
        'all': mine_reports,
        'pending_reimbursement': [
            report for report in mine_reports
            if report.settlement_type == ReportSettlementType.EMPLOYEE_REIMBURSEMENT
            and report.status == ReportStatus.APPROVED
        ],
        'paid': [
            report for report in mine_reports
            if report.settlement_type == ReportSettlementType.EMPLOYEE_REIMBURSEMENT
            and report.status == ReportStatus.PAID
        ],
        'corporate_card': [
            report for report in mine_reports
            if report.settlement_type == ReportSettlementType.CORPORATE_CARD
        ],
    }
    finance_reports = report_views.get('finance', [])
    finance_payment_views = {
        'all': finance_reports,
        'por_pagar': [
            report for report in finance_reports
            if report.settlement_type == ReportSettlementType.EMPLOYEE_REIMBURSEMENT
            and report.status == ReportStatus.APPROVED
        ],
        'paid': [
            report for report in finance_reports
            if report.settlement_type == ReportSettlementType.EMPLOYEE_REIMBURSEMENT
            and report.status == ReportStatus.PAID
        ],
        'corporate_card': [
            report for report in finance_reports
            if report.settlement_type == ReportSettlementType.CORPORATE_CARD
            and report.status in (ReportStatus.APPROVED, ReportStatus.PAID)
        ],
    }

    scope_payment_views = mine_payment_views if current_scope == 'mine' else finance_payment_views if current_scope == 'finance' else {}
    current_payment_filter = payment_filter if payment_filter in scope_payment_views else 'all'
    if current_scope == 'mine':
        reports = mine_payment_views[current_payment_filter]
    elif current_scope == 'finance':
        reports = finance_payment_views[current_payment_filter]

    report_ids = [r.id for r in reports]
    expense_counts = {}
    if report_ids:
        expense_counts = {
            report_id: count for report_id, count in db.session.query(
                Expense.report_id, func.count(Expense.id)
            ).filter(
                Expense.report_id.in_(report_ids)
            ).group_by(Expense.report_id).all()
        }

    # Lista de usuarios para el dropdown de filtros (solo admin/finanzas).
    company_users = []
    if can_use_advanced_filters:
        company_users = (
            User.query
            .filter_by(company_id=current_user.company_id, is_active=True)
            .order_by(User.full_name.asc())
            .all()
        )

    return render_template(
        'reports/index.html',
        reports=reports,
        expense_counts=expense_counts,
        current_scope=current_scope,
        current_payment_filter=current_payment_filter,
        mine_payment_views=mine_payment_views,
        finance_payment_views=finance_payment_views,
        report_views=report_views,
        can_use_advanced_filters=can_use_advanced_filters,
        company_users=company_users,
        filters={
            'date_from': date_from,
            'date_to': date_to,
            'user_id': user_filter,
            'status': status_filter,
            'settlement_type': settlement_filter,
        },
        has_active_filters=has_active_filters,
    )

@reports_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    # Only show expenses that are Draft or Rejected (needs resubmission) and not already in a report
    available_expenses = Expense.query.filter_by(
        user_id=current_user.id,
        report_id=None
    ).filter(
        Expense.status.in_([ExpenseStatus.DRAFT, ExpenseStatus.REJECTED])
    ).all()
    
    if request.method == 'POST':
        title = request.form.get('title')
        description = request.form.get('description')
        settlement_type = request.form.get('settlement_type') or ReportSettlementType.EMPLOYEE_REIMBURSEMENT
        selected_expense_ids = request.form.getlist('expense_ids')
        
        if not title:
            flash('Debes ingresar un título para el informe.', 'warning')
            return redirect(url_for('reports.new'))
            
        if not selected_expense_ids:
            flash('Debes seleccionar al menos un gasto para incluir en el informe.', 'warning')
            return redirect(url_for('reports.new'))

        if not Report.is_valid_settlement_type(settlement_type):
            flash('Debes seleccionar un tipo de rendicion valido.', 'warning')
            return redirect(url_for('reports.new'))
            
        try:
            # Create report
            report = Report(
                company_id=current_user.company_id,
                user_id=current_user.id,
                title=title,
                description=description,
                status=ReportStatus.DRAFT,
                settlement_type=settlement_type,
            )
            db.session.add(report)
            db.session.flush() # get report.id
            
            # Add expenses to report in one query to avoid N+1 lookups
            valid_expense_ids = []
            for exp_id in selected_expense_ids:
                try:
                    valid_expense_ids.append(UUID(exp_id))
                except ValueError:
                    continue

            selected_expenses = Expense.query.filter(
                Expense.id.in_(valid_expense_ids),
                Expense.user_id == current_user.id
            ).all()
            if not selected_expenses:
                db.session.rollback()
                flash('No se encontraron gastos válidos para incluir en el informe.', 'warning')
                return redirect(url_for('reports.new'))

            total_amount = 0
            for exp in selected_expenses:
                exp.report_id = report.id
                total_amount += Decimal(str(exp.amount_clp or exp.amount or 0))
            
            report.total_amount = total_amount
            db.session.commit()
            notify_report_created(report)
            
            flash('Informe creado exitosamente.', 'success')
            return redirect(url_for('reports.show', id=report.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al crear informe: {str(e)}', 'danger')
            
    return render_template(
        'reports/form.html',
        expenses=available_expenses,
        settlement_type_options=ReportSettlementType.CHOICES,
        default_settlement_type=ReportSettlementType.EMPLOYEE_REIMBURSEMENT,
    )

@reports_bp.route('/<uuid:id>')
@login_required
def show(id):
    report = Report.query.options(
        joinedload(Report.user),
        joinedload(Report.approval_flow).selectinload(ApprovalFlow.steps),
        selectinload(Report.decisions).joinedload(ApprovalDecision.user)
    ).get_or_404(id)
    
    # Simple ACL
    if not _can_view_report(report):
        flash('No tienes permiso para ver este informe.', 'danger')
        return redirect(url_for('dashboard.index'))

    _resolve_active_step(report, persist=True)
        
    expenses = Expense.query.options(
        selectinload(Expense.category)
    ).filter_by(
        report_id=report.id
    ).order_by(
        Expense.created_at.desc()
    ).all()

    return render_template(
        'reports/show.html',
        report=report,
        expenses=expenses,
        expense_count=len(expenses),
        can_manage_draft_report=_can_manage_draft_report(report),
        can_approve_now=_can_user_approve_report(report),
        can_mark_report_paid=_can_mark_report_paid(report),
        latest_info_request=next((decision for decision in report.decisions if decision.decision == 'info_requested'), None),
    )


@reports_bp.route('/<uuid:id>/delete', methods=['POST'])
@login_required
def delete(id):
    report = Report.query.get_or_404(id)

    if not _can_manage_draft_report(report) or report.status not in EDITABLE_REPORT_STATUSES:
        flash('Solo puedes eliminar rendiciones en borrador o con antecedentes solicitados.', 'warning')
        return redirect(url_for('reports.show', id=id))

    try:
        detached_expense_count = 0
        for expense in report.expenses.all():
            expense.report_id = None
            expense.status = ExpenseStatus.DRAFT
            detached_expense_count += 1

        report_title = report.title
        report_id = report.id
        db.session.delete(report)
        db.session.commit()

        log_action(
            action='report_deleted',
            entity_type='report',
            entity_id=report_id,
            description=f"Rendición '{report_title}' eliminada. {detached_expense_count} gasto(s) volvieron a borrador."
        )
        flash('Rendición eliminada. Sus gastos volvieron a Mis Gastos.', 'success')
        return redirect(url_for('reports.index'))
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar la rendición: {str(e)}', 'danger')
        return redirect(url_for('reports.show', id=id))


@reports_bp.route('/<uuid:report_id>/expenses/<uuid:expense_id>/remove', methods=['POST'])
@login_required
def remove_expense(report_id, expense_id):
    report = Report.query.get_or_404(report_id)
    expense = Expense.query.get_or_404(expense_id)

    if not _can_manage_draft_report(report) or report.status not in EDITABLE_REPORT_STATUSES:
        flash('Solo puedes modificar rendiciones en borrador o con antecedentes solicitados.', 'warning')
        return redirect(url_for('reports.show', id=report_id))

    if expense.report_id != report.id:
        flash('El gasto no pertenece a esta rendición.', 'warning')
        return redirect(url_for('reports.show', id=report_id))

    try:
        if expense.id is not None and expense.duplicate_of_id == expense.id:
            expense.is_duplicate = False
            expense.duplicate_of_id = None
        expense.report_id = None
        expense.status = ExpenseStatus.DRAFT
        report.total_amount = _recalculate_report_total(report.id)
        db.session.commit()

        log_action(
            action='expense_removed_from_report',
            entity_type='report',
            entity_id=report.id,
            description=f"Gasto '{expense.public_id}' removido de la rendición '{report.title}'."
        )
        flash('Gasto quitado de la rendición y devuelto a Mis Gastos.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al quitar el gasto: {str(e)}', 'danger')

    return redirect(url_for('reports.show', id=report_id))

@reports_bp.route('/<uuid:id>/submit', methods=['POST'])
@login_required
def submit(id):
    report = Report.query.get_or_404(id)
    
    if report.user_id != current_user.id or report.status not in EDITABLE_REPORT_STATUSES:
        flash('Acción no permitida.', 'danger')
        return redirect(url_for('reports.show', id=id))
        
    try:
        selected_flow = None
        info_response_comment = (request.form.get('info_response_comment') or '').strip()
        is_resubmitting_info = report.status == ReportStatus.NEEDS_INFO

        if is_resubmitting_info:
            if not info_response_comment:
                flash('Debes indicar qué antecedentes adicionales estás entregando antes de reenviar la rendición.', 'warning')
                return redirect(url_for('reports.show', id=id))

            if not report.approval_flow_id or not report.current_step:
                flash('La rendición no tiene un paso de aprobación válido para retomar la revisión.', 'danger')
                return redirect(url_for('reports.show', id=id))

            selected_flow = report.approval_flow
            decision = ApprovalDecision(
                report_id=report.id,
                user_id=current_user.id,
                step_number=report.current_step,
                decision='info_submitted',
                comments=info_response_comment
            )
            db.session.add(decision)
        else:
            selected_flow = _select_approval_flow(current_user.company_id, report.total_amount)
            
            if not selected_flow or not selected_flow.steps:
                db.session.rollback()
                flash('No existe un flujo de aprobación activo para esta rendición. El informe se mantiene en borrador hasta que un administrador configure uno.', 'warning')
                return redirect(url_for('reports.show', id=id))

            report.approval_flow_id = selected_flow.id
            report.current_step = 1 # Start at step 1

        report.status = ReportStatus.UNDER_REVIEW
        report.submitted_at = datetime.utcnow()
        
        # Update underlying expenses
        for exp in report.expenses:
            exp.status = ExpenseStatus.SUBMITTED
            
        current_step_obj, skipped_steps = _resolve_active_step(report, persist=True)

        if current_step_obj:
            _notify_step_if_needed(report, current_step_obj)
            db.session.commit()
            notify_report_submitted(report)

            if is_resubmitting_info:
                flash('Antecedentes adicionales reenviados al mismo aprobador.', 'success')
            elif skipped_steps:
                flash(
                    f'Informe enviado a revisión siguiendo el flujo: {selected_flow.name}. '
                    'Se omitió el paso de manager porque el solicitante no tiene manager asignado.',
                    'success',
                )
            else:
                flash(f'Informe enviado a revisión siguiendo el flujo: {selected_flow.name}', 'success')
        else:
            report.status = ReportStatus.APPROVED
            report.approved_at = datetime.utcnow()
            for exp in report.expenses:
                exp.status = ExpenseStatus.APPROVED

            db.session.commit()
            notify_report_approved(report)
            flash('La rendición quedó aprobada automáticamente porque no había aprobadores disponibles en el flujo.', 'info')
        
        log_action(
            action='report_resubmitted_with_info' if is_resubmitting_info else 'report_submitted',
            entity_type='report',
            entity_id=report.id,
            description=(
                f"Rendición '{report.title}' reenviada con antecedentes adicionales."
                if is_resubmitting_info else
                f"Informe '{report.title}' enviado para aprobación."
            )
        )
    except Exception as e:
        db.session.rollback()
        flash(f'Error al enviar informe: {str(e)}', 'danger')
        
    return redirect(url_for('reports.show', id=id))

@reports_bp.route('/<uuid:id>/approve', methods=['POST'])
@login_required
def approve(id):
    report = Report.query.get_or_404(id)
    comment = request.form.get('comment', '')

    if report.status not in REVIEW_STATUSES:
        flash('La rendición no está actualmente en revisión.', 'warning')
        return redirect(url_for('reports.show', id=id))
    
    # Check if report is in a flow
    if not report.approval_flow_id:
        # Fallback para rendiciones legacy sin flujo: solo admin puede actuar.
        if not current_user.is_admin:
            flash('No tienes permiso.', 'danger')
            return redirect(url_for('reports.show', id=id))
        
        try:
            report.status = ReportStatus.APPROVED
            report.approved_at = datetime.utcnow()
            for exp in report.expenses:
                exp.status = ExpenseStatus.APPROVED
            db.session.commit()
            flash('Informe aprobado.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('reports.show', id=id))

    # Flow based logic
    current_step, skipped_steps = _resolve_active_step(report, persist=True)
    
    if not current_step:
        flash('No existen aprobadores disponibles para el paso actual del flujo.', 'warning')
        return redirect(url_for('reports.show', id=id))
        
    # Verify if user can approve this step
    if not _is_user_current_step_approver(report, allow_admin_override=False):
        flash('No eres el aprobador designado para este paso.', 'warning')
        return redirect(url_for('reports.show', id=id))

    try:
        # Record decision
        decision = ApprovalDecision(
            report_id=report.id,
            user_id=current_user.id,
            step_number=report.current_step,
            decision='approved',
            comments=comment
        )
        db.session.add(decision)
        
        # Check for next step
        report.current_step += 1
        next_step, skipped_steps = _resolve_active_step(report, persist=True)
        if next_step:
            _notify_step_if_needed(report, next_step)

            if skipped_steps:
                flash('Paso aprobado. Se omitió un paso de manager sin asignación y la rendición avanzó al siguiente aprobador.', 'info')
            else:
                flash('Paso aprobado. El informe avanza al siguiente nivel.', 'info')
        else:
            # Final approval
            report.status = ReportStatus.APPROVED
            report.approved_at = datetime.utcnow()
            for exp in report.expenses:
                exp.status = ExpenseStatus.APPROVED
            
            # Notify requester of full approval
            notify_report_approved(report)
            
            flash('Aprobación final completada.', 'success')

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'danger')
        
    return redirect(url_for('reports.show', id=id))

@reports_bp.route('/<uuid:id>/reject', methods=['POST'])
@login_required
def reject(id):
    report = Report.query.get_or_404(id)
    comment = request.form.get('comment', '')

    if report.status not in REVIEW_STATUSES:
        flash('La rendición no está actualmente en revisión.', 'warning')
        return redirect(url_for('reports.show', id=id))
    
    if not comment:
        flash('Debes indicar un motivo de rechazo.', 'warning')
        return redirect(url_for('reports.show', id=id))

    try:
        # Record decision
        decision = ApprovalDecision(
            report_id=report.id,
            user_id=current_user.id,
            step_number=report.current_step,
            decision='rejected',
            comments=comment
        )
        db.session.add(decision)
        
        report.status = 'rejected'
        for exp in report.expenses:
            exp.status = 'rejected'
            
        # Notify requester of rejection
        notify_report_rejected(report, comment)
            
        db.session.commit()
        
        log_action(
            action='report_rejected',
            entity_type='report',
            entity_id=report.id,
            description=f"Informe '{report.title}' rechazado por {current_user.full_name}. Motivo: {comment}"
        )
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'danger')
        
    return redirect(url_for('reports.show', id=id))


@reports_bp.route('/<uuid:id>/request-info', methods=['POST'])
@login_required
def request_info(id):
    report = Report.query.get_or_404(id)
    comment = (request.form.get('comment') or '').strip()

    if report.status not in REVIEW_STATUSES:
        flash('La rendición no está actualmente en revisión.', 'warning')
        return redirect(url_for('reports.show', id=id))

    if not comment:
        flash('Debes indicar qué antecedentes adicionales estás solicitando.', 'warning')
        return redirect(url_for('reports.show', id=id))

    if report.approval_flow_id:
        current_step = ApprovalStep.query.filter_by(
            flow_id=report.approval_flow_id,
            step_number=report.current_step
        ).first()

        if not current_step:
            flash('Error en configuración de flujo.', 'danger')
            return redirect(url_for('reports.show', id=id))

        if not _is_user_current_step_approver(report, allow_admin_override=False):
            flash('No eres el aprobador designado para este paso.', 'warning')
            return redirect(url_for('reports.show', id=id))
    elif not current_user.is_admin:
        flash('No tienes permiso.', 'danger')
        return redirect(url_for('reports.show', id=id))

    try:
        decision = ApprovalDecision(
            report_id=report.id,
            user_id=current_user.id,
            step_number=report.current_step,
            decision='info_requested',
            comments=comment
        )
        db.session.add(decision)

        report.status = ReportStatus.NEEDS_INFO
        for exp in report.expenses:
            exp.status = ExpenseStatus.DRAFT

        db.session.commit()
        notify_report_info_requested(report, comment)

        log_action(
            action='report_info_requested',
            entity_type='report',
            entity_id=report.id,
            description=f"Se solicitaron antecedentes adicionales para la rendición '{report.title}'. Motivo: {comment}"
        )
        flash('Se solicitaron antecedentes adicionales al solicitante.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al solicitar antecedentes: {str(e)}', 'danger')

    return redirect(url_for('reports.show', id=id))

@reports_bp.route('/<uuid:id>/export')
@login_required
def export_pdf(id):
    report = Report.query.get_or_404(id)
    
    # Solo se puede exportar si está aprobado o si es para revisión interna
    if not _can_view_report(report):
        flash('No tienes permiso', 'danger')
        return redirect(url_for('reports.index'))
        
    return generate_report_pdf(report)


@reports_bp.route('/<uuid:id>/mark-paid', methods=['POST'])
@login_required
def mark_paid(id):
    report = Report.query.get_or_404(id)

    if not _can_mark_report_paid(report):
        flash('No tienes permiso para marcar esta rendición como pagada.', 'danger')
        return redirect(url_for('reports.show', id=id))

    try:
        report.status = ReportStatus.PAID
        report.paid_at = datetime.utcnow()
        for expense in report.expenses:
            expense.status = ExpenseStatus.PAID

        db.session.commit()
        notify_report_paid(report)

        log_action(
            action='report_paid',
            entity_type='report',
            entity_id=report.id,
            description=f"Rendición '{report.title}' marcada como pagada por {current_user.full_name}."
        )
        flash('Rendición marcada como pagada.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al marcar la rendición como pagada: {str(e)}', 'danger')

    return redirect(url_for('reports.show', id=id))
