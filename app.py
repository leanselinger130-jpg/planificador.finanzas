import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import math
import io
import requests

DEFAULT_AHORRO_RATIO = 0.3
RATIO_GASTOS_ALTO = 0.7
RATIO_AHORRO_BAJO = 0.1
RATIO_AHORRO_OBJETIVO = 0.2
OBJ_POR_FILA = 3

CATEGORIAS = ["Fondo de Emergencia", "Educación", "Vivienda", "Vehículo",
              "Viaje/Ocio", "Tecnología", "Salud", "Otro"]
PRIORIDADES = ["Baja", "Media", "Alta"]
PRIO_ORDER = {"Alta": 0, "Media": 1, "Baja": 2}
COLOR_PRIORIDAD = {"Alta": "#E74C3C", "Media": "#F1C40F", "Baja": "#3498DB"}
MONEDAS = ["ARS", "USD", "EUR"]

PERFIL_OPCIONES = {
    "Me preocupa mucho, prefiero seguridad": "Bajo",
    "Lo acepto si es temporal": "Medio",
    "No me afecta, busco mayor rendimiento": "Alto",
}

# Defaults editables por el usuario. Inflación y rendimiento son nominales anuales en %.
SUPUESTOS_DEFAULT = {
    "ARS": {"inflacion": 80.0, "rendimiento": 90.0},
    "USD": {"inflacion": 3.0, "rendimiento": 5.0},
    "EUR": {"inflacion": 2.5, "rendimiento": 4.0},
}
# ARS por 1 unidad de la moneda. ARS siempre 1.0 (pivote).
TIPOS_CAMBIO_DEFAULT = {"ARS": 1.0, "USD": 1200.0, "EUR": 1300.0}
CASAS_DOLAR = ["oficial", "blue", "bolsa", "contadoconliqui", "cripto", "tarjeta"]

PLAZO_CORTO_MAX = 3
PLAZO_MEDIO_MAX = 12
PLAZO_LARGO_MAX = 36

RECOMENDACIONES = {
    "corto": {
        "*": {"tipo": "Liquidez / Money Market",
              "descripcion": "FCI money market o cuenta remunerada. Rescate en 24-48hs, capital preservado.",
              "emoji": "🟢"},
    },
    "medio": {
        "Bajo": {"tipo": "Renta Fija",
                 "descripcion": "FCI de renta fija o bonos cortos. Rendimiento moderado, baja volatilidad.",
                 "emoji": "🟡"},
        "*": {"tipo": "Renta Fija con cobertura inflacionaria",
              "descripcion": "FCI renta fija + instrumento indexado UVA/CER. Protege el poder adquisitivo.",
              "emoji": "🟡"},
    },
    "largo": {
        "Bajo": {"tipo": "Renta Fija Diversificada",
                 "descripcion": "Mix de bonos y FCI de renta fija a mayor plazo.",
                 "emoji": "🟠"},
        "Medio": {"tipo": "Cartera Mixta 60/40",
                  "descripcion": "60% renta fija + 40% renta variable. Equilibrio entre estabilidad y crecimiento.",
                  "emoji": "🟠"},
        "Alto": {"tipo": "Renta Variable",
                 "descripcion": "Acciones locales o CEDEARs. Mayor volatilidad pero potencial de rendimiento real.",
                 "emoji": "🔴"},
    },
    "muy_largo": {
        "Bajo": {"tipo": "Renta Fija largo plazo",
                 "descripcion": "Bonos soberanos o FCI de duration alta.",
                 "emoji": "🟠"},
        "*": {"tipo": "Renta Variable / Cartera de crecimiento",
              "descripcion": "Acciones, CEDEARs o ETFs. El horizonte largo reduce el riesgo.",
              "emoji": "🔴"},
    },
}

EXPORT_COLUMNS = ["Meta", "Categoría", "Prioridad", "Moneda",
                  "Costo Total", "Costo Futuro Estimado", "Ya Ahorrado",
                  "Plazo (Meses)", "Cuota Ideal", "Monto Asignado",
                  "Estado", "Instrumento Sugerido"]


