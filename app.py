"""
INFINITY CRYPTO PRO  -  versão Liquidity Sweep

Porta para Python a lógica do indicador "Liquidity Sweep Strategy [Pro]" (Pine):
  - Pivôs viram níveis de liquidez: BSL (topos) e SSL (fundos)
  - Sweep = pavio rompe o nível e o preço REJEITA (fecha de volta para dentro)
  - Filtro de tendência (EMA/SMA, opcionalmente em timeframe superior, sem lookahead)
  - SL = pavio do sweep +/- padding em ATR | TP = Risk/Reward configurável
  - Backtest embutido (taxa de acerto, expectativa em R, profit factor, drawdown)

Regras de ouro do app:
  - Só candles FECHADOS são usados (o candle em formação é descartado).
  - Entrada = fechamento do candle de sweep (a mesma regra do indicador Pine).
  - Custos (taxa + slippage) entram no backtest e filtram stops curtos demais.

requirements.txt:  streamlit  ccxt  pandas  numpy  streamlit-echarts
"""
import numpy as np
import pandas as pd
import streamlit as st
import ccxt
from streamlit_echarts import st_echarts

COL_BSL = "#ff6f61"   # topos  (Buy-Side Liquidity)  - coral
COL_SSL = "#26a69a"   # fundos (Sell-Side Liquidity) - teal
COL_TP = "#00ffcc"
COL_SL = "#ff4b4b"
TZ_LOCAL = "America/Sao_Paulo"


# ============================================================================
# 1. EXCHANGE / DADOS
# ============================================================================
@st.cache_resource
def get_exchange():
    # defaultType=swap: o scanner trabalha com perpétuos USDT (linear)
    return ccxt.mexc({"timeout": 30000, "enableRateLimit": True, "options": {"defaultType": "swap"}})


def tf_seconds(tf):
    return get_exchange().parse_timeframe(tf)


@st.cache_data(ttl=3600, show_spinner=False)
def get_timeframes():
    """Só oferece timeframes que a exchange realmente suporta (a MEXC não tem 10m/2h/12h)."""
    ex = get_exchange()
    tfs = list((ex.timeframes or {}).keys())
    if not tfs:
        tfs = ["1m", "5m", "15m", "30m", "1h", "4h", "8h", "1d", "1w"]
    return sorted(tfs, key=lambda t: ex.parse_timeframe(t))


@st.cache_data(ttl=30, show_spinner=False)
def _fetch_ohlcv(symbol, timeframe, limit):
    bars = get_exchange().fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    return pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "vol"])


def fetch_data(symbol, timeframe, limit=1000):
    """Retorna candles FECHADOS (descarta o último, que ainda está em formação)."""
    try:
        df = _fetch_ohlcv(symbol, timeframe, limit)
    except Exception as e:
        st.session_state["fetch_err"] = f"{symbol} {timeframe}: {type(e).__name__}: {str(e)[:140]}"
        return pd.DataFrame()
    if len(df) < 3:
        return pd.DataFrame()
    df = df.iloc[:-1].reset_index(drop=True)
    df["vol"] = df["vol"].fillna(0.0)
    return df


@st.cache_data(ttl=600, show_spinner=False)
def top_symbols(n):
    """Perpétuos USDT mais líquidos (por volume em USDT), em vez de 'os 100 primeiros da lista'."""
    ex = get_exchange()
    markets = ex.load_markets()
    syms = [s for s, m in markets.items()
            if m.get("swap") and m.get("linear") and m.get("quote") == "USDT" and m.get("active") is not False]
    try:
        tickers = ex.fetch_tickers(syms)
        syms.sort(key=lambda s: (tickers.get(s) or {}).get("quoteVolume") or 0, reverse=True)
    except Exception:
        pass
    return syms[:n]


# ============================================================================
# 2. INDICADORES
# ============================================================================
def calc_ma(close, kind, n):
    if kind == "EMA":
        return close.ewm(span=n, adjust=False, min_periods=n).mean()
    return close.rolling(n).mean()


def calc_atr(df, n):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()  # RMA de Wilder


