
# ha-ppc-smgw-han

<img src="custom_components/smgw_han/brand/icon.png" alt="SMGW Icon" width="128" align="left" style="margin-right: 16px;">

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=TRON4R&repository=ha-ppc-smgw-han)

**Home Assistant Custom Integration zum Abruf __geeichter Tagesendwerte__ von PPC Smart Meter Gateways über die HAN-Schnittstelle.**

<a href="README.en.md">English version</a>

<br clear="left">

## Was macht diese Integration?

Die Integration verbindet sich einmal täglich mit dem PPC SMGW und ruft die offiziellen, eichrechtskonformen Tagesendwerte vom Zählerstand-Endpunkt ab. Sie berechnet:

- **Tagesverbrauch (gesamt)** - gesamter Stromverbrauch des Vortags
- **Tagesverbrauch (Zeitfenster 1)** - Verbrauch im ersten Tarifzeitraum (Standard: 00:00–04:59)
- **Tagesverbrauch (Zeitfenster 2)** - Verbrauch im zweiten Tarifzeitraum (Standard: 05:00–23:59)
- **Tageseinspeisung (gesamt)** - gesamte Netzeinspeisung des Vortags

Alle Sensoren sind kompatibel mit dem **Home Assistant Energie-Dashboard**.

## Unterschied zu ha-ppc-smgw

