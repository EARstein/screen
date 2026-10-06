import math
import sys
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
import yfinance as yf
from streamlit.runtime.scriptrunner import get_script_run_ctx

# --- MODIFICACIÓN 1 -----------------------------------------------------
# Si el script se ejecuta con `python screnner.py` (sin `streamlit run`),
# st.stop() no detiene nada y el código sigue con resultado = None, lo que
# provoca el TypeError. Cortamos acá con un mensaje claro.
if get_script_run_ctx() is None:
    print(
        "\nEsta app de Streamlit no se ejecuta con 'python'.\n"
        "Usá:\n\n"
        f"    {sys.executable} -m streamlit run \"{__file__}\"\n"
    )
    sys.exit(0)
# ------------------------------------------------------------------------

st.set_page_config(
    page_title="Scanner USA",
    page_icon="📈",
    layout="wide",
)

st.markdown("""
<style>
.stApp { background: #0b1018; color: #e6edf3; }
[data-testid="stSidebar"] { background: #121a27; }
h1 { letter-spacing: -1px; }
</style>
""", unsafe_allow_html=True)

st.title("📈 Scanner USA")
st.caption("Yahoo Finance · Acciones de Estados Unidos · Indicadores diarios")


def numero(valor):
    try:
        valor = float(valor)
        return valor if math.isfinite(valor) else None
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=1200, max_entries=8, show_spinner=False)
def consultar(precio_min, cap_min, cap_max, limite):
    consulta = yf.EquityQuery("and", [
        yf.EquityQuery("eq", ["region", "us"]),
        yf.EquityQuery("gt", ["intradayprice", precio_min]),
        yf.EquityQuery("btwn", [
            "intradaymarketcap", cap_min * 1e9, cap_max * 1e9
        ]),
    ])

    cotizaciones = {}
    total = None

    for inicio in range(0, limite, 250):
        respuesta = yf.screen(
            consulta,
            offset=inicio,
            size=min(250, limite - inicio),
            sortField="intradaymarketcap",
            sortAsc=False,
        )

        if not isinstance(respuesta, dict) or "quotes" not in respuesta:
            raise RuntimeError("Yahoo no devolvió una respuesta válida.")

        if respuesta.get("total") is not None:
            total = int(respuesta["total"])

        lote = respuesta["quotes"]

        for accion in lote:
            simbolo = accion.get("symbol")
            if simbolo:
                cotizaciones[simbolo] = accion

        if not lote or (total is not None and inicio + len(lote) >= total):
            break

    filas = []
    omitidas = []
    simbolos = list(cotizaciones)

    for inicio in range(0, len(simbolos), 50):
        lote = simbolos[inicio:inicio + 50]

        try:
            historicos = yf.download(
                lote,
                period="2y",
                interval="1d",
                auto_adjust=False,
                group_by="ticker",
                threads=4,
                progress=False,
                timeout=20,
            )
        except Exception:
            omitidas.extend(lote)
            continue

        for simbolo in lote:
            try:
                datos = (
                    historicos[simbolo]
                    if isinstance(historicos.columns, pd.MultiIndex)
                    else historicos
                )
                datos = datos[["Close", "High", "Low", "Volume"]].dropna()
                datos = datos[datos["Close"] > 0]

                if len(datos) < 100:
                    raise ValueError("Histórico insuficiente")

                cierre = datos["Close"]
                ultimo_cierre = float(cierre.iloc[-1])

                ema20 = float(
                    cierre.ewm(span=20, adjust=False).mean().iloc[-1]
                )
                ema50 = float(
                    cierre.ewm(span=50, adjust=False).mean().iloc[-1]
                )

                # Fórmula ADR% de TradingView: rango medio de
                # 14 sesiones dividido por el último cierre.
                rango = (
                    datos["High"].tail(14).mean()
                    - datos["Low"].tail(14).mean()
                )
                adr = float(rango / ultimo_cierre * 100)

                accion = cotizaciones[simbolo]
                precio = numero(accion.get("regularMarketPrice"))
                capitalizacion = numero(accion.get("marketCap"))

                if accion.get("currency", "USD") != "USD":
                    raise ValueError("Cotización fuera de USD")

                if precio is None or capitalizacion is None:
                    raise ValueError("Falta precio o capitalización")

                if not all(math.isfinite(v) for v in [ema20, ema50, adr]):
                    raise ValueError("Indicadores incompletos")

                fecha_cotizacion = numero(accion.get("regularMarketTime"))
                fecha_cotizacion = (
                    datetime.fromtimestamp(
                        fecha_cotizacion, timezone.utc
                    ).strftime("%Y-%m-%d %H:%M UTC")
                    if fecha_cotizacion is not None
                    else "Sin dato"
                )

                filas.append({
                    "Símbolo": simbolo,
                    "Empresa": (
                        accion.get("shortName")
                        or accion.get("longName")
                        or simbolo
                    ),
                    "Precio USD": precio,
                    "Cambio %": numero(
                        accion.get("regularMarketChangePercent")
                    ),
                    "Cap. mercado B USD": capitalizacion / 1e9,
                    "Volumen": numero(accion.get("regularMarketVolume")),
                    "EMA 20": ema20,
                    "EMA 50": ema50,
                    "ADR %": adr,
                    "Sector": accion.get("sector") or "Sin dato",
                    "Fecha indicadores": str(datos.index[-1].date()),
                    "Fecha cotización": fecha_cotizacion,
                    "Yahoo": f"https://finance.yahoo.com/quote/{simbolo}/",
                })
            except Exception:
                omitidas.append(simbolo)

    if simbolos and not filas:
        raise RuntimeError(
            "No se pudieron obtener históricos completos de Yahoo."
        )

    return {
        "datos": pd.DataFrame(filas),
        "candidatas": len(simbolos),
        "total": total,
        "omitidas": omitidas,
        "consulta": datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        ),
    }


