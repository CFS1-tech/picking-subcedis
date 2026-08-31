"""
App de Picking / Validacion para Subcedis
Secciones (navegación en el panel izquierdo):
 1. Cargar pedido -> consolidado + CSV para WMS
 2. Validacion (escaneo por tienda)
 3. Historial
"""
import io
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import parser as pk
import parser_recepcion as pr
import report

TZ_PERU = ZoneInfo("America/Lima")


def _hora_actual():
    """Hora actual en zona horaria de Peru (los servidores de Streamlit Cloud
    corren en UTC, asi que sin esto el log de escaneos mostraria la hora
    adelantada)."""
    return datetime.now(TZ_PERU)

st.set_page_config(page_title="Picking Subcedis", layout="wide")

# Estilos para que la pantalla de escaneo (operario) sea grande y clara.
st.markdown(
    """
    <style>
    /* Menos espacio arriba, en toda pantalla (no solo celular), para que el
       contenido quede más arriba y se aproveche mejor la pantalla. */
    .block-container {
        padding-top: 1.5rem !important;
    }
    div[data-testid="stForm"] input {
        font-size: 1.6rem !important;
        padding: 0.9rem !important;
        text-align: center;
        background-color: #eaf3ff !important;
        border: 2px solid #0f2f5c !important;
    }
    div[data-testid="stForm"] button {
        font-size: 1.3rem !important;
        padding: 0.7rem 1.2rem !important;
        width: 100%;
    }
    div[data-testid="stMetricValue"] {
        font-size: 2.1rem !important;
    }
    /* Franja de la tienda activa: angosta, ocupa poco alto */
    .tienda-activa-header {
        background-color: #0f2f5c;
        color: white;
        padding: 0.5rem 1rem;
        border-radius: 8px;
        margin-bottom: 0.8rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
    }
    .tienda-activa-header h3 {
        color: white;
        margin: 0;
        font-size: 2.1rem;
    }
    .tienda-activa-header span {
        font-size: 1rem;
        opacity: 0.85;
    }
    /* Botón "Cambiar tienda": que no se corte el texto ni se recorte */
    div[data-testid="column"] button {
        white-space: nowrap !important;
        min-width: fit-content !important;
        padding: 0.6rem 1rem !important;
        font-size: 1rem !important;
        height: auto !important;
    }
    /* Botones de navegación en el panel izquierdo, ancho completo */
    section[data-testid="stSidebar"] button {
        width: 100%;
        text-align: left;
    }
    /* Fila de "últimos escaneados": un poco más compacta */
    .ultimo-escaneo-row {
        padding: 0.35rem 0;
        border-bottom: 1px solid #eee;
    }

    /* ---------- Ajustes para celular (pantallas angostas) ---------- */
    @media (max-width: 640px) {
        .block-container {
            padding-top: 1.2rem !important;
            padding-left: 0.8rem !important;
            padding-right: 0.8rem !important;
        }
        h1 {
            font-size: 1.6rem !important;
        }
        h2, h3, .tienda-activa-header h3 {
            font-size: 1.1rem !important;
        }
        div[data-testid="column"] {
            min-width: 100% !important;
            flex: 1 1 100% !important;
        }
        div[data-testid="stMetricValue"] {
            font-size: 1.6rem !important;
        }
        div[data-testid="stMetricLabel"] {
            font-size: 0.85rem !important;
        }
        div[data-testid="stForm"] input {
            font-size: 1.4rem !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Persistencia: Google Sheets si hay credenciales configuradas en secrets,
# si no, cae automáticamente a SQLite local (útil para pruebas rápidas).
if "gcp_oauth" in st.secrets:
    import sheets_db as db
    PERSISTENCIA = "Google Sheets"
else:
    import db
    PERSISTENCIA = "SQLite local"

conn = db.init_db()

# ------------------------------------------------------------------
# Selector de módulo: Picking (validación de pedidos) o Recepciones
# (packing lists de proveedores). Se muestra siempre arriba del todo,
# antes de cualquier otra cosa.
# ------------------------------------------------------------------
if "modulo_activo" not in st.session_state:
    st.session_state["modulo_activo"] = "picking"

st.sidebar.title("Picking Subcedis")
mcol1, mcol2 = st.sidebar.columns(2)
with mcol1:
    if st.button(
        "📦 Picking", key="modulo_picking", use_container_width=True,
        type="primary" if st.session_state["modulo_activo"] == "picking" else "secondary",
    ):
        st.session_state["modulo_activo"] = "picking"
        st.rerun()
with mcol2:
    if st.button(
        "📥 Recepciones", key="modulo_recepcion", use_container_width=True,
        type="primary" if st.session_state["modulo_activo"] == "recepcion" else "secondary",
    ):
        st.session_state["modulo_activo"] = "recepcion"
        st.rerun()
st.sidebar.markdown("---")

modulo_activo = st.session_state["modulo_activo"]

# ==================================================================
# MÓDULO: RECEPCIONES (packing lists AP tipo1/tipo2, con pools por caja)
# Todo este bloque termina en st.stop(), así que el módulo de Picking
# de abajo queda completamente sin tocar y no se ejecuta en este modo.
# ==================================================================
if modulo_activo == "recepcion":
    if "seccion_recepcion" not in st.session_state:
        st.session_state["seccion_recepcion"] = "1"

    SECCIONES_RECEPCION = [
        ("1", "1. Cargar packing list"),
        ("2", "2. Validar (escaneo)"),
        ("3", "3. Historial"),
    ]
    for clave, etiqueta in SECCIONES_RECEPCION:
        es_activa = st.session_state["seccion_recepcion"] == clave
        if st.sidebar.button(
            etiqueta, key=f"nav_rec_{clave}", use_container_width=True,
            type="primary" if es_activa else "secondary",
        ):
            st.session_state["seccion_recepcion"] = clave
            st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Persistencia activa: **{PERSISTENCIA}**")
    if PERSISTENCIA == "SQLite local":
        st.sidebar.caption(
            "⚠️ No se detectaron credenciales de Google Sheets en `st.secrets['gcp_oauth']`. "
            "La app está usando SQLite local, que puede reiniciarse en Streamlit Cloud."
        )

    seccion_recepcion = st.session_state["seccion_recepcion"]

    # -------------------- SECCIÓN 1: Cargar packing list --------------------
    if seccion_recepcion == "1":
        st.subheader("Cargar packing list de recepción")
        st.caption(
            "Sube el Excel del packing list (formato AP tipo 1 o tipo 2, se detecta "
            "automáticamente según el código). Se identifica por el ORDER NUMBER (documento)."
        )
        uploaded_rec = st.file_uploader("Archivo Excel del packing list", type=["xlsx"], key="rec_uploader")

        if uploaded_rec is not None:
            try:
                resumen, detalle_df, pools_df = pr.cargar_packing_list(uploaded_rec, nombre_archivo=uploaded_rec.name)
            except Exception as e:
                st.error(f"Error al leer el archivo: {e}")
            else:
                st.success(
                    f"Documento detectado: **{resumen['documento']}** — formato **{resumen['formato_detectado']}** — "
                    f"{resumen['total_lineas']} líneas ({len(pools_df)} caja(s) con códigos agrupados)."
                )
                with st.expander("Ver detalle consolidado", expanded=False):
                    detalle_display = detalle_df.copy()
                    detalle_display.index = range(1, len(detalle_display) + 1)
                    st.dataframe(detalle_display, use_container_width=True)
                if not pools_df.empty:
                    with st.expander("Ver cajas con códigos agrupados (pools)", expanded=False):
                        pools_display = pools_df.copy()
                        pools_display.index = range(1, len(pools_display) + 1)
                        st.dataframe(pools_display, use_container_width=True)

                documentos_existentes = [d for d, *_ in db.list_documentos_recepcion(conn)]
                if resumen["documento"] in documentos_existentes:
                    st.warning(
                        f"Ya existe un packing list cargado para el documento {resumen['documento']}. "
                        "Si guardas de nuevo, se reemplazará y se reiniciarán los escaneos de sus cajas."
                    )

                if st.button("Guardar packing list", type="primary"):
                    db.guardar_packing_list_recepcion(conn, resumen, detalle_df, pools_df)
                    st.success(f"Packing list del documento {resumen['documento']} guardado.")
                    st.balloons()

    # -------------------- SECCIÓN 2: Validar por escaneo --------------------
    elif seccion_recepcion == "2":
        documentos = [d for d, *_ in db.list_documentos_recepcion(conn)]

        if not documentos:
            st.subheader("Validar recepción por escaneo")
            st.info("Primero carga un packing list en la sección 1.")

        elif not st.session_state.get("rec_activo"):
            st.subheader("Validar recepción por escaneo")
            st.caption(
                "Elige el documento y la caja física que estás recibiendo. Cada caja se "
                "valida por separado, aunque traiga el mismo grupo de códigos que otra."
            )

            documento_sel = st.selectbox("Documento", documentos, key="rec_documento_sel")
            cajas_pendientes = db.list_cajas_pendientes(conn, documento_sel)

            if not cajas_pendientes:
                st.success("Todas las cajas de este documento ya tienen su validación cerrada. 🎉")
            else:
                caja_sel = st.selectbox("Caja (box number)", cajas_pendientes, key="rec_caja_sel")
                if st.button("▶ Iniciar validación de esta caja", type="primary"):
                    st.session_state["rec_activo"] = True
                    st.session_state["rec_documento"] = documento_sel
                    st.session_state["rec_caja"] = caja_sel
                    st.rerun()

        else:
            documento_sel = st.session_state["rec_documento"]
            caja_sel = st.session_state["rec_caja"]

            header_col, salir_col = st.columns([4, 1.5], vertical_alignment="center")
            with header_col:
                st.markdown(
                    f"""<div class="tienda-activa-header">
                            <h3>📥 Caja {caja_sel}</h3>
                            <span>Documento {documento_sel}</span>
                        </div>""",
                    unsafe_allow_html=True,
                )
            with salir_col:
                if st.button("⬅ Cambiar caja", key="rec_cambiar_caja"):
                    st.session_state["rec_activo"] = False
                    st.rerun()

            detalle_map = db.get_detalle_caja(conn, documento_sel, caja_sel)
            pools_map = db.get_pools_caja(conn, documento_sel, caja_sel)

            cache_key = f"rec_scans_{documento_sel}_{caja_sel}"
            if cache_key not in st.session_state:
                st.session_state[cache_key] = db.get_scans_caja(conn, documento_sel, caja_sel)
            scans_map = st.session_state[cache_key]

            log_key = f"rec_log_{cache_key}"
            if log_key not in st.session_state:
                st.session_state[log_key] = []
            log_scans = st.session_state[log_key]

            metricas_placeholder = st.container()

            with st.form("rec_scan_form", clear_on_submit=True):
                col_codigo, col_cantidad = st.columns([3, 1])
                with col_codigo:
                    codigo_input = st.text_input(
                        "Escanea el código",
                        key="rec_scan_input",
                        placeholder="Escanea aquí con el lector USB (o escribe el código y Enter)",
                    )
                with col_cantidad:
                    cantidad_input = st.number_input("Cantidad", min_value=1, value=1, step=1, key="rec_scan_cantidad")
                st.caption(
                    "Si ya contaste varias unidades físicamente, primero pon la cantidad aquí "
                    "y RECIÉN escanea el código. Si dejas Cantidad en 1, escanea como siempre."
                )
                submitted = st.form_submit_button("✅ Registrar recepción")

            resultado_box = st.empty()

            if submitted and codigo_input:
                codigo_limpio = codigo_input.strip()
                cantidad_a_registrar = int(cantidad_input) if cantidad_input else 1
                prev_state = scans_map.get(codigo_limpio)
                resultado = db.register_scan_recepcion(
                    conn, documento_sel, caja_sel, codigo_limpio, detalle_map, pools_map, scans_map,
                    prev_state, cantidad=cantidad_a_registrar,
                )

                if resultado["estado"] != "no_pertenece":
                    scans_map[codigo_limpio] = {
                        "recibido": resultado["recibido_total"],
                        "devuelto": resultado["devuelto_total"],
                        "row": resultado["row"],
                        "pool_id": detalle_map.get(codigo_limpio, {}).get("pool_id", ""),
                    }
                    st.session_state[cache_key] = scans_map
                    log_scans.append({
                        "codigo": codigo_limpio,
                        "cantidad": cantidad_a_registrar,
                        "escaneado_delta": resultado.get("escaneado_delta", 0),
                        "devuelto_delta": resultado.get("devuelto_delta", 0),
                        "hora": _hora_actual().strftime("%H:%M:%S"),
                    })
                    st.session_state[log_key] = log_scans

                def _fmt(n):
                    return int(n) if float(n).is_integer() else n

                if resultado["estado"] == "no_pertenece":
                    resultado_box.error(f"❌ El código **{codigo_limpio}** NO pertenece a esta caja.")
                elif resultado["estado"] == "excedente":
                    extra = f" (x{cantidad_a_registrar})" if cantidad_a_registrar > 1 else ""
                    tope_txt = "del pool compartido" if detalle_map.get(codigo_limpio, {}).get("tipo_linea") == "pool" else "esperada"
                    resultado_box.warning(
                        f"⚠️ El código **{codigo_limpio}**{extra} superó la cantidad {tope_txt} "
                        f"({_fmt(resultado['tope'])}). El excedente se registra como **excedente**."
                    )
                else:
                    extra = f" (+{cantidad_a_registrar})" if cantidad_a_registrar > 1 else ""
                    if resultado.get("pool_recibido_total") is not None:
                        resultado_box.success(
                            f"✅ OK{extra} — {codigo_limpio}: {_fmt(resultado['recibido_total'])} recibidos de este código · "
                            f"pool {_fmt(resultado['pool_recibido_total'])} / {_fmt(resultado['pool_tope'])}"
                        )
                    else:
                        resultado_box.success(
                            f"✅ OK{extra} — {codigo_limpio}: {_fmt(resultado['recibido_total'])} / {_fmt(resultado['tope'])}"
                        )

            st.markdown("#### Últimos escaneados")
            if not log_scans:
                st.caption("Aún no has escaneado nada en esta caja.")
            else:
                indices_recientes = list(range(len(log_scans)))[-5:]
                indices_recientes.reverse()
                for idx in indices_recientes:
                    entry = log_scans[idx]
                    codigo_e = entry["codigo"]
                    cantidad_e = entry.get("cantidad", 1)
                    recibido_e = scans_map.get(codigo_e, {}).get("recibido", 0)
                    devuelto_e = scans_map.get(codigo_e, {}).get("devuelto", 0)
                    hay_exceso = devuelto_e and devuelto_e > 0

                    ec1, ec2, ec3 = st.columns([3, 3, 1])
                    with ec1:
                        icono = "⚠️" if hay_exceso else "✅"
                        etiqueta_cant = f"  (x{int(cantidad_e)})" if cantidad_e > 1 else ""
                        st.markdown(f"{icono} **{codigo_e}**{etiqueta_cant}  ·  {entry['hora']}")
                    with ec2:
                        texto_cant = f"{int(recibido_e)} recibidos"
                        if hay_exceso:
                            texto_cant += f"  (+{int(devuelto_e)} excedente)"
                        st.markdown(texto_cant)
                    with ec3:
                        if st.button("↩ Deshacer", key=f"rec_undo_{cache_key}_{idx}"):
                            prev = scans_map.get(codigo_e)
                            resultado_undo = db.deshacer_scan_recepcion(
                                conn, documento_sel, caja_sel, codigo_e,
                                entry.get("escaneado_delta", 0), entry.get("devuelto_delta", 0), prev,
                            )
                            scans_map[codigo_e] = {
                                "recibido": resultado_undo["recibido_total"],
                                "devuelto": resultado_undo["devuelto_total"],
                                "row": prev.get("row") if prev else None,
                                "pool_id": prev.get("pool_id", "") if prev else "",
                            }
                            st.session_state[cache_key] = scans_map
                            log_scans.pop(idx)
                            st.session_state[log_key] = log_scans
                            st.rerun()

            resumen_rows = []
            for codigo, info in detalle_map.items():
                if info["tipo_linea"] == "pool":
                    tope = pools_map.get(info["pool_id"], {}).get("cantidad_total_esperada", 0)
                else:
                    tope = info["cantidad_esperada"]
                recibido = scans_map.get(codigo, {}).get("recibido", 0)
                devuelto = scans_map.get(codigo, {}).get("devuelto", 0)
                resumen_rows.append({
                    "codigo": codigo,
                    "esperado": tope,
                    "recibido": recibido,
                    "falta": max(tope - recibido, 0) if info["tipo_linea"] == "individual" else 0,
                    "excedente": devuelto,
                })

            resumen_df = pd.DataFrame(resumen_rows)

            with metricas_placeholder:
                if not resumen_df.empty:
                    total_esperado_pools = sum(p["cantidad_total_esperada"] for p in pools_map.values())
                    total_esperado_individual = sum(
                        info["cantidad_esperada"] for info in detalle_map.values() if info["tipo_linea"] == "individual"
                    )
                    total_recibido = resumen_df["recibido"].sum()
                    total_excedente = resumen_df["excedente"].sum()
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Esperado total", int(total_esperado_pools + total_esperado_individual))
                    c2.metric("Recibido", int(total_recibido))
                    c3.metric("Excedente", int(total_excedente))

            if not resumen_df.empty:
                with st.expander("Ver detalle por código", expanded=False):
                    resumen_display = resumen_df.copy()
                    for col in ["esperado", "recibido", "falta", "excedente"]:
                        resumen_display[col] = resumen_display[col].astype(int)
                    resumen_display.index = range(1, len(resumen_display) + 1)

                    def _resaltar_faltantes_rec(row):
                        if row["falta"] and row["falta"] != 0:
                            return ["background-color: #fdecea"] * len(row)
                        return [""] * len(row)

                    st.dataframe(
                        resumen_display.style.apply(_resaltar_faltantes_rec, axis=1),
                        use_container_width=True,
                    )

                st.markdown("")
                if st.button("🔒 Cerrar validación de esta caja y guardar en historial", type="primary"):
                    db.guardar_historial_recepcion(conn, documento_sel, caja_sel, resumen_rows)
                    st.success(f"Historial guardado para la caja {caja_sel} del documento {documento_sel}.")
                    st.session_state["rec_activo"] = False
                    st.balloons()

    # -------------------- SECCIÓN 3: Historial --------------------
    elif seccion_recepcion == "3":
        st.subheader("Historial de recepciones")
        documentos = [d for d, *_ in db.list_documentos_recepcion(conn)]
        col1, col2 = st.columns(2)
        with col1:
            doc_filter = st.selectbox("Filtrar por documento", ["(todos)"] + documentos, key="rec_hist_doc")
        with col2:
            caja_filter = st.text_input("Filtrar por caja (ej. 1/3)", key="rec_hist_caja")

        hist = db.get_historial_recepcion(
            conn,
            documento=None if doc_filter == "(todos)" else doc_filter,
            box_number=caja_filter or None,
        )
        if hist:
            hist_df = pd.DataFrame(
                hist,
                columns=["documento", "caja", "fecha_cierre", "esperado_total", "recibido_total", "faltante_total", "excedente_total"],
            )
            hist_df.index = range(1, len(hist_df) + 1)
            st.dataframe(hist_df, use_container_width=True)
        else:
            st.info("Aún no hay historial de recepciones. Cierra la validación de una caja en la sección 2.")

    st.stop()

# ==================================================================
# MÓDULO: PICKING (flujo original, exactamente igual que antes)
# ==================================================================


def _hay_validacion_sin_guardar():
    """True si hay una validación en curso (tienda activa en la sección 2) con
    al menos un escaneo registrado que todavía no se ha cerrado/guardado en
    el historial. Se usa para bloquear el acceso a otras secciones y evitar
    que se pierda de vista una validación a medio hacer."""
    if not st.session_state.get("escaneo_activo"):
        return False
    if st.session_state.get("escaneo_guardado"):
        return False
    week = st.session_state.get("escaneo_week")
    tienda = st.session_state.get("escaneo_tienda")
    if not week or not tienda:
        return False
    cache_key = f"scans_cache_{week}_{tienda}"
    scans_map = st.session_state.get(cache_key, {})
    return any(
        (v.get("escaneado", 0) or 0) > 0 or (v.get("devuelto", 0) or 0) > 0
        for v in scans_map.values()
    )


def _guardar_validacion_actual():
    """Cierra y guarda en el historial la validación de la tienda actualmente
    activa en la sección 2, usando lo que haya en el cache de escaneos."""
    week_sel = st.session_state.get("escaneo_week")
    tienda_sel = st.session_state.get("escaneo_tienda")
    if not week_sel or not tienda_sel:
        return
    solicitado_map = db.get_pedido_tienda(conn, week_sel, tienda_sel)
    cache_key = f"scans_cache_{week_sel}_{tienda_sel}"
    scans_map = st.session_state.get(cache_key, {})
    resumen_rows = []
    for codigo, solicitado in solicitado_map.items():
        escaneado = scans_map.get(codigo, {}).get("escaneado", 0)
        devuelto = scans_map.get(codigo, {}).get("devuelto", 0)
        resumen_rows.append(
            {
                "codigo": codigo,
                "solicitado": solicitado,
                "tenido": escaneado,
                "falta": max(solicitado - escaneado, 0),
                "devuelto": devuelto,
            }
        )
    db.guardar_historial(conn, week_sel, tienda_sel, resumen_rows)
    st.session_state["escaneo_guardado"] = True


hay_validacion_sin_guardar = _hay_validacion_sin_guardar()

# ------------------------------------------------------------------
# Navegación en el panel izquierdo (reemplaza las pestañas de arriba,
# para no ocupar espacio horizontal en la sección activa)
# ------------------------------------------------------------------
if "seccion_activa" not in st.session_state:
    st.session_state["seccion_activa"] = "2" if st.session_state.get("escaneo_activo") else "1"

SECCIONES = [
    ("1", "1. Cargar pedido"),
    ("2", "2. Validación (escaneo)"),
    ("3", "3. Historial"),
]

for clave, etiqueta in SECCIONES:
    es_activa = st.session_state["seccion_activa"] == clave
    if st.sidebar.button(
        etiqueta,
        key=f"nav_{clave}",
        type="primary" if es_activa else "secondary",
        use_container_width=True,
    ):
        if clave != "2" and hay_validacion_sin_guardar:
            st.session_state["confirmar_cambio_seccion"] = clave
        else:
            st.session_state["seccion_activa"] = clave
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption(f"Persistencia activa: **{PERSISTENCIA}**")
if PERSISTENCIA == "SQLite local":
    st.sidebar.caption(
        "⚠️ No se detectaron credenciales de Google Sheets en `st.secrets['gcp_oauth']`. "
        "La app está usando SQLite local, que puede reiniciarse en Streamlit Cloud. "
        "Configura las credenciales (ver README.md) para guardar todo permanentemente en tu Google Sheet."
    )

seccion_activa = st.session_state["seccion_activa"]

# ------------------------------------------------------------------
# Confirmación al intentar cambiar de sección con escaneos sin guardar
# ------------------------------------------------------------------
if st.session_state.get("confirmar_cambio_seccion"):
    destino = st.session_state["confirmar_cambio_seccion"]
    tienda_nombre_actual = st.session_state.get("escaneo_tienda_nombre", "")
    st.warning(
        f"⚠️ Tienes escaneos en **{tienda_nombre_actual}** que todavía no se han "
        "guardado en el historial. ¿Qué deseas hacer antes de cambiar de sección?"
    )
    cconf1, cconf2, cconf3 = st.columns(3)
    with cconf1:
        if st.button("🔒 Guardar y continuar", type="primary", key="conf_sec_guardar"):
            _guardar_validacion_actual()
            st.session_state["seccion_activa"] = destino
            st.session_state["confirmar_cambio_seccion"] = None
            st.rerun()
    with cconf2:
        if st.button("⚠️ Continuar sin guardar", key="conf_sec_salir"):
            st.session_state["seccion_activa"] = destino
            st.session_state["confirmar_cambio_seccion"] = None
            st.rerun()
    with cconf3:
        if st.button("Cancelar", key="conf_sec_cancelar"):
            st.session_state["confirmar_cambio_seccion"] = None
            st.rerun()

# ------------------------------------------------------------------
# SECCIÓN 1: Cargar pedido y generar CSV para WMS
# ------------------------------------------------------------------
elif seccion_activa == "1":
    st.subheader("Cargar pedido (Excel)")
    st.caption(
        "Sube el archivo de Picking Subcedis. La app detecta automaticamente la hoja "
        "'Picking Subcedis W##' sin importar el numero de semana."
    )

    uploaded = st.file_uploader("Archivo Excel del pedido", type=["xlsx"], key="pedido_uploader")

    if uploaded is not None:
        try:
            week_tag, consolidado, detalle_crudo = pk.cargar_y_consolidar(uploaded)
        except Exception as e:
            st.error(f"Error al leer el archivo: {e}")
        else:
            st.success(f"Semana detectada: **{week_tag}** — {consolidado['tienda'].nunique()} tiendas, "
                       f"{len(consolidado)} lineas consolidadas (código único por tienda).")

            with st.expander("Ver consolidado (agrupado por tienda + código)", expanded=False):
                consolidado_display = consolidado.copy()
                consolidado_display.index = range(1, len(consolidado_display) + 1)
                st.dataframe(consolidado_display, use_container_width=True)

            existing_weeks = db.list_week_tags(conn)
            if week_tag in existing_weeks:
                st.warning(
                    f"Ya existe un pedido cargado para la semana {week_tag}. "
                    "Si guardas de nuevo, se reemplazara el pedido y se reiniciaran los escaneos de esa semana."
                )

            col1, col2 = st.columns(2)
            with col1:
                fecha_emision = st.date_input("Fecha de emision", value=date.today())
            with col2:
                fecha_entrega = st.date_input("Fecha de entrega")

            if st.button("Guardar pedido y generar CSV para WMS", type="primary"):
                db.replace_pedido(conn, week_tag, consolidado)
                db.guardar_pedido_detalle(conn, week_tag, detalle_crudo)
                # limpia el cache de escaneos en sesión de esta semana (el pedido
                # se reemplazó, así que los escaneos previos ya no aplican)
                for key in list(st.session_state.keys()):
                    if key.startswith(f"scans_cache_{week_tag}_") or key.startswith(f"log_scans_{week_tag}_"):
                        del st.session_state[key]
                st.session_state["pedido_guardado_week"] = week_tag
                st.session_state["pedido_guardado_consolidado"] = consolidado
                st.session_state["pedido_guardado_fecha_emision"] = fecha_emision
                st.session_state["pedido_guardado_fecha_entrega"] = fecha_entrega
                st.success(f"Pedido de la semana {week_tag} guardado.")

            # ------------------------------------------------------------
            # Filtro de tiendas para el CSV descargable (por nombre_departamento)
            # ------------------------------------------------------------
            if st.session_state.get("pedido_guardado_week") == week_tag:
                st.markdown("---")
                st.markdown("#### Filtrar tiendas para el CSV a descargar")

                cons_guardado = st.session_state["pedido_guardado_consolidado"]
                pares_tienda_nombre = (
                    cons_guardado[["tienda", "nombre_tienda"]]
                    .drop_duplicates()
                    .sort_values("tienda")
                    .itertuples(index=False, name=None)
                )
                pares_tienda_nombre = list(pares_tienda_nombre)

                def _marcar_todas():
                    valor = st.session_state["chk_todas_tiendas"]
                    for t, _n in pares_tienda_nombre:
                        st.session_state[f"chk_tienda_{week_tag}_{t}"] = valor

                st.checkbox(
                    "Seleccionar todas las tiendas",
                    value=True,
                    key="chk_todas_tiendas",
                    on_change=_marcar_todas,
                )

                cols = st.columns(3)
                tiendas_seleccionadas = []
                for i, (t, nombre_tienda) in enumerate(pares_tienda_nombre):
                    chk_key = f"chk_tienda_{week_tag}_{t}"
                    if chk_key not in st.session_state:
                        st.session_state[chk_key] = True
                    with cols[i % 3]:
                        marcado = st.checkbox(nombre_tienda, key=chk_key)
                    if marcado:
                        tiendas_seleccionadas.append(t)

                if st.button("Generar CSV filtrado", type="primary"):
                    if not tiendas_seleccionadas:
                        st.warning("Selecciona al menos una tienda.")
                    else:
                        consolidado_filtrado = cons_guardado[
                            cons_guardado["tienda"].isin(tiendas_seleccionadas)
                        ]
                        csv_df = pk.generar_csv_wms(
                            consolidado_filtrado,
                            st.session_state["pedido_guardado_fecha_emision"],
                            st.session_state["pedido_guardado_fecha_entrega"],
                        )
                        csv_bytes = csv_df.to_csv(index=False, sep=";").encode("utf-8-sig")
                        st.session_state["last_csv_bytes"] = csv_bytes
                        st.session_state["last_csv_name"] = f"consolidado_wms_{week_tag}.csv"
                        st.success(
                            f"CSV generado para {len(tiendas_seleccionadas)} tienda(s) — "
                            f"{len(consolidado_filtrado)} líneas."
                        )

                if "last_csv_bytes" in st.session_state:
                    st.download_button(
                        "Descargar CSV para WMS",
                        data=st.session_state["last_csv_bytes"],
                        file_name=st.session_state["last_csv_name"],
                        mime="text/csv",
                    )

# ------------------------------------------------------------------
# SECCIÓN 2: Validacion por escaneo — en dos fases:
#   Fase 1: elegir semana y tienda
#   Fase 2: pantalla dedicada de escaneo (grande, clara, para el operario)
# ------------------------------------------------------------------
elif seccion_activa == "2":
    weeks = db.list_week_tags(conn)

    if not weeks:
        st.subheader("Validacion de picking por escaneo")
        st.info("Primero carga un pedido en la sección 1.")

    elif not st.session_state.get("escaneo_activo"):
        # -------------------- FASE 1: selección --------------------
        st.subheader("Validacion de picking por escaneo")
        st.caption("Elige la semana y la tienda que vas a validar, luego inicia el escaneo.")

        week_sel = st.selectbox("Semana", weeks, key="val_week")
        tiendas = db.list_tiendas(conn, week_sel)

        # Excluimos las tiendas que ya tienen una validación cerrada para esta
        # semana, para que el operario no vuelva a elegir por error una tienda
        # que ya se cerró.
        historial_semana = db.get_historial(conn, week_tag=week_sel)
        tiendas_cerradas = {str(h[1]) for h in historial_semana}
        tiendas_pendientes = [(t, n) for t, n in tiendas if str(t) not in tiendas_cerradas]

        if not tiendas_pendientes:
            st.success("Todas las tiendas de esta semana ya tienen su validación cerrada. 🎉")
        else:
            tienda_labels = {f"{t} - {n}": t for t, n in tiendas_pendientes}
            tienda_label_sel = st.selectbox("Tienda", list(tienda_labels.keys()), key="val_tienda")
            tienda_sel = tienda_labels[tienda_label_sel]

            if st.button("▶ Iniciar validación de esta tienda", type="primary"):
                st.session_state["escaneo_activo"] = True
                st.session_state["escaneo_week"] = week_sel
                st.session_state["escaneo_tienda"] = tienda_sel
                st.session_state["escaneo_tienda_nombre"] = tienda_label_sel
                st.session_state["escaneo_guardado"] = False
                st.session_state["confirmar_salida"] = False
                st.rerun()

    else:
        # -------------------- FASE 2: pantalla de escaneo --------------------
        week_sel = st.session_state["escaneo_week"]
        tienda_sel = st.session_state["escaneo_tienda"]
        tienda_nombre = st.session_state["escaneo_tienda_nombre"]

        header_col, salir_col = st.columns([4, 1.5], vertical_alignment="center")
        with header_col:
            st.markdown(
                f"""<div class="tienda-activa-header">
                        <h3>📦 {tienda_nombre}</h3>
                        <span>Semana {week_sel}</span>
                    </div>""",
                unsafe_allow_html=True,
            )
        with salir_col:
            if st.button("⬅ Cambiar tienda"):
                if hay_validacion_sin_guardar:
                    st.session_state["confirmar_salida"] = True
                else:
                    st.session_state["escaneo_activo"] = False
                st.rerun()

        if st.session_state.get("confirmar_salida"):
            st.warning(
                "⚠️ Tienes escaneos registrados en **" + tienda_nombre + "** que todavía "
                "no se han guardado en el historial. ¿Qué deseas hacer?"
            )
            cconf1, cconf2, cconf3 = st.columns(3)
            with cconf1:
                if st.button("🔒 Guardar y cambiar tienda", type="primary"):
                    _guardar_validacion_actual()
                    st.session_state["escaneo_activo"] = False
                    st.session_state["confirmar_salida"] = False
                    st.rerun()
            with cconf2:
                if st.button("⚠️ Salir sin guardar"):
                    st.session_state["escaneo_activo"] = False
                    st.session_state["confirmar_salida"] = False
                    st.rerun()
            with cconf3:
                if st.button("Cancelar"):
                    st.session_state["confirmar_salida"] = False
                    st.rerun()

        if not st.session_state.get("confirmar_salida"):
            solicitado_map = db.get_pedido_tienda(conn, week_sel, tienda_sel)

            # Cache en sesión de los escaneos de esta tienda/semana: se carga UNA
            # vez desde la hoja y luego se actualiza en memoria con cada escaneo,
            # sin releer toda la hoja de Google Sheets en cada rerun (eso es lo
            # que agotaba la cuota de la API al escanear varios códigos seguidos).
            cache_key = f"scans_cache_{week_sel}_{tienda_sel}"
            if cache_key not in st.session_state:
                st.session_state[cache_key] = db.get_scans_tienda(conn, week_sel, tienda_sel)
            scans_map = st.session_state[cache_key]

            # Log en sesión de los últimos escaneos (código + tipo), para el
            # resumen de "últimos escaneados" y para poder deshacerlos.
            log_key = f"log_scans_{cache_key}"
            if log_key not in st.session_state:
                st.session_state[log_key] = []
            log_scans = st.session_state[log_key]

            # Reservamos aquí arriba el espacio visual de las métricas (van
            # justo debajo de la cabecera de tienda), pero las llenamos MÁS
            # ABAJO en el código, después de procesar el escaneo de este run,
            # para que el número se actualice sin esperar un rerun adicional.
            metricas_placeholder = st.container()

            with st.form("scan_form", clear_on_submit=True):
                col_codigo, col_cantidad = st.columns([3, 1])
                with col_codigo:
                    codigo_input = st.text_input(
                        "Escanea el código",
                        key="scan_input",
                        placeholder="Escanea aquí con el lector USB (o escribe el código y Enter)",
                        label_visibility="visible",
                    )
                with col_cantidad:
                    cantidad_input = st.number_input(
                        "Cantidad",
                        min_value=1,
                        value=1,
                        step=1,
                        key="scan_cantidad",
                    )
                st.caption(
                    "Si ya contaste varias unidades físicamente, primero pon la cantidad "
                    "aquí y RECIÉN escanea el código (el escaneo manda el Enter que "
                    "envía todo el formulario junto). Si dejas Cantidad en 1, escanea como siempre."
                )
                submitted = st.form_submit_button("✅ Registrar escaneo")

            resultado_box = st.empty()

            if submitted and codigo_input:
                # El lector escanea el código tal como viene en la etiqueta (con punto,
                # ej. 150079.001). El pedido consolidado lo guarda sin punto, así que
                # aplicamos la misma limpieza usada al armar el consolidado para que
                # crucen exactamente. Todo se trata como texto (nunca como número),
                # por lo que ceros finales tipo .010 o .1140 no se pierden ni se redondean.
                codigo_limpio = pk.quitar_punto(codigo_input.strip())
                cantidad_a_registrar = int(cantidad_input) if cantidad_input else 1
                prev_state = scans_map.get(codigo_limpio)
                resultado = db.register_scan(
                    conn, week_sel, tienda_sel, codigo_limpio, solicitado_map, prev_state,
                    cantidad=cantidad_a_registrar,
                )

                if resultado["estado"] != "no_pertenece":
                    # actualizamos el cache en memoria, sin releer la hoja
                    scans_map[codigo_limpio] = {
                        "escaneado": resultado["escaneado_total"],
                        "devuelto": resultado["devuelto_total"],
                        "row": resultado["row"],
                    }
                    st.session_state[cache_key] = scans_map

                    log_scans.append(
                        {
                            "codigo": codigo_limpio,
                            "tipo": resultado["estado"],
                            "cantidad": cantidad_a_registrar,
                            "escaneado_delta": resultado.get("escaneado_delta", 0),
                            "devuelto_delta": resultado.get("devuelto_delta", 0),
                            "hora": _hora_actual().strftime("%H:%M:%S"),
                        }
                    )
                    st.session_state[log_key] = log_scans

                def _fmt(n):
                    return int(n) if float(n).is_integer() else n

                if resultado["estado"] == "no_pertenece":
                    resultado_box.error(
                        f"❌ El código **{codigo_limpio}** NO pertenece al pedido de esta tienda."
                    )
                elif resultado["estado"] == "excedente":
                    extra = f" (x{cantidad_a_registrar})" if cantidad_a_registrar > 1 else ""
                    resultado_box.warning(
                        f"⚠️ El código **{codigo_limpio}**{extra} superó la cantidad solicitada "
                        f"({resultado['solicitado']}). El excedente se registra como **excedente**."
                    )
                else:
                    extra = f" (+{cantidad_a_registrar})" if cantidad_a_registrar > 1 else ""
                    resultado_box.success(
                        f"✅ OK{extra} — {codigo_limpio}: {_fmt(resultado['escaneado_total'])} / {_fmt(resultado['solicitado'])}"
                    )

            # -------- Resumen de los últimos 5 escaneados (con deshacer) --------
            st.markdown("#### Últimos escaneados")
            if not log_scans:
                st.caption("Aún no has escaneado nada en esta sesión.")
            else:
                indices_recientes = list(range(len(log_scans)))[-5:]
                indices_recientes.reverse()
                for idx in indices_recientes:
                    entry = log_scans[idx]
                    codigo_e = entry["codigo"]
                    cantidad_e = entry.get("cantidad", 1)
                    solicitado_e = solicitado_map.get(codigo_e, 0)
                    escaneado_e = scans_map.get(codigo_e, {}).get("escaneado", 0)
                    devuelto_e = scans_map.get(codigo_e, {}).get("devuelto", 0)
                    hay_exceso = devuelto_e and devuelto_e > 0

                    ec1, ec2, ec3 = st.columns([3, 3, 1])
                    with ec1:
                        icono = "⚠️" if hay_exceso else "✅"
                        etiqueta_cant = f"  (x{int(cantidad_e)})" if cantidad_e > 1 else ""
                        st.markdown(f"{icono} **{codigo_e}**{etiqueta_cant}  ·  {entry['hora']}")
                    with ec2:
                        texto_cant = f"{int(escaneado_e)} / {int(solicitado_e) if solicitado_e else 0}"
                        if hay_exceso:
                            texto_cant += f"  (+{int(devuelto_e)} excedente)"
                        st.markdown(texto_cant)
                    with ec3:
                        if st.button("↩ Deshacer", key=f"undo_{cache_key}_{idx}"):
                            prev = scans_map.get(codigo_e)
                            # entradas viejas del log (antes de tener cantidad) solo
                            # traen 'tipo' — deshacer_scan sabe interpretar ambas.
                            esc_delta = entry.get("escaneado_delta")
                            dev_delta = entry.get("devuelto_delta")
                            if esc_delta is None and dev_delta is None:
                                resultado_undo = db.deshacer_scan(
                                    conn, week_sel, tienda_sel, codigo_e,
                                    entry.get("tipo", "ok"), prev_state=prev,
                                )
                            else:
                                resultado_undo = db.deshacer_scan(
                                    conn, week_sel, tienda_sel, codigo_e,
                                    esc_delta or 0, dev_delta or 0, prev,
                                )
                            scans_map[codigo_e] = {
                                "escaneado": resultado_undo["escaneado_total"],
                                "devuelto": resultado_undo["devuelto_total"],
                                "row": prev.get("row") if prev else None,
                            }
                            st.session_state[cache_key] = scans_map
                            log_scans.pop(idx)
                            st.session_state[log_key] = log_scans
                            st.rerun()

            resumen_rows = []
            for codigo, solicitado in solicitado_map.items():
                escaneado = scans_map.get(codigo, {}).get("escaneado", 0)
                devuelto = scans_map.get(codigo, {}).get("devuelto", 0)
                falta = max(solicitado - escaneado, 0)
                resumen_rows.append(
                    {
                        "codigo": codigo,
                        "solicitado": solicitado,
                        "tenido": escaneado,
                        "falta": falta,
                        "devuelto": devuelto,
                    }
                )

            # códigos escaneados que no pertenecen al pedido no se guardan (no_pertenece),
            # así que no aparecen aquí — es intencional según la validación pedida.

            resumen_df = pd.DataFrame(resumen_rows)

            # Llenamos ahora el espacio de métricas reservado arriba, con los
            # datos ya actualizados de este mismo run.
            with metricas_placeholder:
                if not resumen_df.empty:
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Solicitado", int(resumen_df["solicitado"].sum()))
                    c2.metric("Validado", int(resumen_df["tenido"].sum()))
                    c3.metric("Falta", int(resumen_df["falta"].sum()))
                    c4.metric("Excedente", int(resumen_df["devuelto"].sum()))

            if not resumen_df.empty:
                with st.expander("Ver detalle por código", expanded=False):
                    resumen_df_display = resumen_df.rename(
                        columns={"tenido": "validado", "devuelto": "excedente"}
                    )
                    for col in ["solicitado", "validado", "falta", "excedente"]:
                        resumen_df_display[col] = resumen_df_display[col].astype(int)
                    resumen_df_display.index = range(1, len(resumen_df_display) + 1)

                    def _resaltar_faltantes(row):
                        if row["falta"] and row["falta"] != 0:
                            return ["background-color: #fdecea"] * len(row)
                        return [""] * len(row)

                    st.dataframe(
                        resumen_df_display.style.apply(_resaltar_faltantes, axis=1),
                        use_container_width=True,
                    )

                st.markdown("")
                if st.button("🔒 Cerrar validación y guardar en historial", type="primary"):
                    db.guardar_historial(conn, week_sel, tienda_sel, resumen_rows)
                    st.session_state["escaneo_guardado"] = True
                    st.success(f"Historial guardado para {tienda_nombre} — semana {week_sel}.")
                    st.balloons()
            else:
                st.info("No hay items en el pedido para esta tienda.")

# ------------------------------------------------------------------
# SECCIÓN 3: Historial
# ------------------------------------------------------------------
elif seccion_activa == "3":
    st.subheader("Historial de validaciones")

    weeks = db.list_week_tags(conn)
    col1, col2 = st.columns(2)
    with col1:
        week_filter = st.selectbox("Filtrar por semana", ["(todas)"] + weeks, key="hist_week")
    with col2:
        tienda_filter = st.text_input("Filtrar por tienda (ej. 4201)", key="hist_tienda")

    hist = db.get_historial(
        conn,
        week_tag=None if week_filter == "(todas)" else week_filter,
        tienda=tienda_filter or None,
    )

    if hist:
        hist_df = pd.DataFrame(
            hist,
            columns=[
                "week_tag", "tienda", "fecha_cierre",
                "solicitado_total", "validado_total", "faltante_total", "excedente_total",
            ],
        )
        hist_df.index = range(1, len(hist_df) + 1)
        st.dataframe(hist_df, use_container_width=True)
    else:
        st.info("Aún no hay historial guardado. Cierra una validación en la sección 2 para generar registros.")

    st.markdown("---")
    st.markdown("#### Reporte descargable (solicitado + validación por tienda)")
    st.caption(
        "Genera un Excel con una pestaña RESUMEN (por tienda: cantidad de códigos, "
        "solicitado, validado, falta y excedente según la última validación cerrada) y una "
        "pestaña de DETALLE con el pedido consolidado, igual que tu reporte de referencia."
    )

    if weeks:
        week_reporte = st.selectbox("Semana para el reporte", weeks, key="reporte_week")
        if st.button("Generar reporte"):
            reporte_bytes, resumen_preview, _, stock_warning = report.generar_reporte(db, conn, week_reporte)
            st.session_state["reporte_bytes"] = reporte_bytes
            st.session_state["reporte_name"] = f"REPOR Picking Subcedis {week_reporte}.xlsx"
            st.session_state["reporte_preview"] = resumen_preview
            st.session_state["reporte_stock_warning"] = stock_warning
            if stock_warning:
                st.warning(f"⚠️ Descripciones de producto: {stock_warning}")

        if "reporte_preview" in st.session_state:
            reporte_preview_display = st.session_state["reporte_preview"].copy()
            reporte_preview_display.index = range(1, len(reporte_preview_display) + 1)
            st.dataframe(reporte_preview_display, use_container_width=True)

        if "reporte_bytes" in st.session_state:
            st.download_button(
                "Descargar reporte Excel",
                data=st.session_state["reporte_bytes"],
                file_name=st.session_state["reporte_name"],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    else:
        st.info("Primero carga un pedido en la sección 1 para poder generar un reporte.")
