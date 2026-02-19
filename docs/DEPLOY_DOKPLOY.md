# Despliegue de Aspeia Accounting en Dokploy

Guía paso a paso para subir la aplicación al servidor con Dokploy y que las descargas SAT y la sincronización con Odoo funcionen correctamente usando variables de entorno.

**Repositorio del proyecto:** https://github.com/diegopartida22/eqidis-sat-sync.git

### Resumen rápido

1. **Vincular y subir código** → Sección 0: cambiar `origin` al nuevo repo y `git push`.
2. **Crear app en Dokploy** → **Importante:** usar tipo **Docker Compose** (no solo Dockerfile) para que se levanten también PostgreSQL y Redis (Sección 1.5).
3. **Variables de entorno** → Configurar en Dokploy (Sección 2–3); con Compose, `POSTGRES_HOST=db` y `CELERY_BROKER_URL=redis://redis:6379/0`.
4. **Primer deploy** → Build y deploy; luego en el contenedor web: `migrate`, `sync_odoo_from_env` (Sección 4).
5. **Comprobar** → Web, CFDIs, Odoo, Celery (Sección 6).

---

## 0. Usar el repositorio nuevo (eqidis-sat-sync)

Si el código está actualmente vinculado a otro repo y quieres desplegar desde **eqidis-sat-sync**:

### 0.1 Cambiar el remote a eqidis-sat-sync

Desde la raíz del proyecto (carpeta `aspeia_accounting`):

```bash
# Ver el remote actual
git remote -v

# Opción A: Reemplazar el origin por el nuevo repo
git remote set-url origin https://github.com/diegopartida22/eqidis-sat-sync.git

# Opción B: Si prefieres dejar el viejo y añadir el nuevo
git remote rename origin old-repo
git remote add origin https://github.com/diegopartida22/eqidis-sat-sync.git
```

### 0.2 Subir el código al nuevo repo

Asegúrate de tener commit y push de todo lo que quieras desplegar:

```bash
# Añadir todos los archivos necesarios (revisa que no se suba .env ni __pycache__)
git add .
git status   # revisar qué se va a subir

# Si .env está en .gitignore (recomendado), no se subirá
git commit -m "Preparar despliegue Dokploy: Docker, Odoo multiempresa, CFDIs"

# Primera vez: crear rama main en el remoto si el repo está vacío
git push -u origin master
# Si el repo nuevo espera la rama "main":
# git branch -M main && git push -u origin main
```

Si GitHub te pide autenticación, usa un **Personal Access Token** (Settings → Developer settings → Personal access tokens) como contraseña, o configura SSH y usa la URL `git@github.com:diegopartida22/eqidis-sat-sync.git` como remote.

### 0.3 En Dokploy: conectar este repositorio

- Al crear la aplicación (o en Configuración → General), en **Repository URL** pon:  
  `https://github.com/diegopartida22/eqidis-sat-sync.git`
- Rama: `master` (o `main`, según la que uses).
- Ruta del Docker Compose (si aplica): `docker-compose.prod.yml` (está en la raíz del repo).

---

## 1. Requisitos previos

- Servidor con **Dokploy** instalado.
- **PostgreSQL** y **Redis** (se pueden levantar con el mismo Docker Compose del repo; ver abajo).
- **Odoo** accesible desde el servidor (URL, base de datos, usuario y contraseña).
- **Bucket S3** (AWS o compatible) para certificados FIEL/CSD y XMLs del SAT.

---

## 1.5 Cómo levantar PostgreSQL y Redis en Dokploy (importante)

Si creaste la app **solo con Dockerfile**, Dokploy solo levanta un contenedor (tu app). **PostgreSQL y Redis no se levantan solos**; por eso ves "Connection refused" a localhost.

Tienes dos formas de tener base de datos y Redis:

### Opción A (recomendada): Usar Docker Compose en Dokploy

Así Dokploy levanta **todo el stack** del repo: app web, PostgreSQL, Redis, Celery worker y Celery beat.

1. En Dokploy, **crea una nueva aplicación** (o edita la que tienes si permite cambiar el tipo).
2. En tipo de aplicación, elige **Docker Compose** (o "Compose"), **no** "Dockerfile".
3. Conecta el repositorio: `https://github.com/diegopartida22/eqidis-sat-sync.git`.
4. Indica que el archivo Compose es: **`docker-compose.prod.yml`** (ruta en la raíz del repo).
5. En **variables de entorno** de la aplicación, define al menos:
   - `POSTGRES_PASSWORD` = una contraseña segura para PostgreSQL (obligatoria; el compose la usa para el servicio `db`).
   - Las demás (Django, S3, Odoo, etc.) según la Sección 2.
