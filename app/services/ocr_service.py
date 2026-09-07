import os
import json
import base64
import re
import tempfile
import time
import imagehash
from PIL import Image
from openai import OpenAI
from flask import current_app
import pypdfium2 as pdfium


# ---------------------------------------------------------------------------
# Circuit breaker por provider (en memoria, por worker)
# ---------------------------------------------------------------------------
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_SECONDS = 300

_provider_breaker = {}  # key -> {'failures': int, 'open_until': float}


def _breaker_key(config):
    return f"{config.get('provider', '?')}:{config.get('base_url', '')}"


def _breaker_is_open(key):
    state = _provider_breaker.get(key)
    if not state:
        return False
    return bool(state.get('open_until')) and time.time() < state['open_until']


def _breaker_record(key, success):
    state = _provider_breaker.setdefault(key, {'failures': 0, 'open_until': 0.0})
    if success:
        state['failures'] = 0
        state['open_until'] = 0.0
        return
    state['failures'] += 1
    if state['failures'] >= BREAKER_THRESHOLD:
        state['open_until'] = time.time() + BREAKER_COOLDOWN_SECONDS
        state['failures'] = 0
        current_app.logger.warning(
            'OCR circuit breaker OPEN para %s (%ss): el provider lleva fallos consecutivos.',
            key, BREAKER_COOLDOWN_SECONDS,
        )


def _extract_json_payload(raw_content):
    if not raw_content:
        return None

    text = raw_content.strip()

    # Direct JSON response
    try:
        return json.loads(text)
    except Exception:
        pass

    # Markdown fenced JSON (```json ... ``` or ``` ... ```)
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass

    # Last resort: extract first JSON object-like block
    object_match = re.search(r"\{[\s\S]*\}", text)
    if object_match:
        try:
            return json.loads(object_match.group(0))
        except Exception:
            return None

    return None


def _is_pdf(path):
    return os.path.splitext(path)[1].lower() == '.pdf'


def _render_pdf_first_page_to_png(pdf_path):
    pdf = None
    output_path = None
    try:
        pdf = pdfium.PdfDocument(pdf_path)
        if len(pdf) < 1:
            return None
        page = pdf[0]
        bitmap = page.render(scale=2.0)
        image = bitmap.to_pil()
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
        output_path = temp_file.name
        temp_file.close()
        image.save(output_path, format='PNG')
        return output_path
    except Exception as exc:
        current_app.logger.error("No se pudo renderizar PDF para OCR: %s", exc)
        if output_path and os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        return None
    finally:
        if pdf is not None:
            try:
                pdf.close()
            except Exception:
                pass


def _run_inference(client, model, prompt, base64_image, mime_type, timeout):
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_image}"},
                    },
                ],
            }
        ],
        temperature=0.1,
        timeout=timeout,
    )
    return response.choices[0].message.content


def _try_single_provider(config, base64_image, mime_type):
    """Intenta UN provider (modelo primario + fallback dentro del provider).

    Devuelve el dict parseado o None. Registra en el circuit breaker.
    """
    key = _breaker_key(config)
    if _breaker_is_open(key):
        current_app.logger.info('OCR breaker abierto, saltando %s directo.', key)
        return None

    base_url = config.get('base_url')
    api_key = config.get('api_key') or 'sk-no-key-required'
    timeout = int(config.get('timeout') or 60)
    prompt = config.get('prompt')
    primary_model = config.get('model')
    fallback_model = config.get('model_fallback')

    if not base_url or not primary_model:
        current_app.logger.warning('OCR config incompleta para %s (falta base_url o modelo).', config.get('provider'))
        _breaker_record(key, False)
        return None

    client = OpenAI(base_url=base_url, api_key=api_key)
    models_to_try = [m for m in [primary_model, fallback_model] if m]
    last_error = None
    for model in models_to_try:
        try:
            content = _run_inference(client, model, prompt, base64_image, mime_type, timeout)
            parsed = _extract_json_payload(content)
            if parsed is not None:
                _breaker_record(key, True)
                return parsed
            last_error = 'Respuesta sin JSON parseable'
            current_app.logger.warning("OCR model %s devolvió respuesta sin JSON.", model)
        except Exception as exc:
            last_error = str(exc)
            current_app.logger.warning("OCR model %s falló: %s", model, exc)
            continue

    _breaker_record(key, False)
    if last_error:
        current_app.logger.error("OCR provider %s agotó modelos. Último error: %s", config.get('provider'), last_error)
    return None


def extract_expense_data(image_path, config_override=None):
    """
    Extrae datos de un recibo usando IA. Cadena: primario → fallback.

    Cada provider intenta su modelo primario y luego su modelo fallback.
    Si todo el primario falla y hay fallback configurado, se intenta el
    segundo provider. El circuit breaker saltea providers con fallos
    consecutivos recientes.
    """
    from app.services.ocr_settings_service import get_company_ocr_config, get_company_ocr_fallback_config

    if config_override is not None:
        config = config_override
        fallback_config = None
    else:
        config = get_company_ocr_config()
        fallback_config = get_company_ocr_fallback_config()

    if not config or not config.get('enabled'):
        current_app.logger.info('OCR deshabilitado o sin configurar. Skip.')
        return None

    temp_image_path = None
    try:
        source_path = image_path
        if _is_pdf(image_path):
            temp_image_path = _render_pdf_first_page_to_png(image_path)
            if not temp_image_path:
                return None
            source_path = temp_image_path

        with open(source_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')

        file_ext = os.path.splitext(source_path)[1].lower()
        mime_type = "image/png" if file_ext == '.png' else "image/jpeg"

        # 1) Provider primario
        result = _try_single_provider(config, base64_image, mime_type)
        if result is not None:
            current_app.logger.info('ocr: primary=%s OK', config.get('provider'))
            return result

        # 2) Provider fallback (si está configurado y el primario falló)
        if fallback_config:
            current_app.logger.warning(
                'ocr: primary=%s FAIL → probando fallback=%s',
                config.get('provider'), fallback_config.get('provider'),
            )
            result = _try_single_provider(fallback_config, base64_image, mime_type)
            if result is not None:
                current_app.logger.info('ocr: primary=%s FAIL → fallback=%s OK',
                                        config.get('provider'), fallback_config.get('provider'))
                return result
            current_app.logger.error('ocr: ALL FAIL (primary=%s, fallback=%s)',
                                     config.get('provider'), fallback_config.get('provider'))
        else:
            current_app.logger.error('ocr: primary=%s FAIL (sin fallback configurado)',
                                     config.get('provider'))
        return None

    except Exception as e:
        current_app.logger.error(f"Error en OCR Service: {e}")
        return None
    finally:
        if temp_image_path and os.path.exists(temp_image_path):
            try:
                os.remove(temp_image_path)
            except OSError:
                pass

def calculate_receipt_hash(image_path):
    """
    Calcula un pHash (perceptual hash) de la imagen para detectar duplicados visuales.
    """
    temp_image_path = None
    try:
        source_path = image_path
        if _is_pdf(image_path):
            temp_image_path = _render_pdf_first_page_to_png(image_path)
            if not temp_image_path:
                return None
            source_path = temp_image_path
        hash_val = imagehash.phash(Image.open(source_path))
        return str(hash_val)
    except Exception as e:
        current_app.logger.warning(f"No se pudo calcular hash de la imagen: {e}")
        return None
    finally:
        if temp_image_path and os.path.exists(temp_image_path):
            try:
                os.remove(temp_image_path)
            except OSError:
                pass
