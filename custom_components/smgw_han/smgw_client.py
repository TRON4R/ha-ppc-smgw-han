"""Client for communicating with PPC Smart Meter Gateway via HAN interface."""

from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from .const import OBIS_EXPORT, OBIS_IMPORT

_LOGGER = logging.getLogger(__name__)


class SmgwClientError(Exception):
    """Base exception for SMGW client errors."""


class SmgwAuthError(SmgwClientError):
    """Authentication error."""


class SmgwConnectionError(SmgwClientError):
    """Connection error."""


class SmgwServerError(SmgwClientError):
    """HTTP 4xx/5xx from the SMGW. On the CMS export endpoint this typically
    means the gateway has no data for the requested range (e.g. before the
    meter was commissioned)."""


class SmgwNoDataError(SmgwClientError):
    """The SMGW responded normally but has no usable readings for the target day.

    Distinct from :class:`SmgwParseError` (broken/unexpected HTML): here the page
    parsed fine, the gateway just has no (complete) daily values for the requested
    date — e.g. a frozen meter after an account/meter swap, or a fresh install for
    a date the gateway never recorded. The coordinator turns this into a
    self-healing HA repair issue instead of an auth/connection failure."""


class SmgwParseError(SmgwClientError):
    """HTML parsing error."""


@dataclass
class MeterReading:
    """A single meter reading from the SMGW."""

    timestamp: datetime
    obis_code: str
    value: float
    unit: str
    quality: str


@dataclass
class SmgwDeviceInfo:
    """Device information parsed from the SMGW."""

    meter_id: str  # e.g. "1lgz0072999211" (the selected or first one)
    firmware_version: str  # e.g. "00861-34788"
    available_meter_ids: list[str] = field(default_factory=list)
    """All meter IDs visible in the SMGW's meter dropdown.

    Single-meter SMGWs return a list of length 1; multi-meter SMGWs
    (e.g. Modul-2 installations with separate import and PV-production
    meters) return >=2. Used by the config flow to decide whether a
    meter-selection step is needed.
    """


@dataclass
class DailyData:
    """Processed daily meter data."""

    date: date
    import_midnight: float
    import_tariff_switch: float
    import_next_midnight: float
    export_midnight: float
    export_next_midnight: float
    daily_import_total: float
    daily_import_go: float
    daily_import_standard: float
    daily_export_total: float
    raw_readings: list[MeterReading] = field(default_factory=list)


def find_closest_reading(
    meter_readings: list[MeterReading],
    target_dt: datetime,
    tolerance_minutes: int = 7,
) -> MeterReading | None:
    """Find the reading closest to ``target_dt`` within a tolerance window.

    Matches the SMGW's 15-minute reading grid (default +/- 7 minutes).
    Returns ``None`` if no reading falls inside the window. Shared by the
    strict daily processing and the tolerant range aggregation.
    """
    best: MeterReading | None = None
    best_delta = timedelta.max
    for r in meter_readings:
        delta = abs(r.timestamp - target_dt)
        if delta <= timedelta(minutes=tolerance_minutes) and delta < best_delta:
            best = r
            best_delta = delta
    return best


def find_closest_value(
    meter_readings: list[MeterReading],
    target_dt: datetime,
    tolerance_minutes: int = 7,
) -> float | None:
    """Value of the reading closest to ``target_dt`` (see find_closest_reading)."""
    reading = find_closest_reading(meter_readings, target_dt, tolerance_minutes)
    return reading.value if reading else None


