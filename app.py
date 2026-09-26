import streamlit as st
import requests
import pandas as pd
import math
import plotly.express as px
from datetime import datetime, timedelta

# --- UI Configuration ---
st.set_page_config(page_title="Kalshi Weather Sentinel | Core Engine V2", page_icon="⛈️", layout="wide")
st.title("⛈️ Quantitative Weather Arbitrage Scanner V2")
st.markdown("81-Member Super-Ensemble (ECMWF + NOAA GEFS) vs. True Kalshi Order Book Spreads")

# --- Institutional Settlement Coordinates & Timezones ---
# NWS stations settle strictly on local time midnight-to-midnight. UTC ruins the model.
STATIONS = {
    "Chicago": {"lat": 41.7868, "lon": -87.7522, "airport": "Midway (KMDW)", "ticker": "KXHIGHCHI", "tz": "America/Chicago"},
    "New York": {"lat": 40.7829, "lon": -73.9654, "airport": "Central Park (KNYC)", "ticker": "KXHIGHNY", "tz": "America/New_York"},
    "Austin": {"lat": 30.1945, "lon": -97.6699, "airport": "Bergstrom (KAUS)", "ticker": "KXHIGHAUS", "tz": "America/Chicago"},
    "Miami": {"lat": 25.7959, "lon": -80.2870, "airport": "Miami Int (KMIA)", "ticker": "KXHIGHMIA", "tz": "America/New_York"}
}

# --- Sidebar Parameters ---
with st.sidebar:
    st.header("⚙️ Risk & Execution Parameters")
    bankroll = st.number_input("Total Bankroll ($)", min_value=100, value=1000, step=100)
    min_edge = st.slider("Minimum Edge Required (%)", min_value=2, max_value=20, value=6, step=1)
    
    st.markdown("---")
    st.markdown("**Quarter-Kelly Constraints:**")
    st.markdown("Position sizing is strictly capped at 25% of the theoretical Kelly Criterion to prevent ruin during model variance.")

# --- Helper Functions ---
@st.cache_data(ttl=900, show_spinner=False)
def fetch_super_ensemble(lat, lon, tz):
    """Pulls an 81-member blended super-ensemble (50 ECMWF + 31 NOAA GEFS)."""
    try:
        url = (
            f"https://ensemble-api.open-meteo.com/v1/ensemble?"
            f"latitude={lat}&longitude={lon}&daily=temperature_2m_max&"
            f"models=ecmwf_ifs025,gfs_seamless&temperature_unit=fahrenheit&timezone={tz}"
        )
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return None

@st.cache_data(ttl=60, show_spinner=False)
def fetch_kalshi_markets(series_ticker):
    """Pulls live public markets from Kalshi using the primary public routing gateway."""
    try:
        url = f"https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker={series_ticker}"
        headers = {"Accept": "application/json"}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("markets", [])
    except Exception:
        pass
    return []

def calculate_quarter_kelly(prob_model, execution_price_cents, bankroll):
    p = prob_model / 100.0
    q = 1.0 - p
    b = (100.0 - execution_price_cents) / execution_price_cents 
    
    if p * 100 <= execution_price_cents or b <= 0:
        return 0.0
        
    kelly_fraction = (b * p - q) / b
    quarter_kelly = kelly_fraction * 0.25
    return min(bankroll * quarter_kelly, bankroll * 0.10)

