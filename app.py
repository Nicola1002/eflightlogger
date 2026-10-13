"""eFlightLogger – local web UI that fills the flight log (xlsx or Google Sheet via OAuth).

Start:  python app.py      (opens http://127.0.0.1:8765)
"""
import json
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, redirect, request, send_from_directory, session
from googleapiclient.errors import HttpError

import checks
import weather
from auth import GoogleAuth
from storage import NotAuthenticated, make_backend

BASE = Path(__file__).resolve().parent


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


# config.json is committed; config.local.json (git-ignored) holds backend selection and the spreadsheet id
CFG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
if (BASE / "config.local.json").exists():
    _merge(CFG, json.loads((BASE / "config.local.json").read_text(encoding="utf-8")))
SHEETS = CFG["sheets"]
TZ = ZoneInfo(CFG["timezone"])
PENDING = BASE / "data" / "pending.json"
PENDING.parent.mkdir(exist_ok=True)

# first location; the pilot position from ConOps 3.2.1
LOCATION_HEADERS = ["Ort", "Lat", "Lon"]
LOCATION_SEED = [["Möhrendorf", 49.643001, 10.960292]]

HOST, PORT = "127.0.0.1", 8765
GOOGLE = CFG["backend"] == "google"
auth = GoogleAuth(BASE, CFG["google"]["client_secret_file"], f"http://{HOST}:{PORT}/auth/callback")
store = make_backend(CFG, BASE, auth.credentials)

app = Flask(__name__, static_folder="static")
app.secret_key = auth.secret_key()
_ready = False  # the Orte sheet is ensured once; for Google only possible after login
_master = {"t": 0, "data": None}
_lock = threading.Lock()


# ---------- master data ----------
def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _ensure_locations():
    global _ready
    if not _ready:
        store.ensure_sheet(SHEETS["locations"], LOCATION_HEADERS, LOCATION_SEED)
        _ready = True


def load_master(force=False):
    with _lock:
        _ensure_locations()
        if not force and _master["data"] and time.time() - _master["t"] < 30:
            return _master["data"]
        drones = [{"name": str(r["Drones"]).strip(), "status": r.get("Status"),
                   "last_service": r.get("letzte Wartung")}
                  for r in store.read_table(SHEETS["drones"]) if r.get("Drones")]
        batteries = [{"name": str(r["Battery"]).strip(), "status": r.get("Status"),
                      "config": r.get("Konfiguration")}
                     for r in store.read_table(SHEETS["batteries"]) if r.get("Battery")]
        locations = [{"name": str(r["Ort"]).strip(), "lat": _num(r.get("Lat")), "lon": _num(r.get("Lon"))}
                     for r in store.read_table(SHEETS["locations"]) if r.get("Ort")]
        flights = store.read_table(SHEETS["flights"])
        persons = sorted({str(f[k]).strip() for f in flights
                          for k in ("RPIC", "Operator", "Backup-Pilot", "Airspace Observer 1", "Airspace Observer 2")
                          if f.get(k)})
        pre = CFG["battery_prefix"]
        data = {
            "drones": drones,
            "main_batteries": [b for b in batteries if b["name"].upper().startswith(pre["main"])],
            "aux_batteries": [b for b in batteries if b["name"].upper().startswith(pre["aux"])],
            "locations": locations,
            "persons": persons,
            "flight_rules": CFG["flight_rules"],
            "status_options": sorted({*CFG["status_options"], *[str(f["Status"]) for f in flights if f.get("Status")]},
                                     key=str.lower),
            "backend": CFG["backend"],
            "user": auth.user() if GOOGLE else None,
        }
        _master.update(t=time.time(), data=data)
        return data


# ---------- evaluation ----------
def evaluate(form):
    m = load_master()
    loc = next((l for l in m["locations"] if l["name"] == form.get("location")), None)
    has_pos = bool(loc and loc["lat"] is not None and loc["lon"] is not None)
    errors, w = [], None
    manual = form.get("manual") or {}
    if manual.get("enabled"):
        try:
            w = checks.manual_to_weather(manual)
        except ValueError:
            errors.append("Ungültiger Wert in der manuellen Wettereingabe")
    elif has_pos:
        try:
            w = weather.metar_near(loc["lat"], loc["lon"])
        except Exception as e:  # network / API problems must not break the form
            errors.append(f"METAR-Abfrage fehlgeschlagen: {e}")
    sw = None
    try:
        sw = weather.space_weather()
    except Exception as e:
        errors.append(f"Space-Weather-Abfrage fehlgeschlagen: {e}")

    now = datetime.now(TZ)
    cl = checks.asset_checks(CFG, form, m["drones"], m["main_batteries"] + m["aux_batteries"])
    if not has_pos:
        cl.append({"id": "location", "label": "Ort", "status": "fail", "detail": "Ort ohne GPS-Position"})
    else:
        cl += checks.weather_checks(CFG, w)
        cl.append(checks.daylight_check(CFG, loc["lat"], loc["lon"], now))
    cl += checks.space_checks(CFG, sw)
    return {"now": now.isoformat(), "weather": w, "space": sw, "checks": cl,
            "summary": checks.summarize(cl), "errors": errors, "location": loc}