def _bucket_plazo(meses):
    if meses <= PLAZO_CORTO_MAX:
        return "corto"
    if meses <= PLAZO_MEDIO_MAX:
        return "medio"
    if meses <= PLAZO_LARGO_MAX:
        return "largo"
    return "muy_largo"


@st.cache_data
def recomendar_instrumento(plazo_meses, perfil):
    bucket = RECOMENDACIONES[_bucket_plazo(plazo_meses)]
    return bucket.get(perfil, bucket.get("*"))


def convertir(monto, de_moneda, a_moneda, tipos_cambio):
    if de_moneda == a_moneda or monto == 0:
        return monto
    return monto * tipos_cambio[de_moneda] / tipos_cambio[a_moneda]


def _tasa_mensual(tasa_anual_pct):
    return (1 + tasa_anual_pct / 100) ** (1 / 12) - 1


def calcular_cuota_meta(obj, supuestos):
    """Calcula costo futuro, faltante y cuota mensual EN LA MONEDA DE LA META.

    Modelo:
      - Costo futuro = Costo * (1+π)^n con π mensual
      - Capital ya ahorrado se capitaliza a rendimiento r
      - Cuota mensual = anualidad ordinaria; si r=0 cae a faltante/n
    """
    n = int(obj.get("Plazo (Meses)") or 0)
    moneda = obj.get("Moneda") or "ARS"
    sup = supuestos.get(moneda, SUPUESTOS_DEFAULT[moneda])
    pi_m = _tasa_mensual(sup["inflacion"])
    r_m = _tasa_mensual(sup["rendimiento"])

    costo_presente = float(obj.get("Costo Total") or 0)
    ahorrado_presente = float(obj.get("Ya Ahorrado") or 0)

    costo_futuro = costo_presente * (1 + pi_m) ** n
    ahorrado_futuro = ahorrado_presente * (1 + r_m) ** n
    faltante = max(0.0, costo_futuro - ahorrado_futuro)

    if n <= 0 or faltante == 0:
        cuota_ideal = 0.0
    elif r_m > 1e-9:
        cuota_ideal = faltante * r_m / ((1 + r_m) ** n - 1)
    else:
        cuota_ideal = faltante / n

    return {
        "moneda_meta": moneda,
        "costo_futuro": costo_futuro,
        "ahorrado_futuro": ahorrado_futuro,
        "faltante_futuro": faltante,
        "cuota_ideal": cuota_ideal,
        "r_mensual": r_m,
    }


def meses_para_acumular(faltante_futuro, cuota, r_mensual):
    """Plazo real para acumular `faltante_futuro` ahorrando `cuota` por mes a tasa r."""
    if cuota <= 0:
        return None
    if r_mensual <= 1e-9:
        return math.ceil(faltante_futuro / cuota)
    base = 1 + faltante_futuro * r_mensual / cuota
    if base <= 0:
        return None
    return math.ceil(math.log(base) / math.log(1 + r_mensual))


def estado_meta(cuota_asignada, cuota_ideal):
    if cuota_asignada >= cuota_ideal and cuota_ideal > 0:
        return "En curso"
    if cuota_asignada > 0:
        return "Parcial"
    return "En espera"


def fmt(monto, codigo):
    return f"{codigo} {monto:,.2f}"


@st.cache_data(ttl=3600)
def fetch_cotizaciones():
    """Trae cotizaciones de dolarapi.com. Devuelve dict con datos, "error_ssl" para fallas
    de certificado, o None para otros fallos de red/parseo."""
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r1 = requests.get("https://dolarapi.com/v1/dolares", headers=headers, timeout=5)
        r2 = requests.get("https://dolarapi.com/v1/cotizaciones/eur", headers=headers, timeout=5)
        dolares = r1.json()
        eur = r2.json()
        usd_por_casa = {d.get("casa"): float(d["venta"]) for d in dolares if d.get("venta")}
        return {
            "USD": usd_por_casa,
            "EUR": float(eur.get("venta", 0)) or None,
            "actualizado": dolares[0].get("fechaActualizacion") if dolares else None,
        }
    except requests.exceptions.SSLError:
        return "error_ssl"
    except (requests.RequestException, ValueError, KeyError):
        return None


@st.cache_data
def build_excel(rows):
    df_reporte = pd.DataFrame(list(rows), columns=EXPORT_COLUMNS)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        df_reporte.to_excel(writer, index=False, sheet_name="Ruta Crítica")
    return buf.getvalue()


