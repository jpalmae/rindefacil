from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import os
import re

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models.category import Category
from app.models.expense import Expense, ExpenseCurrency, ExpenseStatus, ExpenseType
from app.services.audit_service import log_action
from app.services.exchange_rate_service import get_usd_exchange_rate_for_date
from app.services.location_service import evaluate_expense_integrity, reverse_geocode
from app.services.ocr_service import calculate_receipt_hash, extract_expense_data

expenses_bp = Blueprint('expenses', __name__)

MILEAGE_DEFAULT_FUEL_PRICE = Decimal('1390')
MILEAGE_DEFAULT_EFFICIENCY = Decimal('12')
MILEAGE_DEFAULT_CORRECTION_FACTOR = Decimal('0.8')


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


def _normalize_currency(value):
    currency = (str(value).strip().upper() if value is not None else ExpenseCurrency.CLP) or ExpenseCurrency.CLP
    if currency not in ExpenseCurrency.CHOICES:
        return None
    return currency


def _normalize_expense_type(value):
    expense_type = (str(value).strip().lower() if value is not None else ExpenseType.RECEIPT) or ExpenseType.RECEIPT
    if expense_type not in ExpenseType.LABELS:
        return None
    return expense_type


def _compute_amount_clp(amount, currency, exchange_rate):
    if amount is None:
        return None
    if currency == ExpenseCurrency.CLP:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if exchange_rate is None or exchange_rate <= 0:
        return None
    return (amount * exchange_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _compute_mileage_amount(distance_km, fuel_price_per_liter, vehicle_efficiency_km_l, correction_factor):
    if any(value is None for value in (distance_km, fuel_price_per_liter, vehicle_efficiency_km_l, correction_factor)):
        return None
    if distance_km <= 0 or fuel_price_per_liter <= 0 or vehicle_efficiency_km_l <= 0 or correction_factor < 0:
        return None
    adjusted_distance = distance_km * (Decimal("1") + correction_factor)
    return ((adjusted_distance / vehicle_efficiency_km_l) * fuel_price_per_liter).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


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
        expense_type = _normalize_expense_type(request.form.get('expense_type'))
        amount_raw = request.form.get('amount')
        amount = _parse_amount(amount_raw)
        currency = _normalize_currency(request.form.get('currency'))
        exchange_rate = _parse_non_negative_decimal(request.form.get('exchange_rate'))
        merchant = (request.form.get('merchant') or '').strip() or None
        client_partner = request.form.get('client_partner')
        date_str = request.form.get('date')
        category_id = request.form.get('category_id') or None
        description = request.form.get('description', '')
        receipt_time = _parse_time(request.form.get('receipt_time'))
        distance_km = _parse_non_negative_decimal(request.form.get('distance_km'))
        fuel_price_per_liter = _parse_non_negative_decimal(request.form.get('fuel_price_per_liter'))
        vehicle_efficiency_km_l = _parse_non_negative_decimal(request.form.get('vehicle_efficiency_km_l'))
        correction_factor = _parse_non_negative_decimal(request.form.get('correction_factor'))
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

        if expense_type is None:
            flash('El tipo de gasto seleccionado no es válido.', 'danger')
            return redirect(url_for('expenses.new'))

        auto_rate_date = None
        if date_str:
            try:
                auto_rate_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            except ValueError:
                auto_rate_date = None

        if expense_type == ExpenseType.MILEAGE:
            amount = _compute_mileage_amount(
                distance_km=distance_km,
                fuel_price_per_liter=fuel_price_per_liter,
                vehicle_efficiency_km_l=vehicle_efficiency_km_l,
                correction_factor=correction_factor,
            )
            if amount is None or amount <= 0:
                flash('Debes ingresar kilómetros, precio litro, rendimiento y factor de corrección válidos para vehículo particular.', 'danger')
                return redirect(url_for('expenses.new'))
            currency = ExpenseCurrency.CLP
            exchange_rate = Decimal('1')
        else:
            if amount is None or amount <= 0:
                flash('El monto ingresado no es válido.', 'danger')
                return redirect(url_for('expenses.new'))

            if currency is None:
                flash('La moneda seleccionada no es válida.', 'danger')
                return redirect(url_for('expenses.new'))

            if currency == ExpenseCurrency.USD and (exchange_rate is None or exchange_rate <= 0):
                auto_rate = get_usd_exchange_rate_for_date(auto_rate_date) if auto_rate_date else None
                exchange_rate = auto_rate['exchange_rate'] if auto_rate else None
                if exchange_rate is None or exchange_rate <= 0:
                    flash('Debes ingresar un tipo de cambio válido para gastos en USD.', 'danger')
                    return redirect(url_for('expenses.new'))

            if currency == ExpenseCurrency.CLP:
                exchange_rate = Decimal('1')

        amount_clp = _compute_amount_clp(amount, currency, exchange_rate)
        if amount_clp is None or amount_clp <= 0:
            flash('No fue posible calcular el monto en CLP para este gasto.', 'danger')
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
                currency=currency,
                exchange_rate=exchange_rate,
                amount_clp=amount_clp,
                distance_km=distance_km if expense_type == ExpenseType.MILEAGE else None,
                fuel_price_per_liter=fuel_price_per_liter if expense_type == ExpenseType.MILEAGE else None,
                vehicle_efficiency_km_l=vehicle_efficiency_km_l if expense_type == ExpenseType.MILEAGE else None,
                correction_factor=correction_factor if expense_type == ExpenseType.MILEAGE else None,
                merchant=merchant,
                client_partner=client_partner,
                date=expense_date,
                receipt_time=receipt_time,
                category_id=category_id,
                description=description,
                status=ExpenseStatus.DRAFT,
                expense_type=expense_type,
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
                    Expense.currency == currency,
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
                if float(amount_clp) > max_amt:
                    flash(
                        f'El monto equivalente en CLP excede el límite permitido por la política de su empresa (${max_amt:,.0f}). Guardado como borrador con advertencia.',
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
                description=f"Gasto {expense.expense_type_label} creado por {expense.amount} {expense.currency} (CLP {expense.amount_clp}) en {expense.merchant or 'sin comercio'}",
            )

            flash('Gasto creado exitosamente.', 'success')
            return redirect(url_for('expenses.index'))

        except Exception as e:
            db.session.rollback()
            flash(f'Error al crear gasto: {str(e)}', 'danger')

    return render_template(
        'expenses/form.html',
        categories=categories,
        currency_options=ExpenseCurrency.CHOICES,
        default_currency=ExpenseCurrency.CLP,
        expense_type_options={
            ExpenseType.RECEIPT: ExpenseType.LABELS[ExpenseType.RECEIPT],
            ExpenseType.MILEAGE: ExpenseType.LABELS[ExpenseType.MILEAGE],
        },
        default_expense_type=ExpenseType.RECEIPT,
        mileage_defaults={
            'fuel_price_per_liter': _amount_for_input(MILEAGE_DEFAULT_FUEL_PRICE),
            'vehicle_efficiency_km_l': _amount_for_input(MILEAGE_DEFAULT_EFFICIENCY),
            'correction_factor': _amount_for_input(MILEAGE_DEFAULT_CORRECTION_FACTOR),
        },
    )


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


@expenses_bp.route('/exchange-rate', methods=['GET'])
@login_required
def exchange_rate_lookup():
    currency = _normalize_currency(request.args.get('currency'))
    date_raw = request.args.get('date')
    if currency != ExpenseCurrency.USD:
        return jsonify({'exchange_rate': '1', 'source': 'manual', 'date': date_raw}), 200

    if not date_raw:
        return jsonify({'error': 'Debes indicar fecha.'}), 400

    try:
        target_date = datetime.strptime(date_raw, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Fecha inválida. Usa YYYY-MM-DD.'}), 400

    result = get_usd_exchange_rate_for_date(target_date)
    if not result:
        return jsonify({'error': 'No fue posible obtener el tipo de cambio automático para esa fecha.'}), 404

    return jsonify({
        'exchange_rate': format(result['exchange_rate'], 'f'),
        'source': result['source'],
        'source_detail': result['source_detail'],
        'date': result['date'].isoformat(),
    }), 200
