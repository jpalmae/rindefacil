FROM python:3.12-slim

# Evitar que Python escriba archivos .pyc en disco y forzar la salida stdout/stderr (útil para logs en Docker)
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema necesarias (build-essential, librerías de reportlab/weasyprint)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Establecer directorio de trabajo
WORKDIR /app

# Instalar dependencias de Python
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código de la aplicación
COPY . /app/

# Exponer el puerto
EXPOSE 5001

# Crear script de entrada por defecto
RUN echo '#!/bin/sh\n\nflask db upgrade\n\n: "${WEB_CONCURRENCY:=3}"\n: "${GUNICORN_THREADS:=4}"\n: "${GUNICORN_TIMEOUT:=60}"\n\nexec gunicorn \\\n  --bind 0.0.0.0:5001 \\\n  --worker-class gthread \\\n  --workers "${WEB_CONCURRENCY}" \\\n  --threads "${GUNICORN_THREADS}" \\\n  --timeout "${GUNICORN_TIMEOUT}" \\\n  --keep-alive 5 \\\n  "app:create_app()"' > /start.sh && \
    chmod +x /start.sh

# Comando para ejecutar la app
CMD ["/start.sh"]
