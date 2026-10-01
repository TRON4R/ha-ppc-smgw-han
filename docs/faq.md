# FAQ – häufige Fragen

<!--
Pflegeregel für die Kennungen:
- Die Kennungen (E1, S1, ...) sind in faq.md und faq.en.md identisch, damit ein Verweis wie „FAQ S1" in beiden Sprachen passt.
- Kennungen werden nie umnummeriert oder neu vergeben, weil Issues und Discussions darauf verweisen.
- Eine neue Frage bekommt die nächste freie Nummer ihres Abschnitts, wird am Ende des Abschnitts angehängt und in beide Dateien übernommen.
- Wird eine Frage gestrichen, bleibt ihre Kennung unbesetzt.
- Jede Frage trägt einen kurzen Sprunganker <a id="..."></a> in der Überschrift und steht in der Übersicht.
-->

Die Fragen stammen aus den [Issues](https://github.com/TRON4R/ha-ppc-smgw-han/issues) und [Discussions](https://github.com/TRON4R/ha-ppc-smgw-han/discussions) dieses Repos. Jede Frage hat eine feste Kennung wie **S1**, auf die du direkt verlinken kannst: `https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/docs/faq.md#s1`.

Fehlt deine Frage, lies bitte zuerst [H1 – Bevor du ein Issue eröffnest](#h1). Dann geht die Hilfe deutlich schneller.

Zurück zum [README](https://github.com/TRON4R/ha-ppc-smgw-han#readme) · [English version](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/docs/faq.en.md)

## Übersicht

**Einrichtung und Verbindung**
- [E1](#e1) Home Assistant und SMGW liegen in unterschiedlichen Netzen – wie verbinde ich sie?
- [E2](#e2) Die Einrichtung bricht mit einer Fehlermeldung ab
- [E3](#e3) Ping auf das SMGW geht nicht
- [E4](#e4) Mein Laptop bekommt am SMGW keine IP-Adresse
- [E5](#e5) Woher bekomme ich die HAN-Zugangsdaten?
- [E6](#e6) Wie sehe ich die Daten direkt am SMGW an – im Browser oder mit TRuDI?
- [E7](#e7) Funktioniert die Integration auch mit SMGW anderer Hersteller?
- [E8](#e8) Muss ich Kunde von Octopus Energy sein?

**Sensoren und Werte**
- [S1](#s1) Der Verbrauch kommt an, die Einspeisung bleibt aber 0
- [S2](#s2) Warum aktualisieren sich die Werte nur einmal am Tag?
- [S3](#s3) Nach der Einrichtung zeigen die Sensoren „Unbekannt"
- [S4](#s4) Was bedeuten die Hinweise unter „Reparaturen"?
- [S5](#s5) Die Werte erscheinen einen Tag zu spät
- [S6](#s6) Kann ich Werte aus der Zeit vor der Installation übernehmen?
- [S7](#s7) Welche Sensoren gehören ins Energie-Dashboard?
- [S8](#s8) Ich habe zwei Zähler
- [S9](#s9) Mein Passwort hat sich geändert oder der Zähler wurde getauscht

**Tarife**
- [T1](#t1) Mein Tarif hat mehrere Zeitfenster pro Preisstufe
- [T2](#t2) Wie bilde ich zeitvariable Netzentgelte (Modul 3) ab?

**Datenexport**
- [X1](#x1) Die Download-Links führen ins Leere oder auf das Dashboard
- [X2](#x2) Mein Export „bis 23:59:59" ist unvollständig
- [X3](#x3) Kann ich den Export automatisieren?
- [X4](#x4) Wie lade ich die Logdaten des SMGW herunter – und warum kommt dabei ein ZIP?

**Dashboard-Kachel**
- [K1](#k1) Die Kachel „Verbrauchshistorie" zeigt keine Balken

**Hilfe holen**
- [H1](#h1) Bevor du ein Issue eröffnest

---

## Einrichtung und Verbindung

### <a id="e1"></a>E1 · Home Assistant und SMGW liegen in unterschiedlichen Netzen – wie verbinde ich sie?

Das SMGW hat eine feste IP-Adresse in einem eigenen Netz, meist `192.168.100.100`, und die lässt sich nicht ändern. Dein Heimnetz nutzt in der Regel einen anderen Bereich, z. B. `192.168.2.x`. Ohne Weiteres erreichen sich Home Assistant und SMGW deshalb nicht.

Die einfachste Lösung kommt ohne Routen, VLANs oder einen Umbau deines Netzes aus:

1. Den **HAN-Port des SMGW** per LAN-Kabel an denselben Switch anschließen, an dem auch der Home-Assistant-Server hängt.
2. **Home Assistant eine zweite IP-Adresse** im Netz des SMGW geben, z. B. `192.168.100.12`.

Die Schritt-für-Schritt-Anleitung mit Screenshot steht in der [Netzwerk-Einrichtung](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/docs/network-setup.md).

Gut zu wissen:

- **Läuft Home Assistant als virtuelle Maschine** (z. B. auf einem NAS), spielt die IP-Adresse des Host-Geräts keine Rolle. Die zweite Adresse bekommt die Home-Assistant-Instanz selbst.
- **Die URL in der Integration ändert sich dadurch nicht.** Dort bleibt die Adresse des SMGW eingetragen (z. B. `https://192.168.100.100/cgi-bin/hanservice.cgi`), nicht die neue Adresse von Home Assistant.
- **Ein zweiter Router oder Routing zwischen den Netzen hilft nach Nutzerberichten nicht:** Das SMGW antwortet offenbar nicht auf Pakete, die über einen Router weitergeleitet werden.
- **Hat dein SMGW eine andere Adresse** als `192.168.100.100`, nimm für Home Assistant eine Adresse aus dessen Bereich – bei `192.168.1.200` also z. B. `192.168.1.12`.

### <a id="e2"></a>E2 · Die Einrichtung bricht mit einer Fehlermeldung ab – woran liegt das?

Die Meldung verrät meist schon die Ursache:

**„Verbindung zum SMGW fehlgeschlagen. URL und Netzwerkverbindung prüfen."**

Home Assistant erreicht das SMGW nicht. Die häufigsten Gründe:

- **Falsche IP-Adresse in der URL.** Nicht jedes SMGW hat `192.168.100.100`. Aus den Issues sind auch `192.168.1.200` und `10.11.120.2` bekannt – die Adresse hängt vom Messstellenbetreiber (MSB) ab. Frag im Zweifel dort nach.
- **Home Assistant und SMGW sind nicht im selben Netz:** keine zweite IP-Adresse in Home Assistant, der HAN-Port hängt nicht am selben Switch, oder die Verbindung läuft über einen Router. Siehe [E1](#e1).
- **Die URL ist unvollständig.** Sie muss mit `https://` beginnen und den Pfad enthalten, z. B. `https://192.168.100.100/cgi-bin/hanservice.cgi`.

**„Ungültiger Benutzername oder Passwort."**

- **Punkt am Ende des Passworts?** Octopus verschickt das HAN-Passwort teilweise per SMS mit einem Satzpunkt direkt dahinter. Der Punkt gehört **nicht** zum Passwort.
- Gemeint sind die **HAN-Zugangsdaten** des Smart Meter Gateways – nicht die Zugangsdaten deines Kundenkontos beim Stromlieferanten oder für das Portal deines Netzbetreibers.
- Ist dein Gateway kein PPC-Gerät, funktioniert die Anmeldung ebenfalls nicht, siehe [E7](#e7).

**„Antwort des SMGW konnte nicht ausgewertet werden. … bis zu 15 Minuten warten und erneut versuchen."**

- **Das SMGW sperrt die Anmeldung vorübergehend**, wenn kurz hintereinander mehrere Anmeldeversuche scheitern. Einige Minuten warten und dann **einmal** mit sicher korrekten Zugangsdaten erneut versuchen – nicht in schneller Folge wiederholen.
- **Eine andere Sitzung ist noch offen.** Das SMGW erlaubt nur eine aktive Sitzung. Hast du dich parallel im Browser oder mit TRuDI angemeldet, dort abmelden bzw. das Programm schließen.

**„Dieser Zähler wird mit dieser Auswertung bereits erfasst."**

Für diesen Zähler gibt es schon einen Eintrag. Wie du einen zweiten Zähler, einen zweiten Login oder eine zweite Auswertung desselben Zählers anlegst, steht im README unter [Mehrere SMGWs / mehrere Zugänge](https://github.com/TRON4R/ha-ppc-smgw-han#mehrere-smgws--mehrere-zug%C3%A4nge).

### <a id="e3"></a>E3 · Ping auf das SMGW geht nicht – ist es kaputt?

Nein. Das SMGW antwortet grundsätzlich nicht auf `ping`. Ein fehlgeschlagener Ping sagt also nichts über die Erreichbarkeit aus. Taugliche Tests sind die Einrichtung der Integration selbst oder die direkte Anmeldung im Browser bzw. mit TRuDI ([E6](#e6)).

### <a id="e4"></a>E4 · Mein Laptop bekommt am SMGW keine IP-Adresse

Das SMGW hat keinen DHCP-Server. Du musst deinem Rechner von Hand eine feste IP-Adresse aus dem Netz des SMGW geben – bei einem SMGW mit `192.168.100.100` z. B. `192.168.100.50` mit der Netzmaske `255.255.255.0`. Die letzte Zahl darf nicht die des SMGW sein und nicht die, die du schon Home Assistant gegeben hast.

### <a id="e5"></a>E5 · Woher bekomme ich die HAN-Zugangsdaten?

Von deinem **Messstellenbetreiber (MSB)** – das ist nicht unbedingt dein Stromlieferant. Hat Octopus dein Smart Meter eingebaut, ist das in der Regel Octopus Energy Metering.

Erfahrungswerte aus den Issues:

- Die Bearbeitung dauerte zwischen wenigen Tagen und drei Monaten. Hartnäckiges Nachfragen hilft.
- Der Benutzername kam oft per E-Mail, das Passwort per Brief oder SMS (Achtung beim Punkt am Ende, siehe [E2](#e2)).
- Manche MSB vergeben **getrennte Zugänge für Bezug und Einspeisung**. Dann brauchst du beide, siehe [S1](#s1).

### <a id="e6"></a>E6 · Wie sehe ich die Daten direkt am SMGW an – im Browser oder mit TRuDI?

Für den Betrieb der Integration brauchst du das nicht. Zur Fehlersuche ist es aber der beste Gegencheck: Du siehst mit eigenen Augen und **unabhängig von dieser Integration**, was dein SMGW mit deinen Zugangsdaten herausgibt.

Für beide Wege braucht dein Rechner eine IP-Adresse im Netz des SMGW (siehe [E4](#e4) und die Hinweise in der [Netzwerk-Einrichtung](https://github.com/TRON4R/ha-ppc-smgw-han/blob/main/docs/network-setup.md)).

**Im Browser**

1. Die URL aufrufen, die du auch in der Integration eingetragen hast, z. B. `https://192.168.100.100/cgi-bin/hanservice.cgi`.
2. Es erscheint eine **Zertifikatswarnung**. Das ist normal, weil das SMGW ein selbstsigniertes Zertifikat verwendet. In Firefox klappt das Weiterkommen am einfachsten: „Erweitert…" → „Risiko akzeptieren und fortfahren". Chrome macht hier deutlich mehr Probleme.
3. Mit den HAN-Zugangsdaten anmelden, den Zähler im Auswahlfeld wählen und die Zählerstände für den gewünschten Zeitraum anzeigen lassen. Jeder Wert steht mit seiner OBIS-Kennzahl da, z. B. `1-0:1.8.0` für Bezug und `1-0:2.8.0` für Einspeisung.
4. **Danach wieder abmelden.** Solange die Sitzung offen ist, kann sich die Integration nicht anmelden.

**Mit TRuDI**

TRuDI ist die offizielle Anzeigesoftware für Smart Meter Gateways. Du meldest dich dort mit denselben HAN-Zugangsdaten an und siehst die ausgelesenen Werte samt OBIS-Kennzahlen. Die Zertifikatswarnungen des Browsers entfallen; einige Nutzer kamen damit leichter ans Ziel als mit dem Browser. Auch hier gilt: TRuDI danach schließen, damit die Sitzung wieder frei ist.

### <a id="e7"></a>E7 · Funktioniert die Integration auch mit SMGW anderer Hersteller?

Nein, nur mit Gateways von **PPC** (Power Plus Communications). Die Integration liest die Weboberfläche der HAN-Schnittstelle von PPC aus. Andere Hersteller wie Theben, EMH oder Sagemcom Dr. Neuhaus haben eigene Oberflächen und Schnittstellen – selbst TRuDI bringt für jeden Hersteller einen eigenen Adapter mit. Da ich selbst nur ein PPC-Gateway habe, kann ich andere Hersteller auch nicht testen.

Welcher Hersteller verbaut ist, steht auf dem Typenschild des Gateways.

### <a id="e8"></a>E8 · Muss ich Kunde von Octopus Energy sein?

Nein. Das Smart Meter Gateway gehört zur Messstelle, nicht zum Stromvertrag. Die Integration läuft bei jedem Stromlieferanten, solange ein PPC-Gateway verbaut ist und du die HAN-Zugangsdaten hast. Die Octopus-Tarife sind bei der Einrichtung nur als Vorlage hinterlegt; eigene Umschaltzeiten kannst du frei eingeben.

---

## Sensoren und Werte

### <a id="s1"></a>S1 · Der Verbrauch kommt an, die Einspeisung bleibt aber 0 – warum?

Hast du keine PV-Anlage, ist 0 korrekt. Andernfalls gilt: **Die Integration liefert genau das, was das SMGW mit deinen Zugangsdaten herausgibt.** Manche Messstellenbetreiber vergeben für die Einspeisung einen eigenen Login – mit dem Bezugs-Login ist die Einspeisung dann schlicht nicht sichtbar.

Prüfen kannst du das auf zwei Wegen. Entscheidend ist in beiden Fällen die OBIS-Kennzahl: `1-0:1.8.0` steht für Bezug (Verbrauch), `1-0:2.8.0` für Einspeisung.

**a) Schnell: mit dem Export der Integration**

1. **Einstellungen → Geräte & Dienste → PPC SMGW HAN Daily Import → Konfigurieren** (Zahnrad) → „SMGW-Zählerdaten für einen wählbaren Zeitraum exportieren".
2. Zeitraum **„Gestern"**, **„Excel (XLSX) erzeugen"** anhaken, exportieren.
3. In der Excel-Datei das Blatt **„Rohdaten"** öffnen und in der Spalte „OBIS" nach `1-0:2.8.0` suchen.

**b) Mit eigenen Augen: direkt am SMGW**

Der Export meldet sich genauso am SMGW an wie die Integration. Willst du ausschließen, dass die Integration selbst die Einspeisewerte verschluckt, melde dich mit denselben HAN-Zugangsdaten direkt am SMGW an – im Browser oder mit TRuDI (Anleitung unter [E6](#e6)) – und lass dir die Zählerstände von gestern anzeigen. Was du dort siehst, kommt ohne Umweg über diese Integration direkt vom Gateway. Ein Screenshot davon ist zugleich ein guter Beleg, wenn du beim MSB die Zugangsdaten für die Einspeisung anforderst.

**Fehlt `1-0:2.8.0`**, stellt das SMGW deinem Login keine Einspeisewerte bereit. Das kann keine Software ändern – fordere bei deinem MSB die Zugangsdaten für die Einspeisung an. Mit diesen legst du anschließend einen **zweiten Eintrag** an und vergibst einen sprechenden Gerätenamen wie „SMGW Einspeisung" (Details im README unter [Mehrere SMGWs / mehrere Zugänge](https://github.com/TRON4R/ha-ppc-smgw-han#mehrere-smgws--mehrere-zug%C3%A4nge)).

**Zeigt das SMGW `1-0:2.8.0`, die Integration aber trotzdem 0**, liegt der Fehler bei der Integration. Dann eröffne bitte ein Issue (siehe [H1](#h1)) und hänge den Screenshot vom SMGW an.

> [!IMPORTANT]
> Das **Portal deines Netzbetreibers** ist nicht das SMGW. Dass du dort Einspeisewerte siehst, heißt nicht, dass dein HAN-Login sie auch sieht. Maßgeblich ist, was das Gateway selbst liefert.

### <a id="s2"></a>S2 · Warum aktualisieren sich die Werte nur einmal am Tag? Ich habe doch 15 Minuten eingestellt.

Die **„Abrufzeit"** (Standard `00:15`) ist eine **Uhrzeit, kein Intervall**: Die Integration holt einmal täglich um 00:15 Uhr die Werte des Vortags ab. Das ist Absicht:

- Die geeichten Tagesendwerte stehen erst **nach Mitternacht** fest.
- Häufige Abfragen bergen das Risiko, dass das SMGW den Zugang sperrt – und eine Entsperrung beim MSB kann Wochen dauern.

Die 15-Minuten-Werte bekommst du trotzdem: Der [Datenexport](https://github.com/TRON4R/ha-ppc-smgw-han#-datenexport-f%C3%BCr-beliebige-zeitr%C3%A4ume) liefert jeden einzelnen 15-Minuten-Wert, rückwirkend für die gesamte im Gateway gespeicherte Zeit.

Für **Live-Werte** (z. B. Überschussladen) ist das SMGW grundsätzlich ungeeignet. Dafür gibt es optische Leseköpfe am Zähler oder das Smart Meter deines Wechselrichters. Deren Daten kann diese Integration nicht verarbeiten.

### <a id="s3"></a>S3 · Nach der Einrichtung zeigen die Sensoren „Unbekannt"

Normalerweise nicht: Direkt bei der Einrichtung holt die Integration den Vortag ab, die Sensoren haben also sofort Werte. Bleiben sie „Unbekannt", schau unter **Einstellungen → System → Reparaturen** nach einem Hinweis der Integration (siehe [S4](#s4)). Häufigster Fall: Das SMGW liefert für gestern keine vollständigen Daten, etwa weil der MSB gerade etwas am Zähler oder Konto umbaut. Ob Daten da sind, zeigt dir ein Export für „Gestern".

### <a id="s4"></a>S4 · Was bedeuten die Hinweise unter „Reparaturen"?

Die Integration meldet Probleme beim nächtlichen Abruf, statt still alte Werte stehen zu lassen:

| Hinweis | Bedeutung | Was tun? |
|---|---|---|
| **SMGW-Abruf fehlgeschlagen – wird wiederholt** | Der Abruf ist gescheitert. Die Integration versucht es automatisch erneut (nach 15, 30 und 60 Minuten, danach alle 2 Stunden). | Nichts. Der Hinweis verschwindet von selbst, sobald ein Abruf gelingt. |
| **SMGW-Abruf für einen Tag aufgegeben** | Alle Versuche sind gescheitert, bevor der nächste reguläre Abruf fällig war. Dieser Tag fehlt dauerhaft in den Sensoren. | Verbindung prüfen. Die Werte des Tages lassen sich meist noch über den Export abrufen. |
| **SMGW liefert keine aktuellen Daten** | Anmeldung und Verbindung funktionieren, aber das Gateway hat für den Tag keine vollständigen Werte – z. B. nach einem Zähler- oder Kontoumbau oder einem Ausfall beim MSB. | Mit einem Export prüfen, ob für den Tag überhaupt Werte existieren. Fehlen sie dort auch, liegt es am Gateway bzw. am MSB. |

Zusätzlich gibt es die Benachrichtigung **„SMGW-Tagesdaten fehlen"**, wenn mehr als zwei Tage in Folge fehlen. Sie bleibt absichtlich stehen, bis du sie wegklickst, damit eine Lücke nicht unbemerkt bleibt.

Ändern sich deine Zugangsdaten, erscheint keiner dieser Hinweise – Home Assistant fordert dich dann zur erneuten Anmeldung auf („SMGW erneut authentifizieren").

### <a id="s5"></a>S5 · Die Werte erscheinen einen Tag zu spät – im Energie-Dashboard, in ApexCharts oder im Vergleich mit anderen Quellen

Die Werte selbst stimmen, nur ihr **Datum in Home Assistant** ist um einen Tag verschoben. Das liegt am Prinzip der Tageswerte:

Der Verbrauch vom 26.09. steht erst nach Mitternacht fest und kommt um 00:15 Uhr am **27.09.** in Home Assistant an. Home Assistant verbucht einen Wert immer in der Stunde, in der er eintrifft – hier also am 27.09. zwischen 0 und 1 Uhr. Einen Wert rückwirkend auf den Vortag zu buchen, lässt Home Assistant für Sensoren nicht zu. Welcher Tag gemeint ist, zeigt dir der Sensor **„Tagesdatum"**.

Was das praktisch bedeutet:

- **Energie-Dashboard:** Die Tagesansicht zeigt den Verbrauch des Vortags, und zwar komplett in der Stunde von 0 bis 1 Uhr. Die Monatssumme enthält den letzten Tag des Vormonats, der letzte Tag des laufenden Monats landet im Folgemonat. Das Energie-Dashboard lässt sich nicht verschieben.
- **Abgleich mit der Stromrechnung:** Nimm dafür den [Datenexport](https://github.com/TRON4R/ha-ppc-smgw-han#-datenexport-f%C3%BCr-beliebige-zeitr%C3%A4ume). Die Blätter „Tagesendwerte" und „Tarifzonen" sind tagesgenau datiert.
- **ApexCharts-Karten mit `statistics:`:** Verschiebe jede Serie um einen Tag zurück, dann steht jeder Balken beim richtigen Datum. Die [Dashboard-Kachel](https://github.com/TRON4R/ha-ppc-smgw-han#dashboard-kachel-verbrauchshistorie-t%C3%A4glich) aus diesem Repo macht das bereits – falls du sie vor dem 28.09.2026 eingebunden hast, übernimm bitte die aktuelle Version. Für eigene Karten:

  ```yaml
  series:
    - entity: sensor.dein_smgw_tagesverbrauch_gesamt
      type: column
      statistics:
        type: change
        period: day
        align: start
      offset: '+1d'
      show:
        offset_in_name: false
  ```

  `offset: '+1d'` holt die Werte des jeweils folgenden Tages an die richtige Stelle, `offset_in_name: false` verhindert, dass „+1d" an den Seriennamen angehängt wird.

- **Zählerstand-Sensoren („Endstand Vortag")** sind davon kaum betroffen: Sie zeigen den Zählerstand von Mitternacht und sind beim Eintreffen nur etwa 15 Minuten alt. Waren sie bei dir 24 Stunden hinterher, nutzt du eine Version vor v2.6.0 – bitte aktualisieren.

### <a id="s6"></a>S6 · Kann ich Werte aus der Zeit vor der Installation in Home Assistant übernehmen?

Nicht in die Sensoren – Home Assistant nimmt Sensorwerte nur zum aktuellen Zeitpunkt an. Die Daten sind aber nicht verloren: Der [Datenexport](https://github.com/TRON4R/ha-ppc-smgw-han#-datenexport-f%C3%BCr-beliebige-zeitr%C3%A4ume) liefert sie für jeden Zeitraum, den das Gateway gespeichert hat (je nach Gerät 15–24 Monate) – als CSV, Excel oder signiertes CMS-Original.

### <a id="s7"></a>S7 · Welche Sensoren gehören ins Energie-Dashboard?

- **Netzbezug:** entweder **„Tagesverbrauch gesamt"** oder – wenn du pro Tarifzone einen eigenen Preis hinterlegen willst – die einzelnen Sensoren **„Tagesverbrauch *Zonenname*"**. **Nie beides**, sonst zählt das Dashboard den Verbrauch doppelt.
- **Netzeinspeisung:** **„Tageseinspeisung gesamt"**.
- Die **Zählerstand-Sensoren** nicht zusätzlich eintragen.
- Wertest du denselben Zähler zweimal aus (z. B. nach Stromtarif und nach Modul-3-Zeitfenstern), nimm nur **eines** der beiden Geräte ins Energie-Dashboard.

Beachte den Tagesversatz, siehe [S5](#s5).

### <a id="s8"></a>S8 · Ich habe zwei Zähler (z. B. einen separaten Erzeugungszähler)

Wird der zweite Zähler über dasselbe SMGW ausgelesen, erkennt die Integration das beim Anlegen eines neuen Eintrags und lässt dich den Zähler auswählen. Schritt für Schritt steht das im README unter [Mehrere SMGWs / mehrere Zugänge](https://github.com/TRON4R/ha-ppc-smgw-han#mehrere-smgws--mehrere-zug%C3%A4nge). Ein reiner Erzeugungszähler liefert keinen Bezug – dessen Verbrauchssensoren zeigen dann dauerhaft 0. Das ist korrekt; du kannst sie in Home Assistant ausblenden.

### <a id="s9"></a>S9 · Mein Passwort hat sich geändert oder der Zähler wurde getauscht

- **Neues Passwort:** Home Assistant fordert dich beim nächsten Abruf selbst zur erneuten Anmeldung auf. Alternativ: **Konfigurieren → Einstellungen ändern**.
- **Zählertausch:** Zugangsdaten über **Konfigurieren → Einstellungen ändern** aktualisieren. Entitäten und Statistik bleiben erhalten, Details im README unter [Verhalten beim Zählertausch](https://github.com/TRON4R/ha-ppc-smgw-han#verhalten-beim-z%C3%A4hlertausch).

---

## Tarife

### <a id="t1"></a>T1 · Mein Tarif hat mehrere Zeitfenster pro Preisstufe (z. B. Octopus Heat)

Das geht: Gib jedes Zeitfenster als eigenen Umschaltpunkt ein und benenne Fenster mit gleichem Preis gleich – sie werden zu einem Sensor zusammengezählt. Für Octopus Heat gibt es eine fertige Vorlage. Details im README unter [Konfiguration](https://github.com/TRON4R/ha-ppc-smgw-han#konfiguration).

### <a id="t2"></a>T2 · Wie bilde ich zeitvariable Netzentgelte (§ 14a EnWG, Modul 3) ab?

Lege denselben Zähler ein zweites Mal an – einmal mit den Zeitfenstern deines Stromtarifs, einmal mit denen deines Netzbetreibers. So prüfst du jede Abrechnung gegen genau das Raster, nach dem sie berechnet wurde. Da Netzbetreiber ihre Zeitfenster meist zum Jahreswechsel ändern, kannst du das neue Schema vorab hinterlegen. Details im README unter [Mehrere SMGWs / mehrere Zugänge](https://github.com/TRON4R/ha-ppc-smgw-han#mehrere-smgws--mehrere-zug%C3%A4nge) (Punkt „Ein Zähler, zwei Tarifraster") und [Tarifzonen-Umschaltung vorab planen](https://github.com/TRON4R/ha-ppc-smgw-han#tarifzonen-umschaltung-vorab-planen).

---

## Datenexport

### <a id="x1"></a>X1 · Die Download-Links führen ins Leere oder auf das Dashboard

- **Aktuelle Version installieren.** Ältere Versionen öffneten die Links im selben Tab, wo Home Assistant sie abfing.
- **Home-Assistant-URL prüfen:** Unter **Einstellungen → System → Netzwerk** müssen die URLs stimmen, unter denen du Home Assistant erreichst. Die Links werden daraus gebaut – eine falsche URL ergibt falsche Links.
- **Ordner `config/www/` fehlt?** Einmal von Hand anlegen und Home Assistant neu starten.

Die Links sind **ohne Anmeldung** erreichbar. Lösche nicht mehr benötigte Exporte gelegentlich aus `config/www/smgw_han_exports/`.

### <a id="x2"></a>X2 · Mein Export „bis 23:59:59" ist unvollständig

Das SMGW schreibt den Zählerstand für das letzte Viertelstunden-Intervall eines Tages erst um **00:00:01 Uhr des Folgetags**. Endet dein Zeitraum um 23:59:59, fehlen die letzten 15 Minuten – bei laufender Wärmepumpe oder Wallbox können das einige kWh sein. Setze das Ende deshalb auf **00:15 Uhr des Folgetags**. Die fertigen Zeiträume („Gestern", „Letzter Monat" …) machen das automatisch.

### <a id="x3"></a>X3 · Kann ich den Export automatisieren?

Ja, über die Aktionen `smgw_han.export_readings` und `smgw_han.export_period`, z. B. in einer monatlichen Automation. Parameter und Beispiele stehen im README unter [Datenexport für beliebige Zeiträume](https://github.com/TRON4R/ha-ppc-smgw-han#-datenexport-f%C3%BCr-beliebige-zeitr%C3%A4ume). Bitte nicht in Schleifen oder kurzen Abständen aufrufen – jeder Aufruf öffnet eine echte Sitzung am SMGW.

### <a id="x4"></a>X4 · Wie lade ich die Logdaten des SMGW herunter – und warum kommt dabei ein ZIP?

Über **Konfigurieren → „SMGW-Logdaten für einen wählbaren Zeitraum exportieren"**, Details im README unter [SMGW-Logdaten exportieren](https://github.com/TRON4R/ha-ppc-smgw-han#-smgw-logdaten-exportieren). Das SMGW gibt pro Export höchstens 1000 Einträge heraus und meldet sonst „Die Abfrage liefert … Datensätze zurück. Es sind nur 1000 erlaubt." Enthält dein Zeitraum mehr, teilt die Integration ihn automatisch auf. Jeder Teil ist ein eigenes, vom SMGW signiertes Original, und signierte Dateien lassen sich nicht zusammenfügen, ohne die Signatur zu zerstören. Deshalb liegen die Teile gemeinsam in einer ZIP-Datei. CSV und Excel enthalten trotzdem alle Einträge in je einer Datei.

---

## Dashboard-Kachel

### <a id="k1"></a>K1 · Die Kachel „Verbrauchshistorie" zeigt keine Balken

- **ApexCharts Card installiert?** Die Kachel braucht die [ApexCharts Card](https://github.com/RomRider/apexcharts-card) aus HACS. Danach den Browser neu laden.
- **Entity-IDs angepasst?** Die IDs in der YAML-Datei sind Beispiele und müssen durch deine eigenen ersetzt werden (**Einstellungen → Geräte & Dienste → Entitäten**).
- **Erst ab dem zweiten Abruf:** Der erste Wert eines neuen Sensors dient Home Assistant als Startpunkt der Statistik. Balken erscheinen deshalb erst nach dem zweiten nächtlichen Abruf.

---

## Hilfe holen

### <a id="h1"></a>H1 · Bevor du ein Issue eröffnest

Mit diesen Angaben kann ich dir meist direkt helfen, statt erst nachzufragen:

1. **Neueste Version?** Prüfe in HACS, ob ein Update bereitsteht, und teste damit.
2. **Versionen:** Version der Integration und von Home Assistant.
3. **Gateway:** Hersteller und Typ vom Typenschild, dein Messstellenbetreiber.
4. **Fehlermeldung im Wortlaut** oder als Screenshot.
5. **Was liefert das SMGW?** Das Ergebnis des Exports für „Gestern" (welche OBIS-Kennzahlen stehen im Blatt „Rohdaten"?) und – wenn möglich – ein Screenshot der direkten Anmeldung im Browser oder mit TRuDI. Anleitungen unter [S1](#s1) und [E6](#e6).
6. **Diagnosedaten:** Einstellungen → Geräte & Dienste → PPC SMGW HAN Daily Import → Drei-Punkte-Menü am Eintrag → „Diagnosedaten herunterladen". Benutzername und Passwort werden darin automatisch entfernt.
7. **Debug-Log:** Drei-Punkte-Menü am Eintrag → „Debug-Protokollierung aktivieren" → einen Export für „Gestern" auslösen (bei Problemen mit dem nächtlichen Abruf stattdessen die Nacht abwarten) → „Debug-Protokollierung deaktivieren". Home Assistant bietet das Log dann zum Herunterladen an. Relevant sind die Zeilen mit `custom_components.smgw_han`.

> [!CAUTION]
> Poste **niemals** dein HAN-Passwort – auch nicht „nur als Beispiel". Sieh Logs und Screenshots vor dem Hochladen kurz durch.
