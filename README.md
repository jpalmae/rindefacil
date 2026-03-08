# Rinde Fácil

Aplicación web para rendición de gastos empresariales: registro de gastos, análisis de boletas con IA (OCR), creación de informes, flujos de aprobación, notificaciones y branding por empresa.

## Funcionalidades principales

- Gestión de gastos con adjunto de comprobante (imagen/PDF).
- OCR con IA vía OpenRouter al subir la boleta (autocompletado de monto, comercio, fecha y categoría).
- Normalización de montos para formato local (CLP): separador de miles y decimales.
- Detección de duplicados por hash de imagen y por monto/fecha.
- Informes de rendición con múltiples gastos.
- Flujo de aprobación configurable por pasos (`rol`, `usuario`, `manager`).
- Notificaciones in-app y envío de correos para aprobaciones/rechazos.
- Panel administrativo: usuarios, centros de costo, flujos, auditoría y branding.
- Branding por empresa: nombre de app, logo y dominio por defecto para emails de usuarios.
- Selector de temas visuales (Executive / Paper / Midnight).

## Stack técnico

- Backend: Flask 3, SQLAlchemy, Flask-Login, Flask-Migrate, Flask-Mail, Flask-Limiter.
- Base de datos: PostgreSQL.
- Frontend: Jinja2 + Alpine.js + UnoCSS.
- OCR IA: OpenRouter (SDK `openai`).
- Exportación: ReportLab (PDF).
- Infra: Docker + Docker Compose.

## Requisitos

- Docker + Docker Compose (recomendado).
- Opcional para desarrollo frontend: Node.js 18+ (`npm`).
- Opcional para ejecución local sin Docker: Python 3.12 + PostgreSQL.

## Inicio rápido con Docker

1. Copiar variables de entorno:

```bash
cp .env.example .env
```

2. Ajustar al menos:

- `OPENROUTER_API_KEY` (si quieres OCR IA).
- `SECRET_KEY` y credenciales de BD para tu entorno.

3. Levantar servicios:

```bash
docker compose up -d --build
```

4. Abrir la app:

- [http://localhost:5001](http://localhost:5001)

La imagen ejecuta `flask db upgrade` al iniciar.

## Datos de demo (opcional)

El script `seed.py` recrea la base completa (borra datos actuales) y crea usuarios demo:

```bash
docker compose exec web python seed.py
```

Credenciales demo:

- `admin@demo.com / admin123`
- `user@demo.com / user123`

## Desarrollo local (sin Docker)

1. Crear y activar entorno virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Instalar dependencias:

```bash
pip install -r requirements.txt
npm install
```

3. Variables de entorno:

```bash
cp .env.example .env
```

Define `DATABASE_URL` apuntando a PostgreSQL local.

4. Migraciones:

```bash
flask db upgrade
```

5. Generar CSS:

```bash
npm run css-build
```

Modo watch:

```bash
npm run css-watch
```

6. Ejecutar:

```bash
python run_dev.py
```

## Branding (por empresa)

Ruta: `Admin -> Branding` (`/admin/branding`).

Permite configurar:

- Nombre visible de la app (`brand_app_name`).
- Logo corporativo (PNG/JPG/WEBP/SVG).
- Dominio de correo por defecto para creación de usuarios (`brand_user_default_domain`).

Valor por defecto global: `Rinde Fácil`.

## OCR y comportamiento de gastos

- El análisis se dispara automáticamente al seleccionar un archivo en “Nuevo Gasto”.
- Si OCR no detecta campos, el formulario sigue disponible para carga manual.
- El endpoint de extracción es `POST /expenses/extract-data`.
- Archivos se guardan localmente en `app/static/uploads`.

## Estructura del proyecto

```text
app/
  blueprints/      # auth, dashboard, expenses, reports, admin
  models/          # entidades SQLAlchemy
  services/        # OCR, notificaciones, correo, auditoría, exportación
  templates/       # vistas Jinja
  static/          # css/js/img/uploads
migrations/        # alembic/flask-migrate
docker-compose.yml
Dockerfile
run_dev.py
seed.py
```

## Troubleshooting

### No se ven los estilos CSS

- Ejecuta `npm run css-build`.
- Verifica que exista `app/static/css/uno.css`.
- Recarga dura del navegador (`Cmd+Shift+R` / `Ctrl+F5`).

### OCR no completa datos

- Revisa `OPENROUTER_API_KEY` en `.env`.
- Verifica conectividad saliente a `https://openrouter.ai`.
- Si falla, la carga manual sigue disponible.

### Error de conexión a DB

- Confirma `DATABASE_URL`.
- Ejecuta `flask db upgrade`.

### Push por SSH a GitHub falla (`Permission denied (publickey)`)

```bash
ssh-add ~/.ssh/id_rsa
```

## Seguridad

- `.env` está excluido del repo.
- No subas credenciales reales en archivos versionados.
- Cambia secretos por defecto antes de producción.

