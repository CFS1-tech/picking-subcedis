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
from zoneinfo import ZoneInfo

import gspread
import pandas as pd

TZ_PERU = ZoneInfo("America/Lima")


def _ahora():
    """Hora actual en zona horaria de Peru (los servidores de Streamlit Cloud
    corren en UTC, asi que sin esto el historial mostraria la hora adelantada)."""
    return datetime.now(TZ_PERU)
import streamlit as st
from google.oauth2.credentials import Credentials
from openpyxl.utils import get_column_letter

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

# ------------------------------------------------------------------
# Recepciones (packing lists de recepción)
# ------------------------------------------------------------------
RECEPCION_PEDIDO_HEADERS = ["documento", "fecha_carga", "nombre_archivo", "formato_detectado", "total_lineas"]
RECEPCION_DETALLE_HEADERS = ["documento", "box_number", "codigo", "tipo_linea", "pool_id", "cantidad_esperada"]
RECEPCION_POOLS_HEADERS = ["pool_id", "documento", "box_number", "codigos_miembros", "cantidad_total_esperada"]
RECEPCION_SCANS_HEADERS = [
    "documento", "box_number", "pool_id", "codigo", "cantidad_recibida", "cantidad_devuelta",
    "hora", "estado", "row",
]
RECEPCION_HISTORIAL_HEADERS = [
    "documento", "box_number", "fecha_cierre",
    "total_esperado", "total_recibido", "faltante_total", "excedente_total", "detalle_json",
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
    ws_scans = _ensure_worksheet(_sh, "scans", SCANS_HEADERS)
    _ensure_worksheet(_sh, "historial", HISTORIAL_HEADERS)
    _ensure_worksheet(_sh, "pedido_detalle", PEDIDO_DETALLE_HEADERS)

    # Deja la columna 'codigo' (C) de 'scans' formateada como TEXTO por
    # adelantado, para un rango amplio de filas. Como esto corre cacheado
    # (una sola vez por sesión), no cuesta una llamada extra en cada escaneo,
    # y evita que Sheets convierta códigos tipo "005" a número al guardarlos.
    try:
        ws_scans.format("C1:C20000", {"numberFormat": {"type": "TEXT"}})
    except Exception:
        pass

    _ensure_worksheet(_sh, "recepcion_pedido", RECEPCION_PEDIDO_HEADERS)
    _ensure_worksheet(_sh, "recepcion_detalle", RECEPCION_DETALLE_HEADERS)
    _ensure_worksheet(_sh, "recepcion_pools", RECEPCION_POOLS_HEADERS)
    ws_rec_scans = _ensure_worksheet(_sh, "recepcion_scans", RECEPCION_SCANS_HEADERS)
    _ensure_worksheet(_sh, "recepcion_historial", RECEPCION_HISTORIAL_HEADERS)

    try:
        # documento(1), box_number(2), pool_id(3), codigo(4): texto siempre.
        ws_rec_scans.format("A1:D20000", {"numberFormat": {"type": "TEXT"}})
    except Exception:
        pass

    return True


def init_db():
    """Equivalente a db.init_db(): asegura que existan las 3 pestañas con encabezados.
    Devuelve el objeto Spreadsheet (se pasa como 'conn' al resto de funciones)."""
    sh = _get_spreadsheet()
    _ensure_all_worksheets(sh)
    return sh


def _records_df(ws, headers, numericise_ignore=None):
    """numericise_ignore: índices de columna (1-based) que NO se deben
    convertir a número al leer. gspread por defecto intenta convertir
    cualquier celda que "parezca" número (ej. '001' -> 1), incluso si en la
    hoja el texto se ve bien — por eso hay que excluir explícitamente las
    columnas de códigos/ids que pueden tener ceros a la izquierda."""
    values = ws.get_all_records(expected_headers=headers, numericise_ignore=numericise_ignore or [])
    if not values:
        return pd.DataFrame(columns=headers)
    return pd.DataFrame(values)


def _forzar_formato_texto(ws, headers, columnas_texto, num_filas):
    """Fuerza formato TEXTO en columnas que deben preservar ceros a la
    izquierda (codigo, cod, color, etc.). No basta con mandar value_input_option
    RAW si la columna quedó con formato numérico de un guardado anterior:
    Sheets sigue mostrando el valor sin los ceros. Hay que fijar el formato
    de la columna a TEXTO antes de escribir los valores nuevos."""
    if num_filas <= 0:
        return
    for col_name in columnas_texto:
        if col_name not in headers:
            continue
        col_idx = headers.index(col_name) + 1
        col_letter = get_column_letter(col_idx)
        try:
            ws.format(f"{col_letter}1:{col_letter}{num_filas + 1}", {"numberFormat": {"type": "TEXT"}})
        except Exception:
            pass  # si falla el formateo no debe romper el guardado de datos


def _write_df(ws, df, headers, columnas_texto=None):
    ws.clear()
    ws.append_row(headers)
    if columnas_texto:
        _forzar_formato_texto(ws, headers, columnas_texto, len(df))
    if not df.empty:
        rows = df[headers].fillna("").values.tolist()
        # RAW (no USER_ENTERED): evita que Sheets "interprete" texto como
        # "005" y lo convierta al número 5, perdiendo ceros a la izquierda
        # en columnas como codigo, cod o color.
        ws.append_rows(rows, value_input_option="RAW")


# ------------------------------------------------------------------
# pedido_items
# ------------------------------------------------------------------
# El pedido casi no cambia durante una sesión de escaneo, así que cacheamos
# estas lecturas (evita relecturas de toda la hoja en cada rerun de Streamlit,
# que es lo que agota la cuota de la API de Google Sheets al escanear seguido).
@st.cache_data(ttl=120, show_spinner=False)
def _pedido_df_cached(_conn, cache_key):
    ws = _conn.worksheet("pedido_items")
    # tienda(2) y codigo(4): no numericé, para no perder ceros a la izquierda
    return _records_df(ws, PEDIDO_HEADERS, numericise_ignore=[2, 4])


def replace_pedido(conn, week_tag, df):
    sh = conn
    ws = sh.worksheet("pedido_items")
    # mismo cuidado que en guardar_pedido_detalle: sin numericise_ignore aquí,
    # cada carga nueva iba dañando el 'codigo' de semanas anteriores al
    # releerlas y reescribirlas.
    current = _records_df(ws, PEDIDO_HEADERS, numericise_ignore=[2, 4])
    if not current.empty:
        current = current[current["week_tag"].astype(str) != str(week_tag)]
        for col in ["tienda", "codigo"]:
            current[col] = current[col].apply(lambda v: "" if v is None or v == "" else str(v))
            current[col] = current[col].str.replace(r"\.0$", "", regex=True)

    now = _ahora().isoformat(timespec="seconds")
    new_rows = df.copy()
    new_rows["week_tag"] = week_tag
    new_rows["fecha_carga"] = now
    new_rows = new_rows[PEDIDO_HEADERS]

    result = pd.concat([current, new_rows], ignore_index=True)
    _write_df(ws, result, PEDIDO_HEADERS, columnas_texto=["tienda", "codigo"])

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
    # Importante: al releer lo existente de OTRAS semanas para preservarlo,
    # hay que usar el mismo numericise_ignore que en get_pedido_detalle — si
    # no, cada nueva carga de pedido iba reescribiendo (y dañando) el detalle
    # de semanas anteriores, convirtiendo códigos tipo "001" en 1.
    current = _records_df(ws, PEDIDO_DETALLE_HEADERS, numericise_ignore=[2, 3, 4, 6, 7, 10, 11, 12, 13])
    if not current.empty:
        current = current[current["week_tag"].astype(str) != str(week_tag)]
        for col in ["id_cabecera", "id_linea", "codigo_departamento", "codigo_color",
                    "codigo", "cabecera_original", "articulo_original", "cod", "color"]:
            current[col] = current[col].apply(lambda v: "" if v is None or v == "" else str(v))
            current[col] = current[col].str.replace(r"\.0$", "", regex=True)

    new_rows = detalle_df.copy()
    new_rows["week_tag"] = week_tag
    new_rows = new_rows[PEDIDO_DETALLE_HEADERS]

    result = pd.concat([current, new_rows], ignore_index=True)
    _write_df(
        ws, result, PEDIDO_DETALLE_HEADERS,
        columnas_texto=[
            "id_cabecera", "id_linea", "codigo_departamento", "codigo_color",
            "codigo", "cabecera_original", "articulo_original", "cod", "color",
        ],
    )


def get_pedido_detalle(conn, week_tag):
    ws = conn.worksheet("pedido_detalle")
    # índices (1-based) de columnas que NO deben convertirse a número al leer,
    # para no perder ceros a la izquierda en id_cabecera, id_linea,
    # codigo_departamento, codigo_color, codigo, cabecera_original,
    # articulo_original, cod y color.
    ignorar_numerico = [2, 3, 4, 6, 7, 10, 11, 12, 13]
    df = _records_df(ws, PEDIDO_DETALLE_HEADERS, numericise_ignore=ignorar_numerico)
    if df.empty:
        return df
    df = df[df["week_tag"].astype(str) == str(week_tag)].drop(columns=["week_tag"]).reset_index(drop=True)
    # Google Sheets a veces interpreta columnas de texto (códigos, ids) como
    # números al leerlas, lo que rompe los cruces por igualdad de string más
    # adelante (ej. en report.py). Forzamos todo a texto aquí, de forma
    # centralizada, incluyendo quitar el ".0" que pandas agrega a enteros
    # que llegaron como float.
    for col in ["codigo_departamento", "codigo", "cod", "color", "id_cabecera", "id_linea", "cabecera_original", "articulo_original"]:
        df[col] = df[col].apply(lambda v: "" if v is None or v == "" else str(v))
        df[col] = df[col].str.replace(r"\.0$", "", regex=True)
    return df


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
    # codigo es la 3ra columna (week_tag, tienda, codigo, ...): la excluimos
    # de la auto-conversión numérica para no perder ceros a la izquierda.
    df = _records_df(ws, SCANS_HEADERS, numericise_ignore=[3])
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


def register_scan(conn, week_tag, tienda, codigo, solicitado_map, prev_state=None, cantidad=1):
    """Registra un escaneo (o una cantidad contada manualmente de una vez)
    directamente sobre la hoja 'scans'.

    cantidad: unidades a sumar en esta sola llamada (por defecto 1, como un
    escaneo normal). Si escaneado_prev + cantidad supera lo solicitado, lo
    que cabe se registra como validado y el resto como excedente.

    prev_state (opcional): {"escaneado": x, "devuelto": y, "row": n} si ya se
    conoce el estado previo de ese código (evita releer toda la hoja). Si es
    None, se asume que es la primera vez que se escanea ese código en esta
    sesión y se agrega como fila nueva.
    """
    cantidad = max(int(cantidad), 1)
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

    escaneado_delta = 0
    devuelto_delta = 0
    if escaneado_prev >= solicitado:
        estado = "excedente"
        devuelto_delta = cantidad
        nuevo_escaneado = escaneado_prev
        nuevo_devuelto = devuelto_prev + cantidad
    elif escaneado_prev + cantidad > solicitado:
        estado = "excedente"
        cabe = solicitado - escaneado_prev
        escaneado_delta = cabe
        devuelto_delta = cantidad - cabe
        nuevo_escaneado = escaneado_prev + cabe
        nuevo_devuelto = devuelto_prev + devuelto_delta
    else:
        estado = "ok"
        escaneado_delta = cantidad
        nuevo_escaneado = escaneado_prev + cantidad
        nuevo_devuelto = devuelto_prev

    now = _ahora().isoformat(timespec="seconds")

    # Nota: la columna 'codigo' (C) de esta hoja ya se deja formateada como
    # TEXTO por adelantado (ver _ensure_all_worksheets), así que aquí no hace
    # falta una llamada extra a la API por cada escaneo — eso agotaría la
    # cuota rápido. RAW + formato TEXTO ya fijado es suficiente para no
    # perder ceros a la izquierda.
    if row_number is not None:
        ws.update(
            f"A{row_number}:F{row_number}",
            [[week_tag, tienda, codigo, nuevo_escaneado, nuevo_devuelto, now]],
            value_input_option="RAW",
        )
    else:
        response = ws.append_row(
            [week_tag, tienda, codigo, nuevo_escaneado, nuevo_devuelto, now],
            value_input_option="RAW",
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
        "escaneado_delta": escaneado_delta,
        "devuelto_delta": devuelto_delta,
        "row": row_number,
    }


def deshacer_scan(conn, week_tag, tienda, codigo, escaneado_delta=1, devuelto_delta=0, prev_state=None):
    """Revierte un escaneo (o una cantidad en lote) registrado de un código,
    restando exactamente lo que ese evento sumó: escaneado_delta de la
    cantidad validada y devuelto_delta de la cantidad excedente. Requiere
    prev_state (con 'row') para poder actualizar directamente esa fila sin
    releer toda la hoja.

    Por compatibilidad con registros antiguos (guardados antes de tener
    'cantidad'), si se recibe la firma vieja con 'tipo' como string en
    escaneado_delta, se asume 1 unidad."""
    if isinstance(escaneado_delta, str):
        tipo_legacy = escaneado_delta
        escaneado_delta = 0 if tipo_legacy == "excedente" else 1
        devuelto_delta = 1 if tipo_legacy == "excedente" else 0

    ws = conn.worksheet("scans")
    if not prev_state or prev_state.get("row") is None:
        return {"escaneado_total": 0, "devuelto_total": 0}

    escaneado = prev_state.get("escaneado", 0)
    devuelto = prev_state.get("devuelto", 0)
    row_number = prev_state["row"]

    escaneado = max(escaneado - escaneado_delta, 0)
    devuelto = max(devuelto - devuelto_delta, 0)

    now = _ahora().isoformat(timespec="seconds")
    ws.update(
        f"A{row_number}:F{row_number}",
        [[week_tag, tienda, codigo, escaneado, devuelto, now]],
        value_input_option="RAW",
    )

    return {"escaneado_total": escaneado, "devuelto_total": devuelto}


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
            _ahora().isoformat(timespec="seconds"),
            solicitado_total,
            tenido_total,
            faltante_total,
            devuelto_total,
            json.dumps(resumen_rows, ensure_ascii=False),
        ],
        value_input_option="RAW",
    )