def trend_ma_for(df, symbol, tf, P):
    """Média de tendência alinhada ao gráfico. Em HTF usa só candles HTF já fechados (sem lookahead)."""
    n = len(df)
    if not P["use_trend"]:
        return np.full(n, np.nan)
    if P["ma_tf"] == "Gráfico atual" or tf_seconds(P["ma_tf"]) <= tf_seconds(tf):
        return calc_ma(df["close"], P["ma_type"], P["ma_len"]).to_numpy(dtype=float)

    htf = fetch_data(symbol, P["ma_tf"], limit=500)
    if htf.empty:
        return np.full(n, np.nan)
    htf_ma = calc_ma(htf["close"], P["ma_type"], P["ma_len"]).to_numpy(dtype=float)
    d_chart = int(tf_seconds(tf) * 1000)
    d_htf = int(tf_seconds(P["ma_tf"]) * 1000)
    # um valor HTF só fica disponível no FECHAMENTO do candle HTF
    left = pd.DataFrame({"ct": (df["ts"] + d_chart).astype("int64")})
    right = pd.DataFrame({"ct": (htf["ts"] + d_htf).astype("int64"), "ma": htf_ma})
    merged = pd.merge_asof(left, right, on="ct", direction="backward")
    return merged["ma"].to_numpy(dtype=float)


# ============================================================================
# 3. MOTOR LIQUIDITY SWEEP (equivalente ao indicador Pine)
# ============================================================================
def run_engine(df, ma, P):
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    v = df["vol"].to_numpy(dtype=float)
    n = len(df)
    L = int(P["length"])
    atr = calc_atr(df, P["atr_len"]).to_numpy(dtype=float)

    # pivôs: máxima/mínima da janela [i-L, i+L]; só são CONFIRMADOS L candles depois
    win = 2 * L + 1
    is_ph = (df["high"].rolling(win, center=True).max() == df["high"]).to_numpy()
    is_pl = (df["low"].rolling(win, center=True).min() == df["low"]).to_numpy()
    vol_ma = pd.Series(v).rolling(20).mean().shift(1).to_numpy(dtype=float)

    bsl, ssl = [], []          # níveis ativos
    levels, signals = [], []   # histórico p/ desenho e sinais
    skipped = 0

    for i in range(n):
        a = atr[i]
        has_atr = not np.isnan(a)
        dist = P["min_sweep_atr"] * a if has_atr else 0.0
        bull_lv, bear_lv = [], []

        # --- SSL (fundos): sweep altista --------------------------------------
        for lv in ssl[:]:
            p = lv["price"]
            pen = l[i] < p - dist
            if pen or c[i] < p:                      # varrido OU rompido por fechamento
                rej = pen and c[i] > p and (not P["wick_only"] or min(o[i], c[i]) > p)
                if rej:
                    bull_lv.append(p)
                lv["end"] = i
                lv["state"] = "swept" if rej else "broken"
                ssl.remove(lv)

        # --- BSL (topos): sweep baixista --------------------------------------
        for lv in bsl[:]:
            p = lv["price"]
            pen = h[i] > p + dist
            if pen or c[i] > p:
                rej = pen and c[i] < p and (not P["wick_only"] or max(o[i], c[i]) < p)
                if rej:
                    bear_lv.append(p)
                lv["end"] = i
                lv["state"] = "swept" if rej else "broken"
                bsl.remove(lv)

        # --- Filtros + projeção de risco/retorno ------------------------------
        if (bull_lv or bear_lv) and has_atr:
            m = ma[i]
            up = (not P["use_trend"]) or (not np.isnan(m) and c[i] > m)
            dn = (not P["use_trend"]) or (not np.isnan(m) and c[i] < m)
            vol_ok = (not P["use_vol"]) or (not np.isnan(vol_ma[i]) and v[i] >= P["vol_mult"] * vol_ma[i])
            long_ok = bool(bull_lv) and up and vol_ok
            short_ok = bool(bear_lv) and dn and vol_ok
            if long_ok and short_ok:                 # outside bar: prevalece a cor do candle
                if c[i] >= o[i]:
                    short_ok = False
                else:
                    long_ok = False

            for ok, side in ((long_ok, "BUY"), (short_ok, "SELL")):
                if not ok:
                    continue
                entry = c[i]
                if side == "BUY":
                    sl = l[i] - P["sl_pad_atr"] * a
                    risk = entry - sl
                    tp = entry + risk * P["rr"]
                    lvl = max(bull_lv)
                else:
                    sl = h[i] + P["sl_pad_atr"] * a
                    risk = sl - entry
                    tp = entry - risk * P["rr"]
                    lvl = min(bear_lv)
                risk_pct = risk / entry * 100.0
                cost_r = (P["cost_pct"] / risk_pct) if risk_pct > 0 else np.inf
                if cost_r > P["max_cost_r"]:         # stop curto demais: a taxa come o trade
                    skipped += 1
                    continue
                signals.append(dict(idx=i, dir=side, entry=float(entry), sl=float(sl), tp=float(tp),
                                    level=float(lvl), risk_pct=float(risk_pct), cost_r=float(cost_r)))

        # --- Novos níveis a partir do pivô confirmado agora --------------------
        k = i - L
        if k >= L:
            if is_ph[k] and not any(x["price"] == h[k] for x in bsl):
                lv = dict(kind="BSL", price=float(h[k]), start=k, end=None, state="active")
                bsl.append(lv)
                levels.append(lv)
                if len(bsl) > P["max_levels"]:
                    old = bsl.pop(0)
                    old["end"], old["state"] = i, "dropped"
            if is_pl[k] and not any(x["price"] == l[k] for x in ssl):
                lv = dict(kind="SSL", price=float(l[k]), start=k, end=None, state="active")
                ssl.append(lv)
                levels.append(lv)
                if len(ssl) > P["max_levels"]:
                    old = ssl.pop(0)
                    old["end"], old["state"] = i, "dropped"

    return dict(levels=levels, signals=signals, skipped=skipped, atr=atr)


