# Changelog

## Producción `ia-jp`

Este changelog resume las mejoras aplicadas después del paso a producción en `ia-jp`.

## 2026-04-07

### Flujos y aprobación
- Fallback automático para pasos `manager` cuando el solicitante no tiene manager asignado.
- En ese caso, la rendición omite el paso sin destinatario y avanza al siguiente aprobador válido.
- Si no existe un aprobador posterior en el flujo, la rendición puede cerrarse automáticamente según la lógica del flujo.
- Ajuste equivalente en web y API para mantener la misma resolución del paso activo y la misma visibilidad de aprobación.

### Documentación
- README y guía de usuario actualizados para explicar el comportamiento de flujos cuando un solicitante no tiene manager.

## 2026-04-02

### Gastos y comprobantes
- Corrección preventiva en cargas web de comprobantes para evitar colisiones de nombre de archivo.
- Los comprobantes subidos desde la web ahora se almacenan con nombre único por `UUID`, alineando el comportamiento con la API.
- Con esto se evita que dos gastos distintos sobrescriban el mismo archivo cuando el nombre original coincide (por ejemplo `image.jpg`).

## 2026-03-12

### Gastos y OCR
- Soporte de gastos en `USD` con conversión a `CLP` para dashboard, rendiciones, políticas y aprobación.
- Integración automática de tipo de cambio con fuente primaria `mindicador.cl` y fallback `CMF` cuando existe `CMF_API_KEY`.
- Reordenamiento del formulario para dejar `Tipo de Cambio` junto a `Categoría`.
- Nueva categoría de gasto `Suscripciones`.
- Soporte de `Vehículo particular` como tipo de gasto por tramo.
- Cálculo automático de monto por tramo usando:
  - kilómetros,
  - precio litro,
  - rendimiento km/l,
  - factor de corrección.
- OCR habilitado para comprobantes `PDF` mediante conversión de la primera página a imagen.
- Vista previa embebida de comprobantes `PDF` en el formulario de gasto.

### UI y experiencia de usuario
- Nuevo tema visual `Rose`.

### Documentación
- Guía de usuario y README actualizados para cubrir:
  - gastos USD,
  - tipo de cambio,
  - vehículo particular,
  - OCR PDF,
  - vista previa de comprobantes.

## 2026-03-11

### Rendiciones y aprobaciones
- Detalle expandible por gasto dentro de la rendición para aprobadores.
- Inclusión de fecha de rendición/captura en el detalle del gasto.
- Separación de vistas para managers:
  - `Pendientes de Mi Aprobación`,
  - `Mis Rendiciones`,
  - `Todas`,
  - `Finanzas` cuando corresponde.

### Notificaciones
- Nueva bandeja de notificaciones completa.
- Apertura segura de notificaciones individuales.
- Manejo de notificaciones huérfanas sin error.
- Acción explícita `Marcar Todo Como Leído`.
- Campanario mostrando solo notificaciones no leídas.

### PDF y branding
- Inclusión del logo corporativo en la esquina superior derecha de los PDF de rendición.

### UI
- Ajuste visual del botón `Marcar Pagada` para mantener contraste correcto en temas claros.

## 2026-03-10

### Flujos y aprobación
- Solicitud de antecedentes adicionales durante la aprobación sin rechazar la rendición.
- Reenvío de antecedentes al mismo paso del flujo.
- Selección correcta del flujo más restrictivo aplicable:
  - mayor `monto mínimo`,
  - y, en empate, más pasos.
- Corrección del borrado de pasos de flujo usando `UUID`.

### Finanzas
- Nuevos permisos adicionales por usuario:
  - `can_view_approved_reports`,
  - `can_mark_reimbursements_paid`.
- Marcado de rendiciones aprobadas con devolución como `Pagadas`.

### Gastos y rendiciones
- Eliminación de gastos en borrador no asociados a rendición.
- Eliminación de rendiciones en borrador.
- Opción de quitar gastos desde rendiciones borrador.

### Administración
- Buscador de usuarios en panel admin.
- Mejora de manejo de email duplicado al crear o editar usuarios.

### API
- Corrección en `POST /reports/{id}/submit` para lectura correcta del payload.

### UI
- Ajustes de márgenes móviles y visibilidad de acciones en flujos de aprobación.

## 2026-03-09

### Rendiciones y flujo
- Requisito de flujo activo antes de enviar una rendición.
- Renombre funcional de `Informes` a `Rendiciones` en la interfaz.
- Exposición de IDs públicos visibles para:
  - gastos (`GST-...`),
  - rendiciones (`RND-...`).
- Nuevo tipo de rendición:
  - `Solicitar devolución`,
  - `Tarjeta corporativa`.
- Corrección para mostrar acciones de aprobación en rendiciones `under_review`.

### UI y documentación
- Mejora de la guía de usuario para explicar mejor gasto vs rendición vs aprobación.
- Visibilidad explícita del botón `Editar` en usuarios admin.

## 2026-03-08

### Seguridad y autenticación
- Cambio forzado de contraseña para credenciales temporales.
- Logo corporativo visible en login.

### Branding
- Separación correcta entre `logo` e `ícono`.
- Soporte de ícono configurable para favicon y navegación.

### Antifraude y API
- Incorporación de score antifraude con:
  - GPS,
  - fecha de boleta,
  - hora de boleta,
  - horario hábil.
- API keys personales por usuario para agentes IA.

### UI y despliegue
- Inclusión del CSS compilado para despliegues.
- Correcciones de contraste para tema `Midnight`.

### Documentación
- README inicial detallado.
- Guía funcional de usuario integrada.

## Resumen ejecutivo

Desde el paso a producción en `ia-jp`, la plataforma evolucionó en cinco ejes:

1. Operación:
- Rendiciones más claras, borrado controlado, antecedentes adicionales y mejores vistas para managers.

2. Finanzas:
- Tipos de rendición, visibilidad financiera corporativa y cierre de devoluciones pagadas.

3. Control y antifraude:
- GPS obligatorio, score antifraude y flujos de aprobación más consistentes.

4. UX:
- Mejoras de notificaciones, branding, PDF, temas visuales y formularios.

5. Cobertura funcional:
- Soporte de USD, categoría Suscripciones, OCR PDF y rendición de vehículo particular.