def get_historial(conn, week_tag=None, tienda=None):
    ws = conn.worksheet("historial")
    # tienda(2): no numericé, para que el valor devuelto sea siempre texto
    # (ej. "4207") y se pueda comparar/usar como clave de forma consistente
    # con el resto de funciones (list_tiendas, get_pedido_tienda, etc.)
    df = _records_df(ws, HISTORIAL_HEADERS, numericise_ignore=[2])
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


# ------------------------------------------------------------------
# Cruce con el Sheet de stock del almacén (para traer la descripción del
# producto al reporte). Es un archivo externo, compartido con la misma
# cuenta de Google que usan las credenciales OAuth de la app.
# ------------------------------------------------------------------
STOCK_SHEET_ID = "1shWvQmLzdByHCmzPCoEBtgdKBsZIRSwcr12qOvCkD-8"
STOCK_SHEET_TAB = "Hoja 1"
STOCK_FAMILIA = "LA CARCASA MOVIL"


_ULTIMO_ERROR_STOCK = None


def get_stock_error():
    """Devuelve el último mensaje de error/diagnóstico al intentar leer el
    Sheet de stock (None si la última lectura fue exitosa)."""
    return _ULTIMO_ERROR_STOCK


@st.cache_data(ttl=600, show_spinner=False)
def get_stock_descripciones():
    """Devuelve un dict {codigo: descripcion} leyendo el Sheet de stock del
    almacén, filtrado a la familia 'LA CARCASA MOVIL'. Se cachea 10 minutos
    para no gastar cuota de la API en cada reporte generado."""
    global _ULTIMO_ERROR_STOCK
    _ULTIMO_ERROR_STOCK = None
    try:
        client = _get_client()
        sh = client.open_by_key(STOCK_SHEET_ID)
        ws = sh.worksheet(STOCK_SHEET_TAB)
        headers = ws.row_values(1)
        # el código en este sheet ya viene sin punto, pero puede tener ceros
        # a la izquierda, así que lo excluimos de la auto-conversión numérica.
        idx_codigo = headers.index("Código") + 1 if "Código" in headers else None
        values = ws.get_all_records(numericise_ignore=[idx_codigo] if idx_codigo else [])
    except Exception as e:
        # si el sheet de stock no está disponible por algún motivo, el
        # reporte debe seguir funcionando igual (solo sin las descripciones),
        # pero guardamos el motivo para poder mostrarlo en la app.
        _ULTIMO_ERROR_STOCK = f"No se pudo leer el Sheet de stock: {e}"
        return {}

    if not values:
        _ULTIMO_ERROR_STOCK = (
            f"El Sheet de stock (hoja '{STOCK_SHEET_TAB}') se leyó pero está vacío, "
            "o los encabezados no coinciden con lo esperado."
        )
        return {}

    resultado = {}
    filas_familia = 0
    for row in values:
        familia = str(row.get("Familia", "")).strip().upper()
        if familia != STOCK_FAMILIA.upper():
            continue
        filas_familia += 1
        codigo = str(row.get("Código", "")).strip()
        if codigo:
            resultado[codigo] = row.get("Descripción", "")

    if filas_familia == 0:
        familias_vistas = sorted({str(r.get("Familia", "")).strip() for r in values})[:10]
        _ULTIMO_ERROR_STOCK = (
            f"No se encontró ninguna fila con Familia = '{STOCK_FAMILIA}' en el Sheet de stock. "
            f"Familias vistas (ejemplo): {familias_vistas}"
        )
    return resultado


