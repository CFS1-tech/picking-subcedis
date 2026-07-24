"""
App de Picking / Validacion para Subcedis
Tabs:
 1. Cargar pedido -> consolidado + CSV para WMS
 2. Validacion (escaneo por tienda)
 3. Historial
"""
import io
from datetime import date, datetime

import pandas as pd
import streamlit as st

import parser as pk
import report

st.set_page_config(page_title="Picking Subcedis", layout="wide")

# Persistencia: Google Sheets si hay credenciales configuradas en secrets,
# si no, cae automáticamente a SQLite local (útil para pruebas rápidas).
if "gcp_oauth" in st.secrets:
    import sheets_db as db
    PERSISTENCIA = "Google Sheets"
else:
    import db
    PERSISTENCIA = "SQLite local"

conn = db.init_db()

st.title("Picking Subcedis")

tab1, tab2, tab3 = st.tabs(["1. Cargar pedido", "2. Validacion (escaneo)", "3. Historial"])

# ------------------------------------------------------------------
# TAB 1: Cargar pedido y generar CSV para WMS
# ------------------------------------------------------------------
with tab1:
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
                st.dataframe(consolidado, use_container_width=True)

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
                    if key.startswith(f"scans_cache_{week_tag}_"):
                        del st.session_state[key]
                csv_df = pk.generar_csv_wms(consolidado, fecha_emision, fecha_entrega)
                csv_bytes = csv_df.to_csv(index=False, sep=";").encode("utf-8-sig")

                st.session_state["last_csv_bytes"] = csv_bytes
                st.session_state["last_csv_name"] = f"consolidado_wms_{week_tag}.csv"
                st.success(f"Pedido de la semana {week_tag} guardado. Ya puedes descargar el CSV.")

            if "last_csv_bytes" in st.session_state:
                st.download_button(
                    "Descargar CSV para WMS",
                    data=st.session_state["last_csv_bytes"],
                    file_name=st.session_state["last_csv_name"],
                    mime="text/csv",
                )

# ------------------------------------------------------------------
# TAB 2: Validacion por escaneo
# ------------------------------------------------------------------
with tab2:
    st.subheader("Validacion de picking por escaneo")

    weeks = db.list_week_tags(conn)
    if not weeks:
        st.info("Primero carga un pedido en la pestaña 1.")
    else:
        week_sel = st.selectbox("Semana", weeks, key="val_week")
        tiendas = db.list_tiendas(conn, week_sel)
        tienda_labels = {f"{t} - {n}": t for t, n in tiendas}
        tienda_label_sel = st.selectbox("Tienda", list(tienda_labels.keys()), key="val_tienda")
        tienda_sel = tienda_labels[tienda_label_sel]

        solicitado_map = db.get_pedido_tienda(conn, week_sel, tienda_sel)

        # Cache en sesión de los escaneos de esta tienda/semana: se carga UNA
        # vez desde la hoja y luego se actualiza en memoria con cada escaneo,
        # sin releer toda la hoja de Google Sheets en cada rerun (eso es lo
        # que agotaba la cuota de la API al escanear varios códigos seguidos).
        cache_key = f"scans_cache_{week_sel}_{tienda_sel}"
        if cache_key not in st.session_state:
            st.session_state[cache_key] = db.get_scans_tienda(conn, week_sel, tienda_sel)
        scans_map = st.session_state[cache_key]

        st.markdown("#### Escanear producto")
        with st.form("scan_form", clear_on_submit=True):
            codigo_input = st.text_input(
                "Codigo escaneado",
                key="scan_input",
                placeholder="Escanea aqui con el lector USB (o escribe el codigo y Enter)",
            )
            submitted = st.form_submit_button("Registrar")

        if submitted and codigo_input:
            # El lector escanea el código tal como viene en la etiqueta (con punto,
            # ej. 150079.001). El pedido consolidado lo guarda sin punto, así que
            # aplicamos la misma limpieza usada al armar el consolidado para que
            # crucen exactamente. Todo se trata como texto (nunca como número),
            # por lo que ceros finales tipo .010 o .1140 no se pierden ni se redondean.
            codigo_limpio = pk.quitar_punto(codigo_input.strip())
            prev_state = scans_map.get(codigo_limpio)
            resultado = db.register_scan(conn, week_sel, tienda_sel, codigo_limpio, solicitado_map, prev_state)

            if resultado["estado"] != "no_pertenece":
                # actualizamos el cache en memoria, sin releer la hoja
                scans_map[codigo_limpio] = {
                    "escaneado": resultado["escaneado_total"],
                    "devuelto": resultado["devuelto_total"],
                    "row": resultado["row"],
                }
                st.session_state[cache_key] = scans_map

            if resultado["estado"] == "no_pertenece":
                st.error(f"❌ El código **{codigo_limpio}** NO pertenece al pedido de la tienda {tienda_sel}.")
            elif resultado["estado"] == "excedente":
                st.warning(
                    f"⚠️ El código **{codigo_limpio}** ya alcanzó la cantidad solicitada "
                    f"({resultado['solicitado']}). Esta unidad se registra como **devolución**."
                )
            else:
                st.success(
                    f"✅ OK — {codigo_limpio}: {resultado['escaneado_total']} / {resultado['solicitado']}"
                )

        st.markdown("#### Resumen en vivo")

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
        if not resumen_df.empty:
            st.dataframe(resumen_df, use_container_width=True)

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Solicitado total", int(resumen_df["solicitado"].sum()))
            c2.metric("Tenido total", int(resumen_df["tenido"].sum()))
            c3.metric("Falta total", int(resumen_df["falta"].sum()))
            c4.metric("Devuelto total", int(resumen_df["devuelto"].sum()))

            if st.button("Cerrar validacion y guardar en historial", type="primary"):
                db.guardar_historial(conn, week_sel, tienda_sel, resumen_rows)
                st.success(f"Historial guardado para semana {week_sel}, tienda {tienda_sel}.")
        else:
            st.info("No hay items en el pedido para esta tienda.")

