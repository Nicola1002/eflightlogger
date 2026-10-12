/**
 * eFlightLogger – Google Sheets endpoint.
 * Setup: siehe README.md ("Google Sheets anbinden").
 *
 * Das Geheimnis liegt NICHT im Code, sondern unter
 * Projekteinstellungen -> Skripteigenschaften -> TOKEN.
 */
const HEADER_SCAN_ROWS = 15;

function doPost(e) {
  try {
    const req = JSON.parse(e.postData.contents);
    const token = PropertiesService.getScriptProperties().getProperty('TOKEN');
    if (!token || req.token !== token) return out_({ ok: false, error: 'unauthorized' });

    const lock = LockService.getScriptLock();
    lock.waitLock(20000);
    try {
      switch (req.action) {
        case 'read':   return out_({ ok: true, rows: readTable_(req.sheet) });
        case 'append': appendRow_(req.sheet, req.row); return out_({ ok: true });
        case 'ensure': ensureSheet_(req.sheet, req.headers, req.rows || []); return out_({ ok: true });
        default:       return out_({ ok: false, error: 'unknown action' });
      }
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return out_({ ok: false, error: String(err) });
  }
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// first row (within the first 15) that has at least two filled cells = header row
function findHeader_(values) {
  for (let r = 0; r < Math.min(values.length, HEADER_SCAN_ROWS); r++) {
    const filled = values[r].filter(v => v !== '' && v !== null).length;
    if (filled >= 2) return r;
  }
  return -1;
}

function readTable_(name) {
  const sh = SpreadsheetApp.getActive().getSheetByName(name);
  if (!sh) return [];
  const values = sh.getDataRange().getValues();
  const h = findHeader_(values);
  if (h < 0) return [];
  const tz = SpreadsheetApp.getActive().getSpreadsheetTimeZone();
  const names = values[h].map(v => String(v).trim());
  const rows = [];
  for (let r = h + 1; r < values.length; r++) {
    const row = {};
    let any = false;
    names.forEach((n, c) => {
      if (!n) return;
      let v = values[r][c];
      if (v instanceof Date) v = Utilities.formatDate(v, tz, "yyyy-MM-dd'T'HH:mm:ss");
      if (v !== '' && v !== null) any = true;
      row[n] = v === '' ? null : v;
    });
    if (any) rows.push(row);
  }
  return rows;
}

function ensureSheet_(name, headers, rows) {
  const ss = SpreadsheetApp.getActive();
  if (ss.getSheetByName(name)) return;
  const sh = ss.insertSheet(name);
  sh.getRange(1, 1, 1, headers.length).setValues([headers]);
  if (rows.length) sh.getRange(2, 1, rows.length, headers.length).setValues(rows);
}

// {"$t":"date","v":"2026-10-12"} / {"$t":"time","v":"16:31:00"} -> Date + number format
function decode_(v) {
  if (v && typeof v === 'object' && v.$t) {
    const tz = SpreadsheetApp.getActive().getSpreadsheetTimeZone();
    if (v.$t === 'date') {
      const p = v.v.split('-').map(Number);
      return { value: new Date(p[0], p[1] - 1, p[2]), fmt: 'dd.mm.yyyy' };
    }
    if (v.$t === 'time') {
      const p = v.v.split(':').map(Number);
      return { value: new Date(1899, 11, 30, p[0], p[1], p[2] || 0), fmt: 'HH:mm' };
    }
    if (v.$t === 'datetime') {
      return { value: new Date(v.v), fmt: 'dd.mm.yyyy HH:mm' };
    }
  }
  return { value: v === null || v === undefined ? '' : v, fmt: null };
}

function appendRow_(name, row) {
  const sh = SpreadsheetApp.getActive().getSheetByName(name);
  if (!sh) throw new Error('Blatt nicht gefunden: ' + name);
  const values = sh.getDataRange().getValues();
  const h = findHeader_(values);
  if (h < 0) throw new Error('Keine Kopfzeile in ' + name);

  // unknown headers go to the right of the last one
  let names = values[h].map(v => String(v).trim());
  let lastCol = names.length;
  while (lastCol > 0 && names[lastCol - 1] === '') lastCol--;
  names = names.slice(0, lastCol);
  Object.keys(row).forEach(k => {
    if (names.indexOf(k) < 0) {
      names.push(k);
      sh.getRange(h + 1, names.length).setValue(k);
    }
  });

  // first free row below the last row with data
  let last = h;
  for (let r = values.length - 1; r > h; r--) {
    if (values[r].some(v => v !== '' && v !== null)) { last = r; break; }
  }
  const target = last + 2;  // 1-based row of the new line
  names.forEach((n, c) => {
    if (!(n in row)) return;
    const d = decode_(row[n]);
    const cell = sh.getRange(target, c + 1);
    if (d.fmt) cell.setNumberFormat(d.fmt);
    cell.setValue(d.value);
  });
}