with st.sidebar:
    st.header("Filtros")
    st.caption("Mercado fijo: Estados Unidos")

    precio_min = st.number_input(
        "Precio mayor que · USD", min_value=0.0, value=10.0
    )
    cap_min = st.number_input(
        "Capitalización mínima · B USD", min_value=0.0, value=2.0
    )
    cap_max = st.number_input(
        "Capitalización máxima · B USD", min_value=0.0, value=500.0
    )

    st.caption("1 B = 1.000 millones de USD")
    ema20_activa = st.checkbox("Precio por encima de EMA 20", value=True)
    ema50_activa = st.checkbox("Precio por encima de EMA 50", value=True)

    adr_min = st.number_input(
        "ADR mínimo · %", min_value=0.0, value=3.0
    )
    adr_max = st.number_input(
        "ADR máximo · %", min_value=0.0, value=15.0
    )

    limite = st.selectbox(
        "Máximo de acciones a analizar",
        [100, 250, 500, 1000],
        index=1,
    )

    st.caption(
        "Primero se seleccionan las mayores empresas que cumplen "
        "precio y capitalización; después se aplican EMA y ADR."
    )

    actualizar = st.button("Consultar / actualizar", type="primary")

    if actualizar:
        consultar.clear()

    st.caption(
        "Los resultados se reutilizan hasta 20 minutos. "
        "Actualizar vuelve a consultar Yahoo."
    )


if cap_min > cap_max or adr_min > adr_max:
    st.error("El mínimo no puede superar al máximo.")
    st.stop()

firma = (precio_min, cap_min, cap_max, limite)

if actualizar:
    st.session_state.pop("resultado", None)

    try:
        with st.spinner(
            "Consultando Yahoo y calculando indicadores… "
            "La primera consulta puede tardar varios minutos."
        ):
            st.session_state["resultado"] = consultar(*firma)
            st.session_state["firma"] = firma
    except Exception as error:
        st.error(f"No se pudo completar la consulta: {error}")
        st.info(
            "Yahoo puede limitar las consultas. "
            "Probá más tarde o con menos acciones."
        )

