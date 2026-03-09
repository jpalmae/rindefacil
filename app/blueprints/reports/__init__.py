from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models.report import Report, ReportStatus
from app.models.expense import Expense, ExpenseStatus
from app.models.approval import ApprovalFlow, ApprovalStep, ApprovalDecision
from datetime import datetime
from uuid import UUID
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload
from app.services.export_service import generate_report_pdf
from app.services.notification_service import notify_approval_needed, notify_report_approved, notify_report_rejected
from app.models import User
from app.services.audit_service import log_action

reports_bp = Blueprint('reports', __name__)

@reports_bp.route('/')
@login_required
def index():
    base_query = Report.query.options(joinedload(Report.user))
    if current_user.has_role('admin') or current_user.has_role('manager'):
        reports = base_query.filter_by(company_id=current_user.company_id).order_by(Report.created_at.desc()).all()
    else:
        reports = base_query.filter_by(user_id=current_user.id).order_by(Report.created_at.desc()).all()

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

    return render_template('reports/index.html', reports=reports, expense_counts=expense_counts)

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
        selected_expense_ids = request.form.getlist('expense_ids')
        
        if not title:
            flash('Debes ingresar un título para el informe.', 'warning')
            return redirect(url_for('reports.new'))
            
        if not selected_expense_ids:
            flash('Debes seleccionar al menos un gasto para incluir en el informe.', 'warning')
            return redirect(url_for('reports.new'))
            
        try:
            # Create report
            report = Report(
                company_id=current_user.company_id,
                user_id=current_user.id,
                title=title,
                description=description,
                status=ReportStatus.DRAFT
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
                total_amount += exp.amount
            
            report.total_amount = total_amount
            db.session.commit()
            
            flash('Informe creado exitosamente.', 'success')
            return redirect(url_for('reports.show', id=report.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al crear informe: {str(e)}', 'danger')
            
    return render_template('reports/form.html', expenses=available_expenses)

@reports_bp.route('/<uuid:id>')
@login_required
def show(id):
    report = Report.query.options(
        joinedload(Report.user),
        joinedload(Report.approval_flow).selectinload(ApprovalFlow.steps),
        selectinload(Report.decisions).joinedload(ApprovalDecision.user)
    ).get_or_404(id)
    
    # Simple ACL
    if report.company_id != current_user.company_id:
        flash('No tienes permiso para ver este informe.', 'danger')
        return redirect(url_for('dashboard.index'))
        
    if report.user_id != current_user.id and not current_user.has_role('admin') and not current_user.has_role('manager'):
        flash('No tienes permiso para ver este informe.', 'danger')
        return redirect(url_for('dashboard.index'))
        
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
        expense_count=len(expenses)
    )

@reports_bp.route('/<uuid:id>/submit', methods=['POST'])
@login_required
def submit(id):
    report = Report.query.get_or_404(id)
    
    if report.user_id != current_user.id or report.status != ReportStatus.DRAFT:
        flash('Acción no permitida.', 'danger')
        return redirect(url_for('reports.show', id=id))
        
    try:
        # Phase 3: Selection of Approval Flow
        flows = ApprovalFlow.query.filter_by(company_id=current_user.company_id, is_active=True).all()
        selected_flow = None
        
        # Rule matching (Basic: amount based)
        for flow in flows:
            rules = flow.trigger_rules or {}
            min_amount = float(rules.get('min_amount', 0))
            if float(report.total_amount) >= min_amount:
                # Select the most restrictive or just the first matching for now
                selected_flow = flow
                break
        
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
            
        # Notify potential approvers of Step 1
        current_step_obj = selected_flow.steps[0] if selected_flow.steps else None
        if current_step_obj:
            # Find users that match this step to notify
            if current_step_obj.approver_type == 'role':
                potential_approvers = User.query.filter_by(company_id=current_user.company_id, role=current_step_obj.approver_target).all()
                for approver in potential_approvers:
                    notify_approval_needed(approver.id, report)
            elif current_step_obj.approver_type == 'user':
                notify_approval_needed(current_step_obj.approver_target, report)
            elif current_step_obj.approver_type == 'manager' and report.user.manager_id:
                notify_approval_needed(report.user.manager_id, report)
            
        flash(f'Informe enviado a revisión siguiendo el flujo: {selected_flow.name}', 'success')
            
        db.session.commit()
        
        log_action(
            action='report_submitted',
            entity_type='report',
            entity_id=report.id,
            description=f"Informe '{report.title}' enviado para aprobación."
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
    
    # Check if report is in a flow
    if not report.approval_flow_id:
        # Fallback to old behavior for legacy or no-flow reports
        if not current_user.has_role('admin') and not current_user.has_role('manager'):
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
    current_step = ApprovalStep.query.filter_by(
        flow_id=report.approval_flow_id, 
        step_number=report.current_step
    ).first()
    
    if not current_step:
        flash('Error en configuración de flujo.', 'danger')
        return redirect(url_for('reports.show', id=id))
        
    # Verify if user can approve this step
    can_approve = False
    if current_step.approver_type == 'role':
        if current_user.has_role(current_step.approver_target):
            can_approve = True
    elif current_step.approver_type == 'user':
        if str(current_user.id) == current_step.approver_target:
            can_approve = True
    elif current_step.approver_type == 'manager':
        # Check if user is the manager of the reporter
        if report.user.manager_id == current_user.id:
            can_approve = True

    if not can_approve and not current_user.has_role('admin'):
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
        next_step = ApprovalStep.query.filter_by(
            flow_id=report.approval_flow_id,
            step_number=report.current_step + 1
        ).first()
        if next_step:
            report.current_step += 1
            if next_step.approver_type == 'role':
                potential_approvers = User.query.filter_by(company_id=current_user.company_id, role=next_step.approver_target).all()
                for approver in potential_approvers:
                    notify_approval_needed(approver.id, report)
            elif next_step.approver_type == 'user':
                notify_approval_needed(next_step.approver_target, report)
            elif next_step.approver_type == 'manager' and report.user.manager_id:
                notify_approval_needed(report.user.manager_id, report)
            
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

@reports_bp.route('/<uuid:id>/export')
@login_required
def export_pdf(id):
    report = Report.query.get_or_404(id)
    
    # Solo se puede exportar si está aprobado o si es para revisión interna
    if report.company_id != current_user.company_id:
        flash('No tienes permiso', 'danger')
        return redirect(url_for('reports.index'))
        
    return generate_report_pdf(report)