# ------------------------------------------------------------------
# RECEPCIONES (packing lists AP): guardar packing list, validar por
# escaneo (líneas individuales y "pools" de códigos agrupados por caja),
# e historial de cierre por caja.
# ------------------------------------------------------------------
def guardar_packing_list_recepcion(conn, resumen, detalle_df, pools_df):
    """Reemplaza el packing list completo de un documento (detalle + pools)
    y reinicia los escaneos de las cajas que trae este documento."""
    documento = resumen["documento"]
    now = _ahora().isoformat(timespec="seconds")

    # recepcion_pedido: upsert por documento
    ws_pedido = conn.worksheet("recepcion_pedido")
    pedido_df = _records_df(ws_pedido, RECEPCION_PEDIDO_HEADERS, numericise_ignore=[1])
    if not pedido_df.empty:
        pedido_df = pedido_df[pedido_df["documento"].astype(str) != str(documento)]
    nueva_fila = pd.DataFrame([{
        "documento": documento,
        "fecha_carga": now,
        "nombre_archivo": resumen.get("nombre_archivo", ""),
        "formato_detectado": resumen.get("formato_detectado", ""),
        "total_lineas": resumen.get("total_lineas", 0),
    }])
    pedido_df = pd.concat([pedido_df, nueva_fila], ignore_index=True)
    _write_df(ws_pedido, pedido_df, RECEPCION_PEDIDO_HEADERS, columnas_texto=["documento"])

    # recepcion_detalle: reemplaza las filas de este documento
    ws_detalle = conn.worksheet("recepcion_detalle")
    detalle_actual = _records_df(ws_detalle, RECEPCION_DETALLE_HEADERS, numericise_ignore=[1, 2, 3, 5])
    if not detalle_actual.empty:
        detalle_actual = detalle_actual[detalle_actual["documento"].astype(str) != str(documento)]
    detalle_nuevo = detalle_df.copy()
    detalle_nuevo["documento"] = documento
    resultado_detalle = pd.concat([detalle_actual, detalle_nuevo[RECEPCION_DETALLE_HEADERS]], ignore_index=True)
    _write_df(ws_detalle, resultado_detalle, RECEPCION_DETALLE_HEADERS,
              columnas_texto=["documento", "box_number", "codigo", "pool_id"])

    # recepcion_pools: reemplaza las filas de este documento
    ws_pools = conn.worksheet("recepcion_pools")
    pools_actual = _records_df(ws_pools, RECEPCION_POOLS_HEADERS, numericise_ignore=[1, 2, 3])
    if not pools_actual.empty:
        pools_actual = pools_actual[pools_actual["documento"].astype(str) != str(documento)]
    pools_nuevo = pools_df.copy()
    pools_nuevo["documento"] = documento
    resultado_pools = pd.concat([pools_actual, pools_nuevo[RECEPCION_POOLS_HEADERS]], ignore_index=True)
    _write_df(ws_pools, resultado_pools, RECEPCION_POOLS_HEADERS,
              columnas_texto=["pool_id", "documento", "box_number"])

    # recepcion_scans: limpia los escaneos de las cajas que trae este documento
    ws_scans = conn.worksheet("recepcion_scans")
    scans_actual = _records_df(ws_scans, RECEPCION_SCANS_HEADERS, numericise_ignore=[1, 2, 3, 4, 8])
    if not scans_actual.empty:
        scans_actual = scans_actual[scans_actual["documento"].astype(str) != str(documento)]
        _write_df(ws_scans, scans_actual, RECEPCION_SCANS_HEADERS,
                  columnas_texto=["documento", "box_number", "pool_id", "codigo"])

    _recepcion_detalle_cached.clear()
    _recepcion_pools_cached.clear()
    _recepcion_pedido_cached.clear()