6. El `docker-compose.prod.yml` del repo ya define:
   - Servicio **db** (PostgreSQL)
   - Servicio **redis**
   - Servicio **web** (Django)
   - Servicio **celery-worker** y **celery-beat**

   Dokploy los levantará todos. La app usará `POSTGRES_HOST=db` y `CELERY_BROKER_URL=redis://redis:6379/0` (puedes fijarlos en las variables de entorno del compose o en la UI de Dokploy para el stack).

7. Build y Deploy. Después, en el contenedor del servicio **web** ejecuta:  
   `python manage.py migrate` y `python manage.py sync_odoo_from_env`.

Si Dokploy no te deja "cambiar" de Dockerfile a Compose en la app actual, crea una **nueva** aplicación de tipo Docker Compose, apuntando al mismo repo y a `docker-compose.prod.yml`, y despliega esa. La antigua (solo Dockerfile) puedes dejarla parada o borrarla.

### Opción B: Mantener solo Dockerfile y añadir PostgreSQL y Redis aparte

Si quieres seguir usando **solo el Dockerfile**:

1. En Dokploy, crea **otra aplicación** (o usa el catálogo si lo tiene) para **PostgreSQL** (por ejemplo imagen `postgres:16-alpine`) y otra para **Redis** (imagen `redis:7-alpine`). O usa bases de datos/Redis gestionados por Dokploy si los ofrece.
2. Anota el **hostname** o URL interna que Dokploy asigne a PostgreSQL y a Redis (suelen ser el nombre del servicio o una URL tipo `postgres.Proyecto.svc` / similar).
3. En tu aplicación principal (la que usa el Dockerfile), en variables de entorno pon:
   - `POSTGRES_HOST` = hostname del servicio PostgreSQL.
   - `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` según lo que hayas configurado en ese PostgreSQL.
   - `CELERY_BROKER_URL` = `redis://<hostname-redis>:6379/0`.

Resumen: **Opción A** = una sola app en Dokploy (Compose) y se levantan web, db, redis y Celery. **Opción B** = app (Dockerfile) + app PostgreSQL + app Redis, y enlazas con variables.

---

## 2. Variables de entorno necesarias

Configura estas variables en la aplicación dentro de Dokploy (o en tu `.env` si usas compose).

### Django (obligatorias en producción)

| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| `DJANGO_SECRET_KEY` | Clave secreta de Django (generar una nueva) | `abc123...` largo y aleatorio |
| `DJANGO_DEBUG` | Debe ser `false` en producción | `false` |
| `DJANGO_ALLOWED_HOSTS` | Dominios permitidos separados por coma | `aspeia.midominio.com` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Orígenes HTTPS permitidos para CSRF | `https://aspeia.midominio.com` |

### Base de datos PostgreSQL

| Variable | Descripción | Ejemplo (Dokploy suele dar estas) |
|----------|-------------|-----------------------------------|
| `POSTGRES_DB` | Nombre de la base de datos | `aspeia_finance` |
| `POSTGRES_USER` | Usuario | `aspeia` |
| `POSTGRES_PASSWORD` | Contraseña | (la que definas) |
| `POSTGRES_HOST` | Host del servicio PostgreSQL | En compose: `db`. En Dokploy con DB externa: IP o hostname |
| `POSTGRES_PORT` | Puerto | `5432` |

### Celery (Redis)

| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| `CELERY_BROKER_URL` | URL de Redis para Celery | `redis://redis:6379/0` (en compose) o `redis://host:6379/0` |

### AWS S3 (certificados y XMLs)

| Variable | Descripción |
|----------|-------------|
| `AWS_ACCESS_KEY_ID` | Clave de acceso AWS (o compatible S3) |
| `AWS_SECRET_ACCESS_KEY` | Clave secreta |
| `AWS_STORAGE_BUCKET_NAME` | Nombre del bucket |
| `AWS_S3_REGION_NAME` | Región, ej. `us-east-1` |

### Odoo (sincronización de CFDIs, multiempresa)

