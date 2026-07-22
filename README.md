# Picking Subcedis — App de carga y validación

App en Streamlit para:

1. **Cargar pedido**: subir el Excel "Picking Subcedis W##" (la semana es variable, se detecta sola), consolidar cantidades por tienda + código único (quitando el punto del código) y descargar el CSV listo para el WMS (formato `PDE_num_doc; PDE_lin_doc; PDE_fec_emi; PDE_fec_ent; PDE_cod_mat; PDE_pdt_mat; PDE_cod_tdo_rel`).
2. **Validación de picking**: escanear producto a producto (lector USB) por tienda. La app indica si el código pertenece al pedido, si ya se completó la cantidad (excedente/devolución), y muestra un resumen en vivo de tenido / falta / devuelto.
3. **Historial**: al cerrar una validación se guarda un registro por semana y tienda con los totales (solicitado, tenido, faltante, devuelto).

## Reglas de negocio implementadas

- El código de producto viene con punto en el pedido original (`codigo_color`, ej. `146590.056`); se quita el punto para todo (consolidado y CSV).
- Si en el pedido original una misma tienda tiene el mismo código en varias filas, se agrupan sumando `unidades_solicitadas`.
- `PDE_num_doc` = `{tienda}-{ddMM de fecha de emisión}` (ej. `4201-2007`).
- `PDE_fec_emi` = hoy (editable). `PDE_fec_ent` = la fecha que ingreses al cargar.
- `PDE_lin_doc` = 1 y `PDE_cod_tdo_rel` = "PD" fijos para todas las filas.
- Escaneo por tienda: seleccionas semana + tienda y escaneas solo esos productos.
  - Código no está en el pedido de esa tienda → **no pertenece**.
  - Código está y aún no llega a la cantidad solicitada → cuenta como **tenido**.
  - Código ya alcanzó la cantidad solicitada y se vuelve a escanear → se registra como **devolución** (excedente).

## Persistencia: SQLite local vs. Google Sheets

Por defecto la app guarda todo en SQLite local (`data/picking.db`). Esto es rápido para probar, pero en Streamlit Community Cloud el archivo puede perderse si la app se redeploya o "duerme".

Para que **todo se guarde permanentemente en un Google Sheet**, sigue la guía de abajo. Una vez configurados los secrets, la app detecta automáticamente las credenciales y cambia sola a Google Sheets (lo verás confirmado abajo a la izquierda, en la barra lateral).

---

## Guía paso a paso: conectar la app a Google Sheets (OAuth, sin service account)

Como tu organización de Google Workspace bloquea la descarga/creación de claves de *service account*, usamos **OAuth de tu propia cuenta de usuario**. Es un proceso que haces **una sola vez**; el resultado (un `refresh_token`) se guarda en los "Secrets" de la app y esta ya no vuelve a pedirte que inicies sesión.

### Paso 1 — Crear el Google Sheet

1. Ve a https://sheets.google.com y crea una hoja de cálculo nueva (vacía). Nómbrala, por ejemplo, "Picking Subcedis - Base".
2. Copia su **ID**: es la parte de la URL entre `/d/` y `/edit`.
   `https://docs.google.com/spreadsheets/d/`**`1AbCdEfGhIjKlMnOpQrStUvWxYz`**`/edit`
3. No necesitas crear las pestañas a mano — la app las crea solas la primera vez que corre (`pedido_items`, `scans`, `historial`), con estos encabezados:

   - **pedido_items**: `week_tag, tienda, nombre_tienda, codigo, cantidad_solicitada, fecha_carga`
   - **scans**: `week_tag, tienda, codigo, cantidad_escaneada, cantidad_devuelta, ultima_actualizacion`
   - **historial**: `week_tag, tienda, fecha_cierre, solicitado_total, tenido_total, faltante_total, devuelto_total, detalle_json`

   Si prefieres crearlas tú mismo antes, respeta esos nombres y encabezados exactamente.

### Paso 2 — Crear el proyecto y la credencial OAuth en Google Cloud

