import streamlit as st
import ccxt
import pandas as pd
from streamlit_echarts import st_echarts

# --- CONFIGURAÇÃO DE AMBIENTE ---
st.set_page_config(page_title="INFINITY CRYPTO PRO", layout="wide")

# CSS Mantido Original
st.markdown("""
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
    """, unsafe_allow_html=True)

@st.cache_resource
def get_exchange(): 
    # Timeout adicionado para evitar que o worker do Streamlit Cloud trave
    return ccxt.mexc({'timeout': 30000, 'enableRateLimit': True})

def fetch_data(symbol, timeframe, limit=1000):
    try:
        bars = get_exchange().fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(bars, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        df['ts_display'] = pd.to_datetime(df['ts'], unit='ms').dt.strftime('%H:%M')
        return df
    except Exception as e:
        return pd.DataFrame()

def buscar_setups():
    try:
        exchange = get_exchange()
        markets = exchange.fetch_markets()
        symbols = [m['symbol'] for m in markets if m.get('linear') and m.get('quote') == 'USDT'][:100]
        melhores = []
        
        p_bar = st.progress(0)
        for i, s in enumerate(symbols):
            p_bar.progress((i + 1) / 100)
            df_h1 = fetch_data(s, '1h', limit=80)
            if df_h1.empty: continue
            last_p = df_h1['close'].iloc[-1]
            topo = df_h1['high'].rolling(40).max().iloc[-1]
            fundo = df_h1['low'].rolling(40).min().iloc[-1]
            
            if 0 < (topo - last_p) / last_p < 0.009:
                 melhores.append({'symbol': s, 'poi': topo, 'tipo': 'SUPPLY'})
            elif 0 < (last_p - fundo) / last_p < 0.009:
                 melhores.append({'symbol': s, 'poi': fundo, 'tipo': 'DEMAND'})
        p_bar.empty()
        return melhores
    except:
        return []

col_side, col_main = st.columns([1, 5])

with col_side:
    st.markdown("<div class='glass-panel'>", unsafe_allow_html=True)
    tf = st.selectbox("TEMPO:", ["1m", "5m", "15m"], index=1)
    if st.button("⚡ SCANNER"):
        st.session_state['setups'] = buscar_setups()

    selecionado = None
    if 'setups' in st.session_state and st.session_state['setups']:
        opcoes = [x['symbol'] for x in st.session_state['setups']]
        escolha = st.radio("ATIVOS:", opcoes)
        setup = next((item for item in st.session_state['setups'] if item["symbol"] == escolha), None)
        if setup:
            selecionado = setup['symbol']
    st.markdown("</div>", unsafe_allow_html=True)

if selecionado:
    with col_main:
        df = fetch_data(selecionado, tf)
        if not df.empty:
            poi = setup['poi']
            last_v = df['close'].iloc[-1]
            distancia = poi * 0.002 
            
            if setup['tipo'] == 'SUPPLY':
                stop, entry, take, choch = poi + distancia, poi, poi - (distancia * 5), poi - (distancia * 1.5)
                cor_path = '#ff4b4b'
            else:
                stop, entry, take, choch = poi - distancia, poi, poi + (distancia * 5), poi + (distancia * 1.5)
                cor_path = '#00ffcc'

            dates = df['ts_display'].tolist()
            future_dates = [f"F{i}" for i in range(1, 61)]
            full_dates = dates + future_dates
            candlestick_data = df[['open', 'close', 'low', 'high']].values.tolist()
            
            path_data = [None] * (len(dates) - 1)
            path_data.extend([last_v, stop, choch, entry, take])

            options = {
                "backgroundColor": "#000000",
                "xAxis": {"type": "category", "data": full_dates, "scale": True, "axisLine": {"lineStyle": {"color": "#333"}}, "splitLine": {"show": False}},
                "yAxis": {
                    "scale": True, "position": "right", 
                    "axisLine": {"show": False}, "splitLine": {"lineStyle": {"color": "#111"}},
                    "axisLabel": {"color": "#888", "fontSize": 10},
                    "min": "dataMin", "max": "dataMax"
                },
                "dataZoom": [{"type": "inside", "start": 90, "end": 100}],
                "series": [
                    {
                        "type": "candlestick",
                        "data": candlestick_data,
                        "itemStyle": {"color": "#00ffcc", "color0": "#ff4b4b", "borderColor": "#00ffcc", "borderColor0": "#ff4b4b"},
                        "markLine": {
                            "symbol": ["none", "none"],
                            "precision": 6,
                            "label": {"position": "end", "color": "inherit", "fontWeight": "bold", "fontSize": 10},
                            "data": [
                                {"yAxis": poi, "lineStyle": {"color": "yellow", "width": 2}, "label": {"formatter": "POI"}},
                                {"yAxis": stop, "lineStyle": {"color": "#ff4b4b", "type": "dashed"}, "label": {"formatter": "STOP"}},
                                {"yAxis": entry, "lineStyle": {"color": "#ffffff", "type": "dashed"}, "label": {"formatter": "ENTRY"}},
                                {"yAxis": take, "lineStyle": {"color": "#00ffcc", "type": "dashed"}, "label": {"formatter": "TAKE"}}
                            ]
                        }
                    },
                    {
                        "name": "SMC Path",
                        "type": "line",
                        "data": path_data,
                        "smooth": False,
                        "lineStyle": {"color": cor_path, "width": 2, "type": "dotted"},
                        "symbol": "circle", "symbolSize": 6, "itemStyle": {"color": cor_path}
                    }
                ]
            }

            st_echarts(options=options, height="600px", key=f"chart_{selecionado}")

            st.markdown(f"""
            <div class="info-table-container">
                <div style="display: flex; align-items: center; justify-content: space-around;">
                    <div class="info-item" style="border:none">
                        <div class="info-label">ASSET</div>
                        <div class="info-value" style="color:#00ffcc">{selecionado}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">ENTRY POINT</div>
                        <div class="info-value">{entry:.6f}</div>
                    </div>
                    <div class="info-item">
                        <div class="info-label">STOP LOSS</div>
                        <div class="info-value" style="color:#ff4b4b">{stop:.6f}</div>
                    </div>
                    <div class="info-item" style="border:none">
                        <div class="info-label">TAKE PROFIT</div>
                        <div class="info-value" style="color:#00ffcc">{take:.6f}</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)
