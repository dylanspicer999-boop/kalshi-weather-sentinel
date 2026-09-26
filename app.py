import streamlit as st
import requests
import pandas as pd
import math
from datetime import datetime, timedelta

# Fallback-safe timezone importing
try:
    import pytz
    def get_station_time(tz_name):
        return datetime.now(pytz.timezone(tz_name))
except ImportError:
    from zoneinfo import ZoneInfo
    def get_station_time(tz_name):
        return datetime.now(ZoneInfo(tz_name))

st.set_page_config(page_title="Kalshi Weather V4 | Quantitative Sniper", page_icon="🎯", layout="wide")
st.title("🎯 Quantitative Diurnal Sniper V4")
st.markdown("Advanced Settlement-Lag Engine: Convective Filtering, Advection Checks & Strike Decay Math")

# --- Institutional Settlement Stations ---
STATIONS = {
    "Chicago": {"icao": "KMDW", "ticker": "KXHIGHCHI", "tz": "America/Chicago"},
    "New York": {"icao": "KNYC", "ticker": "KXHIGHNY", "tz": "America/New_York"},
    "Austin": {"icao": "KAUS", "ticker": "KXHIGHAUS", "tz": "America/Chicago"},
    "Miami": {"icao": "KMIA", "ticker": "KXHIGHMIA", "tz": "America/New_York"}
}

# --- Sidebar Execution Parameters ---
with st.sidebar:
    st.header("⚙️ Sniper Parameters")
    bankroll = st.number_input("Total Bankroll ($)", min_value=100, value=1000, step=100)
    
    target_scan_day = st.radio("Target Expiration Date", ["Today (Diurnal Lock-Ins)", "Tomorrow (Early Setup)"])
    
    max_no_price = st.slider(
        "Max 'NO' Bid Limit (¢)", 
        min_value=70, max_value=95, value=93, step=1, 
        help="Hard ceiling. At 96¢-99¢, fee drag and Kelly tail-risk balloon to toxic levels."
    )
    temp_drop_threshold = st.slider(
        "Required Temp Drop from Peak (°F)", 
        min_value=0.5, max_value=4.0, value=1.5, step=0.5,
        help="Confirms diurnal cycle has peaked and solar heating is dead."
    )
    lambda_decay = st.slider(
        "Strike Decay Lambda (λ)",
        min_value=0.5, max_value=2.0, value=1.2, step=0.1,
        help="Controls how aggressively probability scales based on the degree buffer."
    )

# --- High-Frequency Data Pipelines ---
@st.cache_data(ttl=120, show_spinner=False)
def fetch_metar_observations(icao):
    """Fetches official FAA/ASOS station observations from aviationweather.gov."""
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={icao}&format=json&hours=12"
        res = requests.get(url, timeout=8)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return []

@st.cache_data(ttl=30, show_spinner=False)
def fetch_kalshi_markets(series_ticker):
    """Queries live public order books directly from Kalshi's election-grade cluster."""
    try:
        url = f"https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker={series_ticker}&status=open"
        headers = {"Accept": "application/json"}
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            return res.json().get("markets", [])
    except Exception:
        pass
    return []

def celsius_to_fahrenheit(c):
    return (c * 9.0 / 5.0) + 32.0

def calculate_dynamic_prob(strike, running_high, lambda_val):
    buffer = strike - running_high
    if buffer <= 0: 
        return 0.0 
    decay_factor = math.exp(-lambda_val * buffer)
    return min((1.0 - decay_factor) * 100, 99.0)

def calculate_quarter_kelly(win_prob, execution_price_cents, bankroll):
    p = win_prob / 100.0
    q = 1.0 - p
    b = (100.0 - execution_price_cents) / execution_price_cents
    if p * 100 <= execution_price_cents or b <= 0:
        return 0.0
    full_kelly = (b * p - q) / b
    return min(bankroll * (full_kelly * 0.25), bankroll * 0.10)

