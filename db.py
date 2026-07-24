"""Capa de acceso a datos (SQLite) para la app de Picking."""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "picking.db")


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
        """
    )
    conn.commit()
    return conn


def replace_pedido(conn, week_tag, df):
    """df: columns tienda, nombre_tienda, codigo, cantidad_solicitada"""
    cur = conn.cursor()
    cur.execute("DELETE FROM pedido_items WHERE week_tag = ?", (week_tag,))
    cur.execute("DELETE FROM scans WHERE week_tag = ?", (week_tag,))
    now = datetime.now().isoformat(timespec="seconds")
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


def register_scan(conn, week_tag, tienda, codigo, solicitado_map, prev_state=None):
    """Registra un escaneo. Devuelve dict con resultado de esta lectura.

    prev_state se ignora aquí (solo lo usa sheets_db.py para optimizar
    llamadas a la API de Google); en SQLite siempre se relee de la base,
    que es prácticamente instantánea al ser local.
    """
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

    if not pertenece:
        estado = "no_pertenece"
        nuevo_escaneado = escaneado_prev
        nuevo_devuelto = devuelto_prev
    elif escaneado_prev + 1 > solicitado:
        estado = "excedente"
        nuevo_escaneado = escaneado_prev  # no sube el "tenido" util
        nuevo_devuelto = devuelto_prev + 1
    else:
        estado = "ok"
        nuevo_escaneado = escaneado_prev + 1
        nuevo_devuelto = devuelto_prev

    now = datetime.now().isoformat(timespec="seconds")
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
        "row": None,  # no aplica en SQLite, solo lo usa sheets_db.py
    }


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
            datetime.now().isoformat(timespec="seconds"),
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
