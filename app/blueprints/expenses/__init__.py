from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import os
import re
import uuid

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models.category import Category
from app.models.expense import Expense, ExpenseCurrency, ExpenseStatus, ExpenseType
from app.models.report import ReportStatus
from app.services.audit_service import log_action
from app.services.exchange_rate_service import get_usd_exchange_rate_for_date
from app.services.location_service import evaluate_expense_integrity, reverse_geocode
from app.services.ocr_service import calculate_receipt_hash, extract_expense_data

expenses_bp = Blueprint('expenses', __name__)

MILEAGE_DEFAULT_FUEL_PRICE = Decimal('1390')
MILEAGE_DEFAULT_EFFICIENCY = Decimal('12')
MILEAGE_DEFAULT_CORRECTION_FACTOR = Decimal('0.8')


def _store_uploaded_receipt(file_storage, company_id):
    filename = secure_filename(file_storage.filename or "receipt")
    if not filename:
        filename = "receipt"

    unique_name = f"{company_id}_{uuid.uuid4().hex}_{filename}"
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_name)
    file_storage.save(file_path)
    return file_path, f"/static/uploads/{unique_name}"


def _parse_amount(value, currency=None):
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


def _amount_for_input(value, currency=None):
    """Serializa Decimal a string estable para input type=number."""
    amount = _parse_amount(value, currency=currency)
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


def _can_edit_expense(expense):
    if expense.user_id != current_user.id:
        return False
    if expense.status != ExpenseStatus.DRAFT:
        return False
    if expense.report_id and expense.report and expense.report.status != ReportStatus.DRAFT:
        return False
    return True


def _recalculate_report_total(report):
    if not report:
        return
    report.total_amount = db.session.query(
        func.coalesce(func.sum(Expense.amount_clp), Decimal("0"))
    ).filter(
        Expense.report_id == report.id
    ).scalar() or Decimal("0")


def _expense_form_context(expense=None):
    initial_expense = None
    if expense:
        receipt_name = None
        if expense.receipt_url:
            receipt_name = os.path.basename(expense.receipt_url.split("?", 1)[0])
        initial_expense = {
            'expenseType': expense.expense_type or ExpenseType.RECEIPT,
            'currency': expense.currency or ExpenseCurrency.CLP,
            'exchangeRate': _amount_for_input(expense.exchange_rate),
            'amount': _amount_for_input(expense.amount, currency=expense.currency),
            'distanceKm': _amount_for_input(expense.distance_km),
            'fuelPricePerLiter': _amount_for_input(expense.fuel_price_per_liter) or _amount_for_input(MILEAGE_DEFAULT_FUEL_PRICE),
            'vehicleEfficiencyKmL': _amount_for_input(expense.vehicle_efficiency_km_l) or _amount_for_input(MILEAGE_DEFAULT_EFFICIENCY),
            'correctionFactor': _amount_for_input(expense.correction_factor) or _amount_for_input(MILEAGE_DEFAULT_CORRECTION_FACTOR),
            'merchant': expense.merchant or '',
            'clientPartner': expense.client_partner or '',
            'date': expense.date.isoformat() if expense.date else '',
            'receiptTime': expense.receipt_time.strftime('%H:%M') if expense.receipt_time else '',
            'categoryId': str(expense.category_id) if expense.category_id else '',
            'description': expense.description or '',
            'receiptUrl': expense.receipt_url or '',
            'receiptName': receipt_name or '',
            'gpsLatitude': _amount_for_input(expense.gps_latitude),
            'gpsLongitude': _amount_for_input(expense.gps_longitude),
            'gpsAccuracyM': _amount_for_input(expense.gps_accuracy_m),
            'gpsCapturedAt': expense.gps_captured_at.isoformat() if expense.gps_captured_at else '',
            'gpsAddress': expense.gps_address or '',
        }

    return {
        'categories': Category.query.filter_by(company_id=current_user.company_id).all(),
        'currency_options': ExpenseCurrency.CHOICES,
        'default_currency': (expense.currency if expense else ExpenseCurrency.CLP),
        'expense_type_options': {
            ExpenseType.RECEIPT: ExpenseType.LABELS[ExpenseType.RECEIPT],
            ExpenseType.MILEAGE: ExpenseType.LABELS[ExpenseType.MILEAGE],
        },
        'default_expense_type': (expense.expense_type if expense else ExpenseType.RECEIPT),
        'mileage_defaults': {
            'fuel_price_per_liter': _amount_for_input(MILEAGE_DEFAULT_FUEL_PRICE),
            'vehicle_efficiency_km_l': _amount_for_input(MILEAGE_DEFAULT_EFFICIENCY),
            'correction_factor': _amount_for_input(MILEAGE_DEFAULT_CORRECTION_FACTOR),
        },
        'expense': expense,
        'initial_expense': initial_expense,
        'form_action': url_for('expenses.edit', id=expense.id) if expense else url_for('expenses.new'),
        'form_title': 'Editar Gasto' if expense else 'Registrar Nuevo Gasto',
        'form_subtitle': 'Actualiza los datos del gasto en borrador.' if expense else 'Ingresa los detalles de tu comprobante',
        'submit_label': 'Guardar Cambios' if expense else 'Guardar Gasto',
    }


