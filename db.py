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

        CREATE TABLE IF NOT EXISTS recepcion_pedido (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento TEXT NOT NULL UNIQUE,
            fecha_carga TEXT,
            nombre_archivo TEXT,
            formato_detectado TEXT,
            total_lineas INTEGER
        );

        CREATE TABLE IF NOT EXISTS recepcion_detalle (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento TEXT NOT NULL,
            box_number TEXT,
            codigo TEXT NOT NULL,
            tipo_linea TEXT NOT NULL,
            pool_id TEXT,
            cantidad_esperada REAL
        );

        CREATE TABLE IF NOT EXISTS recepcion_pools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pool_id TEXT NOT NULL UNIQUE,
            documento TEXT NOT NULL,
            box_number TEXT,
            codigos_miembros TEXT,
            cantidad_total_esperada REAL
        );

        CREATE TABLE IF NOT EXISTS recepcion_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento TEXT NOT NULL,
            box_number TEXT NOT NULL,
            pool_id TEXT,
            codigo TEXT NOT NULL,
            cantidad_recibida REAL NOT NULL DEFAULT 0,
            cantidad_devuelta REAL NOT NULL DEFAULT 0,
            hora TEXT,
            UNIQUE(documento, box_number, codigo)
        );

        CREATE TABLE IF NOT EXISTS recepcion_historial (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            documento TEXT NOT NULL,
            box_number TEXT NOT NULL,
            fecha_cierre TEXT NOT NULL,
            total_esperado REAL,
            total_recibido REAL,
            faltante_total REAL,
            excedente_total REAL,
            detalle_json TEXT
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


# ------------------------------------------------------------------
# RECEPCIONES (packing lists AP): guardar packing list, validar por
# escaneo (líneas individuales y "pools" de códigos agrupados por caja),
# e historial de cierre por documento.
# ------------------------------------------------------------------
def guardar_packing_list_recepcion(conn, resumen, detalle_df, pools_df):
    """Reemplaza el packing list completo de un documento (detalle + pools)
    y reinicia sus escaneos, igual que replace_pedido en Picking."""
    documento = resumen["documento"]
    cur = conn.cursor()
    now = _ahora().isoformat(timespec="seconds")

    cur.execute(
        """INSERT INTO recepcion_pedido (documento, fecha_carga, nombre_archivo, formato_detectado, total_lineas)
           VALUES (?,?,?,?,?)
           ON CONFLICT(documento) DO UPDATE SET
             fecha_carga=excluded.fecha_carga,
             nombre_archivo=excluded.nombre_archivo,
             formato_detectado=excluded.formato_detectado,
             total_lineas=excluded.total_lineas""",
        (documento, now, resumen.get("nombre_archivo", ""), resumen.get("formato_detectado", ""), resumen.get("total_lineas", 0)),
    )

    cur.execute("DELETE FROM recepcion_detalle WHERE documento = ?", (documento,))
    for _, row in detalle_df.iterrows():
        cur.execute(
            """INSERT INTO recepcion_detalle (documento, box_number, codigo, tipo_linea, pool_id, cantidad_esperada)
               VALUES (?,?,?,?,?,?)""",
            (documento, row["box_number"], row["codigo"], row["tipo_linea"], row["pool_id"] or None,
             row["cantidad_esperada"] if row["cantidad_esperada"] != "" else None),
        )

    cur.execute("DELETE FROM recepcion_pools WHERE documento = ?", (documento,))
    for _, row in pools_df.iterrows():
        cur.execute(
            """INSERT INTO recepcion_pools (pool_id, documento, box_number, codigos_miembros, cantidad_total_esperada)
               VALUES (?,?,?,?,?)
               ON CONFLICT(pool_id) DO UPDATE SET
                 box_number=excluded.box_number,
                 codigos_miembros=excluded.codigos_miembros,
                 cantidad_total_esperada=excluded.cantidad_total_esperada""",
            (row["pool_id"], documento, row["box_number"], row["codigos_miembros"], row["cantidad_total_esperada"]),
        )

    cur.execute(
        "DELETE FROM recepcion_scans WHERE documento = ? AND box_number IN (SELECT DISTINCT box_number FROM recepcion_detalle WHERE documento = ?)",
        (documento, documento),
    )
    conn.commit()


def list_documentos_recepcion(conn):
    cur = conn.cursor()
    cur.execute("SELECT documento, fecha_carga, formato_detectado, total_lineas FROM recepcion_pedido ORDER BY fecha_carga DESC")
    return cur.fetchall()


def list_cajas_documento(conn, documento):
    """Todas las cajas (box_number) de un documento, con su cantidad de
    líneas, para que el operario elija cuál está recibiendo físicamente."""
    cur = conn.cursor()
    cur.execute(
        "SELECT box_number, COUNT(*) FROM recepcion_detalle WHERE documento = ? GROUP BY box_number ORDER BY box_number",
        (documento,),
    )
    return cur.fetchall()


def list_cajas_pendientes(conn, documento):
    """Cajas de un documento que aún no tienen un cierre en recepcion_historial."""
    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT box_number FROM recepcion_detalle
           WHERE documento = ?
             AND box_number NOT IN (
                 SELECT box_number FROM recepcion_historial WHERE documento = ?
             )
           ORDER BY box_number""",
        (documento, documento),
    )
    return [r[0] for r in cur.fetchall()]


def get_detalle_caja(conn, documento, box_number):
    """Devuelve detalle_map: {codigo: {tipo_linea, pool_id, cantidad_esperada}}
    con el detalle de una sola caja (evita ambigüedad cuando el mismo grupo
    de códigos se repite idéntico en varias cajas del mismo documento)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT codigo, tipo_linea, pool_id, cantidad_esperada FROM recepcion_detalle WHERE documento = ? AND box_number = ?",
        (documento, box_number),
    )
    detalle_map = {}
    for codigo, tipo_linea, pool_id, cantidad_esperada in cur.fetchall():
        detalle_map[codigo] = {
            "box_number": box_number,
            "tipo_linea": tipo_linea,
            "pool_id": pool_id or "",
            "cantidad_esperada": cantidad_esperada or 0,
        }
    return detalle_map