def read_pending():
    try:
        return json.loads(PENDING.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


# ---------- auth ----------
@app.errorhandler(NotAuthenticated)
def _not_authenticated(_):
    return jsonify(error="Nicht bei Google angemeldet", login_url="/auth/login"), 401


@app.errorhandler(HttpError)
def _google_error(e):
    code = e.resp.status
    if code in (401, 403, 404):
        hint = {401: "Google-Login abgelaufen – bitte neu anmelden",
                403: "Kein Zugriff auf die Tabelle – Freigabe für dieses Google-Konto prüfen",
                404: "Tabelle nicht gefunden – google.spreadsheet_id prüfen"}[code]
        return jsonify(error=hint, **({"login_url": "/auth/login"} if code == 401 else {})), code
    return jsonify(error=f"Google Sheets API: {e}"), 502


@app.get("/api/me")
def api_me():
    if not GOOGLE:
        return jsonify(backend=CFG["backend"], user=None, login_needed=False)
    return jsonify(backend="google", user=auth.user(), configured=auth.configured(),
                   login_needed=auth.credentials() is None)


@app.get("/auth/login")
def auth_login():
    if not GOOGLE:
        return redirect("/")
    try:
        url, state, verifier = auth.start()
    except RuntimeError as e:
        return jsonify(error=str(e)), 500
    session["oauth"] = {"state": state, "verifier": verifier}
    return redirect(url)


@app.get("/auth/callback")
def auth_callback():
    o = session.pop("oauth", None)
    if not o or request.args.get("state") != o["state"]:
        return jsonify(error="Ungültiger Login-Status – bitte erneut anmelden"), 400
    if request.args.get("error"):
        return jsonify(error=f"Login abgebrochen: {request.args['error']}"), 400
    auth.finish(request.url, o["state"], o["verifier"])
    _master.update(t=0, data=None)
    return redirect("/")


@app.post("/auth/logout")
def auth_logout():
    auth.logout()
    _master.update(t=0, data=None)
    return jsonify(ok=True)


# ---------- routes ----------
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/master")
def api_master():
    return jsonify(load_master(force=request.args.get("refresh") == "1"))


@app.post("/api/check")
def api_check():
    return jsonify(evaluate(request.get_json(force=True)))


@app.get("/api/pending")
def api_pending():
    return jsonify(read_pending())


@app.post("/api/fly")
def api_fly():
    body = request.get_json(force=True)
    form, debug = body["form"], bool(body.get("debug"))
    if read_pending():
        return jsonify(error="Es läuft bereits ein Flug"), 409
    ev = evaluate(form)
    if not ev["summary"]["can_fly"] and not debug:
        return jsonify(error="Flug nicht freigegeben", violations=ev["summary"]["violations"]), 409
    pending = {
        "form": form,
        "started": ev["now"],
        "debug": debug,
        "overridden": [f"{c['label']}: {c['detail']}" for c in ev["summary"]["violations"]],
        "weather": ev["weather"],
    }
    PENDING.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
    return jsonify(pending)


@app.post("/api/cancel")
def api_cancel():
    PENDING.unlink(missing_ok=True)
    return jsonify(ok=True)


def _float(v, name, required):
    s = str(v if v is not None else "").strip().replace(",", ".")
    if not s:
        if required:
            raise ValueError(f"{name} fehlt")
        return None
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"{name} ist keine Zahl")


@app.post("/api/save")
def api_save():
    p = read_pending()
    if not p:
        return jsonify(error="Kein Flug vorgemerkt – erst 'Fly!' drücken"), 409
    body = request.get_json(force=True)
    try:
        minutes = _float(body.get("flight_time"), "Flugzeit", True)
        km = _float(body.get("flight_distance"), "Flugstrecke", False)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    status = (body.get("status") or "").strip()
    if not status:
        return jsonify(error="Status fehlt"), 400

    f, w = p["form"], p.get("weather") or {}
    started = datetime.fromisoformat(p["started"])
    comment = (body.get("comment") or "").strip()
    if p["overridden"]:
        note = "[DEBUG – Verstöße ignoriert: " + "; ".join(p["overridden"]) + "]"
        comment = f"{note} {comment}".strip()

    row = {
        "Datum": started.date(),
        "Ort": f.get("location"),
        "Uhrzeit": started.time().replace(second=0, microsecond=0),
        "Flugzeit (min)": minutes,
        "Flugstrecke (km)": km,
        "Avg Wind": w.get("wind_ms"),
        "Max Wind": w.get("gust_ms") if w.get("gust_ms") is not None else w.get("wind_ms"),
        "Status": status,
        "Hauptakku": f.get("main_battery"),
        "Steuerakku": f.get("aux_battery"),
        "Operator": f.get("operator"),
        "Bemerkung": comment or None,
        "METAR": w.get("raw"),
        "Drohne": f.get("drone"),
        "Flight rules": f.get("flight_rules"),
        "Backup-Pilot": f.get("backup_pilot") or None,
        "Airspace Observer 1": f.get("observer1") or None,
        "Airspace Observer 2": f.get("observer2") or None,
    }
    try:
        store.append_row(SHEETS["flights"], row)
    except (NotAuthenticated, HttpError):
        raise  # handled by the error handlers above (401 / readable Google error)
    except Exception as e:
        return jsonify(error=f"Schreiben fehlgeschlagen: {e}"), 502
    PENDING.unlink(missing_ok=True)
    load_master(force=True)
    return jsonify(ok=True)


@app.post("/api/locations")
def api_add_location():
    b = request.get_json(force=True)
    name = (b.get("name") or "").strip()
    lat, lon = _num(b.get("lat")), _num(b.get("lon"))
    if not name:
        return jsonify(error="Name fehlt"), 400
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return jsonify(error="Ungültige GPS-Position"), 400
    if any(l["name"].lower() == name.lower() for l in load_master()["locations"]):
        return jsonify(error="Ort existiert schon"), 409
    store.append_row(SHEETS["locations"], {"Ort": name, "Lat": lat, "Lon": lon})
    load_master(force=True)
    return jsonify(ok=True, name=name)


if __name__ == "__main__":
    url = f"http://{HOST}:{PORT}"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"eFlightLogger läuft auf {url}  (Backend: {CFG['backend']})")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