Die bestehende [ha-ppc-smgw](https://github.com/jannickfahlbusch/ha-ppc-smgw)-Integration fragt aktuelle Zählerstände in festen 10-Minuten-Intervallen ab (unabhängig von der Nutzereinstellung beim Setup). Einige Nutzer berichten, dass sie vom SMGW gesperrt wurden, weil die Abfragehäufigkeit als zu hoch eingestuft wurde. Diese Integration verfolgt einen anderen Ansatz:

- **Ein Abruf pro Tag** (5 HTTP-Requests insgesamt, zu einer konfigurierbaren Uhrzeit. Damit kein Risiko einer SMGW-Sperrung wegen Überbeanspruchung)
- **Geeichte Werte** vom Zählerstand-Endpunkt des SMGW (keine Live-Momentaufnahmen)
- **Exakte Tarifaufteilung** anhand des sekundengenauen Zählerstands zum konfigurierten Tarifwechselzeitpunkt
- **Keine Timing-Probleme** - die Werte basieren auf den offiziellen Tagesgrenzen des SMGW, nicht auf der lokalen Uhrzeit des „Home Assistant"-Servers
- **Mehrere Zähler und SMGWs parallel** - die Integration unterstützt sowohl mehrere SMGWs als auch mehrere Zähler an einem SMGW (Modul-2-Konstellationen, getrennte Logins für Verbrauch und Einspeisung). Details unter [Mehrere SMGWs / mehrere Zugänge](#mehrere-smgws--mehrere-zugänge).

## Voraussetzungen

- PPC Smart Meter Gateway mit aktivierter HAN-Schnittstelle
- HAN-Zugangsdaten (Benutzername + Passwort) vom Messstellenbetreiber (MSB)
- Der "Home Assistant"-Server und das SMGW müssen sich IP-technisch gegenseitig "sehen" können.

> [!TIP]
> **EINE EINFACHE LÖSUNG FÜR DAS SMGW IP-ROUTING-PROBLEM:**  
> _(Home Assistant und SMGW im selben IP-Bereich erreichbar machen)_
>   
> Das SMGW ist in der Regel unveränderbar auf `192.168.100.100` konfiguriert, Home Assistant läuft meist auf einer lokalen IP wie z.B. `192.168.2.x` o.ä.
> Wie du deinem HA-Server ganz einfach eine zweite IP im `192.168.100.x`-Netz gibst und damit die Verbindung herstellst, erklärt die
> [Netzwerk-Einrichtungsanleitung](docs/network-setup.md).

## Installation

### HACS (empfohlen)

1. HACS in Home Assistant öffnen
2. Integrationen → Drei-Punkte-Menü → Benutzerdefinierte Repositories
3. `https://github.com/TRON4R/ha-ppc-smgw-han` als Integration hinzufügen
4. „PPC SMGW HAN Daily Import" installieren
5. Home Assistant neu starten

### Manuell

1. `custom_components/smgw_han/` in das `custom_components/`-Verzeichnis von Home Assistant kopieren
2. Home Assistant neu starten

### Beta-Versionen ausprobieren

Falls eine Vorab-Version (Pre-Release) verfügbar ist und du sie testen möchtest:

1. HACS → Integrationen → „PPC SMGW HAN Daily Import" öffnen
2. Drei-Punkte-Menü oben rechts → **„Erneut herunterladen"**
3. Im Dialog **„Benötigst du eine andere Version?"** aufklappen
4. Im Dropdown **„Release"** die gewünschte Version (mit orangem `pre-release`-Label) auswählen
5. **„Herunterladen"** klicken
6. Home Assistant neu starten

Die Bestandskonfiguration bleibt unverändert — alle Entitäten und die Energy-Dashboard-Historie bleiben erhalten.

## Konfiguration

1. Einstellungen → Geräte & Dienste → Integration hinzufügen
2. Nach „PPC SMGW HAN" suchen
3. Eingeben:
   - **URL**: URL der SMGW HAN-Schnittstelle (Standard: `https://192.168.100.100/cgi-bin/hanservice.cgi`)
   - **Benutzername** und **Passwort**: HAN-Zugangsdaten
   - **Start Standard-Tarif**: Uhrzeit des Tarifwechsels (Standard: 05:00, konfigurierbar)
   - **Abrufzeit**: Uhrzeit des täglichen Datenabrufs (Standard: 00:15)
   - **Gerätename** (optional, siehe nächster Abschnitt)

## Mehrere SMGWs / mehrere Zugänge

Seit Version 2.0 kann die Integration beliebig viele SMGW-Instanzen parallel verwalten. Klicke einfach erneut auf „Integration hinzufügen" und lege einen weiteren Zugang an. Jeder Eintrag bekommt einen eigenen Satz Entitäten und ein eigenes Gerät im Geräte-Register.

Typische Anwendungsfälle:

- **Zwei physische Zähler an *einem* SMGW** (z.B. Modul-2-Konstellation mit Bezugs- und separatem Erzeugungszähler am selben SMGW): Beim Anlegen eines neuen Eintrags erkennt die Integration nach dem Login automatisch, dass der SMGW mehrere Zähler im Dropdown anbietet, und blendet einen zusätzlichen Schritt ein, in dem du auswählst, welcher dieser Zähler dem Eintrag zugeordnet werden soll. Für den zweiten Zähler legst du danach einfach einen weiteren Eintrag mit denselben Zugangsdaten an und wählst dort den anderen Zähler.
- **Zwei getrennte SMGWs** (z.B. zwei Häuser oder unabhängige Messstellen): Jeder SMGW wird mit seinen eigenen Zugangsdaten und ggf. eigener IP-Adresse als separater Eintrag angelegt.
- **Ein SMGW, zwei Logins**: Manche Messstellenbetreiber vergeben separate HAN-Zugangsdaten für die Verbrauchsabfrage (OBIS 1.8.0) und die Einspeiseabfrage (OBIS 2.8.0). Beide Logins können als zwei unabhängige Einträge gegen denselben SMGW konfiguriert werden. In diesem Fall solltest du das optionale Feld **Gerätename** nutzen und sprechende Namen wie „SMGW Verbrauch" und „SMGW Einspeisung" vergeben, damit sich die beiden Geräte in Home Assistant unterscheiden lassen.

Das Feld **Gerätename** bleibt leer, wenn du nur einen einzelnen SMGW konfigurierst oder wenn die SMGWs ohnehin unterschiedliche physische Zähler abfragen — dann genügt der Standardname „PPC SMGW", den Home Assistant bei mehreren gleichnamigen Geräten automatisch durchnummeriert.

### Verhalten beim Zählertausch

Wenn der Messstellenbetreiber den physischen Zähler im Keller tauscht, kannst du die Zugangsdaten einfach über den Optionen-Dialog des bestehenden Eintrags aktualisieren — Entitäten und Statistik-Historie bleiben unverändert erhalten. Auch wenn du den Eintrag stattdessen löschst und neu anlegst, bekommt der neue Eintrag dieselbe interne Nummer wie der vorherige (z.B. wieder „smgw_meter1"), sodass die Long-Term-Statistik im Energie-Dashboard nahtlos weitergeführt wird.

## Sensoren

| Sensor | Beschreibung | Device Class | State Class |
|---|---|---|---|
| Tagesverbrauch gesamt | Gesamtverbrauch des Vortags | `energy` | `total` |
| Tagesverbrauch Zeitfenster 1 | Verbrauch Zeitfenster 1 (Mitternacht → Tarifwechsel) | `energy` | `total` |
| Tagesverbrauch Zeitfenster 2 | Verbrauch Zeitfenster 2 (Tarifwechsel → Mitternacht) | `energy` | `total` |
| Tageseinspeisung gesamt | Gesamteinspeisung des Vortags | `energy` | `total` |
| Zählerstand Verbrauch Endstand Vortag | Absoluter Zählerstand zu Tagesbeginn (00:00) | `energy` | `total_increasing` |
| Zählerstand Verbrauch Tarifwechsel 1 | Absoluter Zählerstand zum Tarifwechselzeitpunkt | `energy` | `total_increasing` |
| Zählerstand Einspeisung Endstand Vortag | Absoluter Einspeise-Zählerstand zu Tagesbeginn (00:00) | `energy` | `total_increasing` |
| Tagesdatum | Datum der zuletzt abgerufenen Daten | `date` | — |

## Datenexport für beliebige Zeiträume

Über den Dienst **`smgw_han.export_readings`** lassen sich die Zählerdaten für einen **frei wählbaren Zeitraum** direkt aus Home Assistant abrufen — ohne Umweg über das SMGW-Webinterface.

**Parameter:**

| Feld | Pflicht | Beschreibung |
|---|---|---|
| `device_id` | ✅ | Das abzufragende SMGW-Gerät |
| `period` | – | **Zeitraum-Vorgabe** als Dropdown: `Gestern`, `Letzte 7 Tage`, `Letzte 30 Tage`, `Aktueller Monat`, `Letzter Monat`. Berechnet `from`/`to` automatisch (inkl. korrektem Tagesabschluss). Bei `Eigener Zeitraum` werden stattdessen die beiden Felder unten genutzt. |
| `from_datetime` | (✅) | Beginn (Datum + Uhrzeit) — **nur bei `Eigener Zeitraum`** |
| `to_datetime` | (✅) | Ende (Datum + Uhrzeit) — **nur bei `Eigener Zeitraum`** |
| `download_cms` | – | Speichert zusätzlich das signierte **CMS-Original** (fälschungssicher, wie der „Exportieren"-Button im Webinterface) |
| `write_csv` | – | Speichert zusätzlich die Rohdaten als **CSV** (semikolongetrennt, Excel-freundlich) |
| `write_xlsx` | – | Speichert zusätzlich eine **Excel-Mappe** (Rohdaten, Tagesendwerte, Tarifzonen) |

> **Tipp Zeitraum-Vorgabe:** Mit `period` musst Du das End-Datum nicht selbst ausrechnen. „Letzter Monat" liefert z.B. automatisch den **vollständigen** Vormonat inklusive des Abschluss-Zählerstands am Monatsersten 00:00 — das ist der Wert, den man von Hand leicht vergisst.

**Rückgabe (Response-Variable):** Der Dienst gibt immer die Roh-Readings (`readings`) und eine Tagessummen-Aufstellung (`daily_summary`) zurück — sichtbar direkt in **Entwicklerwerkzeuge → Aktionen** oder in Skripten via `response_variable`. Sind ein oder mehrere Datei-Schalter gesetzt, enthält die Antwort zusätzlich `files` mit Download-Links.

**Aufruf-Beispiel (Skript/Automation):**

```yaml
action: smgw_han.export_readings
data:
  device_id: <deine Geräte-ID>
  from_datetime: "2026-05-01 00:00:00"
  to_datetime: "2026-05-08 00:00:00"
  write_csv: true
  write_xlsx: true
  download_cms: true
response_variable: smgw_export
```

### Klickbare Download-Links per Benachrichtigung

In den Entwicklerwerkzeugen wird die Antwort als YAML angezeigt — die Links sind dort **nicht anklickbar**. Mit folgendem Skript (Einstellungen → Automationen & Szenen → Skripte → „in YAML bearbeiten") bekommst Du nach dem Export eine **Benachrichtigung mit anklickbaren Links**. Das Skript lässt sich per Knopf, Sprachassistent oder aus einer Automation auslösen:

```yaml
alias: SMGW Export mit Benachrichtigung
fields:
  device_id:
    selector:
      device:
        integration: smgw_han
  period:
    selector:
      select:
        options: [custom, yesterday, last_7_days, last_30_days, current_month, last_month]
sequence:
  - action: smgw_han.export_readings
    data:
      device_id: "{{ device_id }}"
      period: "{{ period | default('last_month') }}"
      download_cms: true
      write_csv: true
      write_xlsx: true
    response_variable: result
  - action: persistent_notification.create
    data:
      title: SMGW Export
      message: >-
        {{ result.reading_count }} Werte, {{ result.daily_summary | count }} Tage.
        {% set f = result.files | default({}) %}
        {% if f.csv %}[CSV]({{ f.csv }}) · {% endif %}
        {% if f.xlsx %}[Excel]({{ f.xlsx }}) · {% endif %}
        {% if f.cms %}[CMS]({{ f.cms }}){% endif %}
```

Die Benachrichtigung erscheint dann unter dem Glocken-Symbol mit klickbaren Links zu CSV/Excel/CMS.

**Wichtige Hinweise:**

- **SMGW schonen:** Jeder Aufruf öffnet eine echte SMGW-Sitzung. Den Dienst **nicht in Schleifen** aufrufen — das SMGW erlaubt nur eine aktive Sitzung und kann bei Überlastung kurzzeitig sperren. Der nächtliche Abruf und ein manueller Export blockieren sich gegenseitig automatisch (kein Konflikt), laufen aber nacheinander.
- **Download-Links sind unauthentifiziert:** Die Dateien landen unter `config/www/smgw_han_exports/<zufallscode>/` und sind als `/local/…`-Link **ohne Anmeldung** erreichbar. Wer den Link kennt, kann die Datei laden. Der Zufallscode im Pfad erschwert das Erraten; lösche nicht mehr benötigte Export-Ordner gelegentlich.
- Erscheinen die `/local/`-Links beim allerersten Export nicht, lege den Ordner `config/www/` einmal manuell an und starte HA neu (Home Assistant bindet `www/` nur beim Start ein).

## Dashboard-Kachel: Verbrauchshistorie (täglich)

**Voraussetzung:** [ApexCharts Card](https://github.com/RomRider/apexcharts-card) (über HACS installierbar)

![Verbrauchshistorie SMGW täglich](dashboard/verbrauchshistorie_taeglich.png)

Die Kachel zeigt die letzten 30 Tage als gestapeltes Balkendiagramm:
- **Go** (blau): Verbrauch im vergünstigten Zeitfenster (Zeitfenster 1)
- **Standard** (pink): Verbrauch im Normalpreis-Zeitfenster (Zeitfenster 2)
- **Tooltip** (mouse-over): Einzelwerte je Tarifsegment pro Tag
- **Kopfzeile**: kumulierter Gesamtverbrauch je Segment im angezeigten Zeitraum

### Einbindung

1. [`dashboard/verbrauchshistorie_taeglich.yaml`](dashboard/verbrauchshistorie_taeglich.yaml) herunterladen
2. In Home Assistant: Dashboard → Kachel hinzufügen → Manuelle Karte
3. YAML einfügen und die Entity-IDs auf die eigenen anpassen:
   - `sensor.octopus_smgw_tagesverbrauch_zeitfenster_2` → eigene Entity-ID für Zeitfenster 2
   - `sensor.octopus_smgw_tagesverbrauch_zeitfenster_1` → eigene Entity-ID für Zeitfenster 1

Die Entity-IDs findest du unter **Einstellungen → Geräte & Dienste → Entitäten**.

## Anwendungsfall

Diese Integration wurde primär für den **Octopus Energy (Intelligent) Go-Tarif** in Deutschland entwickelt, der einen vergünstigten Strompreis zwischen **00:00 und 04:59:59** (Go-Tarif) und einen Normalpreis von **05:00 bis 23:59:59** (Standard-Tarif) bietet.

Der **Tarifwechselzeitpunkt** ist aber für andere Tarife problemlos über das GUI **frei einstellbar**. 

Falls du eine völlig andere Tarifstruktur nutzen solltest, eröffne bitte ein [Issue](https://github.com/TRON4R/ha-ppc-smgw-han/issues) oder idealerweise gleich einen [Pull Request](https://github.com/TRON4R/ha-ppc-smgw-han/pulls), damit wir gemeinsam die Integration entsprechend erweitern können.

## Lizenz

MIT-Lizenz — siehe [LICENSE](LICENSE) für Details.
