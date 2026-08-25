"""Capa de acceso a datos (SQLite) para la app de Picking."""
import sqlite3
import os
from datetime import datetime
from zoneinfo import ZoneInfo

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "picking.db")
TZ_PERU = ZoneInfo("America/Lima")


def _ahora():
    """Hora actual en zona horaria de Peru (los servidores de Streamlit Cloud
    corren en UTC, asi que sin esto el historial mostraria la hora adelantada)."""
    return datetime.now(TZ_PERU)


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS pedido_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_tag TEXT NOT NULL,
            tienda TEXT NOT NULL,
            nombre_tienda TEXT,
            codigo TEXT NOT NULL,
            cantidad_solicitada REAL NOT NULL,
            fecha_carga TEXT NOT NULL,
            UNIQUE(week_tag, tienda, codigo)
        );

        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_tag TEXT NOT NULL,
            tienda TEXT NOT NULL,
            codigo TEXT NOT NULL,
            cantidad_escaneada REAL NOT NULL DEFAULT 0,
            cantidad_devuelta REAL NOT NULL DEFAULT 0,
            ultima_actualizacion TEXT,
            UNIQUE(week_tag, tienda, codigo)
        );

        CREATE TABLE IF NOT EXISTS historial (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_tag TEXT NOT NULL,
            tienda TEXT NOT NULL,
            fecha_cierre TEXT NOT NULL,
            solicitado_total REAL,
            tenido_total REAL,
            faltante_total REAL,
            devuelto_total REAL,
            detalle_json TEXT
        );

        CREATE TABLE IF NOT EXISTS pedido_detalle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_tag TEXT NOT NULL,
            id_cabecera TEXT,
            id_linea TEXT,
            codigo_departamento TEXT,
            nombre_departamento TEXT,
            codigo_color TEXT,
            codigo TEXT,
            unidades_solicitadas REAL,
            unidades_recibidas REAL,
            cabecera_original TEXT,
            articulo_original TEXT,
            cod TEXT,
            color TEXT
        );
        """
    )
    conn.commit()
    return conn


def guardar_pedido_detalle(conn, week_tag, detalle_df):
    """Guarda el detalle crudo del pedido (una fila por línea original, sin
    consolidar), usado únicamente por el reporte descargable."""
    cur = conn.cursor()
    cur.execute("DELETE FROM pedido_detalle WHERE week_tag = ?", (week_tag,))
    for _, row in detalle_df.iterrows():
        cur.execute(
            """INSERT INTO pedido_detalle
               (week_tag, id_cabecera, id_linea, codigo_departamento, nombre_departamento,
                codigo_color, codigo, unidades_solicitadas, unidades_recibidas,
                cabecera_original, articulo_original, cod, color)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                week_tag, row["id_cabecera"], row["id_linea"], row["codigo_departamento"],
                row["nombre_departamento"], row["codigo_color"], row["codigo"],
                row["unidades_solicitadas"], row["unidades_recibidas"],
                row["cabecera_original"], row["articulo_original"], row["cod"], row["color"],
            ),
        )
    conn.commit()


def get_pedido_detalle(conn, week_tag):
    """Devuelve el detalle crudo (lista de dicts) del pedido para esa semana."""
    import pandas as pd
    cols = [
        "id_cabecera", "id_linea", "codigo_departamento", "nombre_departamento",
        "codigo_color", "codigo", "unidades_solicitadas", "unidades_recibidas",
        "cabecera_original", "articulo_original", "cod", "color",
    ]
    cur = conn.cursor()
    cur.execute(f"SELECT {', '.join(cols)} FROM pedido_detalle WHERE week_tag = ?", (week_tag,))
    rows = cur.fetchall()
    return pd.DataFrame(rows, columns=cols)


