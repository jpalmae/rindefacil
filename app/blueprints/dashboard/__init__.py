from flask import Blueprint, render_template
from flask_login import login_required, current_user
from app.models.expense import Expense, ExpenseStatus
from app.models.category import Category
from app.models.cost_center import CostCenter
from app.models.report import Report
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from app.extensions import db

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/')
@login_required
def index():
    # Obtener gastos recientes del usuario
    recent_expenses = Expense.query.filter_by(
        user_id=current_user.id
    ).options(
        selectinload(Expense.category)
    ).order_by(Expense.created_at.desc()).limit(5).all()
    
    # Calcular totales del usuario actual
    total_draft = db.session.query(func.sum(Expense.amount_clp)).filter_by(user_id=current_user.id, status=ExpenseStatus.DRAFT).scalar() or 0
    total_approved = db.session.query(func.sum(Expense.amount_clp)).filter_by(user_id=current_user.id, status=ExpenseStatus.APPROVED).scalar() or 0
    
    # Analytics Corporativos (solo para Admin)
    analytics_data = {}
    if current_user.is_admin:
        # Gastos por Categoría
        cat_stats = db.session.query(Category.name, func.sum(Expense.amount_clp))\
            .join(Expense, Expense.category_id == Category.id)\
            .filter(Expense.company_id == current_user.company_id)\
            .group_by(Category.name).all()
        
        # Gastos por Centro de Costo vs Presupuesto
        cc_data = db.session.query(
            CostCenter.name, 
            CostCenter.monthly_budget,
            func.sum(Expense.amount_clp).label('actual')
        ).join(Expense, Expense.cost_center_id == CostCenter.id, isouter=True)\
         .filter(CostCenter.company_id == current_user.company_id)\
         .group_by(CostCenter.id, CostCenter.name, CostCenter.monthly_budget).all()
            
        analytics_data = {
            'by_category': {name: float(amount) for name, amount in cat_stats},
            'by_cost_center': [
                {
                    'name': name, 
                    'budget': float(budget or 0), 
                    'actual': float(actual or 0)
                } for name, budget, actual in cc_data
            ]
        }

    return render_template('dashboard/index.html', 
                          recent_expenses=recent_expenses,
                          total_draft=total_draft,
                          total_approved=total_approved,
                          analytics=analytics_data)
