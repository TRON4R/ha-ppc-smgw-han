
# ha-ppc-smgw-han

<img src="custom_components/smgw_han/brand/icon.png" alt="SMGW Icon" width="128" align="left" style="margin-right: 16px;">

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration) [![Downloads über GitHub-Releases](https://img.shields.io/github/downloads/TRON4R/ha-ppc-smgw-han/total?label=Downloads&color=41BDF5&logo=github&logoColor=white)](https://github.com/TRON4R/ha-ppc-smgw-han/releases)

<!--
Der Analytics-Zähler bleibt vorerst deaktiviert: Home Assistant Analytics
filtert Custom Integrations gegen brands.home-assistant.io/domains.json, und
seit HA 2026.3 nimmt das Brands-Repo keine Custom Integrations mehr auf. Die
Domain smgw_han kann dort also gar nicht auftauchen, das Badge zeigte nur
"no result". Siehe home-assistant/analytics.home-assistant.io#1094 — wenn das
behoben ist, reicht es, die Kommentarzeichen zu entfernen.

[![Aktive Installationen laut Home Assistant Analytics](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fanalytics.home-assistant.io%2Fcustom_integrations.json&query=%24.smgw_han.total&label=aktive%20Installationen&color=41BDF5&logo=home-assistant&logoColor=white&cacheSeconds=21600)](https://analytics.home-assistant.io/custom_integrations)
-->

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=TRON4R&repository=ha-ppc-smgw-han)

**Home Assistant Custom Integration zum Abruf __geeichter Tagesendwerte__ von PPC Smart Meter Gateways über die HAN-Schnittstelle.**

<a href="README.en.md">English version</a>

<br clear="left">

## Was macht diese Integration?

Die Integration verbindet sich einmal täglich automatisch mit dem PPC SMGW und ruft die offiziellen, eichrechtskonformen Tagesendwerte vom Zählerstand-Endpunkt ab. Sie liefert:

- **Tagesverbrauch (gesamt)** - gesamter Stromverbrauch des Vortags
- **Tagesverbrauch pro Tarifzone** - mit **frei konfigurierbaren Tarifzonen** (seit v3.0.0): von einfachen Zwei-Zonen-Tarifen wie Octopus Go (Standard: 00:00–04:59 / 05:00–23:59) bis zu Tarifen mit mehreren Zeitfenstern pro Zone wie Octopus Heat
- **Tageseinspeisung (gesamt)** - gesamte Netzeinspeisung des Vortags
- **Energie-Dashboard-kompatibel** - alle Sensoren lassen sich direkt im Home Assistant Energie-Dashboard verwenden
- **Separater Datenexport für beliebige Zeiträume** - auf Abruf und unabhängig von den Sensoren: Die Daten kommen direkt aus dem Speicher des SMGW (je nach Gerät 15–24 Monate Historie, also auch aus der Zeit **vor** der Installation der Integration) — bequem aus der Home-Assistant-Oberfläche, ohne umständliches manuelles Einloggen am SMGW-Webinterface. Ausgabe als CSV, Excel oder **signiertes CMS-Original**. Siehe [Datenexport für beliebige Zeiträume](https://github.com/TRON4R/ha-ppc-smgw-han#-datenexport-f%C3%BCr-beliebige-zeitr%C3%A4ume)

## Unterschied zu anderen SMGW-Integrationen

Eine andere SMGW-Integration fragt aktuelle Zählerstände z.B. in festen 10-Minuten-Intervallen ab. Einige Nutzer berichten, dass sie deswegen von ihrem SMGW ausgesperrt wurden, weil die Abfragehäufigkeit als zu hoch eingestuft wurde. Diese Integration verfolgt einen anderen Ansatz:

- **Ein Abruf pro Tag** (5 HTTP-Requests insgesamt, zu einer konfigurierbaren Uhrzeit. Damit kein Risiko einer SMGW-Sperrung wegen Überbeanspruchung)
- **Geeichte Werte** vom Zählerstand-Endpunkt des SMGW (keine Live-Momentaufnahmen)
- **Exakte Tarifaufteilung** anhand der sekundengenauen Zählerstände an den konfigurierten Tarif-Umschaltpunkten
- **Keine Timing-Probleme** - die Werte basieren auf den offiziellen Tagesgrenzen des SMGW, nicht auf der lokalen Uhrzeit des „Home Assistant"-Servers
- **Mehrere Zähler und SMGWs parallel** - die Integration unterstützt sowohl mehrere SMGWs als auch mehrere Zähler an einem SMGW (Modul-2-Konstellationen, getrennte Logins für Verbrauch und Einspeisung). Details unter [Mehrere SMGWs / mehrere Zugänge](https://github.com/TRON4R/ha-ppc-smgw-han#mehrere-smgws--mehrere-zug%C3%A4nge).
- **Export der zertifizierten CMS-Dateien** - die Integration erlaubt den Export von rechtssicheren CMS-Dateien im zertifizierten Original direkt aus dem SMGW (z.B. für den Nachweis von Rechnungsfehlern durch den Stromlieferanten) sowie die Erzeugung von CSV- und Excel-Dateien für die Weiterverarbeitung der Verbrauchs- und Einspeisedaten. Details unter [Datenexport für beliebige Zeiträume](https://github.com/TRON4R/ha-ppc-smgw-han#-datenexport-f%C3%BCr-beliebige-zeitr%C3%A4ume).

## Voraussetzungen

- PPC Smart Meter Gateway mit aktivierter HAN-Schnittstelle
- HAN-Zugangsdaten (Benutzername + Passwort) vom Messstellenbetreiber (MSB)
- Der "Home Assistant"-Server und das SMGW müssen sich IP-technisch gegenseitig "sehen" können.

> [!TIP]
> **EINE EINFACHE LÖSUNG FÜR DAS SMGW IP-ROUTING-PROBLEM**   
> _(Home Assistant und SMGW im selben IP-Bereich erreichbar machen)_: 
>
> Das SMGW ist in der Regel unveränderbar auf `192.168.100.100` konfiguriert, Home Assistant läuft meist auf einer lokalen IP wie z.B. `192.168.2.x` o.ä.
> Wie du deinem HA-Server ganz einfach eine zweite IP im `192.168.100.x`-Netz gibst und damit die Verbindung herstellst, erklärt die
> [Netzwerk-Einrichtungsanleitung](docs/network-setup.md).

## Installation

### HACS (empfohlen)

1. In Home Assistant in der linken Seitenleiste **HACS** öffnen
2. Oben rechts das **Drei-Punkte-Menü** → **Benutzerdefinierte Repositories**
3. Im Dialog:
   - Feld *Repository*: `https://github.com/TRON4R/ha-ppc-smgw-han`
   - Dropdown *Typ*: **Integration**
   - <kbd>HINZUFÜGEN</kbd> klicken, danach den Dialog oben links mit **X** schließen
4. Im HACS-Suchfeld nach `PPC SMGW HAN` suchen, den Eintrag **PPC SMGW HAN Daily Import** öffnen und unten rechts <kbd>HERUNTERLADEN</kbd> klicken
5. Home Assistant neu starten

> [!NOTE]
> Die Integration erscheint in der HACS-Suche **erst nach** Schritt 2–3. Wer vorher sucht, bekommt „Repository nicht gefunden".

### Manuell

1. `custom_components/smgw_han/` in das `custom_components/`-Verzeichnis von Home Assistant kopieren
2. Home Assistant neu starten

### Beta-Versionen ausprobieren

Falls eine Vorab-Version (Pre-Release) verfügbar ist und du sie testen möchtest:

1. **HACS** öffnen und den Eintrag **PPC SMGW HAN Daily Import** anklicken
2. Drei-Punkte-Menü oben rechts → **„Erneut herunterladen"**
3. Im Dialog **„Benötigst du eine andere Version?"** aufklappen
4. Im Dropdown **„Release"** die gewünschte Version (mit orangem `pre-release`-Label) auswählen
5. **„Herunterladen"** klicken
6. Home Assistant neu starten

Die Bestandskonfiguration bleibt unverändert — alle Entitäten und die Energy-Dashboard-Historie bleiben erhalten.

## Konfiguration

1. Einstellungen → Geräte & Dienste → Integration hinzufügen
2. Nach „PPC SMGW HAN" suchen
3. **Tarif auswählen** – es erscheinen drei Schaltflächen:

   | Auswahl | Vorbelegte Umschaltzeiten |
   |---|---|
   | **Octopus Go / Intelligent Octopus Go** | `00:00 Go`, `05:00 Standard` |
   | **Octopus Heat** | `00:00 Standard`, `02:00 Niedrig`, `06:00 Standard`, `12:00 Niedrig`, `16:00 Standard`, `18:00 Hoch`, `21:00 Standard` |
   | **Eigene Zeiten eingeben** | `00:00 Zeitfenster 1`, `05:00 Zeitfenster 2` |

   Die Auswahl **füllt die Zeiten im nächsten Schritt nur vor** – du siehst sie dort und kannst sie beliebig ändern; gespeichert wird nichts ungefragt. Bei Octopus Heat ergeben die sieben Umschaltpunkte drei Sensoren („Standard", „Niedrig", „Hoch"), weil gleichnamige Fenster zusammengezählt werden; das Standard-Fenster 21:00–02:00 läuft über Mitternacht und erscheint deshalb als erster und letzter Eintrag.

   > [!NOTE]
   > Octopus-Zeiten Stand 07/2026. Tarifzeiten können sich ändern und je nach Vertrag abweichen – bitte kurz mit deinem Vertrag abgleichen.

4. Eingeben:
   - **URL**: URL der SMGW HAN-Schnittstelle (Standard: `https://192.168.100.100/cgi-bin/hanservice.cgi`)
   - **Benutzername** und **Passwort**: HAN-Zugangsdaten
   - **Tarifzonen (Umschaltpunkte)**: ein Eintrag pro Umschaltpunkt im Format `HH:MM Zonenname`, beginnend mit `00:00`; der letzte Eintrag gilt bis Mitternacht. Für **Octopus Go/Intelligent Octopus Go** und **Octopus Heat** gibt es fertige Vorlagen (siehe oben) — du kannst die Zeiten aber jederzeit frei eingeben oder anpassen. Gleiche Zonennamen werden zu **einem** gemeinsamen Sensor summiert — so lassen sich auch Tarife mit mehreren Zeitfenstern pro Zone abbilden. Möchtest du stattdessen **jedes Zeitfenster einzeln erfassen** — auch wenn es beim Stromanbieter denselben Namen und Arbeitspreis trägt —, vergib einfach unterschiedliche Namen (z. B. `Standard1`/`Standard2` oder `Niedrig-Nacht`/`Niedrig-Mittag`): dann bekommt jedes Fenster seinen eigenen Sensor. Die Umschaltzeiten müssen auf dem 15-Minuten-Messraster des SMGW liegen (Minuten `00`, `15`, `30` oder `45`) — andere Uhrzeiten werden direkt bei der Eingabe mit einer Fehlermeldung abgelehnt, es wird nichts automatisch gerundet
   - **Abrufzeit**: Uhrzeit des täglichen Datenabrufs (Standard: 00:15)
   - **Gerätename** (optional, siehe nächster Abschnitt)

> [!NOTE]
> **Update von v2.x:** Bestehende Einträge werden beim ersten Start automatisch auf das Tarifzonen-Modell migriert, d.h. die vorhandenen Entitäten, Namen und die Energy-Dashboard-Historie bleiben erhalten. Die Migration ist **einmalig** (ein Downgrade auf eine Version vor 3.0.0 erfordert ein Backup oder das Neuanlegen des Eintrags).

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
| Tagesverbrauch *{Zonenname}* | Verbrauch pro konfigurierter Tarifzone (ein Sensor je Zonenname; Segmente mit gleichem Namen werden summiert) | `energy` | `total` |
| Tageseinspeisung gesamt | Gesamteinspeisung des Vortags | `energy` | `total` |
| Zählerstand Verbrauch Endstand Vortag | Absoluter Zählerstand am Ende des Vortags (Mitternacht) | `energy` | `total_increasing` |
| Zählerstand Verbrauch Tarifwechsel *N* | Absoluter Zählerstand an jedem inneren Umschaltpunkt (ein Sensor je Umschaltpunkt) | `energy` | `total_increasing` |
| Zählerstand Einspeisung Endstand Vortag | Absoluter Einspeise-Zählerstand am Ende des Vortags (Mitternacht) | `energy` | `total_increasing` |
| Tagesdatum | Datum der zuletzt abgerufenen Daten | `date` | — |

## 📤 Datenexport für beliebige Zeiträume

Die Zählerdaten lassen sich für einen **frei wählbaren Zeitraum** direkt aus Home Assistant abrufen — ohne Umweg über das SMGW-Webinterface. Ausgabe als **CSV**, **Excel** und/oder als **signiertes CMS-Original**.

**Drei Wege – vom einfachsten zum flexibelsten** (Details jeweils unten):

- 🛠️ **Am einfachsten – über die Integration:** Beim SMGW-Gerät auf das **Zahnrad „Konfigurieren"** → **„SMGW-Daten für einen wählbaren Zeitraum exportieren"**. Geführtes Formular, keine Vorkenntnisse und keine Helfer erforderlich.
- 📊 **Ein-Klick, nur Vorgaben – Dashboard-Kachel:** Buttons für „Gestern", „Letzter Monat" usw.
- ⚙️ **Volle Kontrolle – Entwicklerwerkzeuge → Aktionen:** beliebige Parameter, Antwort inkl. Download-Links direkt sichtbar.

Technisch dahinter stehen **zwei Aktionen/Dienste**:

- **`smgw_han.export_readings`** — eigener Zeitraum über `from_datetime` / `to_datetime`.
- **`smgw_han.export_period`** — fertige **Zeitraum-Vorgabe** (`Gestern`, `Letzte 7 Tage`, `Letzte 30 Tage`, `Aktueller Monat`, `Letzter Monat`); `from`/`to` inkl. korrektem Tagesabschluss werden automatisch berechnet.

Beide liefern dasselbe Ergebnis: eine Antwort-Variable mit `readings` + `daily_summary`, bei gesetzten Datei-Schaltern zusätzlich `files` mit Download-Links.

---

### Weg 1: Über die Integration („Konfigurieren") – am einfachsten

Ganz ohne Entwicklerwerkzeuge, Helfer oder Dashboard: **Einstellungen → Geräte & Dienste → dein SMGW → Zahnrad „Konfigurieren"** → Menüpunkt **„SMGW-Daten für einen wählbaren Zeitraum exportieren"**. Dort wählst du einen Zeitraum (Vorgabe), bestätigst bzw. änderst im nächsten Schritt die **vorausgefüllten** Von/Bis-Felder, und nach dem Export erscheinen die Download-Links direkt im Abschluss-Schritt **und** als Benachrichtigung 🔔. Die normale Erst-Einrichtung bleibt davon unberührt.

### Weg 2: Über eine Dashboard-Kachel (Schnellwahl)

Eine fertige Kachel mit Buttons für die Zeitraum-Vorgaben liegt unter [`dashboard/datenexport.yaml`](dashboard/datenexport.yaml). Sie ruft ein kleines Skript auf, das nach dem Export eine **Benachrichtigung mit anklickbaren Links** zeigt.

**a) Skript anlegen** (Einstellungen → Automationen & Szenen → Skripte → „in YAML bearbeiten") — ergibt die Entity-ID `script.smgw_export_mit_benachrichtigung`:

```yaml
alias: SMGW Export mit Benachrichtigung
fields:
  period:
    selector:
      select:
        options: [yesterday, last_7_days, last_30_days, current_month, last_month]
sequence:
  - action: smgw_han.export_period
    data:
      # device_id entfällt bei nur einem SMGW (wird automatisch erkannt).
      # Mehrere SMGWs? Hier device_id: <deine-device-id> ergänzen.
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
        {% if f.cms %}[CMS]({{ f.cms }}) · {% endif %}
        {% if f.csv %}[CSV]({{ f.csv }}) · {% endif %}
        {% if f.xlsx %}[Excel]({{ f.xlsx }}){% endif %}
```

**b) Kachel einbinden:** Dashboard → Kachel hinzufügen → Manuelle Karte → YAML aus [`dashboard/datenexport.yaml`](dashboard/datenexport.yaml) einfügen. **Bei nur einem SMGW ist nichts weiter zu tun** — das Gerät wird automatisch erkannt. Ein Klick auf z.B. „Letzter Monat" erzeugt den Export und zeigt die Links als Benachrichtigung.

> **Mehrere SMGWs?** Dann im Skript bzw. an jedem Kachel-Button unter `data:` eine Zeile `device_id: <deine-device-id>` ergänzen. Die `device_id` zeigt die Diagnose-Entität **„Geräte-ID"** am jeweiligen SMGW-Gerät (ihr Status ist die device_id zum Kopieren).

### Weg 3: Über die Entwicklerwerkzeuge → Aktionen (volle Kontrolle)

In **Entwicklerwerkzeuge → Aktionen** lassen sich beide Dienste mit beliebigen Parametern aufrufen; die Antwort (inkl. Download-Links) wird direkt angezeigt. Parameter:

| Feld | Aktion | Beschreibung |
|---|---|---|
| `device_id` | beide | Das abzufragende SMGW-Gerät (bei nur einem SMGW optional) |
| `from_datetime` / `to_datetime` | `export_readings` | Beginn/Ende des Zeitraums (Datum + Uhrzeit) |
| `period` | `export_period` | Fertiger Zeitraum (Dropdown) |
| `download_cms` | beide | Speichert zusätzlich das signierte **CMS-Original** (fälschungssicher, wie der „Exportieren"-Button im Webinterface) |
| `write_csv` | beide | Speichert zusätzlich die Rohdaten als **CSV** (semikolongetrennt, Excel-freundlich) |
| `write_xlsx` | beide | Speichert zusätzlich eine **Excel-Mappe** (Rohdaten, Tagesendwerte, Tarifzonen) |

> **Tipp:** „Letzter Monat" (in `export_period`) liefert automatisch den **vollständigen** Vormonat inklusive des Abschluss-Zählerstands am Monatsersten 00:00 — der Wert, den man bei manueller Eingabe leicht vergisst. Bei `export_readings` daran denken: für den Tagesabschluss des letzten Tages das `to` auf **00:15 des Folgetags** setzen.

**Aufruf-Beispiele (zum Einbau in ein Skript oder eine Automation):**

```yaml
# Eigener Zeitraum
action: smgw_han.export_readings
data:
  device_id: <deine Geräte-ID>   # bei nur einem SMGW weglassen
  from_datetime: "2026-05-01 00:00:00"
  to_datetime: "2026-06-01 00:15:00"
  write_csv: true
  write_xlsx: true
  download_cms: true
response_variable: smgw_export
```

```yaml
# Fertige Vorgabe (kein Datums-Raten)
action: smgw_han.export_period
data:
  period: last_month
  write_csv: true
  write_xlsx: true
  download_cms: true
response_variable: smgw_export
```

Damit die Links **anklickbar** werden, die Antwort in einem Folgeschritt nutzen — z.B. mit dem Benachrichtigungs-Skript aus Weg 2.

> [!CAUTION]
> **Wichtige Hinweise**
>
> - **SMGW schonen:** Jeder Aufruf öffnet eine echte SMGW-Sitzung. Den Dienst **nicht in Schleifen** aufrufen — das SMGW erlaubt nur eine aktive Sitzung und kann bei Überlastung kurzzeitig sperren. Der nächtliche Abruf und ein manueller Export blockieren sich gegenseitig automatisch (kein Konflikt), laufen aber nacheinander.
> - **Download-Links sind unauthentifiziert:** Die Dateien landen unter `config/www/smgw_han_exports/<zufallscode>/` und sind als `/local/…`-Link **ohne Anmeldung** erreichbar. Wer den Link kennt, kann die Datei laden. Der Zufallscode im Pfad erschwert das Erraten; lösche nicht mehr benötigte Export-Ordner gelegentlich.
> - Erscheinen die `/local/`-Links beim allerersten Export nicht, lege den Ordner `config/www/` einmal manuell an und starte HA neu (Home Assistant bindet `www/` nur beim Start ein).

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

Diese Integration wurde anfangs für den **Octopus Energy (Intelligent) Go-Tarif** in Deutschland entwickelt, der einen vergünstigten Strompreis zwischen **00:00 und 04:59:59** (Go-Tarif) und einen Normalpreis von **05:00 bis 23:59:59** (Standard-Tarif) bietet. Inzwischen wurde sie erweitert, sodass sie z.B. auch den Octopus Heat Tarif abbilden kann, aber natürlich auch Tarife von anderen Stromanbietern. 

Die **Tarifzonen** sind für andere Tarife **frei konfigurierbar** — beliebig viele Umschaltpunkte pro Tag, direkt über das GUI.

Falls du eine völlig andere Tarifstruktur nutzen solltest, eröffne bitte ein [Issue](https://github.com/TRON4R/ha-ppc-smgw-han/issues) oder idealerweise gleich einen [Pull Request](https://github.com/TRON4R/ha-ppc-smgw-han/pulls), damit wir gemeinsam die Integration entsprechend erweitern können.

## Lizenz

MIT-Lizenz — siehe [LICENSE](LICENSE) für Details.
