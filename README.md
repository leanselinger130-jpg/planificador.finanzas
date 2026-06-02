# Ruta Crítica Financiera

Planificador de ahorro e inversión con cascada de prioridades, ajuste por inflación, interés compuesto y soporte multi-moneda (ARS/USD/EUR). Trae cotizaciones de [dolarapi.com](https://dolarapi.com) en vivo.

## Correr localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

Abre en `http://localhost:8501`.

## Features

- Perfil de inversor (Bajo/Medio/Alto)
- Cascada de prioridades para asignar el ahorro mensual
- Cuota mensual con fórmula de anualidad (inflación + rendimiento del instrumento)
- Multi-moneda por meta, con conversión vía ARS
- Cotizaciones de dólar (oficial/blue/MEP/CCL/cripto/tarjeta) y euro desde dolarapi.com
- Recomendación automática de instrumento según plazo y perfil
- Export a Excel del reporte completo

## Deploy

Pensada para [Streamlit Community Cloud](https://share.streamlit.io). Apuntar al repo, branch `main`, archivo `app.py`.
