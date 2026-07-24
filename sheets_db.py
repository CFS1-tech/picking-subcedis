"""
Capa de acceso a datos usando Google Sheets (en vez de SQLite),
para que el pedido, los escaneos y el historial se guarden de forma
permanente aunque Streamlit Cloud reinicie el contenedor.

Usa credenciales OAuth de USUARIO (no service account), porque muchas
organizaciones de Google Workspace bloquean la creación/descarga de
claves de service account. El refresh_token se genera UNA sola vez de
forma local con oauth_get_refresh_token.py y luego se guarda en
st.secrets (o en .streamlit/secrets.toml) para que la app lo reutilice
sin volver a abrir el navegador.

Mantiene exactamente la misma interfaz que db.py para que app.py no
tenga que cambiar su lógica, solo el import.
"""
import json
from datetime import datetime

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

PEDIDO_HEADERS = ["week_tag", "tienda", "nombre_tienda", "codigo", "cantidad_solicitada", "fecha_carga"]
SCANS_HEADERS = ["week_tag", "tienda", "codigo", "cantidad_escaneada", "cantidad_devuelta", "ultima_actualizacion"]
HISTORIAL_HEADERS = [
    "week_tag", "tienda", "fecha_cierre",
    "solicitado_total", "tenido_total", "faltante_total", "devuelto_total", "detalle_json",
]
PEDIDO_DETALLE_HEADERS = [
    "week_tag", "id_cabecera", "id_linea", "codigo_departamento", "nombre_departamento",
    "codigo_color", "codigo", "unidades_solicitadas", "unidades_recibidas",
    "cabecera_original", "articulo_original", "cod", "color",
]


def _get_credentials():
    """Arma credenciales OAuth de usuario a partir de client_id/secret/refresh_token
    guardados en st.secrets['gcp_oauth']."""
    cfg = st.secrets["gcp_oauth"]
    creds = Credentials(
        token=None,
        refresh_token=cfg["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        scopes=SCOPES,
    )
    return creds


@st.cache_resource(show_spinner=False)
def _get_client():
    creds = _get_credentials()
    return gspread.authorize(creds)


@st.cache_resource(show_spinner=False)
def _get_spreadsheet():
    client = _get_client()
    sheet_id = st.secrets["gcp_oauth"]["spreadsheet_id"]
    return client.open_by_key(sheet_id)


def _ensure_worksheet(sh, title, headers):
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=1000, cols=len(headers) + 2)
        ws.append_row(headers)
        return ws
    existing_headers = ws.row_values(1)
    if existing_headers != headers:
        if not existing_headers:
            ws.append_row(headers)
    return ws


@st.cache_resource(show_spinner=False)
def _ensure_all_worksheets(_sh):
    """Se ejecuta UNA sola vez por sesión (cacheado), no en cada rerun de
    Streamlit. Antes esto se repetía en cada clic (3 llamadas a la API de
    metadatos por rerun), lo cual agotaba la cuota rápidamente."""
    _ensure_worksheet(_sh, "pedido_items", PEDIDO_HEADERS)
    _ensure_worksheet(_sh, "scans", SCANS_HEADERS)
    _ensure_worksheet(_sh, "historial", HISTORIAL_HEADERS)
    _ensure_worksheet(_sh, "pedido_detalle", PEDIDO_DETALLE_HEADERS)
    return True


def init_db():
    """Equivalente a db.init_db(): asegura que existan las 3 pestañas con encabezados.
    Devuelve el objeto Spreadsheet (se pasa como 'conn' al resto de funciones)."""
    sh = _get_spreadsheet()
    _ensure_all_worksheets(sh)
    return sh


def _records_df(ws, headers):
    values = ws.get_all_records(expected_headers=headers)
    if not values:
        return pd.DataFrame(columns=headers)
    return pd.DataFrame(values)


def _write_df(ws, df, headers):
    ws.clear()
    ws.append_row(headers)
    if not df.empty:
        rows = df[headers].fillna("").values.tolist()
        ws.append_rows(rows, value_input_option="USER_ENTERED")


# ------------------------------------------------------------------
# pedido_items
# ------------------------------------------------------------------
# El pedido casi no cambia durante una sesión de escaneo, así que cacheamos
# estas lecturas (evita relecturas de toda la hoja en cada rerun de Streamlit,
# que es lo que agota la cuota de la API de Google Sheets al escanear seguido).
@st.cache_data(ttl=120, show_spinner=False)
def _pedido_df_cached(_conn, cache_key):
    ws = _conn.worksheet("pedido_items")
    return _records_df(ws, PEDIDO_HEADERS)


