from datetime import datetime, timezone
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
from app.services.location_service import evaluate_expense_integrity, reverse_geocode
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


def _parse_iso_datetime(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None


def _parse_time(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    normalized = raw.replace('.', ':')
    for fmt in ('%H:%M', '%H:%M:%S', '%I:%M %p', '%I:%M%p'):
        try:
            return datetime.strptime(normalized, fmt).time()
        except ValueError:
            continue
    return None


def _normalize_date_for_input(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


@expenses_bp.route('/')
@login_required
def index():
    expenses = Expense.query.filter_by(
        user_id=current_user.id
    ).options(
        selectinload(Expense.category)
    ).order_by(Expense.created_at.desc()).all()

    return render_template('expenses/index.html', expenses=expenses)


@expenses_bp.route('/<uuid:id>/delete', methods=['POST'])
@login_required
def delete(id):
    expense = Expense.query.get_or_404(id)

    if expense.user_id != current_user.id:
        flash('No tienes permiso para eliminar este gasto.', 'danger')
        return redirect(url_for('expenses.index'))

    if expense.status != ExpenseStatus.DRAFT or expense.report_id is not None:
        flash('Solo puedes eliminar gastos en borrador que no estén dentro de una rendición.', 'warning')
        return redirect(url_for('expenses.index'))

    try:
        expense_public_id = expense.public_id
        expense_merchant = expense.merchant or 'Sin comercio'
        db.session.delete(expense)
        db.session.commit()

        log_action(
            action='expense_deleted',
            entity_type='expense',
            entity_id=id,
            description=f"Gasto '{expense_public_id}' ({expense_merchant}) eliminado por el usuario."
        )
        flash('Gasto eliminado.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al eliminar el gasto: {str(e)}', 'danger')

    return redirect(url_for('expenses.index'))


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
        receipt_time = _parse_time(request.form.get('receipt_time'))
        gps_latitude_raw = request.form.get('gps_latitude')
        gps_longitude_raw = request.form.get('gps_longitude')
        gps_accuracy_raw = request.form.get('gps_accuracy_m')
        gps_address = (request.form.get('gps_address') or '').strip() or None
        gps_captured_at = _parse_iso_datetime(request.form.get('gps_captured_at')) or datetime.now(timezone.utc)

        if not (gps_latitude_raw and gps_longitude_raw):
            flash('Debes habilitar GPS para rendir un gasto.', 'danger')
            return redirect(url_for('expenses.new'))

        gps_latitude = _parse_coordinate(gps_latitude_raw, -90, 90)
        gps_longitude = _parse_coordinate(gps_longitude_raw, -180, 180)
        gps_accuracy_m = _parse_non_negative_decimal(gps_accuracy_raw)

        if gps_latitude is None or gps_longitude is None:
            flash('La ubicación GPS no es válida. Intenta capturarla nuevamente.', 'danger')
            return redirect(url_for('expenses.new'))

        if len(description.strip()) < 15:
            flash('El motivo debe tener un mínimo de 15 caracteres.', 'danger')
            return redirect(url_for('expenses.new'))

        if amount is None or amount <= 0:
            flash('El monto ingresado no es válido.', 'danger')
            return redirect(url_for('expenses.new'))

        try:
            expense_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            resolved_address = gps_address
            if not resolved_address:
                geocode_result = reverse_geocode(float(gps_latitude), float(gps_longitude))
                resolved_address = geocode_result.get('display_name') if geocode_result else None
            if not resolved_address:
                resolved_address = f"Lat {gps_latitude}, Lon {gps_longitude}"

            geo_validation = evaluate_expense_integrity(
                merchant=merchant,
                address=resolved_address,
                accuracy_m=gps_accuracy_m,
                receipt_date=expense_date,
                rendered_at=gps_captured_at,
                receipt_time=receipt_time,
                time_tolerance_minutes=20,
            )

            expense = Expense(
                user_id=current_user.id,
                company_id=current_user.company_id,
                amount=amount,
                merchant=merchant,
                client_partner=client_partner,
                date=expense_date,
                receipt_time=receipt_time,
                category_id=category_id,
                description=description,
                status=ExpenseStatus.DRAFT,
                gps_latitude=gps_latitude,
                gps_longitude=gps_longitude,
                gps_accuracy_m=gps_accuracy_m,
                gps_captured_at=gps_captured_at,
                gps_address=resolved_address,
                gps_validation_status=geo_validation['status'],
                gps_validation_score=geo_validation['score'],
                gps_validation_reason=geo_validation['reason'],
                gps_validation_meta={
                    'matched_tokens': geo_validation.get('matched_tokens', []),
                    'components': geo_validation.get('components', []),
                },
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

            if geo_validation['status'] == 'mismatch':
                if geo_validation.get('reason') == 'receipt_date_mismatch':
                    flash('Advertencia: la fecha de la boleta no coincide con la fecha de rendición.', 'warning')
                elif geo_validation.get('reason') == 'receipt_time_mismatch':
                    flash('Advertencia: la hora de la boleta no coincide con la hora de rendición (margen 20 min).', 'warning')
                elif geo_validation.get('reason') == 'weekend_submission':
                    flash('Advertencia: gasto registrado en fin de semana (riesgo potencial).', 'warning')
                elif geo_validation.get('reason') == 'outside_business_hours':
                    flash('Advertencia: gasto fuera del horario habitual (L-V 09:00 a 19:00).', 'warning')
                else:
                    flash('Advertencia: la ubicación GPS no coincide con el comercio informado.', 'warning')
            elif geo_validation['status'] == 'partial':
                if geo_validation.get('reason') == 'weekend_submission':
                    flash('Advertencia: gasto registrado en fin de semana (riesgo potencial).', 'warning')
                elif geo_validation.get('reason') == 'outside_business_hours':
                    flash('Advertencia: gasto fuera del horario habitual (L-V 09:00 a 19:00).', 'warning')
                elif geo_validation.get('reason') == 'outside_business_hours_near':
                    flash('Advertencia: gasto levemente fuera del horario habitual (L-V 09:00 a 19:00).', 'warning')
                else:
                    flash('Advertencia: coincidencia parcial en validaciones de ubicación/fecha/hora.', 'warning')

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

        if data.get('date'):
            normalized_date = _normalize_date_for_input(data.get('date'))
            if normalized_date:
                data['date'] = normalized_date

        if data.get('category'):
            category = Category.query.filter(
                Category.company_id == current_user.company_id,
                Category.name.ilike(f"%{data['category']}%"),
            ).first()
            if category:
                data['category_id'] = str(category.id)

        has_detected_fields = any(
            data.get(key) for key in ('amount', 'merchant', 'date', 'time', 'category_id', 'category')
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


@expenses_bp.route('/reverse-geocode', methods=['POST'])
@login_required
def reverse_geocode_lookup():
    payload = request.get_json(silent=True) or {}
    latitude = _parse_coordinate(payload.get('latitude'), -90, 90)
    longitude = _parse_coordinate(payload.get('longitude'), -180, 180)

    if latitude is None or longitude is None:
        return jsonify({'error': 'Coordenadas inválidas.'}), 400

    result = reverse_geocode(float(latitude), float(longitude))
    return jsonify({'address': result.get('display_name') if result else None}), 200