# --- Core Execution Engine ---
if st.button("📡 Execute Quantitative Scan", type="primary", use_container_width=True):
    progress = st.progress(0)
    status_box = st.empty()
    results = []

    for idx, (city, data) in enumerate(STATIONS.items()):
        status_box.text(f"Auditing METAR physics & order books for {city}...")
        station_time = get_station_time(data["tz"])
        
        if "Today" in target_scan_day:
            target_date_str = station_time.strftime("%y%b%d").upper()
            is_today = True
        else:
            target_date_str = (station_time + timedelta(days=1)).strftime("%y%b%d").upper()
            is_today = False
        
        # 1. Ingest Airport Observation History
        metar_data = fetch_metar_observations(data["icao"])
        if not metar_data:
            continue
            
        temps_f = [
            round(celsius_to_fahrenheit(entry["temp"]), 1)
            for entry in metar_data 
            if entry.get("temp") is not None
        ]
        
        if not temps_f:
            continue
            
        latest_obs = metar_data[0]
        running_high = max(temps_f)
        current_temp = temps_f[0] 
        temp_drop = round(running_high - current_temp, 1)
        
        # Physics Triggers
        is_afternoon = station_time.hour >= 14
        cooling_confirmed = temp_drop >= temp_drop_threshold
        
        # Convective Trap Check
        wx_string = str(latest_obs.get("wxString", "")).upper()
        convective_trap = any(trap in wx_string for trap in ["RA", "TS", "CB", "TCU", "DZ"])
        
        # Advection Risk Check
        wdir = latest_obs.get("wdir")
        wspd = latest_obs.get("wspd")
        advection_risk = False
        if wdir is not None and wspd is not None:
            # Southerly warm winds (135° to 225°) blowing at 10+ knots
            if 135 <= wdir <= 225 and wspd >= 10:
                advection_risk = True

        # 2. Ingest & Filter Kalshi Markets
        markets = fetch_kalshi_markets(data["ticker"])
        
        for m in markets:
            ticker = m.get("ticker", "")
            if target_date_str not in ticker:
                continue
                
            floor = m.get("floor_strike")
            cap = m.get("cap_strike")
            title = m.get("yes_sub_title") or m.get("title", "")
            
            strike_barrier = floor if floor is not None else cap
            if strike_barrier is None:
                continue
                
            # Conservative TWC Rounding Margin (e.g., 71.3°F -> 72°F)
            settlement_high_projected = math.ceil(running_high)
            
            if strike_barrier > running_high:
                no_bid_raw = m.get("no_bid_dollars") or "0"
                no_ask_raw = m.get("no_ask_dollars") or "1"
                best_no_bid = int(round(float(no_bid_raw) * 100))
                best_no_ask = int(round(float(no_ask_raw) * 100))
                
                # Maker Execution Logic
                if best_no_ask - best_no_bid > 1 and best_no_bid > 0:
                    target_bid = best_no_bid + 1
                    exec_mode = "Maker (Bid+1¢)"
                elif best_no_bid > 0:
                    target_bid = best_no_bid
                    exec_mode = "Maker (Join Bid)"
                else:
                    target_bid = min(best_no_ask - 1, 90) if best_no_ask > 1 else 90
                    exec_mode = "Maker (Seeding)"
                
                # Strike Distance Decay Probability
                dynamic_prob = calculate_dynamic_prob(strike_barrier, running_high, lambda_decay)
                
                # Strategy Verdict Evaluation
                bet_size = 0.0
                if not is_today:
                    verdict = "📅 Tomorrow's Market"
                elif not is_afternoon:
                    verdict = "⏳ Waiting for 2:00 PM Gate"
                elif not cooling_confirmed:
                    verdict = f"☀️ Heating (-{temp_drop}°F < req)"
                elif convective_trap:
                    verdict = f"⛈️ Convective Trap (Wx: {wx_string})"
                elif advection_risk:
                    verdict = f"💨 Advection Risk (Wind: {wspd}kt @ {wdir}°)"
                elif strike_barrier <= settlement_high_projected:
                    verdict = "⚠️ PASS (TWC Rounding Margin)"
                elif target_bid > max_no_price:
                    verdict = f"⚠️ Fee/Tail Trap ({target_bid}¢ > {max_no_price}¢)"
                else:
                    bet_size = calculate_quarter_kelly(dynamic_prob, target_bid, bankroll)
                    if bet_size > 0:
                        verdict = f"🟢 BUY 'NO' @ {target_bid}¢"
                    else:
                        verdict = "⚠️ PASS (Negative EV)"

                results.append({
                    "Bracket": f"{city} {title}",
                    "Running High": f"{running_high}°F",
                    "Current": f"{current_temp}°F",
                    "Book (NO)": f"{best_no_bid}¢ / {best_no_ask}¢",
                    "Target Entry": f"{target_bid}¢",
                    "Model P(NO)": f"{dynamic_prob:.1f}%",
                    "Quarter-Kelly": f"${bet_size:,.2f}" if bet_size > 0 else "$0.00",
                    "Status / Action": verdict,
                    "_sort_val": target_bid if bet_size > 0 else 0
                })

        progress.progress((idx + 1) / len(STATIONS))

    status_box.empty()
    progress.empty()

    if results:
        df = pd.DataFrame(results).sort_values(by="_sort_val", ascending=False)
        st.markdown(f"### 📋 Advanced Statistical Matrix ({target_scan_day})")
        st.dataframe(df.drop(columns=["_sort_val"]), use_container_width=True, hide_index=True)
    else:
        st.warning(f"No active Kalshi order books found matching {target_date_str}.")
        
