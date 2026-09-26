import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import pytz

st.set_page_config(page_title="Kalshi Weather V3 | Diurnal Sniper", page_icon="🎯", layout="wide")
st.title("🎯 Diurnal Weather Sniper V3")
st.markdown("Settlement-Lag Arbitrage via AviationWeather METAR & Kalshi Limit Books")

# --- V3 Station Data ---
STATIONS = {
    "Chicago": {"icao": "KMDW", "ticker": "KXHIGHCHI", "tz": "America/Chicago"},
    "New York": {"icao": "KNYC", "ticker": "KXHIGHNY", "tz": "America/New_York"},
    "Austin": {"icao": "KAUS", "ticker": "KXHIGHAUS", "tz": "America/Chicago"},
    "Miami": {"icao": "KMIA", "ticker": "KXHIGHMIA", "tz": "America/New_York"}
}

with st.sidebar:
    st.header("⚙️ Sniper Parameters")
    bankroll = st.number_input("Total Bankroll ($)", min_value=100, value=1000, step=100)
    max_no_price = st.slider("Max 'NO' Bid Limit (¢)", min_value=70, max_value=95, value=93, step=1, help="Do not buy tails above 95¢. Kelly sizing becomes toxic.")
    temp_drop_threshold = st.slider("Required Temp Drop from Peak (°C)", min_value=0.5, max_value=3.0, value=1.0, step=0.1)

# --- V3 API Handlers ---
@st.cache_data(ttl=300, show_spinner=False)
def fetch_metar_data(icao):
    """Pulls the last 12 hours of FAA ASOS readings directly from aviationweather.gov"""
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={icao}&format=json&hours=12"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return []

@st.cache_data(ttl=60, show_spinner=False)
def fetch_kalshi_markets(series_ticker):
    """Pulls live Kalshi order books via the primary elections gateway"""
    try:
        url = f"https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker={series_ticker}"
        headers = {"Accept": "application/json"}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("markets", [])
    except Exception:
        pass
    return []

def celsius_to_fahrenheit(c):
    return (c * 9/5) + 32

def calculate_quarter_kelly(prob_model, execution_price_cents, bankroll):
    p = prob_model / 100.0
    q = 1.0 - p
    b = (100.0 - execution_price_cents) / execution_price_cents 
    if p * 100 <= execution_price_cents or b <= 0: return 0.0
    return min(bankroll * ((b * p - q) / b) * 0.25, bankroll * 0.10)

# --- Core Execution ---
if st.button("🔭 Scan Diurnal Lock-Ins", type="primary", use_container_width=True):
    progress = st.progress(0)
    results = []
    
    for idx, (city, data) in enumerate(STATIONS.items()):
        local_tz = pytz.timezone(data["tz"])
        local_time = datetime.now(local_tz)
        
        # 1. Process METAR Data (True settlement conditions)
        metar_logs = fetch_metar_data(data["icao"])
        if not metar_logs:
            continue
            
        temps_c = [log.get("temp") for log in metar_logs if log.get("temp") is not None]
        if not temps_c:
            continue
            
        running_high_c = max(temps_c)
        running_high_f = round(celsius_to_fahrenheit(running_high_c), 1)
        
        current_temp_c = temps_c[0] # The most recent observation is first
        current_temp_f = round(celsius_to_fahrenheit(current_temp_c), 1)
        
        # Check Diurnal Rules: Must be afternoon AND dropping
        is_afternoon = local_time.hour >= 14 # After 2:00 PM local
        is_dropping = (running_high_c - current_temp_c) >= temp_drop_threshold
        
        locked_in = is_afternoon and is_dropping
        
        # 2. Parse Kalshi Order Book
        kalshi_markets = fetch_kalshi_markets(data["ticker"])
        
        for m in kalshi_markets:
            floor_strike = m.get("floor_strike")
            if not floor_strike:
                continue
                
            # If the Kalshi strike is HIGHER than today's physical running high, and temps are dropping...
            # The YES side is mathematically dead. The NO side is a lock.
            if floor_strike > running_high_f:
                
                # Extract live cents from dollar strings
                yes_bid = int(round(float(m.get("yes_bid_dollars") or "0") * 100))
                yes_ask = int(round(float(m.get("yes_ask_dollars") or "1") * 100))
                
                # Buying NO means hitting the YES bid. To be a maker, we front-run the bid.
                # If YES bid is 10¢, the NO ask is 90¢. We bid 89¢ NO (which means placing a 11¢ YES ask).
                no_maker_price = (100 - yes_bid) - 1
                
                if locked_in and no_maker_price <= max_no_price and no_maker_price > 0:
                    bet_size = calculate_quarter_kelly(99.0, no_maker_price, bankroll)
                    verdict = f"🔴 LIMIT BUY 'NO' @ {no_maker_price}¢"
                else:
                    bet_size = 0.0
                    if not locked_in:
                        verdict = "⏳ Waiting for Sun/Temp Drop"
                    else:
                        verdict = "⚠️ PASS (Too Expensive / Fee Trap)"
                
                results.append({
                    "Market": f"{city} ≥ {floor_strike}°F",
                    "Local Time": local_time.strftime("%I:%M %p"),
                    "Running High": f"{running_high_f}°F",
                    "Current Temp": f"{current_temp_f}°F",
                    "Target Entry": f"{no_maker_price}¢",
                    "Quarter-Kelly": f"${bet_size:,.2f}" if bet_size > 0 else "$0.00",
                    "Verdict": verdict
                })
                
        progress.progress((idx + 1) / len(STATIONS))
        
    progress.empty()
    
    if results:
        df = pd.DataFrame(results)
        st.markdown("### 🔭 Diurnal Sniper Matrix")
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No active markets align with current diurnal metrics.")
    
