import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from storage import GoogleSheetsApiBackend, NotAuthenticated, _cell_value, _find_header, _write_value  # noqa: E402


def cell(v, kind=None):
    if v is None:
        return {}
    c = {"effectiveValue": {"numberValue": v} if isinstance(v, (int, float)) else {"stringValue": v}}
    if kind:
        c["effectiveFormat"] = {"numberFormat": {"type": kind}}
    return c


class Exec:
    def __init__(self, result, sink=None):
        self.result, self.sink = result, sink

    def execute(self, num_retries=0):
        return self.result


class FakeSvc:
    """Just enough of spreadsheets() for the backend."""

    def __init__(self, sheets, grids):
        self.sheets, self.grids, self.batches = sheets, grids, []

    def get(self, spreadsheetId, ranges=None, fields=None, includeGridData=False):
        if ranges:
            title = ranges[0].strip("'")
            return Exec({"sheets": [{"data": [{"rowData": [{"values": r} for r in self.grids[title]]}]}]})
        return Exec({"sheets": [{"properties": p} for p in self.sheets.values()]})

    def batchUpdate(self, spreadsheetId, body):
        self.batches.append(body["requests"])
        return Exec({})


def backend(svc):
    b = GoogleSheetsApiBackend("sid", lambda: object())
    b._svc = lambda: svc
    return b


def props(title, rows=100, cols=26):
    return {"title": title, "sheetId": 7, "gridProperties": {"rowCount": rows, "columnCount": cols}}


def test_find_header_skips_blank_and_single_cell_rows():
    grid = [[], [None, "Titel"], [None, "Drones", "Status"], [None, "A", "ok"]]
    assert _find_header(grid) == (2, {"Drones": 1, "Status": 2})


def test_cell_value_dates_and_numbers():
    assert _cell_value(cell(45292, "DATE")) == "2024-01-01T00:00:00"
    assert _cell_value(cell(0.5, "TIME")) == "12:00:00"
    assert _cell_value(cell(1.5)) == 1.5
    assert _cell_value(cell("x")) == "x"
    assert _cell_value({}) is None


def test_write_value_roundtrip():
    ev, fmt = _write_value(dt.date(2024, 1, 1))
    assert ev == {"numberValue": 45292} and fmt["type"] == "DATE"
    ev, _ = _write_value(dt.time(12, 0))
    assert ev == {"numberValue": 0.5}
    ev, fmt = _write_value(dt.datetime(2024, 1, 1, 12, 0))
    assert ev == {"numberValue": 45292.5} and fmt["type"] == "DATE_TIME"
    assert _write_value("=HYPERLINK()")[0] == {"stringValue": "=HYPERLINK()"}


def test_read_table_with_offset_header_and_missing_sheet():
    grid = [[], [cell(None), cell("Drones"), cell("Status")],
            [cell(None), cell("D1"), cell("ok")], [], [cell(None), cell("D2")]]
    b = backend(FakeSvc({"Overview": props("Overview")}, {"Overview": grid}))
    assert b.read_table("Overview") == [{"Drones": "D1", "Status": "ok"}, {"Drones": "D2", "Status": None}]
    assert b.read_table("Nope") == []


def test_append_row_places_by_header_and_adds_new_column():
    grid = [[cell("Datum"), cell("Ort")], [cell(45000, "DATE"), cell("A")], [], []]
    svc = FakeSvc({"Fluglog": props("Fluglog", rows=100, cols=2)}, {"Fluglog": grid})
    backend(svc).append_row("Fluglog", {"Ort": "B", "Drohne": "D1", "Datum": dt.date(2024, 1, 1)})
    reqs = svc.batches[0]
    cells = [(r["updateCells"]["start"], r["updateCells"]["rows"][0]["values"][0]) for r in reqs if "updateCells" in r]
    # new header "Drohne" in column 2 of the header row, data in row 2 (first free row)
    assert ({"sheetId": 7, "rowIndex": 0, "columnIndex": 2}, {"userEnteredValue": {"stringValue": "Drohne"}}) in cells
    starts = {(s["rowIndex"], s["columnIndex"]) for s, _ in cells}
    assert {(2, 0), (2, 1), (2, 2)} <= starts
    assert any("appendDimension" in r and r["appendDimension"]["dimension"] == "COLUMNS" for r in reqs)
    assert any("copyPaste" in r for r in reqs)  # keeps the previous row's formatting


def test_append_row_grows_grid_when_full():
    grid = [[cell("a"), cell("b")], [cell("x"), cell("y")]]
    svc = FakeSvc({"S": props("S", rows=2, cols=26)}, {"S": grid})
    backend(svc).append_row("S", {"a": "1"})
    assert svc.batches[0][0] == {"appendDimension": {"sheetId": 7, "dimension": "ROWS", "length": 1}}


def test_not_authenticated():
    b = GoogleSheetsApiBackend("sid", lambda: None)
    with pytest.raises(NotAuthenticated):
        b.read_table("x")
