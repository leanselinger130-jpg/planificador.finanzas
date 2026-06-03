import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import math
import io
import json
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

# Pesos del scoring (deben sumar 1.0)
PESO_TOLERANCIA    = 0.35
PESO_CAPACIDAD     = 0.25
PESO_HORIZONTE     = 0.20
PESO_CONOCIMIENTO  = 0.10
PESO_OBJETIVO      = 0.10

OBJETIVOS_FINANCIEROS = [
    "Preservar capital",
    "Generar ingresos pasivos",
    "Compra de vivienda",
    "Jubilación / retiro",
    "Crecimiento patrimonial",
    "Independencia financiera",
    "Viaje / consumo a corto plazo",
]

# Mapa objetivo → ajuste de score (puede restringir recomendaciones)
OBJETIVO_SCORE_AJUSTE = {
    "Preservar capital":          -15,
    "Generar ingresos pasivos":    -5,
    "Compra de vivienda":          -5,
    "Jubilación / retiro":         +5,
    "Crecimiento patrimonial":     +5,
    "Independencia financiera":    +5,
    "Viaje / consumo a corto plazo": -20,
}

# Mapa objetivo → fuerza el plazo máximo permitido para renta variable
OBJETIVO_HORIZONTE_MINIMO = {
    "Compra de vivienda":          None,   # depende del plazo declarado
    "Viaje / consumo a corto plazo": 6,    # máximo 6 meses tolerable
}

# Cada meta usa su propio objetivo en el motor de recomendación,
# en lugar del objetivo general del perfil.
CATEGORIA_A_OBJETIVO = {
    "Fondo de Emergencia":  "Preservar capital",
    "Educación":            "Crecimiento patrimonial",
    "Vivienda":             "Compra de vivienda",
    "Vehículo":             "Preservar capital",
    "Viaje/Ocio":           "Viaje / consumo a corto plazo",
    "Tecnología":           "Preservar capital",
    "Salud":                "Preservar capital",
    "Otro":                 "Crecimiento patrimonial",
}

