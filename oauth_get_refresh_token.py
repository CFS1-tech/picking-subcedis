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


def main():
    if len(sys.argv) != 2:
        print("Uso: python oauth_get_refresh_token.py ruta/a/client_secret.json")
        sys.exit(1)

    client_secret_path = sys.argv[1]
    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
    creds = flow.run_local_server(port=0)

    print("\n===== Copia esto a tu secrets.toml =====\n")
    print("[gcp_oauth]")
    print(f'client_id = "{creds.client_id}"')
    print(f'client_secret = "{creds.client_secret}"')
    print(f'refresh_token = "{creds.refresh_token}"')
    print('spreadsheet_id = "PEGA_AQUI_EL_ID_DE_TU_GOOGLE_SHEET"')
    print("\n=========================================\n")


if __name__ == "__main__":
    main()
