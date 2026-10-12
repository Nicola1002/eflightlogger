"""Pre-flight checks. Limits come from Betriebsgrenzen.pdf (ConOps Kap. 3.1), set in config.json."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun

from weather import wx_codes

KT_TO_MS = 0.514444
RANK = {"ok": 0, "warn": 1, "fail": 2}


def _c(cid, label, status, detail=""):
    return {"id": cid, "label": label, "status": status, "detail": detail}


def _fmt_ms(kt):
    return f"{kt * KT_TO_MS:.1f} m/s ({kt:.1f} kn)"


def manual_to_weather(m):
    """Build a weather dict from the manual-entry fields of the form (wind in m/s)."""
    def num(key):
        v = m.get(key)
        return None if v in (None, "") else float(str(v).replace(",", "."))

    wind, gust = num("wind_ms") or 0.0, num("gust_ms")
    wx = wx_codes(str(m.get("wx") or "").upper().replace(",", " "))
    return {
        "station": "manual", "name": "Manuelle Eingabe", "distance_km": None, "age_min": None,
        "temp_c": num("temp_c"), "wind_dir": m.get("wind_dir"),
        "wind_kt": wind / KT_TO_MS, "gust_kt": gust / KT_TO_MS if gust else None,
        "wind_ms": round(wind, 1), "gust_ms": round(gust, 1) if gust else None,
        "visibility_m": num("visibility_m"), "ceiling_m": num("ceiling_m"),
        "wx": wx, "raw": "manual entry", "manual": True,
    }


def weather_checks(cfg, w):
    L = cfg["limits"]
    out = []
    if w is None:
        return [_c("wx_data", "Wetterdaten", "fail", "Keine METAR-Station in Reichweite – 'Manual entry' nutzen")]

    if w.get("age_min") is not None and w["age_min"] > L["metar_max_age_min"]:
        out.append(_c("metar_age", "METAR-Alter", "warn", f"METAR ist {w['age_min']} min alt"))

    t = w["temp_c"]
    if t is None:
        out.append(_c("temp", "Temperatur", "fail", "keine Temperatur"))
    elif L["temp_min_c"] <= t <= L["temp_max_c"]:
        out.append(_c("temp", "Temperatur", "ok", f"{t:g} °C"))
    else:
        out.append(_c("temp", "Temperatur", "fail",
                      f"{t:g} °C (erlaubt {L['temp_min_c']}–{L['temp_max_c']} °C)"))

    wind, gust = w["wind_kt"], w["gust_kt"]
    if wind > L["wind_kt"]:
        out.append(_c("wind", "Wind", "fail", f"{_fmt_ms(wind)} > {L['wind_kt']} kn"))
    elif gust is not None and gust >= L["gust_kt_below"]:
        out.append(_c("wind", "Wind", "fail", f"Böen {_fmt_ms(gust)} ≥ {L['gust_kt_below']} kn"))
    else:
        g = f", Böen {_fmt_ms(gust)}" if gust else ""
        out.append(_c("wind", "Wind", "ok", f"{_fmt_ms(wind)}{g}"))

    v = w["visibility_m"]
    if v is None:
        out.append(_c("vis", "Sichtweite", "warn", "keine Sichtweite im METAR"))
    elif v > L["visibility_m"]:
        out.append(_c("vis", "Sichtweite", "ok", f"{v:g} m"))
    else:
        out.append(_c("vis", "Sichtweite", "fail", f"{v:g} m (mind. > {L['visibility_m']} m)"))

    ceil = w["ceiling_m"]
    if ceil is None:
        out.append(_c("ceiling", "Ceiling", "ok", "keine Wolkenuntergrenze (BKN/OVC)"))
    elif ceil >= L["ceiling_m"]:
        out.append(_c("ceiling", "Ceiling", "ok", f"{ceil:.0f} m AGL"))
    else:
        out.append(_c("ceiling", "Ceiling", "fail", f"{ceil:.0f} m AGL (mind. {L['ceiling_m']} m)"))

    banned = [f"{c} ({cfg['no_flight_wx'][c]})" for c in w["wx"] if c in cfg["no_flight_wx"]]
    if banned:
        out.append(_c("wx", "Wettererscheinung", "fail", ", ".join(banned)))
    else:
        out.append(_c("wx", "Wettererscheinung", "ok", " ".join(w["wx"]) or "keine"))
    return out


def daylight_check(cfg, lat, lon, now):
    tz = ZoneInfo(cfg["timezone"])
    now = now.astimezone(tz)
    obs = LocationInfo("", "", cfg["timezone"], lat, lon).observer
    s = sun(obs, date=now.date(), tzinfo=tz)
    m = timedelta(minutes=cfg["limits"]["daylight_margin_min"])
    start, end = s["sunrise"] + m, s["sunset"] - m
    window = f"{start:%H:%M}–{end:%H:%M}"
    if start <= now <= end:
        return _c("daylight", "Tageslicht", "ok", window)
    return _c("daylight", "Tageslicht", "fail", f"Flug nur {window} (SR+30 min bis SS−30 min)")


def space_checks(cfg, sw):
    if sw is None:
        return [_c(f"space_{k}", f"Space weather {k}", "warn", "nicht verfügbar") for k in "RSG"]
    th = cfg["space_weather"]
    out = []
    for k in "RSG":
        n = sw[k]["scale"]
        st = "fail" if n >= th["fail_from"] else "warn" if n >= th["warn_from"] else "ok"
        out.append(_c(f"space_{k}", f"Space weather {k}", st, f"{k}{n} ({sw[k]['text']})"))
    return out


def asset_checks(cfg, form, drones, batteries):
    out = []
    drone = next((d for d in drones if d["name"] == form.get("drone")), None)
    if not drone:
        out.append(_c("drone", "Drohne", "fail", "keine Drohne gewählt"))
    else:
        st = drone.get("status") or ""
        out.append(_c("drone", "Drohne", cfg["drone_status"].get(st, "ok"), f"{drone['name']}: {st or 'ok'}"))

    for key, label in (("main_battery", "Hauptakku"), ("aux_battery", "Steuerakku")):
        b = next((b for b in batteries if b["name"] == form.get(key)), None)
        cid = key
        if not b:
            out.append(_c(cid, label, "fail", "kein Akku gewählt"))
            continue
        st = b.get("status") or ""
        status = cfg["battery_status"].get(st, "warn")
        detail = f"{b['name']}: {st or 'unbekannt'}"
        conf = (b.get("config") or "").strip()
        if key == "main_battery" and drone and conf and conf != "universal" and conf != drone["name"]:
            status, detail = "fail", f"{b['name']} ist für {conf} konfiguriert, nicht für {drone['name']}"
        out.append(_c(cid, label, status, detail))

    if not (form.get("operator") or "").strip():
        out.append(_c("operator", "Operator", "fail", "Operator fehlt"))
    return out


def summarize(checks):
    worst = max((c["status"] for c in checks), key=RANK.get, default="ok")
    return {"can_fly": worst != "fail", "worst": worst,
            "violations": [c for c in checks if c["status"] == "fail"],
            "warnings": [c for c in checks if c["status"] == "warn"]}