def _normalize_duplicate_state(expense):
    if expense.id is not None and expense.duplicate_of_id == expense.id:
        expense.is_duplicate = False
        expense.duplicate_of_id = None


def _upsert_expense(expense=None):
    is_edit = expense is not None
    redirect_endpoint = url_for('expenses.edit', id=expense.id) if is_edit else url_for('expenses.new')

    expense_type = _normalize_expense_type(request.form.get('expense_type'))
    amount_raw = request.form.get('amount')
    currency = _normalize_currency(request.form.get('currency'))
    amount = _parse_amount(amount_raw, currency=currency)
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
        return redirect(redirect_endpoint)

    gps_latitude = _parse_coordinate(gps_latitude_raw, -90, 90)
    gps_longitude = _parse_coordinate(gps_longitude_raw, -180, 180)
    gps_accuracy_m = _parse_non_negative_decimal(gps_accuracy_raw)

    if gps_latitude is None or gps_longitude is None:
        flash('La ubicación GPS no es válida. Intenta capturarla nuevamente.', 'danger')
        return redirect(redirect_endpoint)

    if len(description.strip()) < 15:
        flash('El motivo debe tener un mínimo de 15 caracteres.', 'danger')
        return redirect(redirect_endpoint)

    if expense_type is None:
        flash('El tipo de gasto seleccionado no es válido.', 'danger')
        return redirect(redirect_endpoint)

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
            return redirect(redirect_endpoint)
        currency = ExpenseCurrency.CLP
        exchange_rate = Decimal('1')
    else:
        if amount is None or amount <= 0:
            flash('El monto ingresado no es válido.', 'danger')
            return redirect(redirect_endpoint)

        if currency is None:
            flash('La moneda seleccionada no es válida.', 'danger')
            return redirect(redirect_endpoint)

        if currency == ExpenseCurrency.USD and (exchange_rate is None or exchange_rate <= 0):
            auto_rate = get_usd_exchange_rate_for_date(auto_rate_date) if auto_rate_date else None
            exchange_rate = auto_rate['exchange_rate'] if auto_rate else None
            if exchange_rate is None or exchange_rate <= 0:
                flash('Debes ingresar un tipo de cambio válido para gastos en USD.', 'danger')
                return redirect(redirect_endpoint)

        if currency == ExpenseCurrency.CLP:
            exchange_rate = Decimal('1')

    amount_clp = _compute_amount_clp(amount, currency, exchange_rate)
    if amount_clp is None or amount_clp <= 0:
        flash('No fue posible calcular el monto en CLP para este gasto.', 'danger')
        return redirect(redirect_endpoint)

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

        if expense is None:
            expense = Expense(
                user_id=current_user.id,
                company_id=current_user.company_id,
                status=ExpenseStatus.DRAFT,
            )
            db.session.add(expense)

        expense.amount = amount
        expense.currency = currency
        expense.exchange_rate = exchange_rate
        expense.amount_clp = amount_clp
        expense.distance_km = distance_km if expense_type == ExpenseType.MILEAGE else None
        expense.fuel_price_per_liter = fuel_price_per_liter if expense_type == ExpenseType.MILEAGE else None
        expense.vehicle_efficiency_km_l = vehicle_efficiency_km_l if expense_type == ExpenseType.MILEAGE else None
        expense.correction_factor = correction_factor if expense_type == ExpenseType.MILEAGE else None
        expense.merchant = merchant
        expense.client_partner = client_partner
        expense.date = expense_date
        expense.receipt_time = receipt_time
        expense.category_id = category_id
        expense.description = description
        expense.expense_type = expense_type
        expense.gps_latitude = gps_latitude
        expense.gps_longitude = gps_longitude
        expense.gps_accuracy_m = gps_accuracy_m
        expense.gps_captured_at = gps_captured_at
        expense.gps_address = resolved_address
        expense.gps_validation_status = geo_validation['status']
        expense.gps_validation_score = geo_validation['score']
        expense.gps_validation_reason = geo_validation['reason']
        expense.gps_validation_meta = {
            'matched_tokens': geo_validation.get('matched_tokens', []),
            'components': geo_validation.get('components', []),
        }
        expense.is_duplicate = False
        expense.duplicate_of_id = None

        if 'receipt' in request.files:
            file = request.files['receipt']
            if file.filename != '':
                file_path, receipt_url = _store_uploaded_receipt(file, current_user.company_id)
                expense.receipt_url = receipt_url

                r_hash = calculate_receipt_hash(file_path)
                expense.receipt_hash = r_hash
                if r_hash:
                    existing = Expense.query.filter(
                        Expense.company_id == current_user.company_id,
                        Expense.receipt_hash == r_hash,
                        Expense.id != expense.id,
                    ).first()
                    if existing and existing.id != expense.id:
                        expense.is_duplicate = True
                        expense.duplicate_of_id = existing.id
                        flash('¡Atención! Este comprobante parece haber sido subido anteriormente.', 'warning')

        if not expense.is_duplicate:
            existing_data = Expense.query.filter(
                Expense.company_id == current_user.company_id,
                Expense.amount == amount,
                Expense.currency == currency,
                Expense.date == expense_date,
                Expense.id != expense.id,
            ).first()
            if existing_data and existing_data.id != expense.id:
                expense.is_duplicate = True
                expense.duplicate_of_id = existing_data.id
                flash('Existe un gasto con el mismo monto y fecha. Se ha marcado como posible duplicado.', 'warning')

        _normalize_duplicate_state(expense)

        from app.models.policy import Policy

        company_policy = Policy.query.filter_by(company_id=current_user.company_id, is_active=True).first()
        if company_policy and 'max_amount' in company_policy.rules:
            max_amt = float(company_policy.rules['max_amount'])
            if float(amount_clp) > max_amt:
                flash(
                    f'El monto equivalente en CLP excede el límite permitido por la política de su empresa (${max_amt:,.0f}). Guardado como borrador con advertencia.',
                    'warning',
                )

        if expense.report and expense.report.status == ReportStatus.DRAFT:
            _recalculate_report_total(expense.report)

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

        if is_edit:
            log_action(
                action='expense_updated',
                entity_type='expense',
                entity_id=expense.id,
                description=f"Gasto {expense.public_id} actualizado por el usuario.",
            )
            flash('Gasto actualizado exitosamente.', 'success')
        else:
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
        flash(f"Error al {'actualizar' if is_edit else 'crear'} gasto: {str(e)}", 'danger')
        return redirect(redirect_endpoint)


