import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from flask import current_app


NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
STOPWORDS = {
    "spa",
    "ltda",
    "sucursal",
    "local",
    "de",
    "del",
    "la",
    "el",
    "los",
    "las",
    "y",
    "en",
    "restaurant",
    "restaurante",
    "cafe",
    "cafeteria",
}


def reverse_geocode(latitude, longitude):
    """Resolve a human-readable address from GPS coordinates using Nominatim."""
    headers = {
        "User-Agent": f"{current_app.config.get('APP_NAME', 'Rinde Facil')}/1.0",
    }
    params = {
        "format": "jsonv2",
        "lat": latitude,
        "lon": longitude,
        "zoom": 18,
        "addressdetails": 1,
        "accept-language": "es",
    }

    try:
        response = requests.get(
            NOMINATIM_REVERSE_URL,
            params=params,
            headers=headers,
            timeout=(2, 5),
        )
        response.raise_for_status()
        payload = response.json() or {}
        return {
            "display_name": payload.get("display_name"),
            "address": payload.get("address") or {},
            "source": "nominatim",
        }
    except Exception as exc:
        current_app.logger.warning("No se pudo resolver direccion por GPS: %s", exc)
        return None


def evaluate_merchant_location_match(merchant, address, accuracy_m=None):
    """
    Compare merchant text against the resolved address and produce a fraud hint.
    status: match | partial | mismatch | unknown
    """
    if not merchant or not address:
        return {
            "status": "unknown",
            "score": 0.0,
            "reason": "insufficient_data",
            "matched_tokens": [],
        }

    merchant_tokens = _tokens(merchant)
    address_tokens = _tokens(address)
    if not merchant_tokens or not address_tokens:
        return {
            "status": "unknown",
            "score": 0.0,
            "reason": "insufficient_tokens",
            "matched_tokens": [],
        }

    matched = sorted(merchant_tokens.intersection(address_tokens))
    overlap = len(matched) / max(len(merchant_tokens), 1)

    merchant_norm = _normalize_text(merchant)
    address_norm = _normalize_text(address)
    if merchant_norm and merchant_norm in address_norm:
        overlap = max(overlap, 1.0)

    status = "mismatch"
    reason = "no_overlap"
    if overlap >= 0.6:
        status = "match"
        reason = "strong_name_match"
    elif overlap >= 0.25:
        status = "partial"
        reason = "partial_name_match"

    if accuracy_m is not None:
        try:
            accuracy_value = float(accuracy_m)
            if accuracy_value > 250 and status == "match":
                status = "partial"
                reason = "gps_low_accuracy"
            elif accuracy_value > 600:
                status = "unknown"
                reason = "gps_very_low_accuracy"
                overlap = min(overlap, 0.2)
        except (TypeError, ValueError):
            pass

    return {
        "status": status,
        "score": round(float(overlap), 2),
        "reason": reason,
        "matched_tokens": matched,
    }


def evaluate_expense_integrity(
    merchant,
    address,
    accuracy_m=None,
    receipt_date=None,
    rendered_at=None,
    receipt_time=None,
    time_tolerance_minutes=20,
    tz_name=None,
):
    """
    Combined anti-fraud score:
    - merchant/address similarity
    - receipt date vs submission date
    - receipt time vs submission time (with tolerance)
    """
    geo_result = evaluate_merchant_location_match(merchant, address, accuracy_m)
    components = []

    if merchant and address:
        components.append({"name": "geo", "score": geo_result["score"], "weight": 0.5, "status": geo_result["status"]})

    rendered_local = None
    if rendered_at:
        if rendered_at.tzinfo is None:
            rendered_at = rendered_at.replace(tzinfo=timezone.utc)
        rendered_local = rendered_at.astimezone(_app_timezone(tz_name))

    date_component = {"name": "date", "score": 0.0, "weight": 0.3, "status": "unknown", "days_diff": None}
    if receipt_date and rendered_local:
        day_diff = abs((rendered_local.date() - receipt_date).days)
        date_component["days_diff"] = day_diff
        if day_diff == 0:
            date_component["score"] = 1.0
            date_component["status"] = "match"
        elif day_diff == 1:
            date_component["score"] = 0.4
            date_component["status"] = "partial"
        else:
            date_component["score"] = 0.0
            date_component["status"] = "mismatch"
        components.append(date_component)

    time_component = {"name": "time", "score": 0.0, "weight": 0.2, "status": "unknown", "minutes_diff": None}
    if receipt_time and receipt_date and rendered_local:
        receipt_local = datetime.combine(receipt_date, receipt_time, tzinfo=_app_timezone(tz_name))
        minutes_diff = abs((rendered_local - receipt_local).total_seconds()) / 60
        time_component["minutes_diff"] = round(minutes_diff, 1)
        if minutes_diff <= time_tolerance_minutes:
            time_component["score"] = 1.0
            time_component["status"] = "match"
        elif minutes_diff <= 60:
            time_component["score"] = 0.45
            time_component["status"] = "partial"
        else:
            time_component["score"] = 0.0
            time_component["status"] = "mismatch"
        components.append(time_component)

    schedule_component = _evaluate_business_hours_component(receipt_date, receipt_time, rendered_local)
    if schedule_component is not None:
        components.append(schedule_component)

    if not components:
        return {
            "status": "unknown",
            "score": 0.0,
            "reason": "insufficient_data",
            "matched_tokens": geo_result.get("matched_tokens", []),
            "components": [],
        }

    total_weight = sum(item["weight"] for item in components)
    weighted_score = sum(item["score"] * item["weight"] for item in components) / total_weight if total_weight else 0.0

    status = "mismatch"
    if weighted_score >= 0.75:
        status = "match"
    elif weighted_score >= 0.45:
        status = "partial"

    # Never keep a full "match" when the expense happened on weekend/outside business hours.
    if schedule_component and schedule_component.get("status") == "mismatch" and status == "match":
        status = "partial"

    reason = _combined_reason(status, geo_result, date_component, time_component, schedule_component)
    return {
        "status": status,
        "score": round(float(weighted_score), 2),
        "reason": reason,
        "matched_tokens": geo_result.get("matched_tokens", []),
        "components": components,
    }


