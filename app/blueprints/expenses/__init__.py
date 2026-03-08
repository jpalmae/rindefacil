from datetime import datetime
from decimal import Decimal, InvalidOperation
import os
import re

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models.category import Category
from app.models.expense import Expense, ExpenseStatus
from app.services.audit_service import log_action
from app.services.ocr_service import calculate_receipt_hash, extract_expense_data

expenses_bp = Blueprint('expenses', __name__)


def _parse_amount(value):
    """
    Normaliza montos con formatos locales/internacionales a Decimal.
    Ejemplos válidos: 2500, 2.500, 2,500, 2.500,75, 2,500.75
    """
    if value is None:
        return None

    if isinstance(value, Decimal):
        return value

    if isinstance(value, (int, float)):
        return Decimal(str(value))

    raw = str(value).strip()
    if not raw:
        return None

    # Mantener solo dígitos, separadores y signo.
    cleaned = re.sub(r"[^\d,.\-]", "", raw)
    if not cleaned:
        return None

    negative = cleaned.startswith("-")
    cleaned = cleaned.replace("-", "")
    if not cleaned:
        return None

    if "." in cleaned and "," in cleaned:
        # El último separador suele ser el decimal
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
        # Caso chileno típico: 2.500 => miles
        if len(right) == 3 and len(left) >= 1:
            normalized = f"{left}{right}"
        else:
            normalized = cleaned
    elif "," in cleaned and "." not in cleaned:
        left, right = cleaned.split(",", 1)
        # Caso miles con coma: 2,500
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


def _amount_for_input(value):
    """Serializa Decimal a string estable para input type=number."""
    amount = _parse_amount(value)
    if amount is None:
        return None

    normalized = amount.normalize()
    if normalized == normalized.to_integral():
        return str(int(normalized))
    return format(normalized, "f")


@expenses_bp.route('/')
@login_required
def index():
    expenses = Expense.query.filter_by(
        user_id=current_user.id
    ).options(
        selectinload(Expense.category)
    ).order_by(Expense.created_at.desc()).all()

    return render_template('expenses/index.html', expenses=expenses)


@expenses_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new():
    categories = Category.query.filter_by(company_id=current_user.company_id).all()

    if request.method == 'POST':
        amount_raw = request.form.get('amount')
        amount = _parse_amount(amount_raw)
        merchant = request.form.get('merchant')
        client_partner = request.form.get('client_partner')
        date_str = request.form.get('date')
        category_id = request.form.get('category_id') or None
        description = request.form.get('description', '')

        if len(description.strip()) < 15:
            flash('El motivo debe tener un mínimo de 15 caracteres.', 'danger')
            return redirect(url_for('expenses.new'))

        if amount is None or amount <= 0:
            flash('El monto ingresado no es válido.', 'danger')
            return redirect(url_for('expenses.new'))

        try:
            expense_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            expense = Expense(
                user_id=current_user.id,
                company_id=current_user.company_id,
                amount=amount,
                merchant=merchant,
                client_partner=client_partner,
                date=expense_date,
                category_id=category_id,
                description=description,
                status=ExpenseStatus.DRAFT,
            )

            # Subida de imagen local
            if 'receipt' in request.files:
                file = request.files['receipt']
                if file.filename != '':
                    filename = secure_filename(file.filename)
                    file_path = os.path.join(
                        current_app.config['UPLOAD_FOLDER'],
                        f"{current_user.company_id}_{filename}",
                    )
                    file.save(file_path)
                    expense.receipt_url = f"/static/uploads/{current_user.company_id}_{filename}"

                    # Duplicado por hash de imagen
                    r_hash = calculate_receipt_hash(file_path)
                    if r_hash:
                        expense.receipt_hash = r_hash
                        existing = Expense.query.filter(
                            Expense.company_id == current_user.company_id,
                            Expense.receipt_hash == r_hash,
                            Expense.id != expense.id,
                        ).first()
                        if existing:
                            expense.is_duplicate = True
                            expense.duplicate_of_id = existing.id
                            flash('¡Atención! Este comprobante parece haber sido subido anteriormente.', 'warning')

            # Duplicado por monto/fecha
            if not expense.is_duplicate:
                existing_data = Expense.query.filter(
                    Expense.company_id == current_user.company_id,
                    Expense.amount == amount,
                    Expense.date == expense_date,
                    Expense.id != expense.id,
                ).first()
                if existing_data:
                    expense.is_duplicate = True
                    expense.duplicate_of_id = existing_data.id
                    flash('Existe un gasto con el mismo monto y fecha. Se ha marcado como posible duplicado.', 'warning')

            # Políticas
            from app.models.policy import Policy

            company_policy = Policy.query.filter_by(company_id=current_user.company_id, is_active=True).first()
            if company_policy and 'max_amount' in company_policy.rules:
                max_amt = float(company_policy.rules['max_amount'])
                if float(amount) > max_amt:
                    flash(
                        f'El monto excede el límite permitido por la política de su empresa (${max_amt:,.0f}). Guardado como borrador con advertencia.',
                        'warning',
                    )

            db.session.add(expense)
            db.session.commit()

            log_action(
                action='expense_created',
                entity_type='expense',
                entity_id=expense.id,
                description=f"Gasto creado por {expense.amount} {expense.currency} en {expense.merchant}",
            )

            flash('Gasto creado exitosamente.', 'success')
            return redirect(url_for('expenses.index'))

        except Exception as e:
            db.session.rollback()
            flash(f'Error al crear gasto: {str(e)}', 'danger')

    return render_template('expenses/form.html', categories=categories)


@expenses_bp.route('/extract-data', methods=['POST'])
@login_required
def extract_data():
    if 'receipt' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['receipt']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    temp_path = None
    try:
        filename = secure_filename(file.filename)
        temp_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, f"tmp_ocr_{current_user.id}_{filename}")
        file.save(temp_path)

        data = extract_expense_data(temp_path)
        if not data:
            return jsonify({'error': 'No fue posible extraer datos del comprobante. Completa el formulario manualmente.'}), 422

        if data.get('amount') is not None:
            parsed_amount = _amount_for_input(data.get('amount'))
            if parsed_amount is not None:
                data['amount'] = parsed_amount

        if data.get('category'):
            category = Category.query.filter(
                Category.company_id == current_user.company_id,
                Category.name.ilike(f"%{data['category']}%"),
            ).first()
            if category:
                data['category_id'] = str(category.id)

        has_detected_fields = any(
            data.get(key) for key in ('amount', 'merchant', 'date', 'category_id', 'category')
        )
        if not has_detected_fields:
            return jsonify({'error': 'No se detectaron campos útiles en la imagen. Puedes completar los datos manualmente.'}), 422

        return jsonify({'data': data}), 200

    except Exception as e:
        current_app.logger.error(f"Error en endpoint extract_data: {e}")
        return jsonify({'error': str(e)}), 500

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                current_app.logger.warning(f"No se pudo eliminar archivo temporal: {temp_path}")