@st.cache_data(ttl=120, show_spinner=False)
def _recepcion_pedido_cached(_conn):
    ws = _conn.worksheet("recepcion_pedido")
    return _records_df(ws, RECEPCION_PEDIDO_HEADERS, numericise_ignore=[1])


@st.cache_data(ttl=120, show_spinner=False)
def _recepcion_detalle_cached(_conn):
    ws = _conn.worksheet("recepcion_detalle")
    return _records_df(ws, RECEPCION_DETALLE_HEADERS, numericise_ignore=[1, 2, 3, 5])


@st.cache_data(ttl=120, show_spinner=False)
def _recepcion_pools_cached(_conn):
    ws = _conn.worksheet("recepcion_pools")
    return _records_df(ws, RECEPCION_POOLS_HEADERS, numericise_ignore=[1, 2, 3])


def list_documentos_recepcion(conn):
    df = _recepcion_pedido_cached(conn)
    if df.empty:
        return []
    return list(df[["documento", "fecha_carga", "formato_detectado", "total_lineas"]].itertuples(index=False, name=None))


def list_cajas_documento(conn, documento):
    df = _recepcion_detalle_cached(conn)
    if df.empty:
        return []
    df = df[df["documento"].astype(str) == str(documento)]
    conteo = df.groupby("box_number").size().reset_index(name="n")
    return list(conteo.sort_values("box_number").itertuples(index=False, name=None))


