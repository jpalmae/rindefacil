# Mejoras futuras

## Seguridad
- [ ] **Redis como backend de flask-limiter** — Los rate limits se cuentan por worker (Gunicorn corre 3 workers con contadores independientes en memoria), haciendo los límites 3x más permisivos de lo configurado (ej: MFA 3/min real ≈ 9/min). Además cada restart borra los contadores.
  - Fix: contenedor Redis en el stack (`rindefaciljp-redis-1`) + `Limiter(key_func=get_remote_address, storage_uri="redis://rindefaciljp-redis-1:6379")` en `app/extensions.py:14`

## Infraestructura
- [ ] **Limpiar ~37 GB de imágenes Docker no usadas** en server prod (100.106.236.88). CUIDADO: servidor compartido con otros proyectos — revisar con dueños antes de `docker system prune`. Además hay 7.7 GB de build cache reclaimable.

## OCR
- [ ] **Subir timeout de OCR local de 10s a 15s** si se siguen viendo timeouts de `qwen3.6-35b-a3b` (2 el 2026-09-07). El fallback funciona, pero el modelo primario falla por tiempo.

## Observabilidad
- [ ] **Habilitar access logs de Gunicorn** — actualmente solo loguea errores; no hay auditoría de requests HTTP (códigos, latencia por ruta). Config `accesslog` / `logconfig` en gunicorn.conf.py.