st.set_page_config(layout="wide", page_title="Ruta Crítica Financiera", page_icon="💰")

if 'objetivos' not in st.session_state:
    st.session_state.objetivos = []
if 'supuestos' not in st.session_state:
    st.session_state.supuestos = {m: dict(v) for m, v in SUPUESTOS_DEFAULT.items()}
if 'tc_USD' not in st.session_state:
    st.session_state.tc_USD = TIPOS_CAMBIO_DEFAULT["USD"]
if 'tc_EUR' not in st.session_state:
    st.session_state.tc_EUR = TIPOS_CAMBIO_DEFAULT["EUR"]
if 'tc_actualizado' not in st.session_state:
    st.session_state.tc_actualizado = None
if 'fx_msg' not in st.session_state:
    st.session_state.fx_msg = None


def actualizar_cotizaciones_callback():
    """on_click: corre ANTES de re-instanciar widgets, así puede mutar tc_USD/tc_EUR."""
    data = fetch_cotizaciones()
    if data == "error_ssl":
        st.session_state.fx_msg = ("warning",
            "Error de certificado SSL al contactar dolarapi.com. "
            "Revisá tus certificados del sistema o ingresá los valores manualmente.")
        return
    if data is None:
        st.session_state.fx_msg = ("warning",
            "No se pudo conectar a dolarapi.com. Se mantienen los valores manuales.")
        return
    casa = st.session_state.get("casa_dolar", "bolsa")
    usd = data["USD"].get(casa)
    if usd:
        st.session_state.tc_USD = float(usd)
    if data.get("EUR"):
        st.session_state.tc_EUR = float(data["EUR"])
    st.session_state.tc_actualizado = data.get("actualizado")
    st.session_state.fx_msg = ("success",
        f"Cotizaciones actualizadas desde dolarapi.com ({casa}).")

st.title("💰 Planificador de Ruta Crítica Financiera")
st.markdown("Gestión de ahorro por **cascada de prioridades estratégica** con recomendación de inversión.")
st.divider()

st.header("0. Tu Perfil de Inversor")
perfil_label = st.select_slider(
    "¿Cómo reaccionás si tu inversión baja un 15% en un mes?",
    options=list(PERFIL_OPCIONES.keys()),
    value="Lo acepto si es temporal",
)
perfil = PERFIL_OPCIONES[perfil_label]
st.caption(f"Perfil de riesgo detectado: **{perfil}**")

with st.expander("⚙️ Supuestos macro y tipos de cambio", expanded=False):
    st.caption("Inflación y rendimiento nominales anuales. Editá según tu contexto.")
    cols = st.columns(3)
    for i, m in enumerate(MONEDAS):
        with cols[i]:
            st.markdown(f"**{m}**")
            st.session_state.supuestos[m]["inflacion"] = st.number_input(
                f"Inflación anual % ({m})",
                value=float(st.session_state.supuestos[m]["inflacion"]),
                step=0.5, key=f"infl_{m}",
            )
            st.session_state.supuestos[m]["rendimiento"] = st.number_input(
                f"Rendimiento anual % ({m})",
                value=float(st.session_state.supuestos[m]["rendimiento"]),
                step=0.5, key=f"rend_{m}",
            )

    st.divider()
    st.markdown("**Tipos de cambio** (ARS por 1 unidad)")
    fx_cols = st.columns([1, 1, 1.4])
    fx_cols[0].number_input("USD → ARS", step=10.0, key="tc_USD")
    fx_cols[1].number_input("EUR → ARS", step=10.0, key="tc_EUR")
    fx_cols[2].selectbox("Casa para actualizar USD", CASAS_DOLAR, index=2, key="casa_dolar")

    st.button("🔄 Actualizar cotizaciones desde dolarapi.com",
              on_click=actualizar_cotizaciones_callback)

    if st.session_state.fx_msg:
        tipo, msg = st.session_state.fx_msg
        getattr(st, tipo)(msg)

    if st.session_state.tc_actualizado:
        st.caption(f"Última actualización: {st.session_state.tc_actualizado}")
    else:
        st.caption("Cotizaciones manuales (sin actualización remota).")