def list_cajas_pendientes(conn, documento):
    todas = [c for c, _ in list_cajas_documento(conn, documento)]
    ws_hist = conn.worksheet("recepcion_historial")
    hist_df = _records_df(ws_hist, RECEPCION_HISTORIAL_HEADERS, numericise_ignore=[1, 2])
    cerradas = set()
    if not hist_df.empty:
        hist_df = hist_df[hist_df["documento"].astype(str) == str(documento)]
        cerradas = set(hist_df["box_number"].astype(str))
    return [c for c in todas if str(c) not in cerradas]


def get_detalle_caja(conn, documento, box_number):
    df = _recepcion_detalle_cached(conn)
    if df.empty:
        return {}
    df = df[(df["documento"].astype(str) == str(documento)) & (df["box_number"].astype(str) == str(box_number))]
    detalle_map = {}
    for _, r in df.iterrows():
        cantidad = r["cantidad_esperada"]
        detalle_map[str(r["codigo"])] = {
            "box_number": str(r["box_number"]),
            "tipo_linea": r["tipo_linea"],
            "pool_id": str(r["pool_id"]) if r["pool_id"] not in (None, "") else "",
            "cantidad_esperada": float(cantidad) if cantidad not in (None, "") else 0,
        }
    return detalle_map


def get_pools_caja(conn, documento, box_number):
    df = _recepcion_pools_cached(conn)
    if df.empty:
        return {}
    df = df[(df["documento"].astype(str) == str(documento)) & (df["box_number"].astype(str) == str(box_number))]
    pools_map = {}
    for _, r in df.iterrows():
        miembros = [c.strip() for c in str(r["codigos_miembros"]).split(",") if c.strip()]
        pools_map[str(r["pool_id"])] = {
            "codigos_miembros": miembros,
            "cantidad_total_esperada": float(r["cantidad_total_esperada"]) if r["cantidad_total_esperada"] not in (None, "") else 0,
        }
    return pools_map


