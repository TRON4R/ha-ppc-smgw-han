"""Tests for the log export writers (CSV, XLSX, CMS ZIP)."""

from __future__ import annotations

import zipfile
from datetime import datetime

from openpyxl import load_workbook

from custom_components.smgw_han.export_files import (
    LOG_HEADERS,
    write_cms_zip,
    write_log_csv,
    write_log_xlsx,
)
from custom_components.smgw_han.log_parser import parse_log_cms


def _entries(log_cms, log_row):
    return parse_log_cms(
        log_cms(
            [
                log_row(10, datetime(2026, 3, 25, 6, 0, 26)),
                log_row(
                    11,
                    datetime(2026, 3, 25, 6, 5, 0),
                    level="WARNING",
                    event_id="10061",
                    template=(
                        "Max. Anzahl der fehlgeschlagenen Anmeldeversuche "
                        "erreicht, HAN-Interface für kurze Zeit gesperrt"
                    ),
                    values=(),
                ),
                log_row(
                    12,
                    datetime(2026, 4, 18, 15, 53, 30),
                    level="ERROR",
                    event_id="10260",
                    outcome="FAILURE",
                    template="Der Messbetrieb wird eingestellt.",
                    values=(),
                ),
                # A message starting with '=' must not become a formula.
                log_row(13, datetime(2026, 4, 19), template="=SUMME(A1)", values=()),
            ]
        )
    ).entries


def test_log_csv_one_row_per_entry_text_last(tmp_path, log_cms, log_row):
    path = tmp_path / "log.csv"
    write_log_csv(path, _entries(log_cms, log_row))
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM: umlauts open cleanly in Excel
    lines = raw.decode("utf-8-sig").splitlines()
    assert lines[0].split(";") == LOG_HEADERS
    assert LOG_HEADERS[-1] == "Meldungstext"
    assert lines[1].split(";")[:6] == [
        "2026-03-25 06:00:26", "2026-03-25 05:00:26", "INFO", "erfolgreich",
        "10228", "10",
    ]
    assert lines[3].split(";")[3] == "fehlgeschlagen"
    assert lines[4].endswith(";'=SUMME(A1)")
    assert len(lines) == 5


def test_log_xlsx_sheets_highlighting_and_summary(tmp_path, log_cms, log_row):
    entries = _entries(log_cms, log_row)
    path = tmp_path / "log.xlsx"
    meta = {
        "gateway_id": "eppc0000000001",
        "from": "2026-03-01 00:00:00",
        "to": "2026-05-01 00:00:00",
        "created": "2026-10-01 18:00:00",
        "parts": [
            ("2026-03-01 00:00:00", "2026-04-01 00:00:00", 2),
            ("2026-04-01 00:00:00", "2026-05-01 00:00:00", 2),
        ],
        "gaps": [],
        "refused": [("x", "y", 4)],
        "shortfalls": [],
    }
    write_log_xlsx(path, entries, meta)
    wb = load_workbook(path)
    assert wb.sheetnames == ["Logbuch", "Übersicht", "Info"]

    log = wb["Logbuch"]
    assert [c.value for c in log[1]] == LOG_HEADERS
    assert log["A2"].value == datetime(2026, 3, 25, 6, 0, 26)
    assert log["E2"].value == 10228  # numeric id, filterable
    fills = [log.cell(row=r, column=1).fill.start_color.rgb for r in range(2, 6)]
    assert fills[1].endswith("FFEB9C")  # WARNING -> yellow
    assert fills[2].endswith("FFC7CE")  # ERROR + FAILURE -> red
    assert not fills[0].endswith(("FFEB9C", "FFC7CE"))
    assert log["G5"].value == "'=SUMME(A1)"

    summary = wb["Übersicht"]
    rows = [[c.value for c in row] for row in summary.iter_rows()]
    assert rows[2] == ["2026-03", 1, 1, 0, 0, 2, 0]
    assert rows[3] == ["2026-04", 1, 0, 1, 0, 2, 1]

    info = "\n".join(str(c.value or "") for c in wb["Info"]["A"])
    assert "eppc0000000001" in info
    assert "in 2 Teilabrufe zerlegt" in info
    assert "Laufende Nummern 10 bis 13: lückenlos." in info
    assert "mindestens so viele Einträge angekommen" in info


def test_log_xlsx_reports_gaps_and_shortfalls(tmp_path, log_cms, log_row):
    entries = _entries(log_cms, log_row)
    path = tmp_path / "log.xlsx"
    write_log_xlsx(
        path,
        entries,
        {
            "parts": [("a", "b", 4)],
            "gaps": [(14, 20)],
            "refused": [("a", "b", 9)],
            "shortfalls": [("2026-03-01 00:00:00", "2026-05-01 00:00:00", 9, 4)],
        },
    )
    info = "\n".join(str(c.value or "") for c in load_workbook(path)["Info"]["A"])
    assert "in einem Abruf geliefert" in info
    assert "Lücke in den laufenden Nummern: 14 bis 20" in info
    assert "vom SMGW gemeldet 9 Einträge, angekommen 4" in info
    assert "lückenlos" not in info
    # 13 and 21 do not exist here, so there is no clock-jump hint.
    assert "springt die Uhrzeit" not in info


def test_log_xlsx_points_out_clock_jump_at_gap(tmp_path, log_cms, log_row):
    # The real case of 2026-03-02: power loss, entries 497-499 missing, and
    # the entry after the gap stamped 35 minutes before the one before it.
    entries = parse_log_cms(
        log_cms(
            [
                log_row(496, datetime(2026, 3, 2, 14, 37, 27)),
                log_row(500, datetime(2026, 3, 2, 14, 2, 36)),
            ]
        )
    ).entries
    path = tmp_path / "log.xlsx"
    write_log_xlsx(path, entries, {"gaps": [(497, 499)]})
    info = "\n".join(str(c.value or "") for c in load_workbook(path)["Info"]["A"])
    assert "Lücke in den laufenden Nummern: 497 bis 499" in info
    assert (
        "springt die Uhrzeit des SMGW zurück (Nr. 496: 2026-03-02 14:37:27, "
        "Nr. 500: 2026-03-02 14:02:36)" in info
    )
    assert "Ein erneuter Export desselben Zeitraums zeigt, ob es am Abruf lag." in info


def test_cms_zip_keeps_parts_byte_identical(tmp_path):
    members = [("Teil01von02_a.xml.cms", b"\x30\x83signed-1"),
               ("Teil02von02_b.xml.cms", b"\x30\x83signed-2")]
    path = tmp_path / "parts.zip"
    write_cms_zip(path, members)
    with zipfile.ZipFile(path) as zf:
        assert zf.namelist() == [name for name, _ in members]
        for name, content in members:
            assert zf.read(name) == content