| Variable | Descripción | Ejemplo |
|----------|-------------|---------|
| `ODOO_URL` | URL base de Odoo (sin barra final) | `https://odoo.miempresa.com` |
| `ODOO_DB` | Nombre de la base de datos en Odoo | `mi-odoo-db` |
| `ODOO_USERNAME` | Usuario Odoo | `admin` |
| `ODOO_PASSWORD` | Contraseña del usuario | (tu contraseña) |
| `ODOO_EMPRESA_ID` | (Opcional) ID de la Empresa en Aspeia a vincular; si no se pone, se usa la primera empresa activa | `1` |

En **multiempresa** no configures `ODOO_COMPANY_ID`: en la app (CFDIs → Configuración de Sincronización) eliges la empresa Odoo (`res.company`) por cada empresa Aspeia.

---

## 3. Pasos en Dokploy

### 3.1 Crear el proyecto y la aplicación

1. En Dokploy, crea un **nuevo proyecto** (por ejemplo "Aspeia").
2. Añade una **aplicación** tipo **Docker Compose** (recomendado) o **Dockerfile** según cómo quieras desplegar.

### 3.2 Repositorio y build

- **Si usas Docker Compose en Dokploy:**  
  - **Repository URL:** `https://github.com/diegopartida22/eqidis-sat-sync.git` (rama `master` o `main`).  
  - Ruta del compose: `docker-compose.prod.yml` (está en la raíz del repo).  
  - Build: imagen `aspeia-accounting` desde el `Dockerfile` del repo.

- **Si usas solo Dockerfile:**  
  - Conecta el repositorio.  
  - Build: usar el `Dockerfile` de la raíz del proyecto.  
  - Comando por defecto: `gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --threads 2 --timeout 120`.  
  - Luego tendrás que añadir **dos servicios más** (worker y beat) con la misma imagen y los comandos de Celery (ver sección 4).

### 3.3 Configurar variables de entorno

En la configuración de la aplicación en Dokploy, añade **todas** las variables de la tabla anterior. Asegúrate de:

- Poner `DJANGO_DEBUG=false` y `DJANGO_ALLOWED_HOSTS` con tu dominio real.
- Poner `DJANGO_CSRF_TRUSTED_ORIGINS=https://tu-dominio-real`.
- Configurar `POSTGRES_*` y `CELERY_BROKER_URL` según tu PostgreSQL y Redis (los de Dokploy o externos).
- Rellenar las variables **ODOO_*** y **AWS_***.

### 3.4 Base de datos y Redis

- Si usas el `docker-compose.prod.yml` del repo, ya incluye servicios `db` (PostgreSQL) y `redis`. Solo necesitas definir en env (o en el compose) `POSTGRES_PASSWORD` y que `POSTGRES_HOST=db` y `CELERY_BROKER_URL=redis://redis:6379/0` para los servicios web y Celery.
- Si usas una base de datos PostgreSQL y Redis creados por Dokploy (o externos), configura `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_PASSWORD`, etc. y `CELERY_BROKER_URL` con los datos que te den.

### 3.5 Puerto y dominio

- Expón el puerto **8000** (o el que tenga tu servicio web en el compose).
- En Dokploy, asocia el **dominio** (o subdominio) a esta aplicación y, si aplica, activa HTTPS (proxy inverso).

---

## 4. Migraciones y conexión Odoo (primera vez)

Después del primer deploy, hay que aplicar migraciones y crear/actualizar la conexión Odoo desde las variables de entorno.

### Opción A: Ejecutar en el contenedor (recomendado)

1. En Dokploy, abre un **terminal** del contenedor del servicio **web** (o el que tenga el código Django).
2. Ejecuta:

```bash
python manage.py migrate
python manage.py sync_odoo_from_env
python manage.py collectstatic --noinput   # si no se hizo en el build
```

- `sync_odoo_from_env` lee `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD`, y opcionalmente `ODOO_EMPRESA_ID` y `ODOO_COMPANY_ID`, y crea o actualiza la **Conexión Odoo** vinculada a la empresa en Aspeia. En multiempresa, asigna la empresa Odoo (`res.company`) desde la app (CFDIs → Configuración) en lugar de usar `ODOO_COMPANY_ID`.

### Opción B: Comando de inicio (entrypoint)

Si prefieres que en cada arranque se ejecuten migraciones y sync de Odoo, puedes usar un script de entrada que haga algo así (ejemplo conceptual):

```bash
python manage.py migrate --noinput
python manage.py sync_odoo_from_env
exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 ...
```

