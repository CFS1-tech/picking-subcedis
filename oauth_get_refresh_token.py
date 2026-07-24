"""
Ejecuta este script UNA sola vez, de forma LOCAL (en tu computadora, no en
Streamlit Cloud), para obtener el refresh_token que la app usará para siempre.

Requisitos previos (ver README.md, sección "Google Cloud OAuth"):
  1. Haber creado un proyecto en https://console.cloud.google.com
  2. Haber habilitado la "Google Sheets API"
  3. Haber creado una credencial OAuth de tipo "Desktop app" y descargado
     su archivo JSON (este SÍ se puede descargar, a diferencia de las
     claves de service account que tu organización bloquea).
  4. Haber creado tu Google Sheet y compartido su ID (o simplemente
     asegurarte de que la cuenta con la que hagas login aquí tenga acceso
     a esa hoja).

Uso:
    pip install google-auth-oauthlib
    python oauth_get_refresh_token.py ruta/a/client_secret.json

Se abrirá tu navegador para que inicies sesión y aceptes los permisos.
Al terminar, el script imprime client_id, client_secret y refresh_token:
cópialos a tu archivo .streamlit/secrets.toml (local) y a los "Secrets"
de tu app en Streamlit Community Cloud.
"""
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

OUTPUT_FILE = "secrets_generado.toml"


def main():
    if len(sys.argv) < 2:
        print("Uso: python oauth_get_refresh_token.py ruta/a/client_secret.json [spreadsheet_id]")
        sys.exit(1)

    client_secret_path = sys.argv[1]
    spreadsheet_id = sys.argv[2] if len(sys.argv) > 2 else "PEGA_AQUI_EL_ID_DE_TU_GOOGLE_SHEET"

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    creds = flow.run_local_server(port=0)

    contenido = (
        "[gcp_oauth]\n"
        f'client_id = "{creds.client_id}"\n'
        f'client_secret = "{creds.client_secret}"\n'
        f'refresh_token = "{creds.refresh_token}"\n'
        f'spreadsheet_id = "{spreadsheet_id}"\n'
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(contenido)

    print(f"\nListo. Se guardó el archivo '{OUTPUT_FILE}' en esta misma carpeta.")
    print("Abre ese archivo, copia TODO su contenido (Ctrl+A, Ctrl+C) y pégalo")
    print("tal cual en los Secrets de Streamlit Cloud. Así evitas errores de copiado")
    print("por saltos de línea que la consola introduce al mostrar textos largos.\n")
    print("También se imprime aquí abajo por si prefieres copiarlo directo, pero")
    print("es más seguro usar el archivo:\n")
    print(contenido)


if __name__ == "__main__":
    main()