st.divider()

supuestos = st.session_state.supuestos
tipos_cambio = {
    "ARS": 1.0,
    "USD": float(st.session_state.tc_USD),
    "EUR": float(st.session_state.tc_EUR),
}

col_inputs, col_visual = st.columns([1.2, 1], gap="large")

with col_inputs:
    st.header("1. Flujo de Caja Mensual")
    col_moneda, col_sueldo = st.columns([1, 2])
    with col_moneda:
        moneda = st.selectbox("Moneda del ingreso", MONEDAS, index=0)
    with col_sueldo:
        sueldo = st.number_input(f"Sueldo Neto Mensual ({moneda})", min_value=0.0, step=1000.0)

    total_gastos = st.number_input(f"Total Gastos Fijos Mensuales ({moneda})", min_value=0.0, step=1000.0)
    disponible_bruto = float(sueldo - total_gastos)

    st.divider()
    st.subheader("💡 Capacidad de Ahorro")
    ahorro_dispuesto = 0.0
    if sueldo > 0:
        if disponible_bruto > 0:
            st.info(f"Excedente disponible: **{moneda} {disponible_bruto:,.2f}**")
            ahorro_dispuesto = st.slider(
                "¿Cuánto vas a destinar al ahorro/inversión?",
                0.0, disponible_bruto, value=disponible_bruto * DEFAULT_AHORRO_RATIO, step=500.0,
            )
        else:
            st.error("🚨 Sin margen de ahorro.")

with col_visual:
    st.subheader("Distribución Mensual")
    if sueldo > 0:
        remanente_ocio = max(0.0, disponible_bruto - ahorro_dispuesto)
        fig = go.Figure(data=[go.Pie(
            labels=['Gastos Fijos', 'Ahorro Destinado', 'Remanente Ocio'],
            values=[total_gastos, ahorro_dispuesto, remanente_ocio],
            hole=.4, marker_colors=['#262626', '#2ECC71', '#BDC3C7'],
        )])
        fig.update_layout(margin=dict(t=0, b=0, l=0, r=0), height=300)
        st.plotly_chart(fig, use_container_width=True)

if sueldo > 0:
    st.subheader("🤖 Análisis Automático")
    tips = []
    ratio_gastos = total_gastos / sueldo
    ratio_ahorro = ahorro_dispuesto / sueldo

    if disponible_bruto <= 0:
        tips.append(("error", "🚨 Déficit mensual detectado: tus gastos superan tus ingresos."))
    if ratio_gastos > RATIO_GASTOS_ALTO:
        tips.append(("warning", "⚠️ Tus gastos fijos superan el 70% de tu ingreso. La regla 50/30/20 recomienda no más del 50% en necesidades."))
    if ratio_ahorro < RATIO_AHORRO_BAJO:
        tips.append(("info", "📉 Estás ahorrando menos del 10% de tu ingreso. Intentá llevar ese ratio al 20% progresivamente."))
    if ratio_ahorro >= RATIO_AHORRO_OBJETIVO:
        tips.append(("success", "✅ Excelente tasa de ahorro. Estás por encima del benchmark del 20% recomendado."))

    if tips:
        for tipo, msg in tips:
            getattr(st, tipo)(msg)
    else:
        st.success("Tu perfil financiero está equilibrado.")

st.divider()

st.header("2. Definición y Gestión de Objetivos")
col_form, col_lista = st.columns([1, 2.5])

