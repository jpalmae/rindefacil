import io
import os

from flask import current_app, has_request_context

from app.services.secrets_service import decrypt_setting


OCR_PROVIDER_OPENROUTER = 'openrouter'
OCR_PROVIDER_LOCAL = 'local'

OCR_DEFAULT_CLOUD_MODEL = 'openai/gpt-4o-mini'
OCR_DEFAULT_LOCAL_BASE_URL = 'http://localhost:11434/v1'
OCR_DEFAULT_LOCAL_MODEL = 'llama3.2-vision'
OCR_DEFAULT_TIMEOUT_SECONDS = 60

DEFAULT_CLOUD_PROMPT = """Eres un asistente de contabilidad experto. Extrae la información de este recibo comercial.
Devuelve ÚNICAMENTE un objeto JSON válido con las siguientes claves:
- "amount": Número con el total pagado. Si el comprobante está en CLP, devuelve el monto sin símbolos, sin puntos y sin comas de miles. Ej: 12990. Si está en soles peruanos (S/), usa punto decimal y quita comas de miles. Ej: "S/ 1,250.50" → 1250.50. Usa punto SOLO si hay decimales reales en monedas extranjeras. Ej: 2500 o 2500.75
- "merchant": Nombre comercial del proveedor o comercio (si no hay, devuelve null).
- "date": Fecha del gasto en formato DD/MM/YYYY (si no hay, devuelve null).
- "time": Hora del comprobante en formato HH:MM (24h). Si no está visible, devuelve null.
- "category": Clasifica el gasto en una de las siguientes opciones: "Viajes", "Alimentación", "Hospedaje", "Suministros", "Gasto Administrativo", u "Otros"."""

DEFAULT_LOCAL_PROMPT = """Analiza la imagen del recibo y devuelve EXACTAMENTE un JSON, sin texto adicional, sin explicaciones y sin markdown.

Formato requerido:
{
  "amount": <número>,
  "merchant": <texto o null>,
  "date": <DD/MM/YYYY o null>,
  "time": <HH:MM o null>,
  "category": <una de: Viajes, Alimentación, Hospedaje, Suministros, Gasto Administrativo, Otros>
}

Reglas estrictas:
- "amount" es el total pagado en números, sin símbolos ni separadores de miles. Si el monto está en pesos chilenos, es un número entero. Ejemplos: 1990, 50. Si está en soles peruanos (S/), respeta los decimales con punto. Ejemplo: "S/ 1,250.50" → 1250.50.
- Si un campo no aparece visible en la imagen, devuelve null (no lo inventes).
- "category" debe ser exactamente una de las opciones listadas.
- Responde con el JSON únicamente. Nada antes ni después."""


_PRESETS = {
    'ollama': 'http://localhost:11434/v1',
    'lmstudio': 'http://localhost:1234/v1',
    'litellm': 'http://localhost:4000/v1',
    'vllm': 'http://localhost:8000/v1',
    'llamacpp': 'http://localhost:8080/v1',
}


def local_provider_presets():
    return dict(_PRESETS)


def _safe_decrypt(value):
    if not value:
        return ''
    try:
        return decrypt_setting(value) or ''
    except RuntimeError as exc:
        current_app.logger.error('Could not decrypt OCR API key: %s', exc)
        return ''


def _company_settings(company):
    if company is None and has_request_context():
        from flask_login import current_user
        if current_user.is_authenticated:
            company = current_user.company
    return (company.settings or {}) if company is not None else {}


def _config_from_env():
    """Backward-compat fallback: env vars only."""
    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key:
        return None
    return {
        'enabled': True,
        'provider': OCR_PROVIDER_OPENROUTER,
        'base_url': 'https://openrouter.ai/api/v1',
        'api_key': api_key,
        'model': os.environ.get('OPENROUTER_MODEL_OCR', OCR_DEFAULT_CLOUD_MODEL),
        'model_fallback': os.environ.get('OPENROUTER_MODEL_FALLBACK') or None,
        'timeout': OCR_DEFAULT_TIMEOUT_SECONDS,
        'prompt': DEFAULT_CLOUD_PROMPT,
        'source': 'env',
    }


def get_company_ocr_config(company=None):
    """Resuelve la configuración OCR efectiva (provider primario).

    Orden: company.settings > env vars (OPENROUTER_*) > None.
    """
    settings = _company_settings(company)
    if not settings.get('ocr_enabled'):
        return _config_from_env()

    provider = (settings.get('ocr_provider') or OCR_PROVIDER_OPENROUTER).strip().lower()
    return _build_provider_config(settings, provider)


