import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.models.expense import ExpenseCurrency


def parse_amount(value, currency=None):
    """Normaliza montos con formatos locales e internacionales a Decimal."""
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
        normalized = f"{left}{right}" if len(right) == 3 and len(left) >= 1 else cleaned
    elif "," in cleaned and "." not in cleaned:
        left, right = cleaned.split(",", 1)
        normalized = f"{left}{right}" if len(right) == 3 and len(left) >= 1 else cleaned.replace(",", ".")
    else:
        normalized = cleaned

    if negative:
        normalized = f"-{normalized}"

    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def decimal_as_text(value, currency=None):
    amount = parse_amount(value, currency=currency)
    if amount is None:
        return None

    normalized = amount.normalize()
    if normalized == normalized.to_integral():
        return str(int(normalized))
    return format(normalized, "f")


def amount_for_input(value, currency=None):
    return decimal_as_text(value, currency=currency)


def parse_coordinate(value, min_value, max_value):
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


def parse_non_negative_decimal(value):
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


def normalize_currency(value):
    currency = (str(value).strip().upper() if value is not None else ExpenseCurrency.CLP) or ExpenseCurrency.CLP
    if currency not in ExpenseCurrency.CHOICES:
        return None
    return currency


def compute_amount_clp(amount, currency, exchange_rate, base_currency=ExpenseCurrency.CLP):
    """Monto en moneda base de la empresa (columna histórica amount_clp)."""
    if amount is None:
        return None
    if currency == base_currency:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if exchange_rate is None or exchange_rate <= 0:
        return None
    return (amount * exchange_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_date(value):
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


def parse_datetime(value):
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


def parse_time(value):
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


def normalize_date_for_input(value):
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw
