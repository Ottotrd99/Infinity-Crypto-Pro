import streamlit as st
import ccxt
import pandas as pd
import plotly.graph_objects as go

# --- CONFIGURAÇÃO DE AMBIENTE ---
st.set_page_config(page_title="INFINITY | TOP 10 PROFUNDO", layout="wide")

# ==========================================
#        SISTEMA DE LOGIN (GRATUITO)
# ==========================================
def check_password():
    """Retorna True se o usuário inseriu a senha correta."""
    def password_entered():
        # --- ALTERE SEU LOGIN E SENHA AQUI ---
        if st.session_state["username"] == "OttoTrader" and st.session_state["password"] == "MC=4e20$97":
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Limpa a senha da memória
            del st.session_state["username"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # Interface de Login
        st.markdown("<h2 style='text-align:center; color:#9b51e0;'>🛡️ ACESSO RESTRITO</h2>", unsafe_allow_html=True)
        col1, col2, col3 = st.columns([1,2,1])
        with col2:
            st.text_input("Usuário", key="username")
            st.text_input("Senha", type="password", key="password")
            st.button("Entrar no Sistema", on_click=password_entered)
        if "password_correct" in st.session_state:
            st.error("😕 Usuário ou senha incorretos")
        return False
    return True

# Se o login falhar, o código para aqui e não mostra nada abaixo
if not check_password():
    st.stop()

# ==========================================
#        RESTO DO SEU CÓDIGO (LÓGICA)
# ==========================================

st.markdown("""
    <style>
    .stApp { background: #000000; }
    .glass-panel { background: rgba(15, 15, 20, 0.95); border-radius: 12px; border: 1px solid #333; padding: 25px; }
    .info-box { background: rgba(0, 255, 204, 0.05); border-left: 5px solid #00ffcc; padding: 15px; }
    .stButton>button { background: #9b51e0; color: white; font-weight: bold; border-radius: 8px; }
    </style>
    """, unsafe_allow_html=True)

@st.cache_resource
def get_exchange(): return ccxt.mexc()

def fetch_data(symbol, timeframe='15m'):
    try:
        bars = get_exchange().fetch_ohlcv(symbol, timeframe=timeframe, limit=120)
        df = pd.DataFrame(bars, columns=['ts', 'open', 'high', 'low', 'close', 'vol'])
        return df
    except: return pd.DataFrame()

# --- LÓGICA DE SELEÇÃO TOP 10 (RANKING) ---
def buscar_melhores_setups(tf):
    markets = get_exchange().fetch_markets()
    symbols = [m['symbol'] for m in markets if m['linear'] and m['quote'] == 'USDT'][:100]
    melhores = []
    
    for s in symbols:
        df = fetch_data(s, tf)
        if df.empty: continue
        
        last_p = df['close'].iloc[-1]
        poi_p = df['high'].rolling(40).max().iloc[-1]
        dist = (poi_p - last_p) / last_p
        
        if 0.0005 < dist < 0.015:
            score = 1 - dist
            melhores.append({'symbol': s, 'poi': poi_p, 'score': score})
            
    return sorted(melhores, key=lambda x: x['score'], reverse=True)[:10]

# --- UI PRINCIPAL ---
st.markdown("<h1 style='text-align:center; color:#9b51e0;'>🛡️ INFINITY GENESIS: QUEBRA PROFUNDA</h1>", unsafe_allow_html=True)

col1, col2 = st.columns([1, 3])

with col1:
    tf = st.selectbox("🕒 TIME FRAME:", ["1m", "3m", "5m", "15m"], index=2)
    if st.button("⚡ ESCANEAR E FILTRAR TOP 10"):
        st.session_state['top_10_setups'] = buscar_melhores_setups(tf)

if 'top_10_setups' in st.session_state:
    with col1:
        selecionado = st.radio("🎯 SELECIONE O ATIVO:", [x['symbol'] for x in st.session_state['top_10_setups']])
    
    with col2:
        df = fetch_data(selecionado, tf)
        poi_v = next(item['poi'] for item in st.session_state['top_10_setups'] if item['symbol'] == selecionado)
        last_v = df['close'].iloc[-1]
        
        stop_v = poi_v * 1.0025    
        entry_v = poi_v            
        choch_v = entry_v - (abs(stop_v - entry_v) * 1.5) 
        take_v = entry_v - (abs(stop_v - entry_v) * 4.0)

        st.markdown("<div class='glass-panel'>", unsafe_allow_html=True)
        
        idx = len(df) - 1
        x_pts = [idx, idx+4, idx+10, idx+15, idx+25] 
        y_pts = [last_v, stop_v * 0.999, choch_v, entry_v, take_v]

        fig = go.Figure()
        fig.add_trace(go.Candlestick(x=df.index, open=df['open'], high=df['high'], low=df['low'], close=df['close'], opacity=0.2))

        fig.add_trace(go.Scatter(x=x_pts[:4], y=y_pts[:4], mode='lines+markers',
                                 line=dict(color='#ff4b4b', width=3, dash='dot'),
                                 marker=dict(size=8, color='#ff4b4b', symbol='circle-open')))
        
        fig.add_trace(go.Scatter(x=x_pts[3:], y=y_pts[3:], mode='lines',
                                 line=dict(color='#00ffcc', width=10)))

        for val, txt, clr in [(stop_v, "STOP LOSS", "#ff4b4b"), 
                               (entry_v, "ENTRY", "white"), 
                               (take_v, "TAKE PROFIT", "#00ffcc")]:
            fig.add_shape(type="line", x0=idx, y0=val, x1=idx+30, y1=val, line=dict(color=clr, width=1, dash="dash"))
            fig.add_annotation(x=idx+30, y=val, text=f"<b>{txt}</b>", showarrow=False, font=dict(color=clr), xanchor="left")

        fig.update_layout(template="plotly_dark", height=600, paper_bgcolor='black', plot_bgcolor='black', xaxis_visible=False, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

        st.markdown(f"""
        <div class='info-box'>
            <p style='color:#00ffcc; margin:0;'><b>ESTRATÉGIA: {selecionado} - BEARISH POI + CHoCH PROFUNDO</b></p>
            <table style='width:100%; color:white;'>
                <tr>
                    <td><b>ENTRADA:</b> {entry_v:.4f}</td>
                    <td><b>STOP:</b> {stop_v:.4f}</td>
                    <td><b>ALVO:</b> {take_v:.4f}</td>
                </tr>
            </table>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)