with col_form:
    with st.form("nuevo_objetivo", clear_on_submit=True):
        st.subheader("Añadir Nueva Meta")
        nombre_obj = st.text_input("Nombre de la Meta")
        categoria = st.selectbox("Categoría", CATEGORIAS)
        col_m, col_costo = st.columns([1, 2])
        moneda_meta = col_m.selectbox("Moneda", MONEDAS, index=MONEDAS.index(moneda))
        costo_total = col_costo.number_input("Costo Total (hoy)", min_value=0.0)
        ahorro_previo = st.number_input("Ahorrado hoy (misma moneda)", min_value=0.0)
        cp1, cp2 = st.columns([2, 1])
        plazo_num = cp1.number_input("Plazo deseado", min_value=1, value=12)
        plazo_unit = cp2.selectbox("Unidad", ["Meses", "Años"])
        prioridad = st.select_slider("Prioridad", options=PRIORIDADES, value="Media")

        if st.form_submit_button("Añadir a la Ruta"):
            if not nombre_obj.strip():
                st.warning("Falta el nombre de la meta.")
            elif costo_total <= 0:
                st.warning("El costo total debe ser mayor a 0.")
            else:
                meses = plazo_num if plazo_unit == "Meses" else plazo_num * 12
                st.session_state.objetivos.append({
                    "Meta": nombre_obj.strip(),
                    "Categoría": categoria,
                    "Prioridad": prioridad,
                    "Moneda": moneda_meta,
                    "Costo Total": float(costo_total),
                    "Ya Ahorrado": float(min(ahorro_previo, costo_total)),
                    "Plazo (Meses)": int(meses),
                })
                st.rerun()

objetivos_enriquecidos = []

with col_lista:
    if st.session_state.objetivos:
        df_base = pd.DataFrame(st.session_state.objetivos)
        if "Moneda" not in df_base.columns:
            df_base["Moneda"] = moneda
        else:
            df_base["Moneda"] = df_base["Moneda"].fillna(moneda)

        df_base['Cuota Requerida'] = [
            calcular_cuota_meta(o, supuestos)["cuota_ideal"]
            for o in df_base.to_dict('records')
        ]

        st.subheader("Listado Estratégico")
        edited_df = st.data_editor(
            df_base, num_rows="dynamic", use_container_width=True,
            column_config={
                "Categoría": st.column_config.SelectboxColumn("Categoría", options=CATEGORIAS),
                "Prioridad": st.column_config.SelectboxColumn("Prioridad", options=PRIORIDADES),
                "Moneda": st.column_config.SelectboxColumn("Moneda", options=MONEDAS),
                "Costo Total": st.column_config.NumberColumn("Costo Total", format="%.2f"),
                "Ya Ahorrado": st.column_config.NumberColumn("Ahorrado Hoy", format="%.2f"),
                "Cuota Requerida": st.column_config.NumberColumn(
                    "Cuota Requerida", format="%.2f", disabled=True,
                    help="Cuota mensual estimada para llegar al costo futuro ajustado por inflación, "
                         "capitalizando al rendimiento de la moneda.",
                ),
            },
            key="editor_cascada_final",
        )

        cleaned = edited_df.drop(columns=['Cuota Requerida']).copy()
        cleaned["Moneda"] = cleaned["Moneda"].fillna(moneda)
        cleaned = cleaned.dropna(subset=["Meta", "Costo Total", "Plazo (Meses)"])
        cleaned = cleaned[cleaned["Meta"].astype(str).str.strip() != ""]
        if not cleaned.reset_index(drop=True).equals(df_base.drop(columns=['Cuota Requerida']).reset_index(drop=True)):
            st.session_state.objetivos = cleaned.to_dict('records')
            st.rerun()

        objs_sorted = sorted(st.session_state.objetivos, key=lambda x: PRIO_ORDER.get(x.get("Prioridad"), 3))
        ahorro_restante_ingreso = ahorro_dispuesto

        for obj in objs_sorted:
            cuota = calcular_cuota_meta(obj, supuestos)
            moneda_m = cuota["moneda_meta"]
            cuota_ideal_meta = cuota["cuota_ideal"]
            cuota_ideal_ingreso = convertir(cuota_ideal_meta, moneda_m, moneda, tipos_cambio)
            cuota_asignada_ingreso = min(ahorro_restante_ingreso, cuota_ideal_ingreso)
            ahorro_restante_ingreso -= cuota_asignada_ingreso
            cuota_asignada_meta = convertir(cuota_asignada_ingreso, moneda, moneda_m, tipos_cambio)

            objetivos_enriquecidos.append({
                **obj,
                "moneda_meta": moneda_m,
                "costo_futuro": cuota["costo_futuro"],
                "faltante_futuro": cuota["faltante_futuro"],
                "r_mensual": cuota["r_mensual"],
                "cuota_ideal_meta": cuota_ideal_meta,
                "cuota_asignada_meta": cuota_asignada_meta,
                "cuota_ideal_ingreso": cuota_ideal_ingreso,
                "cuota_asignada_ingreso": cuota_asignada_ingreso,
                "estado": estado_meta(cuota_asignada_meta, cuota_ideal_meta),
                "instrumento": recomendar_instrumento(obj.get("Plazo (Meses)", 0), perfil),
            })

        st.info(f"💰 Ahorro sobrante tras cubrir prioridades: **{moneda} {ahorro_restante_ingreso:,.2f}**")
    else:
        st.info("Cargá una meta para ver la tabla.")

