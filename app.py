import os
import json
import math
import shutil
import subprocess
import requests
import logging
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify, send_file
from jinja2 import Environment, FileSystemLoader

app = Flask(__name__)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
ICONS_DIR = os.path.join(BASE_DIR, "static", "icons")

os.makedirs(OUTPUT_DIR, exist_ok=True)

UNITS = {
    "standard": {"temperature": "K",   "speed": "m/s", "distance": "km"},
    "metric":   {"temperature": "°C",  "speed": "m/s", "distance": "km"},
    "imperial": {"temperature": "°F",  "speed": "mph", "distance": "mi"},
}

OPEN_METEO_FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&hourly=weather_code,temperature_2m,precipitation,precipitation_probability,"
    "relative_humidity_2m,surface_pressure,visibility"
    "&daily=weathercode,temperature_2m_max,temperature_2m_min,sunrise,sunset"
    "&current=temperature,windspeed,winddirection,is_day,precipitation,"
    "weather_code,apparent_temperature"
    "&timezone=auto"
    "&models=best_match"
    "&forecast_days=8"
    "&{unit_params}"
)

OPEN_METEO_AQI_URL = (
    "https://air-quality-api.open-meteo.com/v1/air-quality"
    "?latitude={lat}&longitude={lon}"
    "&hourly=european_aqi,uv_index"
    "&timezone=auto"
)

OPEN_METEO_UNIT_PARAMS = {
    "standard": "temperature_unit=celsius&wind_speed_unit=ms&precipitation_unit=mm",
    "metric":   "temperature_unit=celsius&wind_speed_unit=ms&precipitation_unit=mm",
    "imperial": "temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch",
}


# ── helpers ──────────────────────────────────────────────────────────────────

def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

def icon_path(name):
    """Return absolute path to an icon file."""
    return os.path.join(ICONS_DIR, name)

def map_weather_code_to_icon(code, is_day=1):
    if code == 0:             icon = "01d"
    elif code == 1:           icon = "022d"
    elif code == 2:           icon = "02d"
    elif code == 3:           icon = "04d"
    elif code in [51,61,80]:  icon = "51d"
    elif code in [53,63,81]:  icon = "53d"
    elif code in [55,65,82]:  icon = "09d"
    elif code == 45:          icon = "50d"
    elif code == 48:          icon = "48d"
    elif code in [56,66]:     icon = "56d"
    elif code in [57,67]:     icon = "57d"
    elif code in [71,85]:     icon = "71d"
    elif code == 73:          icon = "73d"
    elif code in [75,86]:     icon = "13d"
    elif code == 77:          icon = "77d"
    elif code in [95,96,99]:  icon = "11d"
    else:                     icon = "01d"

    if is_day == 0:
        night_map = {"01d": "01n", "022d": "022n", "02d": "02n", "10d": "10n"}
        icon = night_map.get(icon, icon)
    return icon

def get_wind_arrow(deg):
    deg = deg % 360
    dirs = [("↓",22.5),("↙",67.5),("←",112.5),("↖",157.5),
            ("↑",202.5),("↗",247.5),("→",292.5),("↘",337.5),("↓",360)]
    for arrow, bound in dirs:
        if deg < bound:
            return arrow
    return "↓"

def format_time_str(iso_str, time_format="12h", hour_only=False, include_am_pm=True):
    """Format an ISO time string."""
    try:
        dt = datetime.fromisoformat(iso_str)
        return format_time(dt, time_format, hour_only, include_am_pm)
    except Exception:
        return iso_str

def format_time(dt, time_format="12h", hour_only=False, include_am_pm=True):
    if time_format == "24h":
        return dt.strftime("%H:00" if hour_only else "%H:%M")
    if include_am_pm:
        fmt = "%I %p" if hour_only else "%I:%M %p"
    else:
        fmt = "%I" if hour_only else "%I:%M"
    return dt.strftime(fmt).lstrip("0")


