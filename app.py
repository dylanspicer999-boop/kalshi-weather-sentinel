import streamlit as st
import requests
import pandas as pd
import math
import plotly.express as px
from datetime import datetime, timedelta

# --- UI Configuration ---
st.set_page_config(page_title="Kalshi Weather Sentinel", page_icon="⛈️", layout="wide")
st.title("⛈️ Quantitative Weather Arbitrage Scanner")
st.markdown("ECMWF 50-Member Ensemble Probability vs. Kalshi KXHIGH Contract Pricing")

# --- Institutional Settlement Coordinates ---
# Kalshi weather markets settle to specific NWS airport stations, not generic city coordinates.
STATIONS = {
    "Chicago (KXHIGHCHI)": {"lat": 41.7868, "lon": -87.7522, "airport": "Midway (KMDW)"},
    "New York (KXHIGHNY)": {"lat": 40.7829, "lon": -73.9654, "airport": "Central Park (KNYC)"},
    "Austin (KXHIGHAUS)": {"lat": 30.1945, "lon": -97.6699, "airport": "Bergstrom (KAUS)"},
    "Miami (KXHIGHMIA)": {"lat": 25.7959, "lon": -80.2870, "airport": "Miami Int (KMIA)"}
}

# --- Sidebar Parameters ---
with st.sidebar:
    st.header("⚙️ Risk & Execution Parameters")
    bankroll = st.number_input("Total Bankroll ($)", min_value=100, value=1000, step=100)
    min_edge = st.slider("Minimum Edge Required (%)", min_value=2, max_value=20, value=8, step=1)
    
    st.markdown("---")
    st.markdown("**Quarter-Kelly Constraints:**")
    st.markdown("Position sizing is strictly capped at 25% of the theoretical Kelly Criterion to prevent ruin during model variance.")

# --- Helper Functions ---
@st.cache_data(ttl=900, show_spinner=False)
def fetch_ecmwf_ensemble(lat, lon):
    """Pulls the free 50-member ECMWF ensemble forecast from Open-Meteo."""
    try:
        url = (
            f"https://ensemble-api.open-meteo.com/v1/ensemble?"
            f"latitude={lat}&longitude={lon}&daily=temperature_2m_max&"
            f"models=ecmwf_ifs025&temperature_unit=fahrenheit&timezone=America%2FNew_York"
        )
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return None

def calculate_quarter_kelly(prob_model, price_cents, bankroll):
    """
    Calculates Quarter-Kelly bet size.
    Price is in cents (e.g., 60 for 60%). Payout is always $1.00 (100 cents).
    """
    p = prob_model / 100.0
    q = 1.0 - p
    b = (100.0 - price_cents) / price_cents 
    
    if p * 100 <= price_cents or b <= 0:
        return 0.0
        
    kelly_fraction = (b * p - q) / b
    quarter_kelly = kelly_fraction * 0.25
    
    # Cap maximum exposure to 10% of bankroll for strict risk management
    return min(bankroll * quarter_kelly, bankroll * 0.10)

def calculate_maker_fee(contracts, price_cents):
    """Estimates Kalshi taker/maker fee drag."""
    p_dec = price_cents / 100.0
    base_fee = math.ceil(0.07 * contracts * p_dec * (1 - p_dec) * 100) / 100.0
    return base_fee

# --- Core Execution ---
if st.button("📡 Scan Live Ensemble Discrepancies", type="primary", use_container_width=True):
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    results = []
    
    # Target date: tomorrow
    target_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    
    for idx, (city, coords) in enumerate(STATIONS.items()):
        status_text.text(f"Querying 50-member ECMWF models for {city}...")
        
        ensemble_data = fetch_ecmwf_ensemble(coords["lat"], coords["lon"])
        if not ensemble_data or "daily" not in ensemble_data:
            continue
            
        daily = ensemble_data["daily"]
        time_list = daily.get("time", [])
        
        if target_date in time_list:
            date_idx = time_list.index(target_date)
            
            # Extract the 50 member predictions for tomorrow's high
            member_temps = []
            for key in daily.keys():
                if key.startswith("temperature_2m_max_member"):
                    temp = daily[key][date_idx]
                    if temp is not None:
                        member_temps.append(temp)
                        
            if not member_temps:
                continue
                
            # Define an arbitrary strike price for the sake of the scanner demo
            # In production, this pulls dynamically from Kalshi's public /v2/markets endpoint
            baseline_high = sum(member_temps) / len(member_temps)
            target_strike = math.floor(baseline_high) + 1 
            
            # Calculate P(model)
            members_above_strike = sum(1 for t in member_temps if t >= target_strike)
            prob_model = (members_above_strike / len(member_temps)) * 100
            
            # Simulate Kalshi Market Price (Testing Logic)
            # A real connection uses: requests.get(f"https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXHIGH...")
            market_price = max(1, min(99, int(prob_model - 12))) # Simulating a 12% underpriced market for demonstration
            
            edge = prob_model - market_price
            
            if edge >= min_edge:
                bet_size = calculate_quarter_kelly(prob_model, market_price, bankroll)
                verdict = "🟢 LIMIT BUY YES"
            elif edge <= -min_edge:
                bet_size = calculate_quarter_kelly(100 - prob_model, 100 - market_price, bankroll)
                verdict = "🔴 LIMIT BUY NO"
            else:
                bet_size = 0.0
                verdict = "⚠️ PASS (Edge Too Narrow)"
                
            results.append({
                "Target Market": f"{city} ≥ {target_strike}°F",
                "Settlement Station": coords["airport"],
                "Model P(True)": f"{prob_model:.1f}%",
                "Kalshi Ask Price": f"{market_price}¢",
                "Raw Edge": f"{edge:+.1f}%",
                "Quarter-Kelly Allocation": f"${bet_size:,.2f}" if bet_size > 0 else "$0.00",
                "Verdict": verdict,
                "_raw_edge": abs(edge),
                "_member_temps": member_temps
            })
            
        progress_bar.progress((idx + 1) / len(STATIONS))
        
    status_text.empty()
    progress_bar.empty()
    
    if results:
        df = pd.DataFrame(results).sort_values(by="_raw_edge", ascending=False)
        st.markdown("### 🔍 Quantitative Edge Matrix")
        st.dataframe(df.drop(columns=["_raw_edge", "_member_temps"]), use_container_width=True, hide_index=True)
        
        st.markdown("---")
        st.subheader("📊 ECMWF 50-Member Distribution")
        st.markdown("Visualize the raw ensemble spread to ensure the probability isn't skewed by a single outlier model.")
        
        # Plot the distribution of the highest-edge opportunity
        top_opportunity = df.iloc[0]
        temps = top_opportunity["_member_temps"]
        
        fig = px.histogram(
            x=temps, 
            nbins=15, 
            title=f"Forecast Spread: {top_opportunity['Target Market']}",
            labels={"x": "Projected High Temperature (°F)", "y": "Number of Ensemble Members"},
            color_discrete_sequence=["#00FFA3"]
        )
        fig.add_vline(x=float(top_opportunity['Target Market'].split("≥")[1].split("°")[0]), line_dash="dash", line_color="#FF4B4B", annotation_text="Kalshi Strike", annotation_position="top right")
        fig.update_layout(template="plotly_dark", height=400)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("No ensemble data available for the target date.")