def get_pools_caja(conn, documento, box_number):
    """Devuelve pools_map: {pool_id: {codigos_miembros: [...], cantidad_total_esperada}}
    de una sola caja."""
    cur = conn.cursor()
    cur.execute(
        "SELECT pool_id, codigos_miembros, cantidad_total_esperada FROM recepcion_pools WHERE documento = ? AND box_number = ?",
        (documento, box_number),
    )
    pools_map = {}
    for pool_id, codigos_miembros, cantidad_total_esperada in cur.fetchall():
        miembros = [c.strip() for c in (codigos_miembros or "").split(",") if c.strip()]
        pools_map[pool_id] = {
            "codigos_miembros": miembros,
            "cantidad_total_esperada": cantidad_total_esperada or 0,
        }
    return pools_map


def get_scans_caja(conn, documento, box_number):
    cur = conn.cursor()
    cur.execute(
        "SELECT codigo, cantidad_recibida, cantidad_devuelta FROM recepcion_scans WHERE documento = ? AND box_number = ?",
        (documento, box_number),
    )
    return {r[0]: {"recibido": r[1], "devuelto": r[2]} for r in cur.fetchall()}


def register_scan_recepcion(conn, documento, box_number, codigo, detalle_map, pools_map, scans_map, prev_state=None, cantidad=1):
    """Registra un escaneo de recepción DENTRO de una caja específica. Si el
    código pertenece a una línea individual, el tope es su propia
    cantidad_esperada (igual que Picking). Si pertenece a un pool, el tope es
    el total del pool compartido entre todos sus códigos miembro DE ESA
    CAJA; lo que exceda ese tope se marca como excedente, igual que en
    Picking pero calculado a nivel de grupo."""
    cantidad = max(int(cantidad), 1)
    info = detalle_map.get(codigo)
    if info is None:
        return {"estado": "no_pertenece", "tope": 0, "recibido_total": 0, "devuelto_total": 0,
                "escaneado_delta": 0, "devuelto_delta": 0, "pool_recibido_total": None, "pool_tope": None}

    recibido_prev = scans_map.get(codigo, {}).get("recibido", 0)
    devuelto_prev = scans_map.get(codigo, {}).get("devuelto", 0)

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

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO recepcion_scans (documento, box_number, pool_id, codigo, cantidad_recibida, cantidad_devuelta, hora)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(documento, box_number, codigo) DO UPDATE SET
             cantidad_recibida=excluded.cantidad_recibida,
             cantidad_devuelta=excluded.cantidad_devuelta,
             hora=excluded.hora""",
        (documento, box_number, pool_id or None, codigo, nuevo_recibido, nuevo_devuelto, now),
    )
    conn.commit()

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
        "row": None,
    }


def deshacer_scan_recepcion(conn, documento, box_number, codigo, escaneado_delta=1, devuelto_delta=0, prev_state=None):
    cur = conn.cursor()
    cur.execute(
        "SELECT cantidad_recibida, cantidad_devuelta FROM recepcion_scans WHERE documento=? AND box_number=? AND codigo=?",
        (documento, box_number, codigo),
    )
    row = cur.fetchone()
    if not row:
        return {"recibido_total": 0, "devuelto_total": 0}
    recibido, devuelto = row
    recibido = max(recibido - escaneado_delta, 0)
    devuelto = max(devuelto - devuelto_delta, 0)
    now = _ahora().isoformat(timespec="seconds")
    cur.execute(
        """UPDATE recepcion_scans SET cantidad_recibida=?, cantidad_devuelta=?, hora=?
           WHERE documento=? AND box_number=? AND codigo=?""",
        (recibido, devuelto, now, documento, box_number, codigo),
    )
    conn.commit()
    return {"recibido_total": recibido, "devuelto_total": devuelto}


def guardar_historial_recepcion(conn, documento, box_number, resumen_rows):
    """resumen_rows: lista de dicts por código con esperado/recibido/falta/excedente."""
    import json

    total_esperado = sum(r["esperado"] for r in resumen_rows)
    total_recibido = sum(r["recibido"] for r in resumen_rows)
    faltante_total = sum(r["falta"] for r in resumen_rows)
    excedente_total = sum(r["excedente"] for r in resumen_rows)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO recepcion_historial (documento, box_number, fecha_cierre, total_esperado, total_recibido, faltante_total, excedente_total, detalle_json)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            documento,
            box_number,
            _ahora().isoformat(timespec="seconds"),
            total_esperado,
            total_recibido,
            faltante_total,
            excedente_total,
            json.dumps(resumen_rows, ensure_ascii=False),
        ),
    )
    conn.commit()


def get_historial_recepcion(conn, documento=None, box_number=None):
    cur = conn.cursor()
    q = "SELECT documento, box_number, fecha_cierre, total_esperado, total_recibido, faltante_total, excedente_total FROM recepcion_historial WHERE 1=1"
    params = []
    if documento:
        q += " AND documento = ?"
        params.append(documento)
    if box_number:
        q += " AND box_number = ?"
        params.append(box_number)
    q += " ORDER BY fecha_cierre DESC"
    cur.execute(q, params)
    return cur.fetchall()