# ------------------------------------------------------------------
# TAB 3: Historial
# ------------------------------------------------------------------
with tab3:
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
                "solicitado_total", "tenido_total", "faltante_total", "devuelto_total",
            ],
        )
        st.dataframe(hist_df, use_container_width=True)
    else:
        st.info("Aún no hay historial guardado. Cierra una validación en la pestaña 2 para generar registros.")

    st.markdown("---")
    st.markdown("#### Reporte descargable (solicitado + validación por tienda)")
    st.caption(
        "Genera un Excel con una pestaña RESUMEN (por tienda: cantidad de códigos, "
        "solicitado, tenido, falta y devuelto según la última validación cerrada) y una "
        "pestaña de DETALLE con el pedido consolidado, igual que tu reporte de referencia."
    )

    if weeks:
        week_reporte = st.selectbox("Semana para el reporte", weeks, key="reporte_week")
        if st.button("Generar reporte"):
            reporte_bytes, resumen_preview, _ = report.generar_reporte(db, conn, week_reporte)
            st.session_state["reporte_bytes"] = reporte_bytes
            st.session_state["reporte_name"] = f"REPOR Picking Subcedis {week_reporte}.xlsx"
            st.session_state["reporte_preview"] = resumen_preview

        if "reporte_preview" in st.session_state:
            st.dataframe(st.session_state["reporte_preview"], use_container_width=True)

        if "reporte_bytes" in st.session_state:
            st.download_button(
                "Descargar reporte Excel",
                data=st.session_state["reporte_bytes"],
                file_name=st.session_state["reporte_name"],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    else:
        st.info("Primero carga un pedido en la pestaña 1 para poder generar un reporte.")

st.sidebar.markdown("---")
st.sidebar.caption(f"Persistencia activa: **{PERSISTENCIA}**")
if PERSISTENCIA == "SQLite local":
    st.sidebar.caption(
        "⚠️ No se detectaron credenciales de Google Sheets en `st.secrets['gcp_oauth']`. "
        "La app está usando SQLite local, que puede reiniciarse en Streamlit Cloud. "
        "Configura las credenciales (ver README.md) para guardar todo permanentemente en tu Google Sheet."
    )