st.divider()

if objetivos_enriquecidos:
    st.header("3. Monitor de Asignación Real (Cascada)")
    num_filas = math.ceil(len(objetivos_enriquecidos) / OBJ_POR_FILA)

    for f in range(num_filas):
        cols = st.columns(OBJ_POR_FILA)
        for c in range(OBJ_POR_FILA):
            idx = f * OBJ_POR_FILA + c
            if idx >= len(objetivos_enriquecidos):
                continue
            o = objetivos_enriquecidos[idx]
            categoria_obj = o.get("Categoría", "Otro")
            color = COLOR_PRIORIDAD.get(o['Prioridad'], '#888')
            m_meta = o['moneda_meta']

            with cols[c]:
                with st.container(border=True):
                    st.markdown(
                        f"### {o['Meta']} "
                        f"<span style='float:right; color:{color}; font-size:16px;'>"
                        f"{o['Prioridad']}</span>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"<span style='background:#eee; color:#444; padding:2px 8px; "
                        f"border-radius:8px; font-size:12px;'>{categoria_obj} · {m_meta}</span>",
                        unsafe_allow_html=True,
                    )

                    fig = go.Figure(go.Indicator(
                        mode="gauge+number",
                        value=o["Ya Ahorrado"],
                        gauge={'axis': {'range': [None, o["Costo Total"]]},
                               'bar': {'color': color}},
                    ))
                    fig.update_layout(height=140, margin=dict(t=10, b=0, l=10, r=10))
                    st.plotly_chart(fig, use_container_width=True, key=f"gauge_{idx}")

                    m1, m2 = st.columns(2)
                    m1.metric("Cuota Ideal", fmt(o['cuota_ideal_meta'], m_meta))
                    delta_val = o['cuota_asignada_meta'] - o['cuota_ideal_meta']
                    m2.metric("Asignación Real", fmt(o['cuota_asignada_meta'], m_meta),
                              delta=f"{delta_val:,.2f}",
                              delta_color="normal" if delta_val >= 0 else "inverse")

                    st.caption(
                        f"Costo futuro estimado: **{fmt(o['costo_futuro'], m_meta)}** "
                        f"(hoy {fmt(o['Costo Total'], m_meta)})"
                    )

                    if o['estado'] == "En curso":
                        st.success("🎯 Meta en curso")
                    elif o['estado'] == "Parcial":
                        meses_reales = meses_para_acumular(
                            o['faltante_futuro'], o['cuota_asignada_meta'], o['r_mensual']
                        )
                        if meses_reales:
                            st.warning(f"⚠️ Meta parcial · ~{meses_reales} meses reales a este ritmo")
                        else:
                            st.warning("⚠️ Meta parcial")
                    else:
                        st.error("⏳ En espera (sin asignación)")

                    instrumento = o['instrumento']
                    st.markdown(f"**{instrumento['emoji']} Instrumento sugerido:** {instrumento['tipo']}")
                    st.caption(instrumento['descripcion'])

    st.divider()

    st.header("4. Exportar Reporte")
    filas = tuple(
        (
            o["Meta"],
            o.get("Categoría", "Otro"),
            o["Prioridad"],
            o["moneda_meta"],
            round(float(o["Costo Total"]), 2),
            round(o["costo_futuro"], 2),
            round(float(o["Ya Ahorrado"]), 2),
            int(o["Plazo (Meses)"]),
            round(o['cuota_ideal_meta'], 2),
            round(o['cuota_asignada_meta'], 2),
            o['estado'],
            o['instrumento']["tipo"],
        )
        for o in objetivos_enriquecidos
    )

    st.download_button(
        label="📥 Exportar reporte a Excel",
        data=build_excel(filas),
        file_name="ruta_critica_financiera.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
