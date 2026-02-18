---
trigger: always_on
---

## Critical Rule: Always Review Documentation

# Arquitectura Ajustada Y Hoja De Ruta Técnica Para SaaS Contable Mexicano

## Stack Tecnológico

| Componente     | Tecnología                |
| -------------- | ------------------------- |
| Backend        | Django 6.0 (Python 3.13)  |
| Templates      | Django Templates          |
| Interactividad | HTMX 2.0                  |
| Estado UI      | Alpine.js 3.x             |
| Estilos        | TailwindCSS + DaisyUI 4.x |
| Base de Datos  | PostgreSQL 16 (Docker)    |
| Auth           | django-allauth            |
| Multi-tenant   | django-multitenant        |
| Permisos       | django-rules              |

---

## Introducción

El desarrollo de un sistema SaaS contable en México es un reto de ingeniería de alto rigor. A diferencia de otros mercados, la contabilidad mexicana está profundamente estandarizada y fiscalizada en tiempo casi real por el Servicio de Administración Tributaria (SAT), mediante especificaciones técnicas como el Anexo 20 (CFDI 4.0) y el Anexo 24 (Contabilidad Electrónica). Por ello, la arquitectura debe priorizar integridad contable, cumplimiento fiscal, auditabilidad y operación sencilla en producción.

Esta versión del documento presenta una arquitectura ajustada, pragmática y battle-tested, diseñada para escalar funcional y operativamente sin introducir complejidad accidental. Se adopta Django (Python) como núcleo por su madurez, seguridad y potencia del ORM, y HTMX como capa de interacción para construir aplicaciones hipermedia ricas, con mínima lógica en el cliente, alta mantenibilidad y excelente rendimiento percibido.

---

## Principios Rectores (No Negociables)

- Un solo Core Contable (Ledger): la partida doble es un invariante. Nada puede romperla.
- Separación estricta de dominios (DDD): Fiscal, Bancos y Reportes no contaminan al Core.
- Multi-tenancy explícito y simple: company_id obligatorio en todas las entidades de negocio.
- Monolito modular: un solo despliegue, dependencias unidireccionales, listo para evolucionar.
- OLTP vs OLAP separados: escritura rápida y lectura optimizada.
- Eventos de dominio explícitos: desacoplamiento, automatización y auditoría.

---

## Arquitectura General y Contextos Delimitados

```

apps/

├── core
├── users
├── companies
├── ledger          ← CORE CONTABLE
├── fiscal
├── banking
├── reporting       ← SOLO LECTURA
├── automation      ← Jobs y procesos asíncronos
└── integrations    ← SAT, Open Finance, terceros

```

---

## Reglas de Dependencia

- ledger no depende de nadie.
- fiscal, banking, reporting dependen de ledger.
- automation escucha eventos y coordina procesos.

---

## Estrategia de Multi-Tenancy (Ajustada)

### Decisión Clave

Se adopta Base de Datos Compartida con company_id obligatorio, evitando esquemas aislados por tenant.

### Justificación

- Migraciones simples y rápidas (una sola operación).
- Reportes cross-tenant eficientes (GROUP BY company_id).
- Menor complejidad operativa y mejor escalabilidad.
- Compatible con PostgreSQL Row Level Security (RLS) si se requiere refuerzo adicional.

### Implementación

- Todas las tablas de negocio incluyen company_id (FK indexada).
- QuerySets forzados por company_id.
- Middleware que inyecta el company_id activo en el request.

---

## Aplicación Core

**Responsabilidad:** infraestructura compartida mínima.

Incluye:

- Modelos abstractos (BaseModel, CompanyScopedModel).
- Utilidades de fechas fiscales.
- Excepciones base de dominio.

No contiene lógica contable ni fiscal.

---

## Aplicación Users

**Responsabilidad:** identidad, autenticación y autorización.