# ============================================================================
# 4. BACKTEST
# ============================================================================
def simulate(df, signals):
    """Cada sinal é simulado de forma independente. Se SL e TP caírem no mesmo candle, conta SL."""
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    n = len(df)
    for sg in signals:
        sg["result"], sg["exit"] = "OPEN", None
        for j in range(sg["idx"] + 1, n):
            if sg["dir"] == "BUY":
                sl_hit, tp_hit = l[j] <= sg["sl"], h[j] >= sg["tp"]
            else:
                sl_hit, tp_hit = h[j] >= sg["sl"], l[j] <= sg["tp"]
            if sl_hit:
                sg["result"], sg["exit"] = "SL", j
                break
            if tp_hit:
                sg["result"], sg["exit"] = "TP", j
                break


def backtest_stats(signals, rr):
    closed = [s for s in signals if s["result"] != "OPEN"]
    if not closed:
        return None
    closed.sort(key=lambda s: s["exit"])
    rs = np.array([(rr if s["result"] == "TP" else -1.0) - s["cost_r"] for s in closed])
    wins = sum(1 for s in closed if s["result"] == "TP")
    gross_win = rs[rs > 0].sum()
    gross_loss = -rs[rs < 0].sum()
    eq = np.concatenate([[0.0], np.cumsum(rs)])
    dd = float((np.maximum.accumulate(eq) - eq).max())
    avg_cost = float(np.mean([s["cost_r"] for s in closed]))
    return dict(n=len(closed), open=len(signals) - len(closed), wr=wins / len(closed),
                exp=float(rs.mean()), total=float(rs.sum()),
                pf=float(gross_win / gross_loss) if gross_loss > 0 else float("inf"),
                dd=dd, be=(1 + avg_cost) / (rr + 1), equity=[float(x) for x in eq])