def _build_provider_config(settings, provider):
    """Construye la config de UN provider (openrouter o local) desde settings."""
    if provider == OCR_PROVIDER_LOCAL:
        timeout_raw = settings.get('ocr_local_timeout') or OCR_DEFAULT_TIMEOUT_SECONDS
        try:
            timeout = int(timeout_raw)
        except (TypeError, ValueError):
            timeout = OCR_DEFAULT_TIMEOUT_SECONDS
        if timeout <= 0:
            timeout = OCR_DEFAULT_TIMEOUT_SECONDS
        return {
            'enabled': True,
            'provider': OCR_PROVIDER_LOCAL,
            'base_url': (settings.get('ocr_local_base_url') or OCR_DEFAULT_LOCAL_BASE_URL).strip(),
            'api_key': (_safe_decrypt(settings.get('ocr_local_api_key')) or 'sk-no-key-required').strip(),
            'model': (settings.get('ocr_local_model') or OCR_DEFAULT_LOCAL_MODEL).strip(),
            'model_fallback': (settings.get('ocr_local_model_fallback') or '').strip() or None,
            'timeout': timeout,
            'prompt': settings.get('ocr_local_prompt') or DEFAULT_LOCAL_PROMPT,
            'source': 'company',
        }

    # OpenRouter (cloud)
    return {
        'enabled': True,
        'provider': OCR_PROVIDER_OPENROUTER,
        'base_url': 'https://openrouter.ai/api/v1',
        'api_key': (_safe_decrypt(settings.get('ocr_openrouter_api_key'))).strip(),
        'model': (settings.get('ocr_openrouter_model') or OCR_DEFAULT_CLOUD_MODEL).strip(),
        'model_fallback': (settings.get('ocr_openrouter_model_fallback') or '').strip() or None,
        'timeout': OCR_DEFAULT_TIMEOUT_SECONDS,
        'prompt': settings.get('ocr_openrouter_prompt') or DEFAULT_CLOUD_PROMPT,
        'source': 'company',
    }


def get_company_ocr_fallback_config(company=None):
    """Resuelve la config del provider FALLBACK (o None si no está activado).

    El fallback es mutuamente excluyente con el primario: si
    ocr_fallback_provider == ocr_provider, se ignora (no tiene sentido
    reintentar el mismo provider que ya falló).
    """
    settings = _company_settings(company)
    if not settings.get('ocr_enabled'):
        return None

    fallback_provider = (settings.get('ocr_fallback_provider') or '').strip().lower()
    if not fallback_provider or fallback_provider not in (OCR_PROVIDER_OPENROUTER, OCR_PROVIDER_LOCAL):
        return None

    primary = (settings.get('ocr_provider') or OCR_PROVIDER_OPENROUTER).strip().lower()
    if fallback_provider == primary:
        return None

    config = _build_provider_config(settings, fallback_provider)
    config['role'] = 'fallback'

    # El fallback necesita credenciales válidas para tener sentido
    if fallback_provider == OCR_PROVIDER_OPENROUTER and not config['api_key']:
        return None
    if fallback_provider == OCR_PROVIDER_LOCAL and not config['base_url']:
        return None

    return config


