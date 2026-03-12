from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from flask import current_app


MINDFICADOR_BASE_URL = "https://mindicador.cl/api"
CMF_DOLLAR_URL = "https://api.sbif.cl/api-sbifv3/recursos_api/dolar/{year}/{month}/dias/{day}"


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