def replace_pedido(conn, week_tag, df):
    sh = conn
    ws = sh.worksheet("pedido_items")
    current = _records_df(ws, PEDIDO_HEADERS)
    if not current.empty:
        current = current[current["week_tag"].astype(str) != str(week_tag)]

    now = datetime.now().isoformat(timespec="seconds")
    new_rows = df.copy()
    new_rows["week_tag"] = week_tag
    new_rows["fecha_carga"] = now
    new_rows = new_rows[PEDIDO_HEADERS]

    result = pd.concat([current, new_rows], ignore_index=True)
    _write_df(ws, result, PEDIDO_HEADERS)

    # limpia también los escaneos previos de esa semana (pedido nuevo = escaneos reiniciados)
    scans_ws = sh.worksheet("scans")
    scans_df = _records_df(scans_ws, SCANS_HEADERS)
    if not scans_df.empty:
        scans_df = scans_df[scans_df["week_tag"].astype(str) != str(week_tag)]
        _write_df(scans_ws, scans_df, SCANS_HEADERS)

    _pedido_df_cached.clear()  # el pedido cambió, invalidamos el cache


def guardar_pedido_detalle(conn, week_tag, detalle_df):
    """Guarda el detalle crudo del pedido (una fila por línea original, sin
    consolidar), usado únicamente por el reporte descargable."""
    ws = conn.worksheet("pedido_detalle")
    current = _records_df(ws, PEDIDO_DETALLE_HEADERS)
    if not current.empty:
        current = current[current["week_tag"].astype(str) != str(week_tag)]

    new_rows = detalle_df.copy()
    new_rows["week_tag"] = week_tag
    new_rows = new_rows[PEDIDO_DETALLE_HEADERS]

    result = pd.concat([current, new_rows], ignore_index=True)
    _write_df(ws, result, PEDIDO_DETALLE_HEADERS)


def get_pedido_detalle(conn, week_tag):
    ws = conn.worksheet("pedido_detalle")
    df = _records_df(ws, PEDIDO_DETALLE_HEADERS)
    if df.empty:
        return df
    return df[df["week_tag"].astype(str) == str(week_tag)].drop(columns=["week_tag"]).reset_index(drop=True)


def list_week_tags(conn):
    df = _pedido_df_cached(conn, "all")
    if df.empty:
        return []
    return sorted(df["week_tag"].astype(str).unique(), reverse=True)


def list_tiendas(conn, week_tag):
    df = _pedido_df_cached(conn, "all")
    if df.empty:
        return []
    df = df[df["week_tag"].astype(str) == str(week_tag)]
    pares = df[["tienda", "nombre_tienda"]].drop_duplicates().sort_values("tienda")
    return list(pares.itertuples(index=False, name=None))


def get_pedido_tienda(conn, week_tag, tienda):
    df = _pedido_df_cached(conn, "all")
    if df.empty:
        return {}
    df = df[(df["week_tag"].astype(str) == str(week_tag)) & (df["tienda"].astype(str) == str(tienda))]
    return {str(r["codigo"]): float(r["cantidad_solicitada"]) for _, r in df.iterrows()}


# ------------------------------------------------------------------
# scans
# ------------------------------------------------------------------
# Nota de rendimiento: durante una sesión de escaneo intensivo, leer toda la
# hoja "scans" en cada escaneo agota rápido la cuota de la API de Google
# Sheets (~60 lecturas/min). Por eso get_scans_tienda se llama UNA vez al
# entrar a una tienda (se cachea en session_state desde app.py), y
# register_scan recibe el estado previo del propio código (prev_state) para
# no tener que releer toda la hoja en cada escaneo: solo hace una escritura.
def get_scans_tienda(conn, week_tag, tienda):
    ws = conn.worksheet("scans")
    df = _records_df(ws, SCANS_HEADERS)
    if df.empty:
        return {}
    mask = (df["week_tag"].astype(str) == str(week_tag)) & (df["tienda"].astype(str) == str(tienda))
    df = df[mask]
    out = {}
    for idx, r in df.iterrows():
        out[str(r["codigo"])] = {
            "escaneado": float(r["cantidad_escaneada"] or 0),
            "devuelto": float(r["cantidad_devuelta"] or 0),
            "row": idx + 2,  # +2 por encabezado (fila 1) y por índice base 0
        }
    return out


