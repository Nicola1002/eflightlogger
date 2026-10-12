# eFlightLogger

Lokale Weboberfläche zum Befüllen des Flug-Logs (Excel lokal oder Google Sheet live).

## Start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip ...
.venv/bin/python app.py                       # öffnet http://127.0.0.1:8765
```

Standardmäßig (`"backend": "local"` in `config.json`, überschreibbar in `config.local.json`) wird in `data/flightlog.xlsx` geschrieben.
Beim ersten Start wird die Datei aus `Example.xlsx` kopiert (liegt nicht im Repo – die Excel-Datei mit den Blättern
`Overview`, `Fluglog`, `Batteries` selbst in den Projektordner legen; das Original bleibt unverändert).

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

## Google Sheets anbinden

1. Google Sheet anlegen/öffnen (Blätter `Overview`, `Fluglog`, `Batteries` wie in `Example.xlsx`; `Orte` wird automatisch angelegt).
2. *Erweiterungen → Apps Script*, Inhalt von `apps_script/Code.gs` einfügen.
3. *Projekteinstellungen → Skripteigenschaften*: Eigenschaft `TOKEN` mit einem langen Zufallswert anlegen.
4. *Bereitstellen → Neue Bereitstellung → Web-App*: Ausführen als **Ich**, Zugriff **Jeder**. URL kopieren
   (der Token schützt den Zugriff).
5. `config.local.example.json` nach `config.local.json` kopieren und URL + Token eintragen (`"backend": "google"`).
   `config.local.json` steht in `.gitignore` und wird nie committet; die Werte überschreiben `config.json`.

Neue Spalten im Fluglog (`Drohne`, `Flight rules`, `Backup-Pilot`, `Airspace Observer 1/2`) werden beim ersten Speichern
rechts an die Kopfzeile angehängt. Bestehende Spalten bleiben unverändert.