1. Entra a https://console.cloud.google.com/ con tu cuenta.
2. Arriba a la izquierda, crea un **proyecto nuevo** (o usa uno existente que no tenga restricciones especiales de tu organización): "Nuevo proyecto" → nómbralo, ej. "picking-subcedis" → Crear.
3. Con el proyecto seleccionado, ve a **"APIs y servicios" → "Biblioteca"**, busca **"Google Sheets API"** y presiona **Habilitar**.
4. Ve a **"APIs y servicios" → "Pantalla de consentimiento OAuth"**:
   - Tipo de usuario: **Externo** (o "Interno" si tu organización lo permite y quieres restringirlo a tu dominio).
   - Completa nombre de la app, correo de soporte y correo de contacto (los tuyos).
   - En "Scopes" no hace falta agregar nada manualmente por ahora.
   - En "Usuarios de prueba" (si quedó en modo "Testing"), agrega tu propio correo (`johanna.alfaro.g@uni.pe`) para poder autenticarte.
   - Guarda.
5. Ve a **"APIs y servicios" → "Credenciales"** → **"Crear credenciales" → "ID de cliente de OAuth"**.
   - Tipo de aplicación: **"Aplicación de escritorio"** (Desktop app). Esta opción **sí permite descargar el JSON**, a diferencia de las claves de service account que tu organización bloquea.
   - Nómbrala, ej. "picking-subcedis-desktop".
   - Crear → descarga el archivo JSON (botón de descarga). Guárdalo en tu compu, por ejemplo como `client_secret.json`.

### Paso 3 — Generar el refresh_token (una sola vez, en tu compu)

1. En tu computadora (no en Streamlit Cloud), instala la dependencia necesaria:
   ```bash
   pip install google-auth-oauthlib
   ```
2. Corre el script incluido en esta carpeta, pasando la ruta al JSON que descargaste:
   ```bash
   python oauth_get_refresh_token.py ruta/a/client_secret.json
   ```
3. Se abrirá tu navegador pidiéndote iniciar sesión con la cuenta que tiene acceso al Google Sheet del Paso 1, y aceptar los permisos. Si Google muestra una advertencia de "app no verificada", es normal (es tu propia app) — dale a "Continuar".
4. El script imprimirá algo así:
   ```
   [gcp_oauth]
   client_id = "xxxx.apps.googleusercontent.com"
   client_secret = "xxxx"
   refresh_token = "xxxx"
   spreadsheet_id = "PEGA_AQUI_EL_ID_DE_TU_GOOGLE_SHEET"
   ```
5. Copia ese bloque, reemplaza `spreadsheet_id` por el ID que copiaste en el Paso 1.

### Paso 4 — Guardar las credenciales como Secrets

**Para correr localmente:**
Crea el archivo `.streamlit/secrets.toml` (usa `secrets.toml.example` como base) dentro de la carpeta `picking-app/`, y pega ahí el bloque `[gcp_oauth]` completo. Este archivo ya está en `.gitignore`, nunca se sube a GitHub.

**Para Streamlit Community Cloud:**
1. Entra a tu app ya desplegada → menú "⋮" → **Settings → Secrets**.
2. Pega el mismo bloque `[gcp_oauth]` ahí.
3. Guarda; la app se reinicia sola y detecta las credenciales automáticamente.

A partir de aquí, todo lo que cargues, escanees y cierres se guarda directamente en tu Google Sheet, sin que se pierda al redesplegar o dormir la app.

---

## Cómo correr localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Cómo subir a GitHub y desplegar en Streamlit Community Cloud

1. Crea un repositorio nuevo en GitHub (puede ser privado) y sube esta carpeta completa:
   ```bash
   cd picking-app
   git init
   git add .
   git commit -m "Picking Subcedis app"
   git branch -M main
   git remote add origin https://github.com/<tu-usuario>/<tu-repo>.git
   git push -u origin main
   ```
2. Entra a https://share.streamlit.io, conecta tu cuenta de GitHub.
3. "New app" → selecciona el repo, branch `main`, archivo principal `app.py`.
4. Antes o después del primer deploy, configura los Secrets como se explicó arriba (Paso 4).
5. Deploy. En unos minutos tendrás la URL pública de tu app, ya guardando todo en tu Google Sheet.