# ── weather fetching ─────────────────────────────────────────────────────────

def get_open_meteo_data(lat, lon, units):
    unit_params = OPEN_METEO_UNIT_PARAMS[units]
    url = OPEN_METEO_FORECAST_URL.format(lat=lat, lon=lon, unit_params=unit_params)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def get_open_meteo_aqi(lat, lon):
    url = OPEN_METEO_AQI_URL.format(lat=lat, lon=lon)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


# ── data parsers ─────────────────────────────────────────────────────────────

def parse_forecast(daily, units, is_day):
    times         = daily.get("time", [])
    codes         = daily.get("weathercode", [])
    temp_max      = daily.get("temperature_2m_max", [])
    temp_min      = daily.get("temperature_2m_min", [])
    if units == "standard":
        temp_max = [t + 273.15 for t in temp_max]
        temp_min = [t + 273.15 for t in temp_min]

    forecast = []
    for i in range(len(times)):
        dt = datetime.fromisoformat(times[i])
        day_label = dt.strftime("%a")
        code = codes[i] if i < len(codes) else 0
        icon_name = map_weather_code_to_icon(code, is_day=1)
        forecast.append({
            "day":  day_label,
            "high": int(temp_max[i]) if i < len(temp_max) else 0,
            "low":  int(temp_min[i]) if i < len(temp_min) else 0,
            "icon": icon_path(f"{icon_name}.png"),
            "moon_phase_pct":  "0",
            "moon_phase_icon": icon_path("newmoon.png"),
        })
    return forecast