def _normalize_text(value):
    lowered = unicodedata.normalize("NFKD", str(value).lower())
    stripped = "".join(ch for ch in lowered if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", stripped)).strip()


def _tokens(value):
    normalized = _normalize_text(value)
    if not normalized:
        return set()
    return {
        token
        for token in normalized.split(" ")
        if len(token) >= 3 and token not in STOPWORDS
    }


def _combined_reason(overall_status, geo_result, date_component, time_component, schedule_component):
    if overall_status == "mismatch":
        if date_component.get("status") == "mismatch":
            return "receipt_date_mismatch"
        if time_component.get("status") == "mismatch":
            return "receipt_time_mismatch"
        if schedule_component and schedule_component.get("status") == "mismatch":
            return schedule_component.get("reason", "outside_business_hours")
        if geo_result.get("status") == "mismatch":
            return "merchant_address_mismatch"
        return "overall_low_score"
    if overall_status == "partial":
        if schedule_component and schedule_component.get("status") == "mismatch":
            return schedule_component.get("reason", "outside_business_hours")
        if time_component.get("status") == "partial":
            return "receipt_time_partial"
        if date_component.get("status") == "partial":
            return "receipt_date_partial"
        if schedule_component and schedule_component.get("status") == "partial":
            return schedule_component.get("reason", "outside_business_hours_near")
        if geo_result.get("status") == "partial":
            return "merchant_address_partial"
        return "overall_medium_score"
    return "overall_high_score"


def _evaluate_business_hours_component(receipt_date, receipt_time, rendered_local):
    """Weekday and working-hours heuristic: Mon-Fri between 09:00 and 19:00."""
    ref_date = receipt_date or (rendered_local.date() if rendered_local else None)
    if ref_date is None:
        return None

    # Prefer receipt time when available, fallback to render time.
    ref_time = receipt_time or (rendered_local.timetz().replace(tzinfo=None) if rendered_local else None)

    weekday = ref_date.weekday()  # Mon=0 ... Sun=6
    is_weekend = weekday >= 5

    component = {
        "name": "schedule",
        "weight": 0.15,
        "score": 0.0,
        "status": "unknown",
        "weekday": weekday,
        "is_weekend": is_weekend,
        "time": ref_time.isoformat(timespec="minutes") if ref_time else None,
        "reason": "insufficient_data",
    }

    if is_weekend:
        component["score"] = 0.0
        component["status"] = "mismatch"
        component["reason"] = "weekend_submission"
        return component

    if ref_time is None:
        component["score"] = 0.6
        component["status"] = "partial"
        component["reason"] = "business_day_without_time"
        return component

    minute_of_day = ref_time.hour * 60 + ref_time.minute
    start = 9 * 60
    end = 19 * 60
    lower_soft = 8 * 60
    upper_soft = 20 * 60

    if start <= minute_of_day <= end:
        component["score"] = 1.0
        component["status"] = "match"
        component["reason"] = "within_business_hours"
    elif lower_soft <= minute_of_day <= upper_soft:
        component["score"] = 0.45
        component["status"] = "partial"
        component["reason"] = "outside_business_hours_near"
    else:
        component["score"] = 0.0
        component["status"] = "mismatch"
        component["reason"] = "outside_business_hours"

    return component


def _app_timezone(tz_name=None):
    """Timezone de evaluación. Prioridad: tz explícita (empresa del gasto) >
    APP_TIMEZONE config > America/Santiago."""
    resolved = tz_name or current_app.config.get("APP_TIMEZONE", "America/Santiago")
    try:
        return ZoneInfo(resolved)
    except Exception:
        try:
            return ZoneInfo("America/Santiago")
        except Exception:
            return timezone.utc
