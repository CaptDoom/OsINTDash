import os
import time
import hashlib
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter
import httpx

from backend.app.redis_pool import get_redis_pool

logger = logging.getLogger("drishya.weather")

router = APIRouter(prefix="/api/weather")

# Pre-computed checksum for change detection
_weather_checksum: Optional[str] = None

SECTORS = {
    "Siachen Glacier": {
        "lat": 35.4212,
        "lon": 77.1095,
        "base_temp": -18,
        "base_condition": "Heavy Snow",
        "base_visibility": 1.2,
        "base_wind": 45,
    },
    "Pangong Tso": {
        "lat": 33.7595,
        "lon": 78.6674,
        "base_temp": -4,
        "base_condition": "Freezing Wind",
        "base_visibility": 8.0,
        "base_wind": 28,
    },
    "Tawang Sector": {
        "lat": 27.5860,
        "lon": 91.8594,
        "base_temp": 6,
        "base_condition": "Dense Fog",
        "base_visibility": 0.8,
        "base_wind": 12,
    },
    "Doklam Sector": {
        "lat": 27.2985,
        "lon": 88.9189,
        "base_temp": 4,
        "base_condition": "Mist / Overcast",
        "base_visibility": 2.5,
        "base_wind": 18,
    },
    "Sir Creek": {
        "lat": 23.6311,
        "lon": 68.2251,
        "base_temp": 34,
        "base_condition": "Haze / Humid",
        "base_visibility": 6.0,
        "base_wind": 22,
    },
    "Jammu Sector": {
        "lat": 32.5700,
        "lon": 74.8000,
        "base_temp": 28,
        "base_condition": "Clear Sky",
        "base_visibility": 10.0,
        "base_wind": 15,
    },
    "Kargil Sector": {
        "lat": 34.5600,
        "lon": 76.1300,
        "base_temp": -2,
        "base_condition": "Snow / Cold",
        "base_visibility": 4.0,
        "base_wind": 20,
    },
    "Rann of Kutch": {
        "lat": 23.8000,
        "lon": 70.0000,
        "base_temp": 36,
        "base_condition": "Sandstorm / Dry",
        "base_visibility": 3.5,
        "base_wind": 32,
    },
    "Lipulekh Pass": {
        "lat": 30.2200,
        "lon": 81.0300,
        "base_temp": -1,
        "base_condition": "Light Snow",
        "base_visibility": 5.0,
        "base_wind": 24,
    },
    "Nathu La Pass": {
        "lat": 27.3800,
        "lon": 88.8400,
        "base_temp": 2,
        "base_condition": "Freezing Mist",
        "base_visibility": 1.8,
        "base_wind": 16,
    },
    "Moreh Sector": {
        "lat": 24.2700,
        "lon": 94.3000,
        "base_temp": 24,
        "base_condition": "Rain / Humid",
        "base_visibility": 5.0,
        "base_wind": 10,
    },
    "Jaigaon Sector": {
        "lat": 26.8400,
        "lon": 89.3800,
        "base_temp": 22,
        "base_condition": "Overcast",
        "base_visibility": 7.0,
        "base_wind": 12,
    },
    "Petrapole": {
        "lat": 23.0400,
        "lon": 88.8900,
        "base_temp": 28,
        "base_condition": "Rainy / Cloudy",
        "base_visibility": 6.0,
        "base_wind": 14,
    },
    "Dhanushkodi Sector": {
        "lat": 9.1800,
        "lon": 79.4200,
        "base_temp": 31,
        "base_condition": "Warm Wind",
        "base_visibility": 9.0,
        "base_wind": 25,
    },
    "Minicoy Island": {
        "lat": 8.2800,
        "lon": 73.0500,
        "base_temp": 30,
        "base_condition": "Tropical Breeze",
        "base_visibility": 10.0,
        "base_wind": 18,
    },
}

# 10-minute in-memory cache
weather_cache: Dict[str, Any] = {
    "data": None,
    "expires_at": 0.0
}