def replace_pedido(conn, week_tag, df):
    """df: columns tienda, nombre_tienda, codigo, cantidad_solicitada"""
    cur = conn.cursor()
    cur.execute("DELETE FROM pedido_items WHERE week_tag = ?", (week_tag,))
    cur.execute("DELETE FROM scans WHERE week_tag = ?", (week_tag,))
    now = _ahora().isoformat(timespec="seconds")
    for _, row in df.iterrows():
        cur.execute(
            """INSERT INTO pedido_items
               (week_tag, tienda, nombre_tienda, codigo, cantidad_solicitada, fecha_carga)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (week_tag, row["tienda"], row["nombre_tienda"], row["codigo"], row["cantidad_solicitada"], now),
        )
    conn.commit()


def list_week_tags(conn):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT week_tag FROM pedido_items ORDER BY week_tag DESC")
    return [r[0] for r in cur.fetchall()]


def list_tiendas(conn, week_tag):
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT tienda, nombre_tienda FROM pedido_items WHERE week_tag = ? ORDER BY tienda",
        (week_tag,),
    )
    return cur.fetchall()


def get_pedido_tienda(conn, week_tag, tienda):
    cur = conn.cursor()
    cur.execute(
        "SELECT codigo, cantidad_solicitada FROM pedido_items WHERE week_tag=? AND tienda=?",
        (week_tag, tienda),
    )
    return {r[0]: r[1] for r in cur.fetchall()}


def get_scans_tienda(conn, week_tag, tienda):
    cur = conn.cursor()
    cur.execute(
        "SELECT codigo, cantidad_escaneada, cantidad_devuelta FROM scans WHERE week_tag=? AND tienda=?",
        (week_tag, tienda),
    )
    return {r[0]: {"escaneado": r[1], "devuelto": r[2]} for r in cur.fetchall()}


def register_scan(conn, week_tag, tienda, codigo, solicitado_map, prev_state=None, cantidad=1):
    """Registra un escaneo (o una cantidad contada manualmente de una vez).
    Devuelve dict con resultado de esta lectura.

    cantidad: unidades a sumar en esta sola llamada (por defecto 1, como un
    escaneo normal). Si escaneado_prev + cantidad supera lo solicitado, lo
    que cabe se registra como validado y el resto como excedente.

    prev_state se ignora aquí (solo lo usa sheets_db.py para optimizar
    llamadas a la API de Google); en SQLite siempre se relee de la base,
    que es prácticamente instantánea al ser local.
    """
    cantidad = max(int(cantidad), 1)
    cur = conn.cursor()
    cur.execute(
        "SELECT cantidad_escaneada, cantidad_devuelta FROM scans WHERE week_tag=? AND tienda=? AND codigo=?",
        (week_tag, tienda, codigo),
    )
    row = cur.fetchone()
    escaneado_prev = row[0] if row else 0
    devuelto_prev = row[1] if row else 0

    pertenece = codigo in solicitado_map
    solicitado = solicitado_map.get(codigo, 0)

    escaneado_delta = 0
    devuelto_delta = 0

    if not pertenece:
        estado = "no_pertenece"
        nuevo_escaneado = escaneado_prev
        nuevo_devuelto = devuelto_prev
    elif escaneado_prev >= solicitado:
        # ya estaba completo (o pasado): todo lo nuevo es excedente
        estado = "excedente"
        devuelto_delta = cantidad
        nuevo_escaneado = escaneado_prev
        nuevo_devuelto = devuelto_prev + cantidad
    elif escaneado_prev + cantidad > solicitado:
        # una parte cabe, el resto se pasa
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
    if pertenece:
        cur.execute(
            """INSERT INTO scans (week_tag, tienda, codigo, cantidad_escaneada, cantidad_devuelta, ultima_actualizacion)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(week_tag, tienda, codigo) DO UPDATE SET
                 cantidad_escaneada=excluded.cantidad_escaneada,
                 cantidad_devuelta=excluded.cantidad_devuelta,
                 ultima_actualizacion=excluded.ultima_actualizacion""",
            (week_tag, tienda, codigo, nuevo_escaneado, nuevo_devuelto, now),
        )
        conn.commit()

    return {
        "estado": estado,
        "solicitado": solicitado,
        "escaneado_total": nuevo_escaneado,
        "devuelto_total": nuevo_devuelto,
        "escaneado_delta": escaneado_delta,
        "devuelto_delta": devuelto_delta,
        "row": None,  # no aplica en SQLite, solo lo usa sheets_db.py
    }


def deshacer_scan(conn, week_tag, tienda, codigo, escaneado_delta=1, devuelto_delta=0, prev_state=None):
    """Revierte un escaneo (o una cantidad en lote) registrado de un código,
    restando exactamente lo que ese evento sumó: escaneado_delta de la
    cantidad validada y devuelto_delta de la cantidad excedente. No borra el
    código, solo ajusta el conteo.

    Por compatibilidad con registros antiguos (guardados antes de tener
    'cantidad'), si se recibe el parámetro viejo 'tipo' como string en vez de
    un número en escaneado_delta, se asume 1 unidad."""
    if isinstance(escaneado_delta, str):
        # llamada con la firma vieja: deshacer_scan(conn, wt, t, codigo, tipo, prev_state)
        tipo_legacy = escaneado_delta
        escaneado_delta = 0 if tipo_legacy == "excedente" else 1
        devuelto_delta = 1 if tipo_legacy == "excedente" else 0

    cur = conn.cursor()
    cur.execute(
        "SELECT cantidad_escaneada, cantidad_devuelta FROM scans WHERE week_tag=? AND tienda=? AND codigo=?",
        (week_tag, tienda, codigo),
    )
    row = cur.fetchone()
    if not row:
        return {"escaneado_total": 0, "devuelto_total": 0}
    escaneado, devuelto = row
    escaneado = max(escaneado - escaneado_delta, 0)
    devuelto = max(devuelto - devuelto_delta, 0)
    now = _ahora().isoformat(timespec="seconds")
    cur.execute(
        """UPDATE scans SET cantidad_escaneada=?, cantidad_devuelta=?, ultima_actualizacion=?
           WHERE week_tag=? AND tienda=? AND codigo=?""",
        (escaneado, devuelto, now, week_tag, tienda, codigo),
    )
    conn.commit()
    return {"escaneado_total": escaneado, "devuelto_total": devuelto}


def guardar_historial(conn, week_tag, tienda, resumen_rows):
    import json

    solicitado_total = sum(r["solicitado"] for r in resumen_rows)
    tenido_total = sum(r["tenido"] for r in resumen_rows)
    faltante_total = sum(r["falta"] for r in resumen_rows)
    devuelto_total = sum(r["devuelto"] for r in resumen_rows)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO historial (week_tag, tienda, fecha_cierre, solicitado_total, tenido_total, faltante_total, devuelto_total, detalle_json)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            week_tag,
            tienda,
            _ahora().isoformat(timespec="seconds"),
            solicitado_total,
            tenido_total,
            faltante_total,
            devuelto_total,
            json.dumps(resumen_rows, ensure_ascii=False),
        ),
    )
    conn.commit()


def get_historial(conn, week_tag=None, tienda=None):
    cur = conn.cursor()
    q = "SELECT week_tag, tienda, fecha_cierre, solicitado_total, tenido_total, faltante_total, devuelto_total FROM historial WHERE 1=1"
    params = []
    if week_tag:
        q += " AND week_tag = ?"
        params.append(week_tag)
    if tienda:
        q += " AND tienda = ?"
        params.append(tienda)
    q += " ORDER BY fecha_cierre DESC"
    cur.execute(q, params)
    return cur.fetchall()


def get_ultimo_detalle_validacion(conn, week_tag, tienda):
    """Devuelve el detalle por código (lista de dicts: codigo, solicitado,
    tenido, falta, devuelto) de la ÚLTIMA validación cerrada para esa
    tienda/semana. None si no se ha cerrado ninguna validación."""
    import json

    cur = conn.cursor()
    cur.execute(
        """SELECT detalle_json FROM historial
           WHERE week_tag = ? AND tienda = ?
           ORDER BY fecha_cierre DESC LIMIT 1""",
        (week_tag, tienda),
    )
    row = cur.fetchone()
    if not row:
        return None
    return json.loads(row[0])


def get_ultimo_detalle_validacion_todas(conn, week_tag):
    """Como get_ultimo_detalle_validacion, pero para TODAS las tiendas de la
    semana de una vez. Devuelve un dict {tienda: [items]}."""
    import json

    cur = conn.cursor()
    cur.execute(
        """SELECT tienda, detalle_json FROM historial
           WHERE week_tag = ? ORDER BY fecha_cierre DESC""",
        (week_tag,),
    )
    resultado = {}
    for tienda, detalle_json in cur.fetchall():
        if tienda in resultado:
            continue
        resultado[tienda] = json.loads(detalle_json) if detalle_json else []
    return resultado


def get_stock_descripciones():
    """En modo SQLite local no hay acceso al Sheet externo de stock del
    almacén (eso requiere las credenciales de Google de sheets_db.py), así
    que el reporte simplemente queda sin descripciones en este modo."""
    return {}


def get_stock_error():
    return None
