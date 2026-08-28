from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import requests
from flask import current_app


MINDFICADOR_BASE_URL = "https://mindicador.cl/api"
CMF_DOLLAR_URL = "https://api.sbif.cl/api-sbifv3/recursos_api/dolar/{year}/{month}/dias/{day}"
OPEN_ER_API_URL = "https://open.er-api.com/v6/latest/USD"


def _parse_decimal(value):
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    if "." in raw and "," in raw:
        last_dot = raw.rfind(".")
        last_comma = raw.rfind(",")
        decimal_sep = "." if last_dot > last_comma else ","
        thousand_sep = "," if decimal_sep == "." else "."
        normalized = raw.replace(thousand_sep, "").replace(decimal_sep, ".")
    elif "," in raw and "." not in raw:
        normalized = raw.replace(".", "").replace(",", ".")
    else:
        normalized = raw
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _date_candidates(target_date, max_days_back=7):
    for offset in range(max_days_back + 1):
        yield target_date - timedelta(days=offset)


def _mindicador_rate(target_date):
    formatted = target_date.strftime("%d-%m-%Y")
    response = requests.get(
        f"{MINDFICADOR_BASE_URL}/dolar/{formatted}",
        timeout=8,
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json() or {}
    series = payload.get("serie") or []
    if not series:
        return None

    value = _parse_decimal(series[0].get("valor"))
    if value is None:
        return None

    return {
        "exchange_rate": value,
        "source": "mindicador.cl",
        "source_detail": "Banco Central de Chile via mindicador.cl",
        "date": target_date,
    }


def _cmf_rate(target_date):
    api_key = current_app.config.get("CMF_API_KEY")
    if not api_key:
        return None

    url = CMF_DOLLAR_URL.format(
        year=target_date.strftime("%Y"),
        month=target_date.strftime("%m"),
        day=target_date.strftime("%d"),
    )
    response = requests.get(
        url,
        params={"apikey": api_key, "formato": "json"},
        timeout=8,
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json() or {}
    entries = payload.get("Dolares") or payload.get("DolaresObservados") or []
    if not entries:
        return None

    value = _parse_decimal(entries[0].get("Valor"))
    if value is None:
        return None

    return {
        "exchange_rate": value,
        "source": "cmfchile.cl",
        "source_detail": "CMF Chile - Dolar observado del Banco Central de Chile",
        "date": target_date,
    }


def get_usd_exchange_rate_for_date(target_date):
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    if not isinstance(target_date, date):
        return None

    for candidate in _date_candidates(target_date):
        try:
            result = _mindicador_rate(candidate)
            if result:
                return result
        except Exception as exc:
            current_app.logger.warning("Mindicador exchange rate lookup failed for %s: %s", candidate, exc)
            response = getattr(exc, "response", None)
            if getattr(response, "status_code", None) == 404:
                continue
            break

    for candidate in _date_candidates(target_date):
        try:
            result = _cmf_rate(candidate)
            if result:
                return result
        except Exception as exc:
            current_app.logger.warning("CMF exchange rate lookup failed for %s: %s", candidate, exc)
            response = getattr(exc, "response", None)
            if getattr(response, "status_code", None) == 404:
                continue
            break

    return None


def _open_er_api_rate(symbol):
    """USD → symbol vía open.er-api.com (sin API key). Tasa del día."""
    response = requests.get(
        OPEN_ER_API_URL,
        params={"symbols": symbol},
        timeout=8,
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json() or {}
    if payload.get("result") != "success":
        return None

    value = _parse_decimal((payload.get("rates") or {}).get(symbol))
    if value is None or value <= 0:
        return None

    return {
        "exchange_rate": value,
        "source": "open.er-api.com",
        "source_detail": f"open.er-api.com (USD/{symbol})",
        "date": date.today(),
    }


def get_usd_rate_for_base(target_date, base_currency):
    """Tasa USD → moneda base de la empresa.

    CLP: cadena mindicador → CMF (histórica).
    PEN: open.er-api.com (del día; fallback manual en la UI).
    """
    base = (base_currency or "CLP").upper()
    if base == "PEN":
        try:
            return _open_er_api_rate("PEN")
        except Exception as exc:
            current_app.logger.warning("open.er-api PEN lookup failed: %s", exc)
            return None
    if base == "CLP":
        return get_usd_exchange_rate_for_date(target_date)
    return None


def resolve_amount_in_base(company, currency, amount, provided_rate=None, rate_date=None):
    """Convierte `amount` en `currency` a la moneda base de la empresa.

    Devuelve (amount_base, exchange_rate_usada). La tasa es None cuando la
    moneda del gasto ES la base (tasa implícita 1). Devuelve (None, None)
    si no se puede resolver (falta tasa válida).
    """
    if amount is None:
        return None, None

    base = (company.base_currency or "CLP").upper()
    if (currency or base) == base:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), None

    rate = provided_rate
    if rate is None or rate <= 0:
        target_date = rate_date or date.today()
        auto = get_usd_rate_for_base(target_date, base)
        rate = auto["exchange_rate"] if auto else None

    if rate is None or rate <= 0:
        return None, None

    return (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), rate