class SmgwClient:
    """Client for PPC SMGW HAN interface."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
    ) -> None:
        """Initialize the SMGW client."""
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._token: str | None = None
        self._client: httpx.AsyncClient | None = None
        # Serializes all SMGW sessions on this client instance. The SMGW
        # allows only one active session per account, so the nightly daily
        # fetch and any user-triggered export must not run concurrently.
        self._lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the httpx client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                verify=False,  # SMGW uses self-signed certificates
                timeout=30.0,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _login(self) -> str:
        """Log in to the SMGW and obtain session cookie + CSRF token.

        Returns the login page HTML for further parsing (e.g. firmware).
        All httpx exceptions are wrapped into SmgwClientError subtypes.
        """
        client = await self._get_client()
        try:
            response = await client.get(
                self._base_url,
                auth=httpx.DigestAuth(self._username, self._password),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            if err.response.status_code in (401, 403):
                raise SmgwAuthError(
                    f"Authentication failed: {err.response.status_code}"
                ) from err
            raise SmgwConnectionError(
                f"HTTP error during login: {err.response.status_code}"
            ) from err
        except (httpx.ConnectError, httpx.RemoteProtocolError) as err:
            raise SmgwConnectionError(
                f"Cannot connect to SMGW at {self._base_url}: {err}"
            ) from err
        except httpx.TimeoutException as err:
            raise SmgwConnectionError(
                f"Timeout connecting to SMGW: {err}"
            ) from err
        except httpx.RequestError as err:
            # Catch-all for any other httpx request errors
            raise SmgwConnectionError(
                f"Request error during login: {err}"
            ) from err

        self._token = self._parse_token(response.text)
        if not self._token:
            raise SmgwParseError("Could not extract CSRF token from login page")

        _LOGGER.debug("SMGW login successful, token obtained")
        return response.text

    async def _post(self, data: dict) -> str:
        """Send a POST request with the current session.

        All httpx exceptions are wrapped into SmgwClientError subtypes.
        """
        if not self._token:
            raise SmgwClientError("Not logged in - no CSRF token available")

        client = await self._get_client()
        post_data = {"tkn": self._token, **data}

        try:
            response = await client.post(
                self._base_url,
                data=post_data,
                auth=httpx.DigestAuth(self._username, self._password),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            raise SmgwConnectionError(
                f"HTTP error: {err.response.status_code}"
            ) from err
        except (httpx.ConnectError, httpx.RemoteProtocolError) as err:
            raise SmgwConnectionError(
                f"Connection lost during request: {err}"
            ) from err
        except httpx.TimeoutException as err:
            raise SmgwConnectionError(
                f"Timeout during request: {err}"
            ) from err
        except httpx.RequestError as err:
            raise SmgwConnectionError(
                f"Request error: {err}"
            ) from err

        new_token = self._parse_token(response.text)
        if new_token:
            self._token = new_token

        return response.text

    async def _post_binary(self, data: dict) -> tuple[bytes, str | None]:
        """Send a POST and return the raw body + suggested filename.

        Like :meth:`_post` but for binary downloads (the signed CMS export):
        returns the response bytes instead of decoded text and does not try to
        parse a CSRF token from the body. All httpx exceptions are wrapped into
        SmgwClientError subtypes.
        """
        if not self._token:
            raise SmgwClientError("Not logged in - no CSRF token available")

        client = await self._get_client()
        post_data = {"tkn": self._token, **data}

        try:
            response = await client.post(
                self._base_url,
                data=post_data,
                auth=httpx.DigestAuth(self._username, self._password),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as err:
            # The CMS export endpoint answers with an HTTP error for a range it
            # has no data for (e.g. before the meter was commissioned); surface
            # that distinctly so the coordinator can map it to a clean "no data".
            raise SmgwServerError(
                f"HTTP error: {err.response.status_code}"
            ) from err
        except (httpx.ConnectError, httpx.RemoteProtocolError) as err:
            raise SmgwConnectionError(
                f"Connection lost during request: {err}"
            ) from err
        except httpx.TimeoutException as err:
            raise SmgwConnectionError(
                f"Timeout during request: {err}"
            ) from err
        except httpx.RequestError as err:
            raise SmgwConnectionError(
                f"Request error: {err}"
            ) from err

        filename = self._parse_content_disposition_filename(
            response.headers.get("content-disposition")
        )
        return response.content, filename

    @staticmethod
    def _parse_content_disposition_filename(
        header: str | None,
    ) -> str | None:
        """Extract a filename from a Content-Disposition header, if any."""
        if not header:
            return None
        # RFC 5987 form first: filename*=UTF-8''percent%20encoded
        match = re.search(
            r"filename\*=(?:UTF-8'')?([^;]+)", header, re.IGNORECASE
        )
        if match:
            return urllib.parse.unquote(match.group(1).strip().strip('"'))
        match = re.search(r'filename="?([^";]+)"?', header, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    async def _logout(self) -> None:
        """Log out from the SMGW."""
        try:
            await self._post({"action": "logout"})
            _LOGGER.debug("SMGW logout successful")
        except SmgwClientError:
            _LOGGER.debug("Logout failed (non-critical)")
        finally:
            await self.close()
            self._token = None

    def _parse_token(self, html: str) -> str | None:
        """Extract CSRF token from HTML hidden input field.

        Improved regex fallback to handle any attribute order.
        """
        soup = BeautifulSoup(html, "html.parser")
        token_input = soup.find("input", {"name": "tkn"})
        if token_input and token_input.get("value"):
            return token_input["value"]
        # Fallback regex: match name=tkn and value= in any order
        match = re.search(
            r'<input[^>]*name=["\']tkn["\'][^>]*value=["\']([^"\']+)["\']',
            html,
        )
        if match:
            return match.group(1)
        # Try reversed order (value before name)
        match = re.search(
            r'<input[^>]*value=["\']([^"\']+)["\'][^>]*name=["\']tkn["\']',
            html,
        )
        return match.group(1) if match else None

    @staticmethod
    def _parse_firmware(html: str) -> str:
        """Extract firmware version from the footer."""
        soup = BeautifulSoup(html, "html.parser")
        fw_div = soup.find("p", id="div_fwversion")
        if fw_div:
            return fw_div.get_text(strip=True)
        return "unknown"

    @staticmethod
    def parse_host_from_url(url: str) -> str:
        """Safely extract host from URL."""
        try:
            parsed = urllib.parse.urlparse(url)
            return parsed.hostname or "unknown"
        except Exception:
            return "unknown"

    @staticmethod
    def _extract_meter_id(option_text: str) -> str:
        """Extract the physical meter ID from a dropdown option text.

        Example: "01005e318002.1lgz0072999211.sm" -> "1lgz0072999211"
        """
        return option_text.removesuffix(".sm").rsplit(".", 1)[-1]

    async def _list_meter_options(self) -> list[tuple[str, str]]:
        """Fetch the meterform page and return all dropdown options.

        Returns a list of (mid, meter_id) tuples in the order they appear in
        the SMGW's meter dropdown. The list is guaranteed non-empty; the
        caller does not need to handle the empty case (a SmgwParseError is
        raised first).
        """
        html = await self._post({"action": "meterform"})
        soup = BeautifulSoup(html, "html.parser")

        select = soup.find("select", id="meterform_select_meter")
        if not select:
            select = soup.find("select", {"name": "mid"})
        if not select:
            raise SmgwParseError("Could not find meter dropdown in meterform")

        options: list[tuple[str, str]] = []
        for opt in select.find_all("option"):
            mid = opt.get("value")
            if not mid:
                continue
            text = opt.get_text(strip=True)
            meter_id = self._extract_meter_id(text)
            if not meter_id:
                _LOGGER.debug(
                    "Skipping dropdown option with unparseable text: %r", text
                )
                continue
            options.append((mid, meter_id))

        if not options:
            raise SmgwParseError("No meter found in meter dropdown")

        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "Meter dropdown contains %d option(s)", len(options)
            )
            for idx, (mid, meter_id) in enumerate(options):
                _LOGGER.debug(
                    "  option[%d]: mid=%r, meter_id=%r", idx, mid, meter_id
                )

        return options

    async def _navigate_to_meter(
        self, target_meter_id: str | None = None
    ) -> tuple[str, str]:
        """Navigate to the meter page and return (mid, meter_id).

        If ``target_meter_id`` is given, returns the matching option from the
        dropdown. If ``None`` (default), returns the first option — preserving
        legacy behaviour for callers that have not yet been updated.
        """
        options = await self._list_meter_options()

        if target_meter_id is None:
            mid, meter_id = options[0]
            _LOGGER.debug(
                "Using first meter (no target specified): mid=%s, meter_id=%s",
                mid, meter_id,
            )
            return mid, meter_id

        for mid, meter_id in options:
            if meter_id == target_meter_id:
                _LOGGER.debug(
                    "Selected configured meter: mid=%s, meter_id=%s",
                    mid, meter_id,
                )
                return mid, meter_id

        available = ", ".join(m for _, m in options)
        raise SmgwParseError(
            f"Configured meter '{target_meter_id}' not found in SMGW dropdown "
            f"(available: {available})"
        )

    async def _get_meter_values_mid(self, mid: str) -> str:
        """Get the session mid needed for showMeterValues requests.

        The mid from the meter dropdown is not directly usable for data
        requests — a fresh mid is issued by the showMeterValuesForm page.
        """
        html = await self._post({"action": "showMeterValuesForm", "mid": mid})
        soup = BeautifulSoup(html, "html.parser")

        # Prefer the mid inside the data form specifically
        form = soup.find("form", {"name": "input_metervalues"})
        if form:
            mid_input = form.find("input", {"name": "mid", "type": "hidden"})
        else:
            mid_input = soup.find("input", {"name": "mid", "type": "hidden"})

        if not mid_input or not mid_input.get("value"):
            raise SmgwParseError(
                "Could not find hidden mid in showMeterValuesForm"
            )

        values_mid = mid_input["value"]
        _LOGGER.debug("Got meter values mid=%s", values_mid)
        return values_mid

    def _parse_meter_values_table(self, html: str) -> list[MeterReading]:
        """Parse the Zählerstand HTML table into MeterReading objects.

        Rows come in pairs: line1 (import, with timestamp) followed by
        line2 (export, timestamp cell empty — inherits line1 timestamp).
        Values are plain text in <td> cells, not in <input> buttons.
        Data is in descending chronological order.
        """
        soup = BeautifulSoup(html, "html.parser")
        readings: list[MeterReading] = []

        table = soup.find("table", id="metervalue")
        if not table:
            # The values table is missing entirely: this is broken/unexpected
            # HTML, not an empty day. Signal a real parse error so the caller
            # keeps it distinct from "no data for the target day".
            raise SmgwParseError(
                "Meter values table not found in SMGW response"
            )

        rows = table.find_all(
            "tr", id=lambda x: x and x.startswith("table_metervalues_line")
        )
        if not rows:
            # Table present but empty: a genuine "no readings for this day".
            # An empty list lets _fetch_daily_data_locked raise SmgwNoDataError.
            _LOGGER.warning("No meter value rows found in table")
            return readings

        last_timestamp: datetime | None = None
        last_quality = "valid"

        for row in rows:
            ts_td = row.find("td", id="table_metervalues_col_timestamp")
            value_td = row.find("td", id="table_metervalues_col_wert")
            unit_td = row.find("td", id="table_metervalues_col_einheit")
            obis_td = row.find("td", id="table_metervalues_col_obis")
            valid_td = row.find("td", id="table_metervalues_col_istvalide")

            # Update running timestamp when a new one appears (line1 rows only)
            if ts_td:
                ts_str = ts_td.get_text(strip=True)
                if ts_str:
                    try:
                        last_timestamp = datetime.strptime(
                            ts_str, "%Y-%m-%d %H:%M:%S"
                        )
                    except ValueError:
                        _LOGGER.debug("Cannot parse timestamp: %s", ts_str)

            # The SMGW "ist valide" flag (1/2/3) is the gateway's own validity
            # status. It appears on the line1 row of each timestamp pair; the
            # line2 (export) row inherits it, mirroring the timestamp handling.
            # The textual "Status" cell holds the same info but has no stable
            # HTML id, so the id-addressable flag is parsed instead.
            if valid_td:
                valid_str = valid_td.get_text(strip=True)
                if valid_str:
                    # SMGW "ist valide" codes: 1=valid, 2=invalid, 3=not present.
                    last_quality = {
                        "1": "valid",
                        "2": "invalid",
                        "3": "not_present",
                    }.get(valid_str, valid_str)

            if not all([value_td, obis_td, last_timestamp]):
                continue

            obis_str = obis_td.get_text(strip=True)
            if obis_str not in (OBIS_IMPORT, OBIS_EXPORT):
                continue

            value_str = value_td.get_text(strip=True)
            unit_str = unit_td.get_text(strip=True) if unit_td else "kWh"

            try:
                readings.append(
                    MeterReading(
                        timestamp=last_timestamp,
                        obis_code=obis_str,
                        value=float(value_str),
                        unit=unit_str,
                        quality=last_quality,
                    )
                )
            except (ValueError, TypeError) as err:
                _LOGGER.debug("Skipping unparseable row: %s", err)

        _LOGGER.debug(
            "Parsed %d meter readings from Zählerstand data", len(readings)
        )
        return readings

    async def async_validate_and_get_device_info(
        self, target_meter_id: str | None = None
    ) -> SmgwDeviceInfo:
        """Validate connection and return device info.

        The returned :class:`SmgwDeviceInfo` always carries the full list of
        meter IDs visible in the SMGW's dropdown (``available_meter_ids``).
        The ``meter_id`` field is set to ``target_meter_id`` if given and
        found in the dropdown; otherwise to the first option (legacy
        behaviour for callers that have not yet been updated).
        """
        try:
            login_html = await self._login()
            firmware = self._parse_firmware(login_html)

            options = await self._list_meter_options()
            available = [meter_id for _mid, meter_id in options]

            if target_meter_id is not None:
                if target_meter_id not in available:
                    raise SmgwParseError(
                        f"Configured meter '{target_meter_id}' not found in "
                        f"SMGW dropdown (available: {', '.join(available)})"
                    )
                selected = target_meter_id
            else:
                selected = available[0]

            _LOGGER.info(
                "SMGW validated: meter_id=%s, firmware=%s, available=%s",
                selected,
                firmware,
                available,
            )

            return SmgwDeviceInfo(
                meter_id=selected,
                firmware_version=firmware,
                available_meter_ids=available,
            )
        finally:
            await self._logout()

    async def async_fetch_daily_data(
        self,
        target_date: date,
        tariff_switch_hour: int = 5,
        tariff_switch_minute: int = 0,
        target_meter_id: str | None = None,
    ) -> DailyData:
        """Fetch and process daily data for a given date.

        If ``target_meter_id`` is given, that specific meter from the SMGW
        dropdown is queried; otherwise the first option is used (legacy
        behaviour for single-meter SMGWs).
        """
        async with self._lock:
            return await self._fetch_daily_data_locked(
                target_date,
                tariff_switch_hour,
                tariff_switch_minute,
                target_meter_id,
            )

    async def _fetch_daily_data_locked(
        self,
        target_date: date,
        tariff_switch_hour: int,
        tariff_switch_minute: int,
        target_meter_id: str | None,
    ) -> DailyData:
        """Body of :meth:`async_fetch_daily_data`, run under ``self._lock``."""
        try:
            await self._login()

            mid_dropdown, _meter_id = await self._navigate_to_meter(
                target_meter_id
            )
            mid = await self._get_meter_values_mid(mid_dropdown)

            next_day = target_date + timedelta(days=1)
            # Use explicit datetime strings matching the SMGW form format.
            # to includes 00:15:00 of next_day to safely capture the 00:00:01
            # closing reading within the 7-minute tolerance window.
            from_str = target_date.strftime("%Y-%m-%d") + " 00:00:00"
            to_str = next_day.strftime("%Y-%m-%d") + " 00:15:00"

            _LOGGER.debug(
                "Fetching Zählerstand data from %s to %s", from_str, to_str
            )

            html = await self._post(
                {
                    "action": "showMeterValues",
                    "mid": mid,
                    "from": from_str,
                    "to": to_str,
                }
            )

            all_readings = self._parse_meter_values_table(html)

            if not all_readings:
                # Table parsed fine but held no rows: the gateway has no daily
                # values for this date (frozen meter / not yet recorded), not a
                # parse failure. The coordinator maps this to a repair issue.
                raise SmgwNoDataError(
                    f"No meter readings found for {target_date}"
                )

            return self._process_readings(
                target_date, all_readings,
                tariff_switch_hour, tariff_switch_minute,
            )

        finally:
            await self._logout()

    async def async_download_cms(
        self,
        from_dt: datetime,
        to_dt: datetime,
        target_meter_id: str | None = None,
    ) -> tuple[bytes, str | None]:
        """Download the signed CMS export for the given range.

        Returns the raw PKCS#7/CMS bytes (the SMGW's tamper-evident original,
        identical to the web interface "Exportieren" button) together with the
        server-suggested filename from ``Content-Disposition`` if present. The
        bytes are not parsed.
        """
        async with self._lock:
            return await self._download_cms_locked(
                from_dt, to_dt, target_meter_id
            )

    async def _download_cms_locked(
        self,
        from_dt: datetime,
        to_dt: datetime,
        target_meter_id: str | None,
    ) -> tuple[bytes, str | None]:
        """Body of :meth:`async_download_cms`, run under ``self._lock``."""
        if from_dt >= to_dt:
            raise SmgwClientError("from_dt must be before to_dt")

        try:
            await self._login()
            mid_dropdown, _meter_id = await self._navigate_to_meter(
                target_meter_id
            )
            mid = await self._get_meter_values_mid(mid_dropdown)

            from_str = from_dt.strftime("%Y-%m-%d %H:%M:%S")
            to_str = to_dt.strftime("%Y-%m-%d %H:%M:%S")
            _LOGGER.debug(
                "Downloading CMS export %s .. %s", from_str, to_str
            )
            content, filename = await self._post_binary(
                {
                    "action": "exportMeterValues",
                    "mid": mid,
                    "from": from_str,
                    "to": to_str,
                }
            )
            # An empty body is not an error here: a range entirely before the
            # meter's first reading legitimately returns no CMS. run_export
            # turns "no data" into a clean user-facing message.
            _LOGGER.info(
                "Downloaded CMS export (%d bytes, filename=%s)",
                len(content), filename,
            )
            return content, filename
        finally:
            await self._logout()

    def _process_readings(
        self,
        target_date: date,
        readings: list[MeterReading],
        tariff_switch_hour: int = 5,
        tariff_switch_minute: int = 0,
    ) -> DailyData:
        """Process raw readings into DailyData with tariff calculations.

        Finds the reading closest to the target time (hour:minute) within
        a tolerance window of +/- 7 minutes to match the 15-minute grid.
        """
        next_day = target_date + timedelta(days=1)

        import_readings = [r for r in readings if r.obis_code == OBIS_IMPORT]
        export_readings = [r for r in readings if r.obis_code == OBIS_EXPORT]

        # Target timestamps
        midnight_start = datetime(
            target_date.year, target_date.month, target_date.day, 0, 0, 1
        )
        tariff_switch = datetime(
            target_date.year, target_date.month, target_date.day,
            tariff_switch_hour, tariff_switch_minute, 1,
        )
        midnight_end = datetime(
            next_day.year, next_day.month, next_day.day, 0, 0, 1
        )

        import_a = find_closest_value(import_readings, midnight_start)
        import_b = find_closest_value(import_readings, tariff_switch)
        import_c = find_closest_value(import_readings, midnight_end)
        export_a = find_closest_value(export_readings, midnight_start)
        export_c = find_closest_value(export_readings, midnight_end)

        tariff_str = f"{tariff_switch_hour:02d}:{tariff_switch_minute:02d}"

        # At least one of import (1.8.0) or export (2.8.0) must be present;
        # otherwise the meter has no usable readings at all.
        if not import_readings and not export_readings:
            raise SmgwNoDataError(
                f"No import (1.8.0) or export (2.8.0) readings found "
                f"for {target_date}"
            )

        if import_readings:
            # Meter has 1.8.0 data (consumption or bidirectional). The three
            # target timestamps (00:00 start, tariff switch, 00:00 next day)
            # are mandatory — missing one indicates a real data problem,
            # not just a meter without import capability.
            missing = []
            if import_a is None:
                missing.append(f"Import at 00:00 on {target_date}")
            if import_b is None:
                missing.append(f"Import at {tariff_str} on {target_date}")
            if import_c is None:
                missing.append(f"Import at 00:00 on {next_day}")

            if missing:
                all_timestamps = sorted(
                    set(r.timestamp for r in import_readings + export_readings)
                )
                # Readings exist but not at the required target timestamps:
                # an incomplete day, not broken HTML. Treat as "no usable data"
                # (self-healing repair issue), not a hard parse failure.
                raise SmgwNoDataError(
                    f"Missing required meter readings: {', '.join(missing)}. "
                    f"Available timestamps: {all_timestamps}"
                )
        else:
            # Export-only meter (e.g. dedicated PV-production meter on a
            # Modul-2 SMGW where the production meter only exposes 2.8.0).
            # Treat consumption as zero, symmetrically to the export-only
            # fallback handled for the inverse case below.
            _LOGGER.info(
                "No import (1.8.0) readings found for %s — "
                "assuming no consumption (export-only meter)",
                target_date,
            )
            import_a = import_b = import_c = 0.0

        # Export readings are optional (not all meters have PV / feed-in)
        if export_a is None or export_c is None:
            if not export_readings:
                _LOGGER.info(
                    "No export (2.8.0) readings found for %s — "
                    "assuming no feed-in (no PV system)",
                    target_date,
                )
                export_a = 0.0
                export_c = 0.0
            elif export_a is not None:
                # Have start but not end — cannot compute delta
                _LOGGER.warning(
                    "Missing export end reading for %s "
                    "(start=%.4f, end=missing) — setting feed-in to 0",
                    target_date, export_a,
                )
                export_c = export_a
            else:
                # Have end but not start — cannot compute delta
                _LOGGER.warning(
                    "Missing export start reading for %s "
                    "(start=missing, end=%.4f) — setting feed-in to 0",
                    target_date, export_c,
                )
                export_a = export_c

        daily_import_go = round(import_b - import_a, 4)
        daily_import_standard = round(import_c - import_b, 4)
        daily_import_total = round(import_c - import_a, 4)
        daily_export_total = round(export_c - export_a, 4)

        for label, val in [
            ("Go import", daily_import_go),
            ("Standard import", daily_import_standard),
            ("Total import", daily_import_total),
            ("Total export", daily_export_total),
        ]:
            if val < 0:
                _LOGGER.warning(
                    "%s is negative (%.4f kWh) for %s - "
                    "meter readings may be inconsistent",
                    label,
                    val,
                    target_date,
                )

        return DailyData(
            date=target_date,
            import_midnight=import_a,
            import_tariff_switch=import_b,
            import_next_midnight=import_c,
            export_midnight=export_a,
            export_next_midnight=export_c,
            daily_import_total=daily_import_total,
            daily_import_go=daily_import_go,
            daily_import_standard=daily_import_standard,
            daily_export_total=daily_export_total,
            raw_readings=readings,
        )