def get_scans_caja(conn, documento, box_number):
    ws = conn.worksheet("recepcion_scans")
    # codigo(4) y pool_id(3): nunca numerizar, para no perder ceros a la izquierda.
    df = _records_df(ws, RECEPCION_SCANS_HEADERS, numericise_ignore=[1, 2, 3, 4, 8])
    if df.empty:
        return {}
    mask = (df["documento"].astype(str) == str(documento)) & (df["box_number"].astype(str) == str(box_number))
    df = df[mask]
    out = {}
    for idx, r in df.iterrows():
        out[str(r["codigo"])] = {
            "recibido": float(r["cantidad_recibida"] or 0),
            "devuelto": float(r["cantidad_devuelta"] or 0),
            "row": idx + 2,
        }
    return out


def register_scan_recepcion(conn, documento, box_number, codigo, detalle_map, pools_map, scans_map, prev_state=None, cantidad=1):
    """Igual que la versión de db.py, pero escribiendo directo en la hoja
    'recepcion_scans' (actualiza la fila si ya existe, o la agrega)."""
    cantidad = max(int(cantidad), 1)
    ws = conn.worksheet("recepcion_scans")

    info = detalle_map.get(codigo)
    if info is None:
        return {"estado": "no_pertenece", "tope": 0, "recibido_total": 0, "devuelto_total": 0,
                "escaneado_delta": 0, "devuelto_delta": 0, "pool_recibido_total": None, "pool_tope": None, "row": None}

    if prev_state is not None:
        recibido_prev = prev_state.get("recibido", 0)
        devuelto_prev = prev_state.get("devuelto", 0)
        row_number = prev_state.get("row")
    else:
        recibido_prev = 0
        devuelto_prev = 0
        row_number = None

    pool_id = info.get("pool_id") or ""
    if info["tipo_linea"] == "pool" and pool_id in pools_map:
        tope = pools_map[pool_id]["cantidad_total_esperada"]
        recibido_prev_grupo = sum(
            scans_map.get(c, {}).get("recibido", 0) for c in pools_map[pool_id]["codigos_miembros"]
        )
    else:
        tope = info["cantidad_esperada"]
        recibido_prev_grupo = recibido_prev

    if recibido_prev_grupo >= tope:
        estado = "excedente"
        escaneado_delta = 0
        devuelto_delta = cantidad
    elif recibido_prev_grupo + cantidad > tope:
        estado = "excedente"
        cabe = tope - recibido_prev_grupo
        escaneado_delta = cabe
        devuelto_delta = cantidad - cabe
    else:
        estado = "ok"
        escaneado_delta = cantidad
        devuelto_delta = 0

    nuevo_recibido = recibido_prev + escaneado_delta
    nuevo_devuelto = devuelto_prev + devuelto_delta
    now = _ahora().isoformat(timespec="seconds")

    if row_number is not None:
        ws.update(
            f"A{row_number}:I{row_number}",
            [[documento, box_number, pool_id, codigo, nuevo_recibido, nuevo_devuelto, now, estado, ""]],
            value_input_option="RAW",
        )
    else:
        response = ws.append_row(
            [documento, box_number, pool_id, codigo, nuevo_recibido, nuevo_devuelto, now, estado, ""],
            value_input_option="RAW",
        )
        try:
            updated_range = response["updates"]["updatedRange"]
            row_number = int("".join(filter(str.isdigit, updated_range.split("!")[1].split(":")[0])))
        except (KeyError, ValueError, IndexError):
            row_number = None

    pool_recibido_total = (recibido_prev_grupo + escaneado_delta) if info["tipo_linea"] == "pool" else None

    return {
        "estado": estado,
        "tope": tope,
        "recibido_total": nuevo_recibido,
        "devuelto_total": nuevo_devuelto,
        "escaneado_delta": escaneado_delta,
        "devuelto_delta": devuelto_delta,
        "pool_recibido_total": pool_recibido_total,
        "pool_tope": tope if info["tipo_linea"] == "pool" else None,
        "row": row_number,
    }


