"""Lectura y consolidación de packing lists de RECEPCIÓN (formatos AP tipo 1 y tipo 2).

Formato tipo 1: el código va partido en dos columnas, CODE (6 dígitos fijos)
y COLOR (3 a 5 dígitos). Se arman como "CODE.COLOR".

Formato tipo 2: CODE ya trae el código completo (más de 6 dígitos: los
primeros 6 son el "cod" y el resto es el "color").

Una misma celda de CODE puede traer VARIOS códigos apilados (separados por
salto de línea, "/", "," o ";"), compartiendo una sola cantidad en
QTY/CARTON. Esas filas se tratan como un "pool": un grupo de códigos con un
total esperado en conjunto, sin cantidad fija por código individual (se
reparte según lo que realmente se escanee al recibir). Cada caja (box_number)
es un pool independiente, aunque el mismo grupo de códigos se repita en
varias cajas del mismo documento.

El formato (tipo1/tipo2) se detecta automáticamente, línea por línea, según
si el token de CODE tiene exactamente 6 dígitos (tipo1, necesita COLOR) o
más de 6 (tipo2, ya viene completo).
"""
import re

import pandas as pd

COL_ALIASES = {
    "box_number": ["box number", "box_number"],
    "order_number": ["order number", "order_number"],
    "code": ["code"],
    "color": ["color"],
    "qty": ["qty/carton", "qty carton", "qty", "cantidad", "quantity"],
}


def _find_col(columns, aliases):
    norm = {str(c).strip().lower(): c for c in columns}
    for alias in aliases:
        if alias in norm:
            return norm[alias]
    return None


def _split_tokens(value):
    """Separa una celda que puede traer uno o varios valores apilados,
    usando salto de línea, '/', ',' o ';' como posibles separadores."""
    if value is None:
        return []
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return []
    tokens = re.split(r"[\n/,;]+", s)
    return [t.strip() for t in tokens if t.strip()]


def _armar_codigo(code_token, color_tokens, idx):
    """Devuelve (codigo_completo, es_tipo1) según la regla de 6 dígitos."""
    digits = re.sub(r"\D", "", code_token)
    if len(digits) == 6:
        color_val = color_tokens[idx] if idx < len(color_tokens) else (
            color_tokens[0] if color_tokens else ""
        )
        return f"{code_token}.{color_val}", True
    else:
        cod = code_token[:6]
        color = code_token[6:]
        return f"{cod}.{color}", False


def cargar_packing_list(xlsx_path_or_buffer, nombre_archivo=""):
    """Lee y consolida un packing list de recepción.

    Devuelve (resumen_dict, detalle_df, pools_df):
      - resumen_dict: documento, nombre_archivo, formato_detectado, total_lineas
      - detalle_df: columnas documento, box_number, codigo, tipo_linea
        ("individual"/"pool"), pool_id, cantidad_esperada
      - pools_df: columnas pool_id, documento, box_number, codigos_miembros,
        cantidad_total_esperada
    """
    # Algunos packing lists traen una fila extra arriba de los encabezados
    # (ej. "CARTON DIMENSION/M" como título de un grupo de columnas), así que
    # no siempre la fila 0 es el encabezado real. Buscamos entre las primeras
    # filas cuál trae "BOX NUMBER" (o similar) y usamos esa como encabezado.
    vista_previa = pd.read_excel(xlsx_path_or_buffer, header=None, nrows=10, dtype=str)
    fila_encabezado = 0
    for i in range(len(vista_previa)):
        valores_fila = [str(v).strip().lower() for v in vista_previa.iloc[i].tolist()]
        if any(v in ("box number", "box_number") for v in valores_fila):
            fila_encabezado = i
            break

    # Si es un archivo subido por Streamlit (file-like), hay que rebobinarlo:
    # la lectura de la vista previa ya movió el cursor al final.
    if hasattr(xlsx_path_or_buffer, "seek"):
        xlsx_path_or_buffer.seek(0)

    df = pd.read_excel(xlsx_path_or_buffer, header=fila_encabezado, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    col_box = _find_col(df.columns, COL_ALIASES["box_number"])
    col_order = _find_col(df.columns, COL_ALIASES["order_number"])
    col_code = _find_col(df.columns, COL_ALIASES["code"])
    col_color = _find_col(df.columns, COL_ALIASES["color"])
    col_qty = _find_col(df.columns, COL_ALIASES["qty"])

    faltantes = [
        nombre for nombre, col in [
            ("BOX NUMBER", col_box), ("ORDER NUMBER", col_order),
            ("CODE", col_code), ("QTY/CARTON", col_qty),
        ] if col is None
    ]
    if faltantes:
        raise ValueError(
            f"No se encontraron las columnas esperadas: {faltantes}. "
            f"Columnas encontradas en el archivo: {list(df.columns)}"
        )

    detalle_rows = []
    pools_rows = []
    documentos_vistos = {}
    conteo_tipo1 = 0
    conteo_tipo2 = 0

    for _, row in df.iterrows():
        box_number = str(row.get(col_box, "")).strip()
        documento = str(row.get(col_order, "")).strip()
        code_raw = row.get(col_code, "")
        color_raw = row.get(col_color, "") if col_color else ""
        qty_raw = row.get(col_qty, "")

        if not documento or documento.lower() == "nan":
            continue

        documentos_vistos[documento] = documentos_vistos.get(documento, 0) + 1

        try:
            cantidad = int(float(str(qty_raw).strip()))
        except (ValueError, TypeError):
            cantidad = 0

        code_tokens = _split_tokens(code_raw)
        color_tokens = _split_tokens(color_raw)
        if not code_tokens:
            continue

        codigos_completos = []
        for idx, tok in enumerate(code_tokens):
            codigo_completo, es_tipo1 = _armar_codigo(tok, color_tokens, idx)
            conteo_tipo1 += 1 if es_tipo1 else 0
            conteo_tipo2 += 0 if es_tipo1 else 1
            codigos_completos.append(codigo_completo)

        if len(codigos_completos) == 1:
            detalle_rows.append({
                "documento": documento,
                "box_number": box_number,
                "codigo": codigos_completos[0],
                "tipo_linea": "individual",
                "pool_id": "",
                "cantidad_esperada": cantidad,
            })
        else:
            pool_id = f"{documento}_{box_number}"
            pools_rows.append({
                "pool_id": pool_id,
                "documento": documento,
                "box_number": box_number,
                "codigos_miembros": ", ".join(codigos_completos),
                "cantidad_total_esperada": cantidad,
            })
            for cod in codigos_completos:
                detalle_rows.append({
                    "documento": documento,
                    "box_number": box_number,
                    "codigo": cod,
                    "tipo_linea": "pool",
                    "pool_id": pool_id,
                    "cantidad_esperada": "",
                })

    formato_detectado = "tipo1" if conteo_tipo1 >= conteo_tipo2 else "tipo2"
    documento_principal = (
        max(documentos_vistos, key=documentos_vistos.get) if documentos_vistos else ""
    )

    resumen = {
        "documento": documento_principal,
        "nombre_archivo": nombre_archivo,
        "formato_detectado": formato_detectado,
        "total_lineas": len(detalle_rows),
    }

    detalle_df = pd.DataFrame(
        detalle_rows,
        columns=["documento", "box_number", "codigo", "tipo_linea", "pool_id", "cantidad_esperada"],
    )
    pools_df = pd.DataFrame(
        pools_rows,
        columns=["pool_id", "documento", "box_number", "codigos_miembros", "cantidad_total_esperada"],
    )

    return resumen, detalle_df, pools_df