# --- Core Execution ---
if st.button("📡 Execute High-Fidelity Market Scan", type="primary", use_container_width=True):
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    results = []
    # Target date: tomorrow
    target_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    for idx, (city, coords) in enumerate(STATIONS.items()):
        status_text.text(f"Querying 81-Member Super-Ensemble & Kalshi Order Books for {city}...")
        
        # 1. Fetch High-Fidelity Weather Data
        ensemble_data = fetch_super_ensemble(coords["lat"], coords["lon"], coords["tz"])
        if not ensemble_data or "daily" not in ensemble_data:
            continue
            
        daily = ensemble_data["daily"]
        time_list = daily.get("time", [])
        
        if target_date in time_list:
            date_idx = time_list.index(target_date)
            member_temps = []
            
            # Extract all 81 members (ECMWF + GFS)
            for key in daily.keys():
                if key.startswith("temperature_2m_max_member"):
                    temp = daily[key][date_idx]
                    if temp is not None:
                        member_temps.append(temp)
                        
            if len(member_temps) < 10:
                continue
                
            baseline_high = sum(member_temps) / len(member_temps)
            target_strike = math.floor(baseline_high) + 1 
            
            members_above_strike = sum(1 for t in member_temps if t >= target_strike)
            prob_model = (members_above_strike / len(member_temps)) * 100
            
            # 2. Extract True Kalshi Order Book Data
            kalshi_markets = fetch_kalshi_markets(coords["ticker"])
            best_bid = 0
            best_ask = 100
            market_found = False
            
            for m in kalshi_markets:
                subtitle = m.get("subtitle", "")
                # Find the bracket that matches our target strike
                if str(target_strike) in subtitle and ("or above" in subtitle or "to" in subtitle):
                    best_bid = m.get("yes_bid", 0)
                    best_ask = m.get("yes_ask", 100)
                    market_found = True
                    break
            
            if not market_found:
                # If Kalshi hasn't opened tomorrow's exact brackets yet, we skip. No fake data.
                continue
                
            # 3. Maker vs Taker Execution Logic
            spread = best_ask - best_bid
            if spread > 1 and best_bid > 0:
                # Wide spread: front-run the bid to act as Maker and save fees
                exec_price = best_bid + 1
                exec_type = "Maker (Resting Limit)"
            else:
                # Tight spread: hit the ask
                exec_price = best_ask
                exec_type = "Taker (Market Order)"
                
            edge = prob_model - exec_price
            
            if edge >= min_edge:
                bet_size = calculate_quarter_kelly(prob_model, exec_price, bankroll)
                verdict = f"🟢 BUY YES @ {exec_price}¢"
            elif edge <= -min_edge:
                # Invert logic for the NO side
                no_prob = 100 - prob_model
                no_price = 100 - best_bid # buying NO means hitting the yes_bid
                bet_size = calculate_quarter_kelly(no_prob, no_price, bankroll)
                verdict = f"🔴 BUY NO @ {no_price}¢"
            else:
                bet_size = 0.0
                verdict = "⚠️ PASS (Edge Too Narrow)"
                
            results.append({
                "Target Market": f"{city} ≥ {target_strike}°F",
                "Spread (Bid/Ask)": f"{best_bid}¢ / {best_ask}¢",
                "Model P(True)": f"{prob_model:.1f}%",
                "Execution Target": f"{exec_price}¢ [{exec_type}]",
                "True Edge": f"{edge:+.1f}%",
                "Quarter-Kelly": f"${bet_size:,.2f}" if bet_size > 0 else "$0.00",
                "Verdict": verdict,
                "_raw_edge": abs(edge),
                "_member_temps": member_temps
            })
            
        progress_bar.progress((idx + 1) / len(STATIONS))
        
    status_text.empty()
    progress_bar.empty()
    
    if results:
        df = pd.DataFrame(results).sort_values(by="_raw_edge", ascending=False)
        st.markdown("### 🔍 Institutional Edge Matrix")
        st.dataframe(df.drop(columns=["_raw_edge", "_member_temps"]), use_container_width=True, hide_index=True)
        
        st.markdown("---")
        st.subheader("📊 81-Member Super-Ensemble Distribution")
        
        top_opportunity = df.iloc[0]
        temps = top_opportunity["_member_temps"]
        
        fig = px.histogram(
            x=temps, 
            nbins=20, 
            title=f"ECMWF + GEFS Spread: {top_opportunity['Target Market']}",
            labels={"x": "Projected High Temperature (°F)", "y": "Number of Ensemble Members"},
            color_discrete_sequence=["#00BFFF"]
        )
        fig.add_vline(x=float(top_opportunity['Target Market'].split("≥")[1].split("°")[0]), line_dash="dash", line_color="#FF4B4B", annotation_text="Kalshi Strike", annotation_position="top right")
        fig.update_layout(template="plotly_dark", height=400)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("No active Kalshi order books found for tomorrow's weather markets yet. Market makers typically populate the books later in the evening.")