TOOLTIPS_INSTRUMENTOS = {
    "FCI money market": (
        "Fondo Común de Inversión que invierte en activos de muy corto plazo "
        "(Letras, cauciones). Permite rescatar el dinero en 24-48hs. "
        "Es el equivalente a una caja de ahorro con rendimiento."
    ),
    "FCI renta fija": (
        "Fondo que invierte en bonos y títulos de deuda. "
        "Ofrece rendimiento predecible con baja volatilidad. "
        "Ideal para plazos de 6 a 24 meses."
    ),
    "Bono CER": (
        "Bono del Tesoro argentino ajustado por CER (índice que sigue la inflación). "
        "Protege el capital de la inflación. Similar a los bonos UVA pero emitidos por el Estado."
    ),
    "Plazo fijo UVA": (
        "Depósito a plazo cuyo capital se ajusta por UVA (Unidad de Valor Adquisitivo), "
        "que sigue la inflación. Garantiza rendimiento real positivo con plazo mínimo de 90 días."
    ),
    "CEDEAR": (
        "Certificado de Depósito Argentino que representa acciones extranjeras (Apple, Google, etc.) "
        "cotizando en pesos en la Bolsa argentina. Permite invertir en empresas globales "
        "con cobertura implícita al dólar."
    ),
    "ETF": (
        "Exchange Traded Fund: fondo que cotiza en bolsa y replica un índice (ej: S&P 500). "
        "Permite diversificación instantánea con bajos costos. "
        "Ideal para inversores con conocimiento moderado que no quieren seleccionar acciones individuales."
    ),
    "Cartera Mixta 60/40": (
        "Estrategia clásica: 60% renta fija (bonos, FCI) + 40% renta variable (acciones, ETFs). "
        "Busca equilibrio entre protección y crecimiento. "
        "El 60/40 es el portafolio de referencia de la industria desde hace décadas."
    ),
    "Renta Variable": (
        "Inversión en acciones o instrumentos cuyo rendimiento no está garantizado. "
        "Mayor potencial de ganancia a largo plazo, pero con volatilidad significativa en el corto plazo. "
        "Requiere horizonte de al menos 3-5 años para mitigar el riesgo."
    ),
    "Cuenta remunerada": (
        "Cuenta bancaria o fintech que paga intereses diarios sobre el saldo disponible. "
        "Sin plazo mínimo, liquidez inmediata. "
        "Ejemplos en Argentina: Mercado Pago, Ualá, Naranja X."
    ),
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

# (umbral_inclusivo, label, emoji, color) — única fuente de verdad para clasificación por risk score.
PERFIL_LEVELS = [
    (20,  "Muy Conservador",  "🔵", "#2196F3"),
    (40,  "Conservador",      "🟢", "#4CAF50"),
    (60,  "Moderado",         "🟡", "#FFC107"),
    (80,  "Moderado Agresivo", "🟠", "#FF9800"),
    (100, "Agresivo",         "🔴", "#F44336"),
]


def clasificar_perfil(score: float) -> tuple[str, str]:
    for umbral, label, emoji, _ in PERFIL_LEVELS:
        if score <= umbral:
            return label, emoji
    return PERFIL_LEVELS[-1][1], PERFIL_LEVELS[-1][2]


def color_perfil(score: float) -> str:
    for umbral, _label, _emoji, color in PERFIL_LEVELS:
        if score <= umbral:
            return color
    return PERFIL_LEVELS[-1][3]


@st.cache_data
def recomendar_instrumento_avanzado(
    risk_score: float,
    plazo_meses: int,
    objetivo: str,
    conocimiento_score: float,
) -> dict:
    """
    Motor de recomendación basado en risk_score, plazo, objetivo y conocimiento.

    Reglas de negocio (en orden de precedencia):
      1. Plazo < 6 meses  → siempre liquidez, sin importar perfil.
      2. Objetivo de corto plazo forzado (viaje, etc.) + plazo ≤ 12m → liquidez.
      3. Compra de vivienda con plazo ≤ 18 meses → renta fija máximo.
      4. Conocimiento < 30 → evitar acciones individuales, priorizar ETFs/FCI.
      5. Score alto + horizonte largo → renta variable permitida.
    """
    # ── Regla 1: plazo urgente ──────────────────────────────────────────────
    if plazo_meses <= 6:
        return {
            "tipo": "Liquidez / Money Market",
            "alternativas": ["Cuenta remunerada", "FCI money market"],
            "descripcion": (
                "Con menos de 6 meses de plazo, la prioridad es la liquidez inmediata. "
                "FCI money market o cuenta remunerada: rescate en 24-48hs, sin riesgo de capital."
            ),
            "emoji": "🟢",
        }

    # ── Regla 2: objetivo de consumo a corto plazo ──────────────────────────
    if objetivo == "Viaje / consumo a corto plazo" and plazo_meses <= 12:
        return {
            "tipo": "Liquidez / Renta Fija Corta",
            "alternativas": ["FCI money market", "Plazo fijo UVA"],
            "descripcion": (
                "Objetivo de consumo próximo. Se prioriza capital garantizado. "
                "Plazo fijo UVA o FCI money market para preservar el valor real."
            ),
            "emoji": "🟢",
        }

    # ── Regla 3: compra de vivienda con horizonte ≤ 18 meses ───────────────
    if objetivo == "Compra de vivienda" and plazo_meses <= 18:
        return {
            "tipo": "Renta Fija / Instrumentos CER-UVA",
            "alternativas": ["Plazo fijo UVA", "Bono CER corto", "FCI renta fija"],
            "descripcion": (
                "Compra de vivienda próxima: no se puede asumir volatilidad. "
                "Instrumentos indexados a inflación (UVA/CER) protegen el poder adquisitivo "
                "sin exponer el capital a caídas de mercado."
            ),
            "emoji": "🟡",
        }

    # ── Clasificación por score + plazo ────────────────────────────────────
    usa_etf = conocimiento_score < 30  # baja literacy → ETFs/FCI sobre acciones

    if risk_score <= 20:
        return {
            "tipo": "Renta Fija / Bonos Cortos",
            "alternativas": ["FCI renta fija", "Plazo fijo UVA", "Letras del Tesoro"],
            "descripcion": (
                "Perfil muy conservador: capital preservado es la prioridad absoluta. "
                "Instrumentos de renta fija con baja duration y emisores de alta calidad."
            ),
            "emoji": "🔵",
        }

    if risk_score <= 40:
        if plazo_meses <= 24:
            return {
                "tipo": "Renta Fija con cobertura inflacionaria",
                "alternativas": ["Bono CER", "FCI renta fija", "Plazo fijo UVA"],
                "descripcion": (
                    "Perfil conservador con horizonte medio. "
                    "Instrumentos indexados a inflación para proteger el poder adquisitivo "
                    "sin asumir volatilidad de renta variable."
                ),
                "emoji": "🟢",
            }
        return {
            "tipo": "Cartera Conservadora 80/20",
            "alternativas": ["FCI renta fija (80%)", "FCI balanceado (20%)", "Bonos soberanos"],
            "descripcion": (
                "80% renta fija diversificada + 20% activos con leve exposición a renta variable. "
                "El horizonte permite absorber volatilidad menor."
            ),
            "emoji": "🟢",
        }

    if risk_score <= 60:
        if plazo_meses <= 12:
            return {
                "tipo": "Renta Fija Diversificada",
                "alternativas": ["FCI renta fija", "Bonos CER", "Letras ajustables"],
                "descripcion": (
                    "Perfil moderado pero horizonte corto: el tiempo no alcanza para "
                    "recuperar caídas de renta variable. Se recomienda renta fija diversificada."
                ),
                "emoji": "🟡",
            }
        instrumento = "ETFs diversificados globales" if usa_etf else "CEDEARs de índices"
        return {
            "tipo": "Cartera Mixta 60/40",
            "alternativas": ["FCI balanceado", instrumento, "Bonos soberanos en USD"],
            "descripcion": (
                "60% renta fija + 40% renta variable. Equilibrio clásico entre estabilidad "
                f"y crecimiento. {'Se priorizan ETFs de índices por bajo conocimiento declarado en acciones individuales.' if usa_etf else 'Con tu nivel de conocimiento podés incorporar CEDEARs selectivos.'}"
            ),
            "emoji": "🟡",
        }

    if risk_score <= 80:
        if plazo_meses < 24:
            return {
                "tipo": "Cartera Mixta 50/50 con sesgo dinámico",
                "alternativas": ["FCI balanceado", "ETFs globales", "Bonos USD"],
                "descripcion": (
                    "Perfil moderado-agresivo pero con horizonte limitado. "
                    "Se modera la exposición a renta variable para evitar cristalizar pérdidas "
                    "si el mercado cae cerca del momento de rescate."
                ),
                "emoji": "🟠",
            }
        instrumento = "ETFs de renta variable (S&P 500, MSCI)" if usa_etf else "Acciones / CEDEARs selectivos"
        return {
            "tipo": "Cartera de Crecimiento 30/70",
            "alternativas": [instrumento, "FCI renta variable", "Bonos HY en USD"],
            "descripcion": (
                "30% renta fija como colchón de liquidez + 70% renta variable. "
                f"{'ETFs diversificados reducen el riesgo idiosincrático sin requerir selección de empresas individuales.' if usa_etf else 'Tu nivel de conocimiento te permite construir una cartera de acciones/CEDEARs con criterio propio.'}"
            ),
            "emoji": "🟠",
        }

    # score > 80: Agresivo
    if plazo_meses < 36:
        instrumento = "ETFs temáticos / sectoriales" if usa_etf else "Acciones locales e internacionales"
        return {
            "tipo": "Renta Variable con diversificación táctica",
            "alternativas": [instrumento, "CEDEARs", "FCI renta variable"],
            "descripcion": (
                "Perfil agresivo con horizonte moderado. Alta exposición a renta variable "
                "con diversificación geográfica y sectorial para mitigar concentración."
            ),
            "emoji": "🔴",
        }
    instrumento_rv = "ETFs de mercados emergentes y desarrollados" if usa_etf else "Acciones + CEDEARs + ETFs globales"
    return {
        "tipo": "Renta Variable / Cartera de Alto Crecimiento",
        "alternativas": [instrumento_rv, "Criptomonedas (fracción)", "REITs / Real assets"],
        "descripcion": (
            "Horizonte largo + perfil agresivo: condiciones ideales para maximizar "
            "rendimiento real. La diversificación geográfica y por clase de activo "
            "es clave. El tiempo juega a favor: las caídas son oportunidades de compra."
        ),
        "emoji": "🔴",
    }


EXPORT_COLUMNS = ["Meta", "Categoría", "Prioridad", "Moneda",
                  "Costo Total", "Costo Futuro Estimado", "Ya Ahorrado",
                  "Plazo (Meses)", "Cuota Ideal", "Monto Asignado",
                  "Estado", "Instrumento Sugerido"]


def convertir(monto, de_moneda, a_moneda, tipos_cambio):
    if de_moneda == a_moneda or monto == 0:
        return monto
    tc_destino = tipos_cambio.get(a_moneda, 0)
    if tc_destino <= 0:
        return monto
    return monto * tipos_cambio[de_moneda] / tc_destino


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


DTYPES_OBJETIVOS = {
    "Costo Total": "float64",
    "Ya Ahorrado": "float64",
    "Plazo (Meses)": "int64",
}

def _normalizar_df(df, moneda_fallback):
    """Limpia y normaliza dtypes del DataFrame de objetivos para comparación segura."""
    df = df.copy()
    if "Moneda" not in df.columns:
        df["Moneda"] = moneda_fallback
    else:
        df["Moneda"] = df["Moneda"].fillna(moneda_fallback)
    df = df.dropna(subset=["Meta", "Costo Total", "Plazo (Meses)"])
    df = df[df["Meta"].astype(str).str.strip() != ""]
    for col, dt in DTYPES_OBJETIVOS.items():
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=list(DTYPES_OBJETIVOS))
    for col, dt in DTYPES_OBJETIVOS.items():
        if col in df.columns:
            df[col] = df[col].astype(dt)
    return df.reset_index(drop=True)

