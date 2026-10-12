"""Storage backends: a local xlsx file (for testing) or a Google Sheet via Apps Script.

Both expose the same interface:
    read_table(sheet)                      -> list[dict]   (header-name -> value)
    append_row(sheet, row)                 -> None         (values placed by header name,
                                                            unknown headers are added at the end)
    ensure_sheet(sheet, headers, rows)     -> None         (create sheet if missing)

Row values may be str, int, float, None, datetime.date or datetime.time.
"""
import datetime as dt
import json
import shutil
import threading
from copy import copy
from pathlib import Path

import requests
from openpyxl import load_workbook

HEADER_SCAN_ROWS = 15


def _clean(v):
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    return v


class LocalXlsxBackend:
    def __init__(self, path, seed=None, base_dir=Path(".")):
        self.path = (base_dir / path).resolve()
        self.lock = threading.Lock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if seed and (base_dir / seed).exists():
                shutil.copy(base_dir / seed, self.path)
            else:
                from openpyxl import Workbook
                Workbook().save(self.path)

    @staticmethod
    def _header(ws):
        for r in range(1, min(ws.max_row, HEADER_SCAN_ROWS) + 1):
            cells = [(c.column, str(c.value).strip()) for c in ws[r] if c.value not in (None, "")]
            if len(cells) >= 2:
                return r, dict((name, col) for col, name in cells)
        return None, {}

    def read_table(self, sheet):
        with self.lock:
            wb = load_workbook(self.path, data_only=True)
            if sheet not in wb.sheetnames:
                return []
            ws = wb[sheet]
            hrow, cols = self._header(ws)
            rows = []
            for r in range(hrow + 1, ws.max_row + 1) if hrow else []:
                row = {n: _clean(ws.cell(r, c).value) for n, c in cols.items()}
                if any(v not in (None, "") for v in row.values()):
                    rows.append(row)
            return rows

    def ensure_sheet(self, sheet, headers, rows=()):
        with self.lock:
            wb = load_workbook(self.path)
            if sheet in wb.sheetnames:
                return
            ws = wb.create_sheet(sheet)
            ws.append(list(headers))
            for r in rows:
                ws.append(list(r))
            wb.save(self.path)

    def append_row(self, sheet, row):
        with self.lock:
            wb = load_workbook(self.path)
            if sheet not in wb.sheetnames:
                raise RuntimeError(f"Blatt '{sheet}' fehlt in {self.path.name}")
            ws = wb[sheet]
            hrow, cols = self._header(ws)
            if not hrow:
                raise RuntimeError(f"Blatt '{sheet}' hat keine Kopfzeile")
            # add headers we do not know yet, right of the last one
            for name in row:
                if name not in cols:
                    col = max(cols.values()) + 1
                    head = ws.cell(hrow, col, name)
                    prev = ws.cell(hrow, col - 1)
                    if prev.has_style:
                        head._style = copy(prev._style)
                    cols[name] = col
            # first free row below the last row that has data in a header column
            last = hrow
            for r in range(ws.max_row, hrow, -1):
                if any(ws.cell(r, c).value not in (None, "") for c in cols.values()):
                    last = r
                    break
            new = last + 1
            for name, col in cols.items():
                src = ws.cell(last, col)
                dst = ws.cell(new, col)
                if last > hrow and src.has_style:
                    dst._style = copy(src._style)
                if name not in row:
                    continue
                v = row[name]
                dst.value = v
                if isinstance(v, dt.datetime):
                    dst.number_format = "DD.MM.YYYY HH:MM"
                elif isinstance(v, dt.date):
                    dst.number_format = "DD.MM.YYYY"
                elif isinstance(v, dt.time):
                    dst.number_format = "HH:MM"
            wb.save(self.path)


class GoogleSheetsBackend:
    """Talks to the Apps Script web app in apps_script/Code.gs."""

    def __init__(self, url, token):
        if not url or not token:
            raise RuntimeError("config.json: google.url und google.token müssen gesetzt sein")
        self.url, self.token = url, token

    def _call(self, payload):
        body = json.dumps({**payload, "token": self.token}, default=self._enc)
        # text/plain avoids a CORS-style preflight; Apps Script answers with a 302 that requests follows
        r = requests.post(self.url, data=body, headers={"Content-Type": "text/plain"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Apps Script: {data.get('error')}")
        return data

    @staticmethod
    def _enc(o):
        if isinstance(o, dt.datetime):
            return {"$t": "datetime", "v": o.strftime("%Y-%m-%dT%H:%M:%S")}
        if isinstance(o, dt.date):
            return {"$t": "date", "v": o.isoformat()}
        if isinstance(o, dt.time):
            return {"$t": "time", "v": o.strftime("%H:%M:%S")}
        raise TypeError(type(o))

    def read_table(self, sheet):
        return self._call({"action": "read", "sheet": sheet})["rows"]

    def ensure_sheet(self, sheet, headers, rows=()):
        self._call({"action": "ensure", "sheet": sheet, "headers": list(headers),
                    "rows": [list(r) for r in rows]})

    def append_row(self, sheet, row):
        self._call({"action": "append", "sheet": sheet, "row": row})


def make_backend(cfg, base_dir):
    if cfg["backend"] == "google":
        return GoogleSheetsBackend(cfg["google"]["url"], cfg["google"]["token"])
    return LocalXlsxBackend(cfg["local"]["path"], cfg["local"].get("seed"), base_dir)