- Usuario global (un solo login).
- Relación Usuario–Empresa con roles:
- Administrador
- Contador Senior
- Capturista
- Auditor
- Solo Lectura
- Permisos evaluados por usuario + empresa + rol.

Soporta que un usuario pertenezca a múltiples empresas con distintos roles.

---

## Aplicación Companies

**Responsabilidad:** identidad fiscal del tenant.

- RFC, Razón Social, Régimen Fiscal.
- Certificados (.cer/.key) almacenados cifrados en S3.
- Validación de vigencia y correspondencia RFC–certificado.
- Configuración fiscal y branding.

---

## Ledger (Core Contable)

**Responsabilidad:** verdad contable inmutable.

### Modelos Clave

- Account: catálogo jerárquico con Código Agrupador SAT.
- Poliza (Transaction): Aggregate Root.
- Movimiento (JournalEntry): debe / haber.

### Invariantes

- Debe == Haber para pólizas posteadas.
- Periodo abierto.
- Inmutabilidad lógica (correcciones vía pólizas de ajuste).

### Saldos

- No se persisten en Account.
- Se calculan como sum(movimientos).
- Se optimizan con snapshots mensuales (account_balance_snapshot).

### Eventos de Dominio

- PolizaPosted
- PeriodClosed

---

## Aplicación Fiscal

**Responsabilidad:** traducción fiscal → contable.

### Funciones

- Ingesta y validación de CFDI 4.0.
- Clasificación (PUE / PPD).
- Generación de pólizas sugeridas.
- DIOT y Contabilidad Electrónica (Anexo 24).
- Validación EFOS (69-B).

Fiscal propone, Ledger registra.

### Eventos

CFDIImported  
FiscalRuleApplied

---

## Aplicación Banking

**Responsabilidad:** realidad financiera y conciliación.

- Integración Open Finance (Belvo, Finerio, Prometeo).
- Modelo espejo de movimientos bancarios.
- Algoritmos de conciliación (monto, fecha, referencia).
- Creación de pólizas desde banco (comisiones, cargos).

### Eventos

BankMovementImported  
PaymentReconciled

---

## Aplicación Reporting (OLAP)

**Responsabilidad:** solo lectura.

- Balanza de Comprobación.
- Auxiliares.
- Estados financieros.

Implementación:

- Read models.
- Vistas materializadas.
- Caching agresivo.

Nunca escribe en el Core.

---

## Automation

**Responsabilidad:** procesos asíncronos y orquestación.

- Descarga masiva SAT.
- Reprocesos contables.
- Reglas automáticas.
- Notificaciones.

Escucha eventos de dominio y actúa sin acoplar lógica al Core.

---

## Frontend: Django + HTMX + Alpine

### Principios

- HTML renderizado en servidor.
- Interacciones parciales con HTMX.
- Estado local mínimo con Alpine.js.

### Patrones

- Formsets dinámicos para captura de pólizas.
- Click-to-edit en grids.
- Scroll infinito con `hx-trigger="revealed"`.
- Modals y drill-down sin APIs JSON.

Resultado: UX tipo Excel, rápida y auditable.

---

## Infraestructura

- Nginx + Gunicorn.
- PostgreSQL 14+.
- Redis + Celery.
- AWS S3 (cifrado y lifecycle).
- Secrets Manager / Vault.

Regiones cercanas a México para baja latencia y cumplimiento LFPDPPP.

---

## CI/CD y Calidad

- Pruebas unitarias críticas en Ledger.
- pytest-django.
- Linting y seguridad (Bandit).
- Pipelines automatizados (GitHub Actions / GitLab CI).

---

## Conclusión

Esta arquitectura ajustada elimina complejidad operativa innecesaria sin sacrificar robustez contable ni cumplimiento fiscal. Mantiene un Core fuerte, auditable y estable, mientras permite evolucionar módulos periféricos con bajo riesgo.

Es una base realista para competir contra soluciones establecidas, escalar sin reescrituras traumáticas y operar en producción con confianza.