@expenses_bp.route('/')
@login_required
def index():
    expenses = Expense.query.filter_by(
        user_id=current_user.id
    ).options(
        selectinload(Expense.category),
        selectinload(Expense.report),
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
        _normalize_duplicate_state(expense)
        db.session.flush()
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
    if request.method == 'POST':
        return _upsert_expense()

    return render_template('expenses/form.html', **_expense_form_context())


@expenses_bp.route('/<uuid:id>/edit', methods=['GET', 'POST'])
@login_required
def edit(id):
    expense = Expense.query.options(selectinload(Expense.category)).get_or_404(id)

    if expense.user_id != current_user.id:
        flash('No tienes permiso para editar este gasto.', 'danger')
        return redirect(url_for('expenses.index'))

    if not _can_edit_expense(expense):
        flash('Solo puedes editar gastos en borrador. Si ya está en flujo o cerrado, crea uno nuevo.', 'warning')
        return redirect(url_for('expenses.index'))

    if request.method == 'POST':
        return _upsert_expense(expense)

    return render_template('expenses/form.html', **_expense_form_context(expense))


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
        currency = _normalize_currency(request.form.get('currency')) or ExpenseCurrency.CLP
        filename = secure_filename(file.filename)
        temp_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, f"tmp_ocr_{current_user.id}_{filename}")
        file.save(temp_path)

        data = extract_expense_data(temp_path)
        if not data:
            return jsonify({'error': 'No fue posible extraer datos del comprobante. Completa el formulario manualmente.'}), 422

        if data.get('amount') is not None:
            parsed_amount = _amount_for_input(data.get('amount'), currency=currency)
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