@st.cache_data
def proyectar_capital(
    ahorrado_presente: float,
    cuota_mensual: float,
    r_mensual: float,
    n_meses: int,
) -> list[float]:
    """
    Devuelve una lista de n_meses+1 valores representando el capital acumulado
    al inicio de cada mes (mes 0 = valor inicial).
    Fórmula: capital[t] = capital[t-1] * (1+r) + cuota
    """
    capital = [ahorrado_presente]
    for _ in range(n_meses):
        siguiente = capital[-1] * (1 + r_mensual) + cuota_mensual
        capital.append(siguiente)
    return capital


def grafico_proyeccion(obj_enriquecido: dict, supuestos: dict) -> go.Figure:
    """
    Construye el gráfico de proyección del capital acumulado para una meta,
    mostrando la línea de capital objetivo (costo futuro) como referencia.
    """
    n = int(obj_enriquecido.get("Plazo (Meses)", 12))
    moneda = obj_enriquecido["moneda_meta"]
    sup = supuestos.get(moneda, SUPUESTOS_DEFAULT[moneda])
    r_m = _tasa_mensual(sup["rendimiento"])
    pi_m = _tasa_mensual(sup["inflacion"])

    capital_ideal = proyectar_capital(
        float(obj_enriquecido.get("Ya Ahorrado", 0)),
        obj_enriquecido["cuota_ideal_meta"],
        r_m, n,
    )
    capital_real = proyectar_capital(
        float(obj_enriquecido.get("Ya Ahorrado", 0)),
        obj_enriquecido["cuota_asignada_meta"],
        r_m, n,
    )
    costo_futuro_mes = [
        float(obj_enriquecido.get("Costo Total", 0)) * (1 + pi_m) ** t
        for t in range(n + 1)
    ]
    meses = list(range(n + 1))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=meses, y=costo_futuro_mes, name="Costo objetivo (ajustado por inflación)",
        line=dict(color="#E74C3C", dash="dash", width=1.5),
        hovertemplate=f"{moneda} %{{y:,.0f}}<extra>Objetivo</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=meses, y=capital_ideal, name="Capital con cuota ideal",
        line=dict(color="#2ECC71", width=2),
        hovertemplate=f"{moneda} %{{y:,.0f}}<extra>Cuota ideal</extra>",
        fill="tozeroy", fillcolor="rgba(46,204,113,0.08)",
    ))
    if obj_enriquecido["cuota_asignada_meta"] < obj_enriquecido["cuota_ideal_meta"]:
        fig.add_trace(go.Scatter(
            x=meses, y=capital_real, name="Capital con cuota asignada",
            line=dict(color="#F39C12", width=2, dash="dot"),
            hovertemplate=f"{moneda} %{{y:,.0f}}<extra>Cuota asignada</extra>",
        ))
    fig.update_layout(
        height=220,
        margin=dict(t=8, b=8, l=8, r=8),
        legend=dict(orientation="h", y=-0.25, font=dict(size=11)),
        xaxis_title="Meses",
        yaxis_title=moneda,
        hovermode="x unified",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


@st.cache_data
def calcular_indicadores_salud(
    sueldo: float,
    total_gastos: float,
    ahorro_dispuesto: float,
    fondo_emergencia_meses: float,
    deuda_mensual: float = 0.0,
) -> list[dict]:
    """
    Calcula KPIs financieros clave y devuelve una lista de dicts con:
      nombre, valor_display, estado ('ok'|'warning'|'error'), descripcion
    """
    indicadores = []

    # 1. Ratio de ahorro
    ratio_ahorro = ahorro_dispuesto / sueldo if sueldo > 0 else 0
    if ratio_ahorro >= 0.20:
        estado_aho, icono_aho = "ok", "✅"
    elif ratio_ahorro >= 0.10:
        estado_aho, icono_aho = "warning", "⚠️"
    else:
        estado_aho, icono_aho = "error", "🚨"
    indicadores.append({
        "nombre": "Tasa de ahorro",
        "valor": f"{ratio_ahorro:.1%}",
        "estado": estado_aho,
        "icono": icono_aho,
        "descripcion": (
            "Porcentaje del ingreso neto destinado al ahorro/inversión. "
            "Referencia: ≥20% excelente · 10-20% aceptable · <10% crítico."
        ),
        "benchmark": "≥ 20%",
    })

    # 2. Cobertura del fondo de emergencia
    if fondo_emergencia_meses >= 6:
        estado_fe, icono_fe = "ok", "✅"
    elif fondo_emergencia_meses >= 3:
        estado_fe, icono_fe = "warning", "⚠️"
    else:
        estado_fe, icono_fe = "error", "🚨"
    indicadores.append({
        "nombre": "Fondo de emergencia",
        "valor": f"{fondo_emergencia_meses:.1f} meses",
        "estado": estado_fe,
        "icono": icono_fe,
        "descripcion": (
            "Meses de gastos cubiertos por el fondo de liquidez disponible. "
            "Antes de invertir, cualquier asesor recomendará tener al menos 3-6 meses cubiertos."
        ),
        "benchmark": "≥ 6 meses",
    })

    # 3. Ratio gastos/ingresos
    ratio_gastos = total_gastos / sueldo if sueldo > 0 else 0
    if ratio_gastos <= 0.50:
        estado_gf, icono_gf = "ok", "✅"
    elif ratio_gastos <= 0.70:
        estado_gf, icono_gf = "warning", "⚠️"
    else:
        estado_gf, icono_gf = "error", "🚨"
    indicadores.append({
        "nombre": "Gastos fijos / ingresos",
        "valor": f"{ratio_gastos:.1%}",
        "estado": estado_gf,
        "icono": icono_gf,
        "descripcion": (
            "Regla 50/30/20: máximo 50% en necesidades fijas, "
            "30% en gastos variables/ocio, 20% en ahorro. "
            "Si superás el 70%, hay poco margen ante imprevistos."
        ),
        "benchmark": "≤ 50%",
    })

    # 4. Ratio deuda/ingreso
    ratio_deuda = deuda_mensual / sueldo if sueldo > 0 else 0
    if ratio_deuda <= 0.15:
        estado_deu, icono_deu = "ok", "✅"
    elif ratio_deuda <= 0.30:
        estado_deu, icono_deu = "warning", "⚠️"
    else:
        estado_deu, icono_deu = "error", "🚨"
    indicadores.append({
        "nombre": "Cuotas de deuda / ingresos",
        "valor": f"{ratio_deuda:.1%}",
        "estado": estado_deu,
        "icono": icono_deu,
        "descripcion": (
            "Porcentaje del ingreso comprometido en el pago de deudas (cuotas, tarjetas, préstamos). "
            "Referencia internacional: ≤15% saludable · 15-30% moderado · >30% sobreendeudado."
        ),
        "benchmark": "≤ 15%",
    })

    # 5. Índice de libertad financiera (ingreso restante después de gastos + deudas)
    libre = sueldo - total_gastos - deuda_mensual
    ratio_libre = libre / sueldo if sueldo > 0 else 0
    if ratio_libre >= 0.30:
        estado_lib, icono_lib = "ok", "✅"
    elif ratio_libre >= 0.15:
        estado_lib, icono_lib = "warning", "⚠️"
    else:
        estado_lib, icono_lib = "error", "🚨"
    indicadores.append({
        "nombre": "Margen financiero libre",
        "valor": f"{ratio_libre:.1%}",
        "estado": estado_lib,
        "icono": icono_lib,
        "descripcion": (
            "Porcentaje del ingreso libre tras cubrir gastos fijos y deudas. "
            "Refleja la flexibilidad ante imprevistos y la capacidad real de inversión."
        ),
        "benchmark": "≥ 30%",
    })

    return indicadores


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
def build_excel(rows, perfil_data: tuple = ()):
    """
    Genera Excel con dos pestañas:
      - 'Ruta Crítica': tabla de objetivos enriquecidos
      - 'Perfil del Inversor': resumen del Risk Score y scoring por dimensión
    """
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        wb = writer.book

        # ── Hoja 1: Ruta Crítica ───────────────────────────────────────────
        df_reporte = pd.DataFrame(list(rows), columns=EXPORT_COLUMNS)
        df_reporte.to_excel(writer, index=False, sheet_name="Ruta Crítica")
        ws1 = writer.sheets["Ruta Crítica"]
        hdr_fmt = wb.add_format({'bold': True, 'bg_color': '#1a1a2e', 'font_color': '#FFFFFF', 'border': 1})
        for col_num, col_name in enumerate(EXPORT_COLUMNS):
            ws1.write(0, col_num, col_name, hdr_fmt)
        ws1.set_column(0, len(EXPORT_COLUMNS) - 1, 20)

        # ── Hoja 2: Perfil del Inversor ────────────────────────────────────
        if perfil_data:
            (rs, label, objetivo, horizonte, conocimiento,
             s_tolerancia, s_capacidad, s_horizonte, s_conocimiento, s_objetivo) = perfil_data

            titulo_fmt  = wb.add_format({'bold': True, 'font_size': 14, 'bg_color': '#1a1a2e',
                                          'font_color': '#FFFFFF', 'border': 1, 'align': 'center'})
            seccion_fmt = wb.add_format({'bold': True, 'bg_color': '#16213e', 'font_color': '#FFFFFF', 'border': 1})
            label_fmt   = wb.add_format({'bold': True, 'bg_color': '#F5F5F5', 'border': 1})
            valor_fmt   = wb.add_format({'border': 1, 'align': 'right'})
            pct_fmt     = wb.add_format({'border': 1, 'align': 'right', 'num_format': '0.0"%"'})

            ws2 = wb.add_worksheet("Perfil del Inversor")
            ws2.set_column(0, 0, 38)
            ws2.set_column(1, 1, 22)

            ws2.merge_range('A1:B1', '💰 Perfil del Inversor — Ruta Crítica Financiera', titulo_fmt)
            filas_perfil = [
                ("RESULTADO GLOBAL", None),
                ("Risk Score (0–100)", rs),
                ("Clasificación", label),
                ("Objetivo financiero", objetivo),
                ("Horizonte temporal", horizonte),
                ("Conocimiento financiero (score)", conocimiento),
                ("", None),
                ("SCORING POR DIMENSIÓN", None),
                (f"Tolerancia psicológica  (peso {int(PESO_TOLERANCIA*100)}%)", round(s_tolerancia, 1)),
                (f"Capacidad financiera    (peso {int(PESO_CAPACIDAD*100)}%)", round(s_capacidad, 1)),
                (f"Horizonte temporal      (peso {int(PESO_HORIZONTE*100)}%)", round(s_horizonte, 1)),
                (f"Conocimiento financiero (peso {int(PESO_CONOCIMIENTO*100)}%)", round(s_conocimiento, 1)),
                (f"Objetivo financiero     (peso {int(PESO_OBJETIVO*100)}%)", round(s_objetivo, 1)),
            ]
            for i, (k, v) in enumerate(filas_perfil, start=1):
                if v is None:
                    ws2.write(i, 0, k, seccion_fmt)
                    ws2.write(i, 1, "", seccion_fmt)
                else:
                    ws2.write(i, 0, k, label_fmt)
                    fmt = pct_fmt if isinstance(v, float) and k != "Risk Score (0–100)" and "clasificación" not in k.lower() and "objetivo" not in k.lower() and "horizonte" not in k.lower() else valor_fmt
                    ws2.write(i, 1, v, fmt)

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
if 'config_msg' not in st.session_state:
    st.session_state.config_msg = None
# Perfil avanzado — persiste entre reruns
if 'perfil_completo' not in st.session_state:
    st.session_state.perfil_completo = False
if 'risk_score' not in st.session_state:
    st.session_state.risk_score = 50.0
if 'objetivo_financiero' not in st.session_state:
    st.session_state.objetivo_financiero = "Crecimiento patrimonial"
if 'horizonte_perfil' not in st.session_state:
    st.session_state.horizonte_perfil = "3 a 5 años"
if 'conocimiento_score' not in st.session_state:
    st.session_state.conocimiento_score = 50.0
# Scores parciales para el reporte Excel
for _k, _v in [('score_tolerancia', 50.0), ('score_capacidad', 50.0),
               ('score_horizonte', 50.0), ('score_conocimiento', 50.0), ('score_objetivo', 50.0)]:
    if _k not in st.session_state:
        st.session_state[_k] = _v


def serializar_config():
    """Devuelve bytes JSON con el estado persistible del usuario."""
    payload = {
        "version": 1,
        "objetivos": st.session_state.objetivos,
        "supuestos": st.session_state.supuestos,
        "tc_USD": float(st.session_state.tc_USD),
        "tc_EUR": float(st.session_state.tc_EUR),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


def cargar_config_callback():
    """on_change del file_uploader: parsea JSON y actualiza session_state + widget keys."""
    uploaded = st.session_state.get("config_upload")
    if uploaded is None:
        return
    try:
        config = json.loads(uploaded.getvalue())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        st.session_state.config_msg = ("error", f"Archivo inválido: {e}")
        return
    if not isinstance(config, dict):
        st.session_state.config_msg = ("error", "Formato JSON inválido (se esperaba un objeto).")
        return

    if isinstance(config.get("objetivos"), list):
        st.session_state.objetivos = config["objetivos"]

    if isinstance(config.get("supuestos"), dict):
        for m, vals in config["supuestos"].items():
            if m in MONEDAS and isinstance(vals, dict):
                if "inflacion" in vals:
                    val = float(vals["inflacion"])
                    st.session_state.supuestos[m]["inflacion"] = val
                    st.session_state[f"infl_{m}"] = val
                if "rendimiento" in vals:
                    val = float(vals["rendimiento"])
                    st.session_state.supuestos[m]["rendimiento"] = val
                    st.session_state[f"rend_{m}"] = val

    if "tc_USD" in config:
        st.session_state.tc_USD = float(config["tc_USD"])
    if "tc_EUR" in config:
        st.session_state.tc_EUR = float(config["tc_EUR"])

    cnt = len(st.session_state.objetivos)
    st.session_state.config_msg = (
        "success",
        f"Configuración cargada: {cnt} objetivo{'s' if cnt != 1 else ''}.",
    )


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

st.header("0. Perfil de Inversor Avanzado")
st.markdown(
    "Completá el cuestionario para obtener tu **Risk Score personalizado** y recomendaciones precisas. "
    "Este análisis combina tu tolerancia psicológica, capacidad financiera, horizonte temporal, "
    "conocimiento y objetivos — igual que los sistemas utilizados por robo-advisors y bancos digitales."
)

with st.expander("📋 Completar / actualizar mi perfil de inversor", expanded=not st.session_state.perfil_completo):
    with st.form("perfil_form"):
        st.subheader("① Tolerancia psicológica al riesgo  ·  35% del score")
        col_t1, col_t2 = st.columns(2)

        with col_t1:
            r_10 = st.radio(
                "Si tu cartera baja **10%** en un mes, ¿qué hacés?",
                ["Vendo todo inmediatamente", "Me preocupo pero no hago nada",
                 "Espero y monitoreo", "Compro más (oportunidad)"],
                key="r_10",
            )
            r_20 = st.radio(
                "Si baja **20%**, ¿qué hacés?",
                ["Vendo todo inmediatamente", "Vendo una parte para reducir pérdidas",
                 "Mantengo sin cambios", "Compro más"],
                key="r_20",
            )
            r_30 = st.radio(
                "Si baja **30%**, ¿qué hacés?",
                ["Vendo todo de inmediato", "Vendo la mitad",
                 "Mantengo y espero recuperación", "Compro más agresivamente"],
                key="r_30",
            )

        with col_t2:
            r_pref = st.radio(
                "¿Qué te importa más al invertir?",
                ["No perder dinero bajo ninguna circunstancia",
                 "Un poco de crecimiento con mínima volatilidad",
                 "Balance entre estabilidad y rendimiento",
                 "Maximizar rendimiento aunque haya caídas fuertes"],
                key="r_pref",
            )
            r_crisis = st.radio(
                "¿Cómo reaccionaste en crisis financieras anteriores (2018, 2020, etc.)?",
                ["Vendí y salí del mercado", "Me angustié mucho pero no hice nada",
                 "Lo tomé con calma", "Aproveché para comprar más",
                 "No tenía inversiones aún"],
                key="r_crisis",
            )

        _t_10   = {"Vendo todo inmediatamente": 0, "Me preocupo pero no hago nada": 33,
                   "Espero y monitoreo": 67, "Compro más (oportunidad)": 100}
        _t_20   = {"Vendo todo inmediatamente": 0, "Vendo una parte para reducir pérdidas": 25,
                   "Mantengo sin cambios": 65, "Compro más": 100}
        _t_30   = {"Vendo todo de inmediato": 0, "Vendo la mitad": 20,
                   "Mantengo y espero recuperación": 60, "Compro más agresivamente": 100}
        _t_pref = {"No perder dinero bajo ninguna circunstancia": 0,
                   "Un poco de crecimiento con mínima volatilidad": 30,
                   "Balance entre estabilidad y rendimiento": 60,
                   "Maximizar rendimiento aunque haya caídas fuertes": 100}
        _t_cris = {"Vendí y salí del mercado": 0, "Me angustié mucho pero no hice nada": 25,
                   "Lo tomé con calma": 60, "Aproveché para comprar más": 100,
                   "No tenía inversiones aún": 50}

        score_tolerancia = (
            _t_10.get(r_10, 50) * 0.20 +
            _t_20.get(r_20, 50) * 0.25 +
            _t_30.get(r_30, 50) * 0.25 +
            _t_pref.get(r_pref, 50) * 0.20 +
            _t_cris.get(r_crisis, 50) * 0.10
        )

        st.divider()
        st.subheader("② Capacidad financiera para asumir riesgo  ·  25% del score")
        cf1, cf2, cf3 = st.columns(3)

        with cf1:
            r_emerg = st.radio(
                "¿Tenés fondo de emergencia (3-6 meses de gastos)?",
                ["No tengo", "Menos de 1 mes", "1 a 3 meses", "3 a 6 meses", "Más de 6 meses"],
                key="r_emerg",
            )
            r_estab = st.radio(
                "Estabilidad laboral / fuente de ingresos",
                ["Inestable / desempleo reciente", "Freelance / variable",
                 "Relación de dependencia estable", "Múltiples fuentes de ingresos"],
                key="r_estab",
            )
        with cf2:
            r_deuda = st.radio(
                "Nivel de endeudamiento respecto a tus ingresos",
                ["Más del 50% de ingresos en deudas", "Entre 30% y 50%",
                 "Entre 10% y 30%", "Menos del 10% o sin deudas"],
                key="r_deuda",
            )
            r_ahorro_pct = st.radio(
                "¿Qué porcentaje de tu ingreso ahorrás habitualmente?",
                ["No ahorro / gasto más de lo que gano", "Menos del 5%",
                 "Entre 5% y 15%", "Entre 15% y 30%", "Más del 30%"],
                key="r_ahorro_pct",
            )
        with cf3:
            r_depend = st.radio(
                "Dependientes económicos a tu cargo",
                ["3 o más", "2", "1", "Ninguno"],
                key="r_depend",
            )

        _c_emerg  = {"No tengo": 0, "Menos de 1 mes": 15, "1 a 3 meses": 40,
                     "3 a 6 meses": 75, "Más de 6 meses": 100}
        _c_estab  = {"Inestable / desempleo reciente": 0, "Freelance / variable": 35,
                     "Relación de dependencia estable": 70, "Múltiples fuentes de ingresos": 100}
        _c_deuda  = {"Más del 50% de ingresos en deudas": 0, "Entre 30% y 50%": 25,
                     "Entre 10% y 30%": 60, "Menos del 10% o sin deudas": 100}
        _c_aho    = {"No ahorro / gasto más de lo que gano": 0, "Menos del 5%": 20,
                     "Entre 5% y 15%": 50, "Entre 15% y 30%": 80, "Más del 30%": 100}
        _c_dep    = {"3 o más": 10, "2": 40, "1": 65, "Ninguno": 100}

        score_capacidad = (
            _c_emerg.get(r_emerg, 50)   * 0.30 +
            _c_estab.get(r_estab, 50)   * 0.25 +
            _c_deuda.get(r_deuda, 50)   * 0.25 +
            _c_aho.get(r_ahorro_pct, 50) * 0.10 +
            _c_dep.get(r_depend, 50)    * 0.10
        )

        st.divider()
        st.subheader("③ Horizonte temporal  ·  20% del score")
        r_horizonte = st.select_slider(
            "¿En cuánto tiempo necesitás o querés disponer del capital invertido?",
            options=["Menos de 1 año", "1 a 3 años", "3 a 5 años", "5 a 10 años", "Más de 10 años"],
            value="3 a 5 años",
            key="r_horizonte",
        )
        _h_score = {"Menos de 1 año": 5, "1 a 3 años": 25, "3 a 5 años": 55,
                    "5 a 10 años": 80, "Más de 10 años": 100}
        score_horizonte = float(_h_score.get(r_horizonte, 50))

        st.divider()
        st.subheader("④ Conocimiento financiero  ·  10% del score")
        st.caption("Marcá los instrumentos que conocés y sabés cómo funcionan (no solo que existen).")
        k1, k2, k3 = st.columns(3)
        con_acciones   = k1.checkbox("Acciones (compra/venta, dividendos)", key="con_acc")
        con_bonos      = k1.checkbox("Bonos (TIR, duration, calificación)", key="con_bon")
        con_etf        = k2.checkbox("ETFs (estructura, tracking error)", key="con_etf")
        con_fci        = k2.checkbox("Fondos Comunes de Inversión", key="con_fci")
        con_divs       = k3.checkbox("Diversificación y correlación de activos", key="con_div")
        con_inflacion  = k3.checkbox("Efecto de la inflación en rendimientos reales", key="con_inf")

        _conocimientos = [con_acciones, con_bonos, con_etf, con_fci, con_divs, con_inflacion]
        score_conocimiento = (sum(_conocimientos) / len(_conocimientos)) * 100

        st.divider()
        st.subheader("⑤ Objetivo financiero principal  ·  10% del score")
        r_objetivo = st.selectbox(
            "¿Cuál es el objetivo principal de esta inversión?",
            OBJETIVOS_FINANCIEROS,
            index=4,
            key="r_objetivo",
        )
        _base_obj = 50.0
        score_objetivo = max(0.0, min(100.0, _base_obj + OBJETIVO_SCORE_AJUSTE.get(r_objetivo, 0)))

        raw_score = (
            score_tolerancia   * PESO_TOLERANCIA   +
            score_capacidad    * PESO_CAPACIDAD    +
            score_horizonte    * PESO_HORIZONTE    +
            score_conocimiento * PESO_CONOCIMIENTO +
            score_objetivo     * PESO_OBJETIVO
        )
        risk_score_calculado = round(max(0.0, min(100.0, raw_score)), 1)

        st.divider()
        submitted_perfil = st.form_submit_button(
            "✅ Calcular mi Risk Score", type="primary", use_container_width=True,
        )

    if submitted_perfil:
        st.session_state.risk_score           = risk_score_calculado
        st.session_state.objetivo_financiero  = r_objetivo
        st.session_state.horizonte_perfil     = r_horizonte
        st.session_state.conocimiento_score   = score_conocimiento
        st.session_state.perfil_completo      = True
        st.session_state.score_tolerancia     = round(score_tolerancia, 1)
        st.session_state.score_capacidad      = round(score_capacidad, 1)
        st.session_state.score_horizonte      = round(score_horizonte, 1)
        st.session_state.score_conocimiento   = round(score_conocimiento, 1)
        st.session_state.score_objetivo       = round(score_objetivo, 1)
        st.rerun()

risk_score          = st.session_state.risk_score
objetivo_financiero = st.session_state.objetivo_financiero
horizonte_perfil    = st.session_state.horizonte_perfil
conocimiento_score  = st.session_state.conocimiento_score
perfil_label_show, perfil_emoji_show = clasificar_perfil(risk_score)

_score_color = color_perfil(risk_score)
_bar_pct = int(risk_score)

st.markdown(f"""
<div style="background:linear-gradient(135deg,#1a1a2e,#16213e);border-radius:16px;padding:24px 28px;margin-bottom:16px;">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
    <div>
      <div style="font-size:13px;color:#aaa;letter-spacing:1px;text-transform:uppercase;">Risk Score</div>
      <div style="font-size:52px;font-weight:900;color:{_score_color};line-height:1.1;">{risk_score}</div>
      <div style="font-size:20px;color:#fff;font-weight:600;">{perfil_emoji_show} {perfil_label_show}</div>
    </div>
    <div style="flex:1;min-width:220px;">
      <div style="background:#333;border-radius:8px;height:12px;margin-bottom:16px;">
        <div style="background:{_score_color};width:{_bar_pct}%;height:100%;border-radius:8px;transition:width 0.4s;"></div>
      </div>
      <table style="width:100%;color:#ddd;font-size:14px;border-collapse:collapse;">
        <tr><td style="padding:4px 0;color:#aaa;">🎯 Objetivo</td><td style="text-align:right;color:#fff;">{objetivo_financiero}</td></tr>
        <tr><td style="padding:4px 0;color:#aaa;">⏳ Horizonte</td><td style="text-align:right;color:#fff;">{horizonte_perfil}</td></tr>
        <tr><td style="padding:4px 0;color:#aaa;">📚 Conocimiento</td><td style="text-align:right;color:#fff;">{int(conocimiento_score)}/100</td></tr>
      </table>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# Recomendación general del perfil
_horizonte_meses_map = {
    "Menos de 1 año": 6, "1 a 3 años": 24, "3 a 5 años": 48,
    "5 a 10 años": 84, "Más de 10 años": 144,
}
horizonte_meses_perfil = _horizonte_meses_map.get(horizonte_perfil, 48)
rec_general = recomendar_instrumento_avanzado(
    risk_score, horizonte_meses_perfil, objetivo_financiero, conocimiento_score
)

with st.container(border=True):
    st.markdown(f"### {rec_general['emoji']} Instrumento principal recomendado: **{rec_general['tipo']}**")
    st.markdown(rec_general["descripcion"])
    st.markdown(f"**Alternativas:** {' · '.join(rec_general['alternativas'])}")

# Expone variables para uso en el resto de la app
perfil = perfil_label_show  # backward compat con secciones inferiores

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
    fx_cols[0].number_input("USD → ARS", min_value=0.01, step=10.0, key="tc_USD")
    fx_cols[1].number_input("EUR → ARS", min_value=0.01, step=10.0, key="tc_EUR")
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

with st.expander("💾 Guardar / Cargar configuración", expanded=False):
    st.caption("Descargá tus objetivos y supuestos como JSON. Cargá un archivo previo para restaurarlos.")
    cfg_cols = st.columns([1, 1])
    cfg_cols[0].download_button(
        label="📥 Descargar configuración",
        data=serializar_config(),
        file_name="configuracion_finanzas.json",
        mime="application/json",
        use_container_width=True,
    )
    cfg_cols[1].file_uploader(
        "📂 Cargar configuración",
        type=["json"],
        key="config_upload",
        on_change=cargar_config_callback,
        label_visibility="collapsed",
    )
    if st.session_state.config_msg:
        tipo, msg = st.session_state.config_msg
        getattr(st, tipo)(msg)

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
            st.info(f"Excedente disponible: **{fmt(disponible_bruto, moneda)}**")
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
    st.subheader("🩺 Diagnóstico de Salud Financiera")

    diag_col1, diag_col2 = st.columns(2)
    with diag_col1:
        fondo_emerg_monto = st.number_input(
            f"Fondo de emergencia actual ({moneda})",
            min_value=0.0, step=1000.0,
            help="Capital líquido disponible para emergencias (cuenta bancaria, FCI money market). "
                 "NO incluyas inversiones que tardan en rescatarse.",
        )
    with diag_col2:
        deuda_mensual_input = st.number_input(
            f"Cuotas de deuda mensuales ({moneda})",
            min_value=0.0, step=500.0,
            help="Suma de todas las cuotas que pagás por mes: créditos, tarjetas en cuotas, préstamos. "
                 "No incluyas gastos corrientes de tarjeta.",
        )

    # Calcular meses de cobertura del fondo de emergencia
    gastos_para_fe = total_gastos if total_gastos > 0 else 1.0
    meses_fondo = fondo_emerg_monto / gastos_para_fe if gastos_para_fe > 0 else 0.0

    indicadores = calcular_indicadores_salud(
        sueldo, total_gastos, ahorro_dispuesto, meses_fondo, deuda_mensual_input
    )

    # Alerta crítica: fondo de emergencia inexistente antes de invertir
    if meses_fondo < 1 and ahorro_dispuesto > 0:
        st.error(
            "🚨 **Prioridad crítica:** No tenés fondo de emergencia. "
            "Antes de invertir en cualquier instrumento, acumulá al menos 3 meses de gastos "
            "en un FCI money market o cuenta remunerada. Sin este colchón, cualquier imprevisto "
            "te obligaría a vender inversiones en el peor momento."
        )

    ind_cols = st.columns(len(indicadores))
    color_estado = {"ok": "#2ECC71", "warning": "#F1C40F", "error": "#E74C3C"}
    for i, ind in enumerate(indicadores):
        col = ind_cols[i]
        c = color_estado[ind["estado"]]
        col.markdown(
            f"""<div style='border:1px solid {c};border-radius:10px;padding:12px 10px;text-align:center;'>
            <div style='font-size:22px;'>{ind['icono']}</div>
            <div style='font-size:11px;color:#888;margin-top:4px;'>{ind['nombre']}</div>
            <div style='font-size:20px;font-weight:700;color:{c};'>{ind['valor']}</div>
            <div style='font-size:10px;color:#aaa;margin-top:2px;'>benchmark: {ind['benchmark']}</div>
            </div>""",
            unsafe_allow_html=True,
        )
        col.caption(ind["descripcion"])

    # Tips adicionales (conservados del original)
    st.divider()
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

if st.session_state.objetivos:
    tiene_fondo = any(
        o.get("Categoría") == "Fondo de Emergencia"
        for o in st.session_state.objetivos
    )
    if not tiene_fondo:
        st.warning(
            "⚠️ **Sin fondo de emergencia en tu ruta.** "
            "Los asesores financieros coinciden: antes de invertir en cualquier instrumento, "
            "es necesario tener 3-6 meses de gastos en activos líquidos. "
            "Considerá agregar una meta de categoría **Fondo de Emergencia** con prioridad Alta."
        )

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
        df_base = _normalizar_df(pd.DataFrame(st.session_state.objetivos), moneda_fallback=moneda)
        records = df_base.to_dict('records')
        cuotas_por_idx = [calcular_cuota_meta(r, supuestos) for r in records]
        df_base['Cuota Requerida'] = [c["cuota_ideal"] for c in cuotas_por_idx]

        st.subheader("Listado Estratégico")
        edited_df = st.data_editor(
            df_base, num_rows="fixed", use_container_width=True,
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

        cleaned = _normalizar_df(edited_df.drop(columns=["Cuota Requerida"]), moneda_fallback=moneda)
        df_actual = df_base.drop(columns=["Cuota Requerida"])
        cols_comunes = [c for c in cleaned.columns if c in df_actual.columns]
        if not cleaned[cols_comunes].equals(df_actual[cols_comunes]):
            st.session_state.objetivos = cleaned.to_dict("records")
            st.rerun()

        sorted_indexed = sorted(enumerate(records), key=lambda t: PRIO_ORDER.get(t[1].get("Prioridad"), 3))
        ahorro_restante_ingreso = ahorro_dispuesto

        for orig_idx, obj in sorted_indexed:
            cuota = cuotas_por_idx[orig_idx]
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
                # Mejora 3: objetivo derivado de la categoría de cada meta
                "objetivo_meta": CATEGORIA_A_OBJETIVO.get(
                    obj.get("Categoría", "Otro"), objetivo_financiero
                ),
                "instrumento": recomendar_instrumento_avanzado(
                    risk_score,
                    obj.get("Plazo (Meses)", 0),
                    CATEGORIA_A_OBJETIVO.get(obj.get("Categoría", "Otro"), objetivo_financiero),
                    conocimiento_score,
                ),
            })

        st.info(f"💰 Ahorro sobrante tras cubrir prioridades: **{fmt(ahorro_restante_ingreso, moneda)}**")
    else:
        st.info("Cargá una meta para ver la tabla.")

st.divider()

if objetivos_enriquecidos:
    st.header("3. Monitor de Asignación Real (Cascada)")

    # ── Mejora 4: Panel de análisis de escenarios ─────────────────────────────
    with st.expander("🔭 Análisis de Escenarios (What-If)", expanded=False):
        st.markdown(
            "Simulá cómo cambian tus metas ante distintos contextos económicos o de ahorro. "
            "Los cambios aquí son **solo para visualización** y no modifican tu planificación base."
        )
        sc1, sc2, sc3 = st.columns(3)
        delta_ahorro_pct = sc1.slider(
            "Cambio en ahorro mensual",
            min_value=-50, max_value=100, value=0, step=5,
            format="%d%%",
            help="Simulá qué pasa si ahorrás más o menos cada mes.",
        )
        delta_inflacion = sc2.slider(
            "Variación de inflación anual (pp)",
            min_value=-30, max_value=60, value=0, step=5,
            format="%+d pp",
            help="Puntos porcentuales adicionales sobre la inflación configurada.",
        )
        delta_rendimiento = sc3.slider(
            "Variación de rendimiento anual (pp)",
            min_value=-10, max_value=20, value=0, step=1,
            format="%+d pp",
            help="Puntos porcentuales adicionales sobre el rendimiento configurado.",
        )

        if delta_ahorro_pct != 0 or delta_inflacion != 0 or delta_rendimiento != 0:
            ahorro_escenario = ahorro_dispuesto * (1 + delta_ahorro_pct / 100)
            supuestos_escenario = {
                m: {
                    "inflacion":   v["inflacion"]   + delta_inflacion,
                    "rendimiento": v["rendimiento"] + delta_rendimiento,
                }
                for m, v in supuestos.items()
            }
            ahorro_rest_esc = ahorro_escenario
            resumen_esc = []
            for obj in sorted(st.session_state.objetivos, key=lambda x: PRIO_ORDER.get(x.get("Prioridad"), 3)):
                cuota_esc = calcular_cuota_meta(obj, supuestos_escenario)
                cuota_ideal_esc = cuota_esc["cuota_ideal"]
                cuota_ideal_ing_esc = convertir(cuota_ideal_esc, cuota_esc["moneda_meta"], moneda, tipos_cambio)
                asignada_ing_esc = min(ahorro_rest_esc, cuota_ideal_ing_esc)
                ahorro_rest_esc -= asignada_ing_esc
                asignada_meta_esc = convertir(asignada_ing_esc, moneda, cuota_esc["moneda_meta"], tipos_cambio)
                resumen_esc.append({
                    "Meta": obj["Meta"],
                    "Cuota ideal (escenario)": round(cuota_ideal_esc, 2),
                    "Asignada (escenario)": round(asignada_meta_esc, 2),
                    "Costo futuro (escenario)": round(cuota_esc["costo_futuro"], 2),
                    "Estado": estado_meta(asignada_meta_esc, cuota_ideal_esc),
                    "Moneda": cuota_esc["moneda_meta"],
                })
            df_esc = pd.DataFrame(resumen_esc)
            st.dataframe(df_esc, use_container_width=True, hide_index=True)
            st.caption(
                f"Escenario: ahorro {'+'if delta_ahorro_pct>=0 else ''}{delta_ahorro_pct}% · "
                f"inflación {'+'if delta_inflacion>=0 else ''}{delta_inflacion}pp · "
                f"rendimiento {'+'if delta_rendimiento>=0 else ''}{delta_rendimiento}pp"
            )
        else:
            st.info("Mové alguno de los sliders para ver el impacto en tus metas.")

    st.divider()

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
                    if instrumento.get("alternativas"):
                        st.caption(f"Alternativas: {' · '.join(instrumento['alternativas'])}")

                    # ── Mejora 7: Tooltips de instrumentos ───────────────────
                    terminos_en_tipo = [k for k in TOOLTIPS_INSTRUMENTOS if k.lower() in instrumento['tipo'].lower()]
                    terminos_en_alts = [k for k in TOOLTIPS_INSTRUMENTOS
                                        for alt in instrumento.get("alternativas", [])
                                        if k.lower() in alt.lower()]
                    terminos = list(dict.fromkeys(terminos_en_tipo + terminos_en_alts))[:2]
                    if terminos:
                        with st.expander("📖 ¿Qué significa?", expanded=False):
                            for t in terminos:
                                st.markdown(f"**{t}:** {TOOLTIPS_INSTRUMENTOS[t]}")

                    # ── Mejora 1: Proyección temporal ─────────────────────────
                    with st.expander("📈 Ver proyección temporal", expanded=False):
                        fig_proy = grafico_proyeccion(o, supuestos)
                        st.plotly_chart(fig_proy, use_container_width=True,
                                        key=f"proy_{idx}")

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

    perfil_data_export = (
        risk_score,
        perfil_label_show,
        objetivo_financiero,
        horizonte_perfil,
        int(conocimiento_score),
        st.session_state.score_tolerancia,
        st.session_state.score_capacidad,
        st.session_state.score_horizonte,
        st.session_state.score_conocimiento,
        st.session_state.score_objetivo,
    )

    st.download_button(
        label="📥 Exportar reporte a Excel",
        data=build_excel(filas, perfil_data=perfil_data_export),
        file_name="ruta_critica_financiera.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
