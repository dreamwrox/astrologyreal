"""
bnn_chart.py — compute Vedic (sidereal) planetary placements for BNN.

Given birth date, time, and place (lat/lon), returns which rashi each of the nine
planets sits in. Uses the Lahiri ayanamsa (the Indian standard). This is what lets
AstroBot do a real BNN reading without asking the user for placements.

Dependency: pyswisseph  (pip install pyswisseph)
"""

import datetime
import swisseph as swe

RASHIS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
          "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]

# Swiss Ephemeris planet ids. Ketu = point opposite Rahu (mean node).
_PLANETS = {
    "Sun": swe.SUN, "Moon": swe.MOON, "Mars": swe.MARS, "Mercury": swe.MERCURY,
    "Jupiter": swe.JUPITER, "Venus": swe.VENUS, "Saturn": swe.SATURN,
    "Rahu": swe.MEAN_NODE,
}


def _rashi_from_longitude(lon):
    """Sidereal longitude (0-360) -> rashi name."""
    return RASHIS[int(lon // 30) % 12]


def compute_chart(date, time_str, lat, lon, tz_offset_hours=5.5):
    """
    date: datetime.date
    time_str: "HH:MM" (24h). If unknown/empty, defaults to 12:00 (noon) and the
              Moon/fast points may be off — caller should warn the user.
    lat, lon: birth place coordinates (decimal degrees; E and N positive).
    tz_offset_hours: timezone offset from UTC (India = 5.5).

    Returns dict: { planet_name: rashi_name, ... } plus "_time_known": bool.
    """
    time_known = bool(time_str and ":" in time_str)
    try:
        hh, mm = (int(x) for x in time_str.split(":")[:2]) if time_known else (12, 0)
    except Exception:
        hh, mm, time_known = 12, 0, False

    # Convert local time to UT, then to Julian day.
    local = datetime.datetime(date.year, date.month, date.day, hh, mm)
    ut = local - datetime.timedelta(hours=tz_offset_hours)
    jd = swe.julday(ut.year, ut.month, ut.day, ut.hour + ut.minute / 60.0)

    # Use Lahiri ayanamsa for sidereal (Vedic) positions.
    swe.set_sid_mode(swe.SIDM_LAHIRI, 0, 0)
    flag = swe.FLG_SIDEREAL | swe.FLG_SWIEPH

    chart = {}
    for name, pid in _PLANETS.items():
        lonp = swe.calc_ut(jd, pid, flag)[0][0]
        chart[name] = _rashi_from_longitude(lonp)

    # Ketu is exactly opposite Rahu.
    rahu_lon = swe.calc_ut(jd, swe.MEAN_NODE, flag)[0][0]
    chart["Ketu"] = _rashi_from_longitude((rahu_lon + 180.0) % 360.0)

    chart["_time_known"] = time_known
    return chart


def chart_summary(chart):
    """Human-readable one-liner of placements for the prompt."""
    order = ["Sun", "Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Rahu", "Ketu"]
    return ", ".join(f"{p} in {chart[p]}" for p in order if p in chart)


# Offline fallback coordinates for common Indian cities (lat, lon).
_CITY_FALLBACK = {
    "delhi": (28.6139, 77.2090), "new delhi": (28.6139, 77.2090),
    "mumbai": (19.0760, 72.8777), "bombay": (19.0760, 72.8777),
    "kolkata": (22.5726, 88.3639), "calcutta": (22.5726, 88.3639),
    "chennai": (13.0827, 80.2707), "madras": (13.0827, 80.2707),
    "bengaluru": (12.9716, 77.5946), "bangalore": (12.9716, 77.5946),
    "hyderabad": (17.3850, 78.4867), "pune": (18.5204, 73.8567),
    "ahmedabad": (23.0225, 72.5714), "jaipur": (26.9124, 75.7873),
    "lucknow": (26.8467, 80.9462), "chandigarh": (30.7333, 76.7794),
    "amritsar": (31.6340, 74.8723), "ludhiana": (30.9010, 75.8573),
    "patna": (25.5941, 85.1376), "bhopal": (23.2599, 77.4126),
    "kochi": (9.9312, 76.2673), "coimbatore": (11.0168, 76.9558),
    "guwahati": (26.1445, 91.7362), "indore": (22.7196, 75.8577),
    "nagpur": (21.1458, 79.0882), "surat": (21.1702, 72.8311),
    "kanpur": (26.4499, 80.3319), "varanasi": (25.3176, 82.9739),
}


def geocode_place(place):
    """
    Turn a place name into (lat, lon). Tries the offline city table first
    (fast, no network), then falls back to live geocoding via geopy.
    Returns (lat, lon) or None if it can't be resolved.
    """
    if not place:
        return None
    key = place.strip().lower()
    # direct or substring match against the fallback table
    if key in _CITY_FALLBACK:
        return _CITY_FALLBACK[key]
    for city, coords in _CITY_FALLBACK.items():
        if city in key:
            return coords
    # live lookup
    try:
        from geopy.geocoders import Nominatim
        geo = Nominatim(user_agent="astrobot")
        loc = geo.geocode(place, timeout=5)
        if loc:
            return (loc.latitude, loc.longitude)
    except Exception:
        pass
    return None


if __name__ == "__main__":
    # Smoke test: a known date/time/place (New Delhi).
    d = datetime.date(1990, 8, 15)
    c = compute_chart(d, "14:30", lat=28.6139, lon=77.2090)
    print(chart_summary(c))
    print("time known:", c["_time_known"])