# ============================================================================
# 5. SCANNER
# ============================================================================
def buscar_setups(tf, P, top_n, recent):
    try:
        symbols = top_symbols(top_n)
    except Exception as e:
        st.error(f"Falha ao listar mercados: {type(e).__name__}: {str(e)[:140]}")
        return []
    out = []
    bar = st.progress(0.0, text="Escaneando...")
    min_len = max(60, 2 * P["length"] + P["atr_len"] + 5)
    for i, s in enumerate(symbols):
        bar.progress((i + 1) / len(symbols), text=f"Escaneando {s}")
        df = fetch_data(s, tf, limit=500)
        if len(df) < min_len:
            continue
        res = run_engine(df, trend_ma_for(df, s, tf, P), P)
        n = len(df)
        rec = [g for g in res["signals"] if g["idx"] >= n - recent]
        if rec:
            g = rec[-1]
            out.append(dict(symbol=s, dir=g["dir"], age=n - 1 - g["idx"], risk_pct=g["risk_pct"]))
    bar.empty()
    out.sort(key=lambda x: x["age"])
    return out


# ============================================================================
# 6. GRÁFICO
# ============================================================================
def fp(x):
    """Formata preço com ~6 dígitos significativos (serve de BTC a moedas micro-cap)."""
    if x is None or not np.isfinite(x) or x == 0:
        return "-"
    dec = int(min(12, max(2, 6 - np.floor(np.log10(abs(x))))))
    return f"{x:.{dec}f}"


def make_labels(df, tf):
    fmt = "%d/%m %H:%M" if tf_seconds(tf) < 86400 else "%d/%m/%y"
    ts = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert(TZ_LOCAL)
    return ts.dt.strftime(fmt).tolist()


