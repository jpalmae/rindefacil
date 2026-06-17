import os
import json
import base64
import re
import tempfile
import imagehash
from PIL import Image
from openai import OpenAI
from flask import current_app
import pypdfium2 as pdfium


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


def extract_expense_data(image_path):
    """
    Lee una imagen de recibo local y usa IA a través de OpenRouter 
    para extraer Fecha, Monto, Comercio y Categoría.
    """
    api_key = os.environ.get('OPENROUTER_API_KEY')
    model_name = os.environ.get('OPENROUTER_MODEL_OCR', 'openai/gpt-4o-mini')
    
    if not api_key:
        current_app.logger.warning("No OPENROUTER_API_KEY en variables de entorno. OCR skip.")
        return None

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    temp_image_path = None
    try:
        source_path = image_path
        if _is_pdf(image_path):
            temp_image_path = _render_pdf_first_page_to_png(image_path)
            if not temp_image_path:
                return None
            source_path = temp_image_path

        # Codificar imagen en base64
        with open(source_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
            
        file_ext = os.path.splitext(source_path)[1].lower()
        mime_type = "image/jpeg"
        if file_ext == '.png': mime_type = "image/png"

        prompt = """
        Eres un asistente de contabilidad experto. Extrae la información de este recibo comercial.
        Devuelve ÚNICAMENTE un objeto JSON válido con las siguientes claves:
        - "amount": Número con el total pagado. Si el comprobante está en CLP, devuelve el monto sin símbolos, sin puntos y sin comas de miles. Ej: 12990. Usa punto SOLO si hay decimales reales en monedas extranjeras. Ej: 2500 o 2500.75
        - "merchant": Nombre comercial del proveedor o comercio (si no hay, devuelve null).
        - "date": Fecha del gasto en formato DD/MM/YYYY (si no hay, devuelve null).
        - "time": Hora del comprobante en formato HH:MM (24h). Si no está visible, devuelve null.
        - "category": Clasifica el gasto en una de las siguientes opciones: "Viajes", "Alimentación", "Hospedaje", "Suministros", "Gasto Administrativo", u "Otros".
        """

        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text", 
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            temperature=0.1
        )
        
        result_content = response.choices[0].message.content
        parsed = _extract_json_payload(result_content)
        if not parsed:
            current_app.logger.warning("OCR response sin JSON parseable.")
            return None

        return parsed
        
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
