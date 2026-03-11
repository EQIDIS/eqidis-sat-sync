# Almacenamiento de Datos de Usuario, Fieles y Credenciales

He analizado el flujo de registro y onboarding tanto en el backend como en el frontend para determinar dónde se guarda la información sensible.

## Resumen de Almacenamiento

### 1. Fieles (Certificados .cer y .key)
- **Almacenamiento Primario**: Los archivos `.cer` y `.key` se suben a **Amazon S3** desde el frontend.
- **Sincronización con Odoo**: El backend sincroniza estos archivos con Odoo:
    - Los certificados **FIEL** se guardan en el modelo `l10n.mx.esignature.certificate`.
    - Los certificados **CSD** se guardan en los modelos `certificate.key` y `certificate.certificate`.
- **Ruta en S3**: Se guardan bajo el prefijo `${userId}/` (ej. `FielCer.cer`, `FielKey.key`).

### 2. Contraseñas de Certificados
- **Almacenamiento**: Se guardan en **DynamoDB** dentro del perfil del usuario (`FielPass` y `CSDPass`).
- **Seguridad**: Las contraseñas se almacenan **encriptadas** mediante un servicio de criptografía en el backend antes de guardarse en la base de datos.

### 3. Credenciales de Odoo y Sistema
- **Conexión Global**: Las credenciales principales de conexión a Odoo (`ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_PASSWORD`) se gestionan a través de **Variables de Entorno** en el servidor.
- **Empresa por Usuario**: El ID de la empresa en Odoo (`odooCompanyId`) asignado a cada usuario se almacena en su perfil en **DynamoDB**.

### 4. Flujo de Registro y Onboarding
- **Autenticación**: Se utiliza **Clerk** para el manejo de sesiones y claims de usuario.
- **Metadatos de Usuario**: Todo el perfil extendido (RFC, Company ID, Passwords encriptados) reside en **DynamoDB**.

---
> [!NOTE]
> Toda la comunicación de archivos sensibles (como la FIEL) se realiza de forma directa entre el cliente y el backend, y el backend se encarga de la persistencia segura en S3 y la validación antes de cualquier sincronización con Odoo.