def build_chart(df, labels, res, ma, P, sel):
    n = len(df)
    future = 40
    total = n + future
    x_labels = labels + [f"+{i}" for i in range(1, future + 1)]
    l = df["low"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    atr = res["atr"]

    series = [{
        "type": "candlestick",
        "data": df[["open", "close", "low", "high"]].values.tolist(),
        "itemStyle": {"color": "#00ffcc", "color0": "#ff4b4b", "borderColor": "#00ffcc", "borderColor0": "#ff4b4b"},
        "z": 2,
    }]

    # média de tendência
    if P["use_trend"] and not np.all(np.isnan(ma)):
        series.append({"type": "line", "silent": True, "symbol": "none", "z": 1,
                       "data": [None if np.isnan(x) else float(x) for x in ma],
                       "lineStyle": {"color": "#ff9800", "width": 1.5}})

    # níveis de liquidez próximos da ação (evita esticar o eixo Y com níveis antigos)
    last_atr = atr[~np.isnan(atr)][-1] if np.any(~np.isnan(atr)) else 0.0
    vis_lo = float(np.min(l[-150:])) - 3 * last_atr
    vis_hi = float(np.max(h[-150:])) + 3 * last_atr
    shown = [x for x in res["levels"]
             if x["state"] != "dropped" and vis_lo <= x["price"] <= vis_hi
             and (x["end"] is None or x["end"] >= n - 150)][-40:]
    for lv in shown:
        ended = lv["end"] is not None
        end = lv["end"] if ended else min(n - 1 + 8, total - 1)
        data = [None] * total
        for i in range(lv["start"], end + 1):
            data[i] = lv["price"]
        color = COL_BSL if lv["kind"] == "BSL" else COL_SSL
        series.append({"type": "line", "silent": True, "symbol": "none", "data": data,
                       "lineStyle": {"color": color, "width": 1, "type": "dotted" if ended else "dashed",
                                     "opacity": 0.45 if ended else 0.95}})

    # sinais (triângulos)
    buys = [s for s in res["signals"] if s["dir"] == "BUY"]
    sells = [s for s in res["signals"] if s["dir"] == "SELL"]
    series.append({"name": "BUY", "type": "scatter", "symbol": "triangle", "symbolSize": 12,
                   "itemStyle": {"color": COL_SSL}, "tooltip": {"formatter": "{b}"}, "z": 5,
                   "data": [{"value": [s["idx"], float(l[s["idx"]] - 0.6 * atr[s["idx"]])],
                             "name": f"BUY @ {fp(s['entry'])} | SL {fp(s['sl'])} | TP {fp(s['tp'])}"} for s in buys]})
    series.append({"name": "SELL", "type": "scatter", "symbol": "triangle", "symbolRotate": 180, "symbolSize": 12,
                   "itemStyle": {"color": COL_BSL}, "tooltip": {"formatter": "{b}"}, "z": 5,
                   "data": [{"value": [s["idx"], float(h[s["idx"]] + 0.6 * atr[s["idx"]])],
                             "name": f"SELL @ {fp(s['entry'])} | SL {fp(s['sl'])} | TP {fp(s['tp'])}"} for s in sells]})

    # projeção Entry / Stop / Take do sinal selecionado
    if sel is not None:
        end = sel["exit"] if sel["exit"] is not None else total - 1
        for name, price, color in (("ENTRY", sel["entry"], "#ffffff"), ("STOP", sel["sl"], COL_SL), ("TAKE", sel["tp"], COL_TP)):
            d = [None] * total
            for i in range(sel["idx"], end + 1):
                d[i] = float(price)
            d[end] = {"value": float(price),
                      "label": {"show": True, "formatter": f"{name} {fp(price)}", "color": color,
                                "position": "top", "fontSize": 10, "fontWeight": "bold"}}
            series.append({"type": "line", "silent": True, "symbol": "none", "data": d, "z": 4,
                           "lineStyle": {"color": color, "type": "dashed", "width": 1.5}})

    start = max(0.0, 100.0 * (1 - 160.0 / total))
    return {
        "backgroundColor": "#000000",
        "animation": False,
        "grid": {"left": 10, "right": 75, "top": 20, "bottom": 40},
        "tooltip": {"trigger": "item"},
        "xAxis": {"type": "category", "data": x_labels, "scale": True,
                  "axisLine": {"lineStyle": {"color": "#333"}}, "splitLine": {"show": False},
                  "axisLabel": {"color": "#666", "fontSize": 10}},
        "yAxis": {"scale": True, "position": "right", "axisLine": {"show": False},
                  "splitLine": {"lineStyle": {"color": "#111"}}, "axisLabel": {"color": "#888", "fontSize": 10}},
        "dataZoom": [{"type": "inside", "start": start, "end": 100}],
        "series": series,
    }


def equity_chart(eq):
    return {
        "backgroundColor": "#000000", "animation": False,
        "grid": {"left": 10, "right": 50, "top": 10, "bottom": 10},
        "xAxis": {"type": "category", "show": False, "data": list(range(len(eq)))},
        "yAxis": {"type": "value", "scale": True, "position": "right", "axisLine": {"show": False},
                  "splitLine": {"lineStyle": {"color": "#111"}}, "axisLabel": {"color": "#888", "fontSize": 10}},
        "series": [{"type": "line", "data": eq, "symbol": "none", "lineStyle": {"color": "#9b51e0", "width": 2},
                    "areaStyle": {"color": "rgba(155,81,224,0.15)"}}],
    }


# ============================================================================
# 7. INTERFACE
# ============================================================================
CSS = """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@700&family=JetBrains+Mono&display=swap');
    .stApp { background: #000000; }
    .header-container { text-align: center; padding: 20px; }
    .infinity-logo {
        font-size: 70px;
        background: linear-gradient(90deg, #00ffcc, #9b51e0);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-weight: bold;
        filter: drop-shadow(0px 0px 15px rgba(0, 255, 204, 0.3));
        line-height: 1;
    }
    .main-title {
        font-family: 'Orbitron', sans-serif;
        color: #ffffff; font-size: 24px; letter-spacing: 5px; margin-top: 5px;
    }
    .glass-panel { background: rgba(10, 10, 15, 0.95); border-radius: 12px; border: 1px solid #222; padding: 15px; }
    .info-table-container {
        margin-top: 15px;
        background: #0a0a0a;
        border-radius: 10px; border: 1px solid #333; padding: 15px;
    }
    .info-item { text-align: center; border-right: 1px solid #222; flex: 1; }
    .info-label { font-family: 'Orbitron'; font-size: 10px; color: #666; margin-bottom: 5px; }
    .info-value { font-family: 'JetBrains Mono'; font-size: 18px; color: #fff; font-weight: bold; }
    </style>
    <div class="header-container">
        <div class="infinity-logo">∞</div>
        <div class="main-title">INFINITY CRYPTO PRO</div>
    </div>
"""


def info_row(items):
    """items: lista de (label, valor, cor). Reaproveita o visual original da tabela de informações."""
    cells = ""
    for i, (label, value, color) in enumerate(items):
        style = "border:none" if i == len(items) - 1 else ""
        cells += (f'<div class="info-item" style="{style}"><div class="info-label">{label}</div>'
                  f'<div class="info-value" style="color:{color}">{value}</div></div>')
    return (f'<div class="info-table-container"><div style="display:flex;align-items:center;'
            f'justify-content:space-around;">{cells}</div></div>')


def main():
    st.set_page_config(page_title="INFINITY CRYPTO PRO", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    tfs = get_timeframes()
    col_side, col_main = st.columns([1.3, 5])

    # ------------------------------ painel lateral -------------------------------
    with col_side:
        tf = st.selectbox("TEMPO:", tfs, index=tfs.index("15m") if "15m" in tfs else 0)

        with st.expander("⚙️ PARÂMETROS"):
            st.caption("ESTRUTURA / SWEEP")
            length = st.number_input("Pivot length", 2, 30, 5)
            max_levels = st.number_input("Máx. níveis por lado", 1, 50, 10)
            wick_only = st.checkbox("Rejeição só por pavio", True)
            min_sweep_atr = st.number_input("Penetração mín. (×ATR)", 0.0, 2.0, 0.05, 0.01)
            st.caption("TENDÊNCIA")
            use_trend = st.checkbox("Filtro de tendência", True)
            ma_type = st.selectbox("Tipo da média", ["EMA", "SMA"])
            ma_len = st.number_input("Período da média", 5, 500, 200)
            ma_opts = ["Gráfico atual"] + [t for t in tfs if tf_seconds(t) > tf_seconds(tf)]
            ma_tf = st.selectbox("Timeframe da média", ma_opts, index=ma_opts.index("4h") if "4h" in ma_opts else 0)
            st.caption("RISCO / ALVO")
            rr = st.number_input("Risk/Reward (1:X)", 0.5, 10.0, 2.0, 0.5)
            sl_pad = st.number_input("Padding do SL (×ATR)", 0.0, 3.0, 0.3, 0.1)
            st.caption("QUALIDADE / CUSTOS")
            use_vol = st.checkbox("Exigir volume acima da média", False)
            vol_mult = st.number_input("Volume ≥ média ×", 0.5, 5.0, 1.2, 0.1)
            cost_pct = st.number_input("Custo round-trip (% do preço)", 0.0, 1.0, 0.10, 0.01)
            max_cost_r = st.number_input("Ignorar se custo > (R)", 0.01, 1.0, 0.15, 0.01)
            st.caption("SCANNER")
            top_n = st.number_input("Ativos no scan (top volume)", 10, 150, 40, 5)
            recent = st.number_input("Sinal nos últimos N candles", 1, 20, 3)

        P = dict(length=int(length), max_levels=int(max_levels), wick_only=wick_only, min_sweep_atr=float(min_sweep_atr),
                 use_trend=use_trend, ma_type=ma_type, ma_len=int(ma_len), ma_tf=ma_tf, rr=float(rr),
                 sl_pad_atr=float(sl_pad), atr_len=14, use_vol=use_vol, vol_mult=float(vol_mult),
                 cost_pct=float(cost_pct), max_cost_r=float(max_cost_r))

        if st.button("⚡ SCANNER"):
            st.session_state["setups"] = buscar_setups(tf, P, int(top_n), int(recent))
            st.session_state["scan_tf"] = tf

        selecionado = None
        setups = st.session_state.get("setups")
        if setups is not None:
            if setups:
                names = {x["symbol"]: f"{x['symbol'].split('/')[0]} {'▲' if x['dir'] == 'BUY' else '▼'} {x['dir']} · "
                                      f"{'agora' if x['age'] == 0 else str(x['age']) + 'c atrás'}" for x in setups}
                selecionado = st.radio("ATIVOS:", list(names), format_func=lambda s: names[s])
                st.caption(f"Scan feito em {st.session_state.get('scan_tf')}")
            else:
                st.caption("Nenhum sinal válido nos últimos candles.")
        manual = st.text_input("ATIVO MANUAL:", placeholder="BTC/USDT:USDT")
        if manual.strip():
            selecionado = manual.strip().upper()

    # ------------------------------ área principal -------------------------------
    with col_main:
        if not selecionado:
            st.info("Clique em ⚡ SCANNER ou digite um ativo manualmente (ex.: BTC/USDT:USDT).")
            return

        df = fetch_data(selecionado, tf, limit=1000)
        if df.empty:
            st.error(f"Sem dados para {selecionado} em {tf}. {st.session_state.get('fetch_err', '')}")
            return
        if len(df) < 2 * P["length"] + P["atr_len"] + 5:
            st.warning("Poucos candles para analisar este ativo/timeframe.")
            return

        ma = trend_ma_for(df, selecionado, tf, P)
        res = run_engine(df, ma, P)
        sigs = res["signals"]
        simulate(df, sigs)
        labels = make_labels(df, tf)

        # escolha do sinal exibido (mais recente por padrão)
        sel = None
        if sigs:
            order = list(range(len(sigs)))[::-1][:15]
            pick = st.selectbox("SINAL EXIBIDO:", order, format_func=lambda i: (
                f"{labels[sigs[i]['idx']]} · {sigs[i]['dir']} · {sigs[i]['result']}"))
            sel = sigs[pick]
        else:
            st.caption("Nenhum sweep válido neste ativo/timeframe com os filtros atuais.")

        st_echarts(options=build_chart(df, labels, res, ma, P, sel), height="600px", key=f"chart_{selecionado}_{tf}")

        if sel is not None:
            cor = COL_SSL if sel["dir"] == "BUY" else COL_BSL
            st.markdown(info_row([
                ("ASSET", selecionado, COL_TP),
                ("DIREÇÃO", sel["dir"], cor),
                ("ENTRY POINT", fp(sel["entry"]), "#fff"),
                ("STOP LOSS", fp(sel["sl"]), COL_SL),
                ("TAKE PROFIT", fp(sel["tp"]), COL_TP),
                ("RISCO", f"{sel['risk_pct']:.2f}%", "#fff"),
                ("CUSTO", f"{sel['cost_r']:.2f}R", "#fff"),
            ]), unsafe_allow_html=True)

        # ------------------------------ backtest -----------------------------------
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        stats = backtest_stats(sigs, P["rr"])
        if stats is None:
            st.caption(f"Backtest: nenhum trade fechado nos {len(df)} candles carregados. "
                       f"Sinais ignorados por custo: {res['skipped']}.")
        else:
            good = stats["exp"] > 0
            st.markdown(info_row([
                ("TRADES", str(stats["n"]), "#fff"),
                ("ACERTO", f"{stats['wr'] * 100:.1f}%", COL_TP if stats["wr"] > stats["be"] else COL_SL),
                ("ACERTO MÍN. P/ LUCRO", f"{stats['be'] * 100:.1f}%", "#888"),
                ("EXPECTATIVA", f"{stats['exp']:+.2f}R", COL_TP if good else COL_SL),
                ("PROFIT FACTOR", "∞" if np.isinf(stats["pf"]) else f"{stats['pf']:.2f}", COL_TP if stats["pf"] > 1 else COL_SL),
                ("TOTAL", f"{stats['total']:+.1f}R", COL_TP if stats["total"] > 0 else COL_SL),
                ("MAX DRAWDOWN", f"{stats['dd']:.1f}R", "#fff"),
            ]), unsafe_allow_html=True)
            st_echarts(options=equity_chart(stats["equity"]), height="160px", key=f"eq_{selecionado}_{tf}")
            if stats["n"] < 30:
                st.warning(f"Amostra pequena ({stats['n']} trades): o resultado não é conclusivo. "
                           "Teste em vários ativos e timeframes antes de operar.")
            st.caption(f"Backtest nos {len(df)} candles carregados · {stats['open']} trade(s) ainda em aberto (fora da conta) · "
                       f"{res['skipped']} sinal(is) ignorado(s) por custo · SL vence se SL e TP caírem no mesmo candle · "
                       "trades simulados de forma independente. Resultado passado não garante resultado futuro.")


if __name__ == "__main__":
    main()