def register_scan(conn, week_tag, tienda, codigo, solicitado_map, prev_state=None):
    """Registra un escaneo directamente sobre la hoja 'scans'.

    prev_state (opcional): {"escaneado": x, "devuelto": y, "row": n} si ya se
    conoce el estado previo de ese código (evita releer toda la hoja). Si es
    None, se asume que es la primera vez que se escanea ese código en esta
    sesión y se agrega como fila nueva.
    """
    ws = conn.worksheet("scans")

    pertenece = codigo in solicitado_map
    solicitado = solicitado_map.get(codigo, 0)

    if not pertenece:
        return {"estado": "no_pertenece", "solicitado": 0, "escaneado_total": 0, "devuelto_total": 0, "row": None}

    if prev_state is not None:
        escaneado_prev = prev_state.get("escaneado", 0)
        devuelto_prev = prev_state.get("devuelto", 0)
        row_number = prev_state.get("row")
    else:
        escaneado_prev = 0
        devuelto_prev = 0
        row_number = None

    if escaneado_prev + 1 > solicitado:
        estado = "excedente"
        nuevo_escaneado = escaneado_prev
        nuevo_devuelto = devuelto_prev + 1
    else:
        estado = "ok"
        nuevo_escaneado = escaneado_prev + 1
        nuevo_devuelto = devuelto_prev

    now = datetime.now().isoformat(timespec="seconds")

    if row_number is not None:
        ws.update(
            f"A{row_number}:F{row_number}",
            [[week_tag, tienda, codigo, nuevo_escaneado, nuevo_devuelto, now]],
            value_input_option="USER_ENTERED",
        )
    else:
        response = ws.append_row(
            [week_tag, tienda, codigo, nuevo_escaneado, nuevo_devuelto, now],
            value_input_option="USER_ENTERED",
        )
        # obtenemos el número de fila directo de la respuesta de la API,
        # sin necesidad de una lectura extra (updatedRange ej. "scans!A6:F6")
        try:
            updated_range = response["updates"]["updatedRange"]
            row_number = int("".join(filter(str.isdigit, updated_range.split("!")[1].split(":")[0])))
        except (KeyError, ValueError, IndexError):
            row_number = None

    return {
        "estado": estado,
        "solicitado": solicitado,
        "escaneado_total": nuevo_escaneado,
        "devuelto_total": nuevo_devuelto,
        "row": row_number,
    }


# ------------------------------------------------------------------
# historial
# ------------------------------------------------------------------
def guardar_historial(conn, week_tag, tienda, resumen_rows):
    ws = conn.worksheet("historial")
    solicitado_total = sum(r["solicitado"] for r in resumen_rows)
    tenido_total = sum(r["tenido"] for r in resumen_rows)
    faltante_total = sum(r["falta"] for r in resumen_rows)
    devuelto_total = sum(r["devuelto"] for r in resumen_rows)

    ws.append_row(
        [
            week_tag,
            tienda,
            datetime.now().isoformat(timespec="seconds"),
            solicitado_total,
            tenido_total,
            faltante_total,
            devuelto_total,
            json.dumps(resumen_rows, ensure_ascii=False),
        ],
        value_input_option="USER_ENTERED",
    )


def get_historial(conn, week_tag=None, tienda=None):
    ws = conn.worksheet("historial")
    df = _records_df(ws, HISTORIAL_HEADERS)
    if df.empty:
        return []
    if week_tag:
        df = df[df["week_tag"].astype(str) == str(week_tag)]
    if tienda:
        df = df[df["tienda"].astype(str) == str(tienda)]
    df = df.sort_values("fecha_cierre", ascending=False)
    cols = HISTORIAL_HEADERS[:-1]  # sin detalle_json, igual que db.get_historial
    return list(df[cols].itertuples(index=False, name=None))


def get_ultimo_detalle_validacion(conn, week_tag, tienda):
    """Devuelve el detalle por código (lista de dicts: codigo, solicitado,
    tenido, falta, devuelto) de la ÚLTIMA validación cerrada para esa
    tienda/semana. None si no se ha cerrado ninguna validación.

    Nota: si necesitas el detalle de VARIAS tiendas (ej. para el reporte),
    usa get_ultimo_detalle_validacion_todas en su lugar — esta función hace
    una lectura completa de la hoja 'historial' cada vez que se llama, así
    que llamarla en un loop por tienda agota la cuota de la API rápido."""
    ws = conn.worksheet("historial")
    df = _records_df(ws, HISTORIAL_HEADERS)
    if df.empty:
        return None
    df = df[(df["week_tag"].astype(str) == str(week_tag)) & (df["tienda"].astype(str) == str(tienda))]
    if df.empty:
        return None
    df = df.sort_values("fecha_cierre", ascending=False)
    detalle_json = df.iloc[0]["detalle_json"]
    if not detalle_json:
        return None
    return json.loads(detalle_json)


def get_ultimo_detalle_validacion_todas(conn, week_tag):
    """Como get_ultimo_detalle_validacion, pero para TODAS las tiendas de la
    semana en una sola lectura de la hoja. Devuelve un dict {tienda: [items]}.
    Úsala cuando necesites el detalle de varias tiendas (ej. el reporte)."""
    ws = conn.worksheet("historial")
    df = _records_df(ws, HISTORIAL_HEADERS)
    if df.empty:
        return {}
    df = df[df["week_tag"].astype(str) == str(week_tag)]
    if df.empty:
        return {}
    df = df.sort_values("fecha_cierre", ascending=False)

    resultado = {}
    for _, row in df.iterrows():
        tienda = str(row["tienda"])
        if tienda in resultado:
            continue  # ya tenemos la más reciente de esta tienda
        detalle_json = row["detalle_json"]
        resultado[tienda] = json.loads(detalle_json) if detalle_json else []
    return resultado
