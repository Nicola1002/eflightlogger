# eFlightLogger

Lokale Weboberfläche zum Befüllen des Flug-Logs (Excel lokal oder Google Sheet live).

## Start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip ...
.venv/bin/python app.py                       # öffnet http://127.0.0.1:8765
```

Standardmäßig (`"backend": "local"` in `config.json`, überschreibbar in `config.local.json`) wird in `data/flightlog.xlsx` geschrieben.
Beim ersten Start wird die Datei aus `Example.xlsx` kopiert – das Original bleibt unverändert.

## Bedienung

1. Links Drohne, Ort, Akkus und Personen wählen. Wetter (METAR der nächsten Station zur GPS-Position des Orts),
   Space Weather und Tageslicht werden automatisch geprüft.
2. **Fly!** ist nur aktiv, wenn alle Prüfungen bestehen. Gründe stehen darunter. Mit **Debug-Modus** geht Fly! trotzdem;
   die ignorierten Verstöße werden dann im Kommentar des Fluglogs vermerkt.
3. Fly! merkt die linke Seite vor (überlebt Neuladen/Neustart, `data/pending.json`) und sperrt sie.
4. Nach dem Flug rechts Flugzeit, Strecke, Status, Kommentar eintragen → **Save** schreibt eine Zeile ins Blatt `Fluglog`.

Neue Orte: im Dropdown „＋ Neuer Ort …“ → Name + GPS. Sie landen im Blatt `Orte` (Spalten `Ort`, `Lat`, `Lon`).

## Konfiguration (`config.json`)

Grenzwerte (aus Betriebsgrenzen.pdf), verbotene Wettercodes, Status → Ampel-Zuordnung für Drohnen/Akkus,
Flight rules, Statusliste, Zeitzone, Backend.

## Google Sheets anbinden (OAuth)

Die App meldet sich mit dem Google-Konto des Nutzers an und greift direkt über die Sheets API zu –
der Nutzer braucht also nur Schreibzugriff auf die Tabelle (Blätter `Overview`, `Fluglog`, `Batteries` wie in `Example.xlsx`;
`Orte` wird automatisch angelegt).

1. In der [Google Cloud Console](https://console.cloud.google.com/) ein Projekt anlegen und die **Google Sheets API** aktivieren.
2. *APIs & Dienste → OAuth-Zustimmungsbildschirm*: Typ „Extern“, im Status „Testing“ die Google-Konten der Nutzer als **Testnutzer** eintragen.
   (Im Status „Testing“ läuft der Login nach 7 Tagen ab – dann einfach erneut anmelden.)
3. *Anmeldedaten → OAuth-Client-ID erstellen*: Typ **Webanwendung**, autorisierte Weiterleitungs-URI
   `http://127.0.0.1:8765/auth/callback` (genau so, mit `127.0.0.1`, nicht `localhost`).
   JSON herunterladen und als `client_secret.json` in den Projektordner legen (steht in `.gitignore`).
4. `config.local.example.json` nach `config.local.json` kopieren und die Spreadsheet-ID eintragen
   (`https://docs.google.com/spreadsheets/d/<ID>/edit`); `"backend": "google"`.
5. App starten → oben rechts **Mit Google anmelden**. Das Token liegt in `data/google_token.json`
   (git-ignoriert); **Abmelden** löscht es.

Neue Spalten im Fluglog (`Drohne`, `Flight rules`, `Backup-Pilot`, `Airspace Observer 1/2`) werden beim ersten Speichern
rechts an die Kopfzeile angehängt. Bestehende Spalten bleiben unverändert.