def parse_data_points(weather_data, aqi_data, units, time_format):
    data_points = []
    daily   = weather_data.get("daily", {})
    current = weather_data.get("current", {})
    hourly  = weather_data.get("hourly", {})
    now     = datetime.now()

    # Sunrise
    sunrises = daily.get("sunrise", [])
    if sunrises:
        sr_dt = datetime.fromisoformat(sunrises[0])
        data_points.append({
            "label": "Sunrise",
            "measurement": format_time(sr_dt, time_format, include_am_pm=False),
            "unit": "" if time_format == "24h" else sr_dt.strftime("%p"),
            "icon": icon_path("sunrise.png"),
            "arrow": None,
        })

    # Sunset
    sunsets = daily.get("sunset", [])
    if sunsets:
        ss_dt = datetime.fromisoformat(sunsets[0])
        data_points.append({
            "label": "Sunset",
            "measurement": format_time(ss_dt, time_format, include_am_pm=False),
            "unit": "" if time_format == "24h" else ss_dt.strftime("%p"),
            "icon": icon_path("sunset.png"),
            "arrow": None,
        })

    # Wind
    wind_speed = current.get("windspeed", 0)
    wind_deg   = current.get("winddirection", 0)
    data_points.append({
        "label": "Wind",
        "measurement": wind_speed,
        "unit": UNITS[units]["speed"],
        "icon": icon_path("wind.png"),
        "arrow": get_wind_arrow(wind_deg),
    })

    # Humidity (from hourly, match current hour)
    humidity = "N/A"
    for i, t in enumerate(hourly.get("time", [])):
        if datetime.fromisoformat(t).hour == now.hour:
            humidity = int(hourly["relative_humidity_2m"][i])
            break
    data_points.append({
        "label": "Humidity", "measurement": humidity, "unit": "%",
        "icon": icon_path("humidity.png"), "arrow": None,
    })

    # Pressure (from hourly)
    pressure = "N/A"
    for i, t in enumerate(hourly.get("time", [])):
        if datetime.fromisoformat(t).hour == now.hour:
            pressure = int(hourly["surface_pressure"][i])
            break
    data_points.append({
        "label": "Pressure", "measurement": pressure, "unit": "hPa",
        "icon": icon_path("pressure.png"), "arrow": None,
    })

    # UV Index (from AQI hourly)
    uv = "N/A"
    for i, t in enumerate(aqi_data.get("hourly", {}).get("time", [])):
        if datetime.fromisoformat(t).hour == now.hour:
            uv = round(aqi_data["hourly"]["uv_index"][i], 1)
            break
    data_points.append({
        "label": "UV Index", "measurement": uv, "unit": "",
        "icon": icon_path("uvi.png"), "arrow": None,
    })

    # Visibility (from hourly)
    vis_raw = "N/A"
    at_max  = False
    vis_conversion = 0.001 if units != "imperial" else 1/5280.0
    vis_max = 10.0 if units != "imperial" else 6.2
    for i, t in enumerate(hourly.get("time", [])):
        if datetime.fromisoformat(t).hour == now.hour:
            v = hourly["visibility"][i] * vis_conversion
            at_max = v >= vis_max
            vis_raw = ("\u2265" if at_max else "") + f"{v:.1f}"
            break
    data_points.append({
        "label": "Visibility", "measurement": vis_raw,
        "unit": UNITS[units]["distance"],
        "icon": icon_path("visibility.png"), "arrow": None,
    })

    # Air Quality (european AQI from AQI hourly)
    aqi_val = "N/A"
    aqi_scale = ""
    for i, t in enumerate(aqi_data.get("hourly", {}).get("time", [])):
        if datetime.fromisoformat(t).hour == now.hour:
            raw = aqi_data["hourly"]["european_aqi"][i]
            aqi_val = round(raw, 1)
            aqi_scale = ["Good","Fair","Moderate","Poor","Very Poor","Ext Poor"][min(int(raw)//20, 5)]
            break
    data_points.append({
        "label": "Air Quality", "measurement": aqi_val, "unit": aqi_scale,
        "icon": icon_path("aqi.png"), "arrow": None,
    })

    return data_points


def parse_hourly(hourly_data, units, time_format, sunrises, sunsets):
    times  = hourly_data.get("time", [])
    temps  = hourly_data.get("temperature_2m", [])
    precip_prob = hourly_data.get("precipitation_probability", [])
    rain   = hourly_data.get("precipitation", [])
    codes  = hourly_data.get("weather_code", [])

    if units == "standard":
        temps = [t + 273.15 for t in temps]

    # Build sun_map: date -> (sunrise_dt, sunset_dt)
    sun_map = {}
    for sr_s, ss_s in zip(sunrises, sunsets):
        sr_dt = datetime.fromisoformat(sr_s)
        ss_dt = datetime.fromisoformat(ss_s)
        sun_map[sr_dt.date()] = (sr_dt, ss_dt)

    now = datetime.now()
    start = 0
    for i, t in enumerate(times):
        dt = datetime.fromisoformat(t)
        if dt.date() == now.date() and dt.hour >= now.hour:
            start = i
            break

    hourly = []
    for i in range(start, min(start + 24, len(times))):
        dt = datetime.fromisoformat(times[i])
        sr, ss = sun_map.get(dt.date(), (None, None))
        is_day = 1 if (sr and ss and sr <= dt < ss) else 0
        code   = codes[i] if i < len(codes) else 0
        icon_name = map_weather_code_to_icon(code, is_day)
        hourly.append({
            "time":        format_time(dt, time_format, hour_only=True),
            "temperature": int(temps[i]) if i < len(temps) else 0,
            "precipitation": (precip_prob[i] / 100) if i < len(precip_prob) else 0,
            "rain":        rain[i] if i < len(rain) else 0,
            "icon":        icon_path(f"{icon_name}.png"),
        })
    return hourly


def build_template_data(weather, aqi, config):
    units       = config["units"]
    time_format = config.get("time_format", "12h")
    current     = weather.get("current", {})
    daily       = weather.get("daily", {})

    is_day     = current.get("is_day", 1)
    code       = current.get("weather_code", 0)
    icon_name  = map_weather_code_to_icon(code, is_day)
    temp_conv  = 273.15 if units == "standard" else 0.0

    now = datetime.now()

    return {
        "title":               config["city"],
        "current_date":        now.strftime("%A, %B %d"),
        "current_day_icon":    icon_path(f"{icon_name}.png"),
        "current_temperature": str(round(current.get("temperature", 0) + temp_conv)),
        "feels_like":          str(round(current.get("apparent_temperature", 0) + temp_conv)),
        "temperature_unit":    UNITS[units]["temperature"],
        "units":               units,
        "time_format":         time_format,
        "last_refresh_time":   now.strftime("%Y-%m-%d %I:%M %p" if time_format == "12h" else "%Y-%m-%d %H:%M"),
        "forecast":            parse_forecast(daily, units, is_day),
        "data_points":         parse_data_points(weather, aqi, units, time_format),
        "hourly_forecast":     parse_hourly(
                                    weather.get("hourly", {}), units, time_format,
                                    daily.get("sunrise", []), daily.get("sunset", [])
                               ),
        "plugin_settings": {
            "displayRefreshTime":  "true",
            "displayMetrics":      "true",
            "displayGraph":        "true",
            "displayRain":         "false",
            "displayGraphIcons":   "false",
            "graphIconStep":       "2",
            "displayForecast":     "true",
            "forecastDays":        str(config.get("forecast_days", 7)),
            "moonPhase":           "false",
            "textColor":           "#000000",
        },
        "static_dir": os.path.join(BASE_DIR, "static"),
        "width":      config["width"],
        "height":     config["height"],
    }


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("settings.html", config=load_config())

@app.route("/save", methods=["POST"])
def save():
    data   = request.get_json()
    config = load_config()
    config.update({
        "city":             data.get("city",             config["city"]),
        "latitude":         float(data.get("latitude",   config["latitude"])),
        "longitude":        float(data.get("longitude",  config["longitude"])),
        "units":            data.get("units",            config["units"]),
        "width":            int(data.get("width",        config["width"])),
        "height":           int(data.get("height",       config["height"])),
        "refresh_interval": int(data.get("refresh_interval", config["refresh_interval"])),
        "time_format":      data.get("time_format",      config.get("time_format", "12h")),
        "forecast_days":    int(data.get("forecast_days", config.get("forecast_days", 7))),
    })
    save_config(config)
    return jsonify({"status": "ok"})

@app.route("/generate")
def generate():
    try:
        config  = load_config()
        weather = get_open_meteo_data(config["latitude"], config["longitude"], config["units"])
        aqi     = get_open_meteo_aqi(config["latitude"], config["longitude"])
        data    = build_template_data(weather, aqi, config)

        # Render weather HTML
        env      = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
        template = env.get_template("weather.html")
        rendered = template.render(**data)

        rendered_path = os.path.join(OUTPUT_DIR, "rendered.html")
        with open(rendered_path, "w") as f:
            f.write(rendered)

        output_path = os.path.join(OUTPUT_DIR, "weather.png")

        # Find chromium binary
        chromium = shutil.which("chromium") or shutil.which("chromium-browser")
        if not chromium:
            raise RuntimeError("Chromium not found. Run: sudo apt install chromium")

        subprocess.run([
            chromium,
            "--headless", "--disable-gpu", "--no-sandbox",
            f"--screenshot={output_path}",
            f"--window-size={config['width']},{config['height']}",
            f"file://{rendered_path}",
        ], check=True, capture_output=True)

        return jsonify({"status": "ok", "message": "Weather image generated!"})
    except Exception as e:
        logger.exception("Generate failed")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/preview")
def preview():
    path = os.path.join(OUTPUT_DIR, "weather.png")
    if not os.path.exists(path):
        return "No image yet. Click Generate first.", 404
    return send_file(path, mimetype="image/png")

@app.route("/rendered")
def rendered():
    path = os.path.join(OUTPUT_DIR, "rendered.html")
    if not os.path.exists(path):
        return "Not rendered yet.", 404
    return send_file(path)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
