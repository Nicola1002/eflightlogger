"""METAR lookup by GPS position (aviationweather.gov) and space weather (NOAA SWPC)."""
import math
import re
import time
from datetime import datetime, timezone

import requests

METAR_URL = "https://aviationweather.gov/api/data/metar"
SWPC_URL = "https://services.swpc.noaa.gov/products/noaa-scales.json"
KT_TO_MS = 0.514444
FT_TO_M = 0.3048

_cache = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


_WX_TOKEN = re.compile(
    r"^[-+]?(VC)?(MI|BC|PR|DR|BL|SH|TS|FZ)*(DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PO|SQ|FC|SS|DS)*$")


def wx_codes(raw):
    """Present-weather codes from the body of a METAR (e.g. '-TSRA' -> TS, RA)."""
    codes = []
    for tok in raw.split():
        if tok in ("RMK", "TEMPO", "BECMG", "NOSIG"):
            break
        if len(tok) < 2 or not _WX_TOKEN.match(tok):
            continue
        t = tok.lstrip("-+")
        if t.startswith("VC"):
            t = t[2:]
        codes += [t[i:i + 2] for i in range(0, len(t) - 1, 2)]
    return codes


def _visibility_m(raw, visib):
    if "CAVOK" in raw.split():
        return 10000
    for tok in raw.split():
        if re.fullmatch(r"\d{4}", tok):
            return int(tok)
    m = re.search(r"\b(\d+)SM\b", raw)
    if m:
        return int(m.group(1)) * 1609
    if visib is not None:
        s = str(visib)
        return 10000 if s.endswith("+") else int(float(s) * 1609.34)
    return None


def _ceiling_m(clouds):
    """Lowest BKN/OVC/VV layer in m AGL, None if no ceiling."""
    bases = []
    for c in clouds or []:
        if c.get("cover") in ("BKN", "OVC", "OVX", "VV") and c.get("base") is not None:
            bases.append(c["base"] * FT_TO_M)
    return min(bases) if bases else None


def _parse(m, lat, lon):
    raw = m.get("rawOb", "")
    wspd, wgst = m.get("wspd"), m.get("wgst")
    obs = datetime.fromtimestamp(m["obsTime"], timezone.utc)
    return {
        "station": m["icaoId"],
        "name": m.get("name"),
        "distance_km": round(distance_km(lat, lon, m["lat"], m["lon"])),
        "obs_time": obs.isoformat(),
        "age_min": round((datetime.now(timezone.utc) - obs).total_seconds() / 60),
        "temp_c": m.get("temp"),
        "wind_dir": m.get("wdir"),
        "wind_kt": wspd or 0,
        "gust_kt": wgst,
        "wind_ms": round((wspd or 0) * KT_TO_MS, 1),
        "gust_ms": round(wgst * KT_TO_MS, 1) if wgst else None,
        "visibility_m": _visibility_m(raw, m.get("visib")),
        "ceiling_m": _ceiling_m(m.get("clouds")),
        "wx": wx_codes(raw),
        "raw": raw,
        "manual": False,
    }


def metar_near(lat, lon):
    """Latest METAR of the geographically nearest station that reported in the last 3 h."""
    def fetch():
        for radius in (0.8, 2.0, 4.0):
            bbox = f"{lat - radius},{lon - radius},{lat + radius},{lon + radius}"
            r = requests.get(METAR_URL, params={"bbox": bbox, "format": "json", "hours": 3}, timeout=15)
            r.raise_for_status()
            if not r.text.strip():
                continue
            latest = {}
            for m in r.json():
                if m.get("obsTime") is None or "lat" not in m:
                    continue
                if m["icaoId"] not in latest or m["obsTime"] > latest[m["icaoId"]]["obsTime"]:
                    latest[m["icaoId"]] = m
            if latest:
                best = min(latest.values(), key=lambda m: distance_km(lat, lon, m["lat"], m["lon"]))
                return _parse(best, lat, lon)
        return None

    return _cached(("metar", round(lat, 3), round(lon, 3)), 120, fetch)


def space_weather():
    """Current NOAA scales: {'R': 0, 'S': 0, 'G': 0} with texts."""
    def fetch():
        r = requests.get(SWPC_URL, timeout=15)
        r.raise_for_status()
        cur = r.json()["0"]
        return {k: {"scale": int(cur[k]["Scale"] or 0), "text": cur[k]["Text"] or "none"} for k in "RSG"}

    return _cached("swpc", 300, fetch)