def deshacer_scan_recepcion(conn, documento, box_number, codigo, escaneado_delta=1, devuelto_delta=0, prev_state=None):
    ws = conn.worksheet("recepcion_scans")
    if not prev_state or prev_state.get("row") is None:
        return {"recibido_total": 0, "devuelto_total": 0}

    recibido = prev_state.get("recibido", 0)
    devuelto = prev_state.get("devuelto", 0)
    row_number = prev_state["row"]
    pool_id = prev_state.get("pool_id", "")

    recibido = max(recibido - escaneado_delta, 0)
    devuelto = max(devuelto - devuelto_delta, 0)
    now = _ahora().isoformat(timespec="seconds")
    estado = "excedente" if devuelto > 0 else "ok"
    ws.update(
        f"A{row_number}:I{row_number}",
        [[documento, box_number, pool_id, codigo, recibido, devuelto, now, estado, ""]],
        value_input_option="RAW",
    )
    return {"recibido_total": recibido, "devuelto_total": devuelto}


def guardar_historial_recepcion(conn, documento, box_number, resumen_rows):
    ws = conn.worksheet("recepcion_historial")
    total_esperado = sum(r["esperado"] for r in resumen_rows)
    total_recibido = sum(r["recibido"] for r in resumen_rows)
    faltante_total = sum(r["falta"] for r in resumen_rows)
    excedente_total = sum(r["excedente"] for r in resumen_rows)

    ws.append_row(
        [
            documento,
            box_number,
            _ahora().isoformat(timespec="seconds"),
            total_esperado,
            total_recibido,
            faltante_total,
            excedente_total,
            json.dumps(resumen_rows, ensure_ascii=False),
        ],
        value_input_option="RAW",
    )


def get_historial_recepcion(conn, documento=None, box_number=None):
    ws = conn.worksheet("recepcion_historial")
    df = _records_df(ws, RECEPCION_HISTORIAL_HEADERS, numericise_ignore=[1, 2])
    if df.empty:
        return []
    if documento:
        df = df[df["documento"].astype(str) == str(documento)]
    if box_number:
        df = df[df["box_number"].astype(str) == str(box_number)]
    df = df.sort_values("fecha_cierre", ascending=False)
    cols = RECEPCION_HISTORIAL_HEADERS[:-1]  # sin detalle_json
    return list(df[cols].itertuples(index=False, name=None))