def get_company_ocr_config_view(company):
    """Config para mostrar en formularios: oculta secretos."""
    config = get_company_ocr_config(company)
    settings = _company_settings(company)

    is_company_managed = bool(settings.get('ocr_enabled'))

    if is_company_managed:
        provider = (settings.get('ocr_provider') or OCR_PROVIDER_OPENROUTER).lower()
        if provider == OCR_PROVIDER_LOCAL:
            return {
                'enabled': True,
                'provider': provider,
                'source': 'company',
                'local_base_url': settings.get('ocr_local_base_url') or '',
                'local_model': settings.get('ocr_local_model') or '',
                'local_model_fallback': settings.get('ocr_local_model_fallback') or '',
                'local_timeout': settings.get('ocr_local_timeout') or OCR_DEFAULT_TIMEOUT_SECONDS,
                'local_has_api_key': bool(settings.get('ocr_local_api_key')),
                'local_masked_api_key': _mask(settings.get('ocr_local_api_key')),
                'local_prompt': settings.get('ocr_local_prompt') or '',
                'openrouter_model': '',
                'openrouter_model_fallback': '',
                'openrouter_prompt': '',
                'openrouter_has_api_key': False,
                'openrouter_masked_api_key': '',
            }
        return {
            'enabled': True,
            'provider': provider,
            'source': 'company',
            'openrouter_model': settings.get('ocr_openrouter_model') or '',
            'openrouter_model_fallback': settings.get('ocr_openrouter_model_fallback') or '',
            'openrouter_prompt': settings.get('ocr_openrouter_prompt') or '',
            'openrouter_has_api_key': bool(settings.get('ocr_openrouter_api_key')),
            'openrouter_masked_api_key': _mask(settings.get('ocr_openrouter_api_key')),
            'local_base_url': '',
            'local_model': '',
            'local_model_fallback': '',
            'local_timeout': OCR_DEFAULT_TIMEOUT_SECONDS,
            'local_has_api_key': False,
            'local_masked_api_key': '',
            'local_prompt': '',
        }

    # Fallback a env vars: lo mostramos como "heredado"
    return {
        'enabled': bool(config),
        'provider': config.get('provider') if config else OCR_PROVIDER_OPENROUTER,
        'source': 'env' if config else 'none',
        'openrouter_model': os.environ.get('OPENROUTER_MODEL_OCR', OCR_DEFAULT_CLOUD_MODEL) if config else '',
        'openrouter_model_fallback': os.environ.get('OPENROUTER_MODEL_FALLBACK', '') if config else '',
        'openrouter_prompt': '',
        'openrouter_has_api_key': bool(config),
        'openrouter_masked_api_key': _mask_env_key(),
        'local_base_url': '',
        'local_model': '',
        'local_model_fallback': '',
        'local_timeout': OCR_DEFAULT_TIMEOUT_SECONDS,
        'local_has_api_key': False,
        'local_masked_api_key': '',
        'local_prompt': '',
    }


def _mask(value):
    if not value:
        return ''
    plain = value
    if isinstance(value, str) and value.startswith('enc:'):
        try:
            plain = decrypt_setting(value) or ''
        except RuntimeError:
            plain = ''
    if len(plain) <= 8:
        return '*' * len(plain)
    return f"{plain[:4]}{'*' * (len(plain) - 8)}{plain[-4:]}"


def _mask_env_key():
    key = os.environ.get('OPENROUTER_API_KEY') or ''
    if not key:
        return ''
    if len(key) <= 8:
        return '*' * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def make_test_image_bytes():
    """Genera una imagen PNG pequeña para pruebas de conectividad."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new('RGB', (420, 220), 'white')
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    lines = [
        'COMERCIO DEMO LTDA',
        'Boleta Electronica',
        'Fecha: 22/06/2026  Hora: 14:30',
        '',
        'Producto prueba     $1.500',
        'Producto prueba      $490',
        '',
        'TOTAL              $1.990',
    ]
    y = 14
    for line in lines:
        draw.text((16, y), line, fill='black', font=font)
        y += 22

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def test_ocr_connection(company):
    """Ejecuta una inferencia mínima y devuelve (ok, message, parsed_data).

    Prueba la cadena completa: primario → fallback (si configurado).
    """
    import base64 as b64mod
    import tempfile
    from app.services.ocr_service import _try_single_provider

    config = get_company_ocr_config(company)
    if not config:
        return False, 'OCR no configurado. Define un proveedor o ajusta OPENROUTER_API_KEY en el servidor.', None

    if not config.get('api_key') and config['provider'] == OCR_PROVIDER_OPENROUTER:
        return False, 'Falta API key para OpenRouter.', None
    if not config.get('base_url'):
        return False, 'Falta base_url del servidor local.', None
    if not config.get('model'):
        return False, 'Falta nombre del modelo.', None

    fallback_config = get_company_ocr_fallback_config(company)

    image_bytes = make_test_image_bytes()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp:
            tmp.write(image_bytes)
            tmp_path = tmp.name

        with open(tmp_path, 'rb') as f:
            base64_image = b64mod.b64encode(f.read()).decode('utf-8')

        # 1) Primario
        result = _try_single_provider(config, base64_image, 'image/png')
        if result is not None:
            return True, f'OK. Modelo {config["model"]} respondió correctamente.', result

        # 2) Fallback
        if fallback_config:
            result = _try_single_provider(fallback_config, base64_image, 'image/png')
            if result is not None:
                return (
                    True,
                    f'OK vía fallback ({fallback_config["provider"]}, modelo {fallback_config["model"]}). '
                    f'El primario ({config["provider"]}) falló.',
                    result,
                )

        return (
            False,
            f'La cadena completa falló (primario: {config["provider"]}'
            + (f' + fallback: {fallback_config["provider"]}' if fallback_config else ', sin fallback')
            + '). Revisa URLs, API keys y que el modelo soporte visión.',
            None,
        )
    except Exception as exc:
        return False, f'Error al ejecutar test: {exc}', None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