resultado = st.session_state.get("resultado")

if resultado is None:
    st.info("Elegí los filtros y presioná «Consultar / actualizar».")
    st.stop()

if st.session_state.get("firma") != firma:
    st.info(
        "Cambiaste precio, capitalización o cantidad. "
        "Presioná «Consultar / actualizar» para aplicar esos cambios."
    )
    st.stop()

# --- MODIFICACIÓN 2 -----------------------------------------------------
# Chequeo defensivo: evita que la app se caiga si resultado o su DataFrame
# vinieran vacíos por cualquier motivo.
if resultado is None or resultado.get("datos") is None:
    st.error("No se pudieron obtener datos. Revisá la conexión o los filtros.")
    st.stop()
# ------------------------------------------------------------------------

datos = resultado["datos"].copy()

if not datos.empty:
    mascara = (
        (datos["Precio USD"] > precio_min)
        & datos["Cap. mercado B USD"].between(cap_min, cap_max)
        & datos["ADR %"].between(adr_min, adr_max)
    )

    if ema20_activa:
        mascara &= datos["Precio USD"] > datos["EMA 20"]

    if ema50_activa:
        mascara &= datos["Precio USD"] > datos["EMA 50"]

    datos = datos[mascara].sort_values(
        "Cap. mercado B USD", ascending=False
    )

busqueda = st.text_input("Buscar símbolo o empresa")

if busqueda and not datos.empty:
    datos = datos[
        datos["Símbolo"].str.contains(busqueda, case=False, regex=False)
        | datos["Empresa"].str.contains(busqueda, case=False, regex=False)
    ]

a, b, c = st.columns(3)
a.metric("Resultados visibles", len(datos))
b.metric("Acciones consultadas", resultado["candidatas"])
c.metric("Sin datos completos", len(resultado["omitidas"]))

st.caption(f"Consulta realizada: {resultado['consulta']}")

total = resultado["total"]

if total is None:
    st.warning(
        "Yahoo no informó el total de candidatas; "
        "no se puede confirmar que el análisis cubra todo el mercado."
    )
elif total > resultado["candidatas"]:
    st.warning(
        f"Análisis parcial: se consultaron {resultado['candidatas']} "
        f"de {total} candidatas, seleccionadas por capitalización."
    )

if datos.empty:
    st.info("No hay resultados para estos filtros y esta selección.")
else:
    st.dataframe(
        datos,
        hide_index=True,
        column_config={
            "Precio USD": st.column_config.NumberColumn(format="$%.2f"),
            "Cambio %": st.column_config.NumberColumn(format="%.2f %%"),
            "Cap. mercado B USD": st.column_config.NumberColumn(
                format="%.2f"
            ),
            "EMA 20": st.column_config.NumberColumn(format="%.2f"),
            "EMA 50": st.column_config.NumberColumn(format="%.2f"),
            "ADR %": st.column_config.NumberColumn(format="%.2f %%"),
            "Yahoo": st.column_config.LinkColumn(display_text="Ver"),
        },
    )

    st.download_button(
        "Descargar resultados CSV",
        datos.to_csv(index=False).encode("utf-8-sig"),
        file_name="scanner_usa.csv",
        mime="text/csv",
    )

if resultado["omitidas"]:
    with st.expander("Acciones omitidas por datos incompletos"):
        st.write(", ".join(resultado["omitidas"]))

st.caption(
    "EMA 20 y EMA 50 calculadas con cierres diarios. "
    "ADR: rango medio de 14 sesiones / último cierre × 100. "
    "El precio corresponde a la cotización disponible en Yahoo; "
    "las fechas pueden diferir y la última vela diaria estar en curso."
)