(No es obligatorio; muchas veces se hace solo la primera vez como en la Opción A.)

---

## 5. Celery (worker y beat)

Para que las **descargas SAT** y la **sincronización con Odoo** se ejecuten en segundo plano:

- **Celery worker:** ejecuta las tareas (descargas SAT, sincronización a Odoo, etc.).
- **Celery beat:** programa las tareas periódicas (sincronización semanal, verificación de solicitudes, etc.).

En Dokploy:

- Si usas **Docker Compose** con el `docker-compose.prod.yml` del repo, ya tienes los servicios `celery-worker` y `celery-beat` definidos. Solo asegúrate de que tengan las **mismas variables de entorno** que el servicio web (sobre todo `POSTGRES_*`, `CELERY_BROKER_URL`, `ODOO_*`, `AWS_*`).
- Si desplegaste solo con **Dockerfile**, añade dos servicios más que usen la **misma imagen** y estos comandos:
  - Servicio 1: `celery -A config worker -l info`
  - Servicio 2: `celery -A config beat -l info`

---

## 6. Comprobar que todo funciona

1. **Web:** Entra a `https://tu-dominio.com`, inicia sesión y que cargue el dashboard.
2. **Empresa:** Crea o selecciona una empresa y configura FIEL/CSD (certificados) si aún no está hecho.
3. **CFDIs:** Entra a **CFDIs** (vista unificada). En “Configuración de Sincronización” debería aparecer la sección **Odoo** con conexión configurada (si ejecutaste `sync_odoo_from_env`).
4. **Probar Odoo:** En la misma sección Odoo, usa “Probar conexión”. Si todo está bien, debería indicar éxito.
5. **Descarga SAT:** Ejecuta una “Sincronizar ahora” o configura la sincronización semanal y espera a que se encolen/ejecuten las tareas (revisa logs del **Celery worker** si algo falla).
6. **Sincronización Odoo:** Tras descargar CFDIs, la sincronización a Odoo se dispara automáticamente si está habilitada en la configuración; también puedes usar “Exportar a Odoo” en la misma pantalla.

---

## 7. Troubleshooting

### "Connection refused" a localhost:5432 (PostgreSQL) o Redis

Si ves algo como:
`connection to server at "localhost" (::1), port 5432 failed: Connection refused`

**Causa:** En el contenedor, `localhost` es el propio contenedor; PostgreSQL y Redis están en otros servicios.

**Solución:** En las variables de entorno de la aplicación en Dokploy, define:

- **POSTGRES_HOST** = hostname del servicio de base de datos.  
  - Si usas el `docker-compose.prod.yml` del repo (servicio `db`), pon: `POSTGRES_HOST=db`.  
  - Si usas una base de datos creada por Dokploy, usa el hostname que te indique Dokploy (nombre del servicio o URL interna).
- **CELERY_BROKER_URL** = URL de Redis con el hostname del servicio Redis.  
  - Con el compose del repo (servicio `redis`): `CELERY_BROKER_URL=redis://redis:6379/0`.  
  - Con Redis de Dokploy: `redis://<hostname-redis>:6379/0`.

Guarda, reinicia/redeploy la aplicación y vuelve a probar.

---

## 8. Resumen de variables mínimas para producción

```env
# Django
DJANGO_SECRET_KEY=...
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=tu-dominio.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://tu-dominio.com

# DB (¡obligatorio en Docker!: no uses localhost; usa el hostname del servicio de PostgreSQL)
POSTGRES_DB=aspeia_finance
POSTGRES_USER=aspeia
POSTGRES_PASSWORD=...
POSTGRES_HOST=db
POSTGRES_PORT=5432

# Redis/Celery (¡obligatorio en Docker!: no uses localhost; usa el hostname del servicio Redis)
CELERY_BROKER_URL=redis://redis:6379/0

# S3
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_STORAGE_BUCKET_NAME=...
AWS_S3_REGION_NAME=us-east-1

# Odoo (multiempresa: asignar empresa Odoo en la app CFDIs → Configuración; ODOO_COMPANY_ID opcional)
ODOO_URL=https://tu-odoo.com
ODOO_DB=nombre_db
ODOO_USERNAME=admin
ODOO_PASSWORD=...
```

Después del primer deploy, ejecutar en el contenedor web: `python manage.py migrate` y `python manage.py sync_odoo_from_env`.
