"""
SkyCast Pro - Private Weatherdef init_db Forecasting Application
Install : pip install flask requests
Run     : python app.py
Server  : http://localhost:5000
"""

import json
import sqlite3
import time
import requests
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)
CACHE_SECONDS = 600                 # cache weather data for 10 minutes
DB_NAME = "weather_cache.db"

# ---------------------------------------------------------------- database
def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key         TEXT PRIMARY KEY,
            data        TEXT,
            fetched_at  REAL
        )
    """)
    conn.commit()
    conn.close()
init_db() 
def cache_key(city=None, lat=None, lon=None):
    """Build a cache key from city name or coordinates."""
    if city:
        return "city:" + city.lower()
    return f"geo:{lat:.3f},{lon:.3f}"

def get_cache(key):
    conn = sqlite3.connect(DB_NAME)
    row = conn.execute(
        "SELECT data, fetched_at FROM cache WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    if row and time.time() - row[1] < CACHE_SECONDS:
        return json.loads(row[0])
    return None

def save_cache(key, data):
    conn = sqlite3.connect(DB_NAME)
    conn.execute(
        "REPLACE INTO cache (key, data, fetched_at) VALUES (?, ?, ?)",
        (key, json.dumps(data), time.time())
    )
    conn.commit()
    conn.close()

# ------------------------------------------------------------- api helpers
def geocode(city):
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": city, "count": 1, "language": "en", "format": "json"}
    res = requests.get(url, params=params, timeout=10).json()
    if not res.get("results"):
        return None
    return res["results"][0]

def reverse_geocode(lat, lon):
    """Get place name from coordinates (BigDataCloud free API - no key)."""
    try:
        url = f"https://api.bigdatacloud.net/data/reverse-geocode-client?latitude={lat}&longitude={lon}&localityLanguage=en"
        res = requests.get(url, timeout=10).json()
        city = res.get("city") or res.get("locality") or "Current Location"
        country = res.get("countryName", "")
        return f"{city}, {country}"
    except Exception:
        return "Current Location"

def fetch_forecast(lat, lon):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ("temperature_2m,relative_humidity_2m,apparent_temperature,"
                    "precipitation,weather_code,wind_speed_10m,wind_direction_10m,"
                    "pressure_msl,cloud_cover,is_day"),
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                  "precipitation_probability_max,sunrise,sunset,uv_index_max,"
                  "wind_speed_10m_max"),
        "forecast_days": 7,
        "timezone": "auto",
    }
    return requests.get(url, params=params, timeout=10).json()

# -------------------------------------------------------------------- routes
import os
import sys
BASE_DIR = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))

@app.route("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")

@app.route("/api/weather")
def weather():
    """
    GET /api/weather?city=Mumbai
    GET /api/weather?lat=19.07&lon=72.87
    """
    city  = request.args.get("city", "").strip()
    lat_q = request.args.get("lat")
    lon_q = request.args.get("lon")

    if not city and not (lat_q and lon_q):
        return jsonify({"error": "Provide ?city= or ?lat=&lon="}), 400

    try:
        # ----- resolve location
        if city:
            key = cache_key(city=city)
            place = geocode(city)
            if not place:
                return jsonify({"error": "City not found. Check the spelling."}), 404
            lat, lon = place["latitude"], place["longitude"]
            location = f'{place["name"]}, {place.get("country", "")}'
        else:
            lat, lon = float(lat_q), float(lon_q)
            key = cache_key(lat=lat, lon=lon)
            location = reverse_geocode(lat, lon)

        # ----- cache check
        cached = get_cache(key)
        if cached:
            cached["cached"] = True
            return jsonify(cached)

        # ----- fetch from Open-Meteo
        data = fetch_forecast(lat, lon)
        hourly = data.get("hourly", {})
        # take next 24 hours from current time
        current_idx = 0
        current_time = data.get("current", {}).get("time", "")
        if current_time in hourly.get("time", []):
            current_idx = hourly["time"].index(current_time)

        result = {
            "location": location,
            "current": data.get("current", {}),
            "hourly": {
                "time": hourly.get("time", [])[current_idx:current_idx+24],
                "temperature_2m": hourly.get("temperature_2m", [])[current_idx:current_idx+24],
                "precipitation_probability": hourly.get("precipitation_probability", [])[current_idx:current_idx+24],
                "weather_code": hourly.get("weather_code", [])[current_idx:current_idx+24],
            },
            "daily": data.get("daily", {}),
            "lat": lat,
            "lon": lon,
        }
        save_cache(key, result)
        result["cached"] = False
        return jsonify(result)

    except requests.RequestException:
        return jsonify({"error": "Could not reach the weather service."}), 502
    except (ValueError, KeyError, IndexError) as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
