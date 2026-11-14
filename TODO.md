# Mejoras futuras

## Seguridad
- [ ] **Redis como backend de flask-limiter** — Los rate limits se cuentan por worker (Gunicorn corre 3 workers con contadores independientes en memoria), haciendo los límites 3x más permisivos de lo configurado (ej: MFA 3/min real ≈ 9/min). Además cada restart borra los contadores.
  - Fix: contenedor Redis en el stack (`rindefaciljp-redis-1`) + `Limiter(key_func=get_remote_address, storage_uri="redis://rindefaciljp-redis-1:6379")` en `app/extensions.py:14`

## Infraestructura
- [ ] **Limpiar ~37 GB de imágenes Docker no usadas** en server prod (100.106.236.88). CUIDADO: servidor compartido con otros proyectos — revisar con dueños antes de `docker system prune`. Además hay 7.7 GB de build cache reclaimable.
- [ ] **Tunnel QA pierde red si se recrea** — `rinde-sixmanager-tunnel` (docker run, token-based) necesita estar en `rindefaciljp_default` Y `rindefacil-qa_default` (qarinde.sixmanager.io resuelve `rindefacil-qa-web-1` por DNS docker). Fix aplicado 2026-11-13 con `docker network connect rindefacil-qa_default rinde-sixmanager-tunnel` (persiste en restarts; se pierde si se recrea el contenedor → reconectar o mover el tunnel a compose con ambas redes).
- [ ] **PR #2 (python 3.12→3.15-slim) y PRs unocss (#5/#6/#12) diferidos** — el de python requiere validar imagen+deps en los 3 entornos; unocss no aporta (CSS compilado va en el repo).

## OCR
- [x] ~~Subir timeout de OCR local de 10s a 15s~~ — hecho 2026-09-16 (prod y QA, ambas empresas)

## Observabilidad
- [ ] **Habilitar access logs de Gunicorn** — actualmente solo loguea errores; no hay auditoría de requests HTTP (códigos, latencia por ruta). Config `accesslog` / `logconfig` en gunicorn.conf.py.