def generate_fallback_weather() -> Dict[str, Any]:
    """
    Generates realistic weather data that fluctuates slightly over time
    for extreme border environments when live API lookup is unavailable.
    """
    import random
    import math
    
    current_time = time.time()
    # Use time to create a slow-moving wave for fluctuation
    wave = math.sin(current_time / 3600.0) # fluctuates over hours
    
    data = {}
    for name, base in SECTORS.items():
        # Fluctuations
        temp_delta = wave * 3.0 + random.uniform(-1.0, 1.0)
        wind_delta = wave * 8.0 + random.uniform(-2.0, 2.0)
        vis_delta = wave * 2.0 + random.uniform(-0.5, 0.5)
        
        temp = round(base["base_temp"] + temp_delta, 1)
        wind = max(0, round(base["base_wind"] + wind_delta, 1))
        visibility = max(0.1, round(base["base_visibility"] + vis_delta, 1))
        
        # Decide condition based on temp
        condition = base["base_condition"]
        if name == "Siachen Glacier" and temp > -12:
            condition = "Light Snow"
        elif name == "Tawang Sector" and visibility > 2.0:
            condition = "Partly Cloudy"
        elif name == "Sir Creek" and temp > 36:
            condition = "Extreme Heat / Haze"

        data[name] = {
            "sector": name,
            "latitude": base["lat"],
            "longitude": base["lon"],
            "temperature": temp,
            "condition": condition,
            "visibility_km": visibility,
            "wind_speed_kmh": wind,
            "source": "TACTICAL-SIMULATOR"
        }
    return data

def _compute_weather_checksum(data: Dict[str, Any]) -> str:
    """Compute a stable checksum of weather data for change detection."""
    import json as _json
    canonical = _json.dumps(data, sort_keys=True, default=str)
    return hashlib.md5(canonical.encode()).hexdigest()


@router.get("/border")
async def get_border_weather():
    now = time.time()
    global _weather_checksum
    
    # 1. Check Redis cache first (5-min TTL)
    pool = await get_redis_pool()
    redis_key = "drishya:weather:border"
    if pool:
        try:
            cached = await pool.get(redis_key)
            if cached:
                import json as _json
                weather_data = _json.loads(cached)
                return weather_data
        except Exception:
            pass
    
    # 2. Return in-memory cache if valid
    if weather_cache["data"] and now < weather_cache["expires_at"]:
        return weather_cache["data"]
        
    api_key = os.getenv("OPENWEATHERMAP_API_KEY") or os.getenv("OPENWEATHER_API_KEY")
    
    if not api_key:
        logger.info("[Weather] No OpenWeatherMap API key found. Using realistic fallback telemetry.")
        weather_data = generate_fallback_weather()
    else:
        # Query OWM API - batch-style with async gather
        weather_data = {}
        async with httpx.AsyncClient() as client:
            async def fetch_sector(name: str, base: dict) -> dict:
                url = f"https://api.openweathermap.org/data/2.5/weather"
                params = {
                    "lat": base["lat"],
                    "lon": base["lon"],
                    "appid": api_key,
                    "units": "metric"
                }
                try:
                    response = await client.get(url, params=params, timeout=5.0)
                    if response.status_code == 200:
                        owm_data = response.json()
                        temp = owm_data.get("main", {}).get("temp", base["base_temp"])
                        visibility = owm_data.get("visibility", base["base_visibility"] * 1000) / 1000.0
                        wind_speed = owm_data.get("wind", {}).get("speed", base["base_wind"] / 3.6) * 3.6
                        cond_list = owm_data.get("weather", [])
                        condition = cond_list[0].get("main", base["base_condition"]) if cond_list else base["base_condition"]
                        return {
                            "sector": name,
                            "latitude": base["lat"],
                            "longitude": base["lon"],
                            "temperature": round(temp, 1),
                            "condition": condition,
                            "visibility_km": round(visibility, 1),
                            "wind_speed_kmh": round(wind_speed, 1),
                            "source": "OPENWEATHERMAP"
                        }
                    else:
                        logger.warning(f"[Weather] OWM API returned {response.status_code} for {name}")
                except Exception as e:
                    logger.error(f"[Weather] OWM request failed for {name}: {e}")
                return {
                    "sector": name,
                    "latitude": base["lat"],
                    "longitude": base["lon"],
                    "temperature": base["base_temp"],
                    "condition": base["base_condition"],
                    "visibility_km": base["base_visibility"],
                    "wind_speed_kmh": base["base_wind"],
                    "source": "TACTICAL-FALLBACK"
                }
            
            import asyncio as _aio
            tasks = [fetch_sector(name, base) for name, base in SECTORS.items()]
            results = await _aio.gather(*tasks)
            for result in results:
                weather_data[result["sector"]] = result

    # 3. Checksum comparison — only push if data actually changed
    new_checksum = _compute_weather_checksum(weather_data)
    if new_checksum == _weather_checksum:
        logger.debug("[Weather] No data change detected, returning cached.")
    _weather_checksum = new_checksum

    # 4. Cache in Redis (5-min TTL) and in-memory (10-min TTL)
    weather_cache["data"] = weather_data
    weather_cache["expires_at"] = now + 600.0
    if pool:
        try:
            import json as _json
            await pool.setex(redis_key, 300, _json.dumps(weather_data, default=str))
        except Exception:
            pass
    return weather_data
