"""Data coordinator for SMGW HAN integration."""

from __future__ import annotations

import logging
from functools import partial
from datetime import date, datetime, time, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_call_later,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_METER_ID,
    CONF_TARIFF_ZONES,
    CONF_UPDATE_TIME,
    DEFAULT_TARIFF_ZONES,
    DEFAULT_UPDATE_TIME,
    DOMAIN,
    ISSUE_NO_RECENT_DATA,
    RETRY_DELAYS_MINUTES,
    SENSOR_DAILY_CONSUMPTION_TOTAL,
    SENSOR_DAILY_FEEDIN_TOTAL,
    SENSOR_DATE,
    SENSOR_METER_CONSUMPTION_PREV_DAY_CLOSE,
    SENSOR_METER_FEEDIN_PREV_DAY_CLOSE,
    STORE_VERSION,
    ZONE_NAME,
    ZONE_TIME,
    slot_key,
    switch_key,
)
from .smgw_client import (
    DailyData,
    SmgwAuthError,
    SmgwClient,
    SmgwClientError,
    SmgwNoDataError,
    SmgwServerError,
    TariffZones,
)

_LOGGER = logging.getLogger(__name__)

# coordinator.data keys whose meaning depends on the configured tariff zones.
# They must never be published under a zone definition they were not computed
# with (the values would look plausible but be mislabeled).
_ZONE_DEPENDENT_KEY_PREFIXES = (
    "daily_consumption_slot_",
    "meter_consumption_switch_",
)


def no_data_issue_id(entry_id: str) -> str:
    """Repair issue id for the 'no recent data' issue of a config entry.

    Single source of truth shared by the coordinator (create/delete on
    fetch) and ``async_remove_entry`` (cleanup when the entry is removed).
    """
    return f"{ISSUE_NO_RECENT_DATA}_{entry_id}"


class SmgwTafCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for daily SMGW HAN data fetching.

    Uses async_track_time_change to trigger exactly at the configured
    time (default 00:15) instead of polling with update_interval.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: SmgwClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,  # No automatic polling
        )
        self.config_entry = config_entry
        self._client = client
        # Store per entry to avoid collisions
        store_key = f"{DOMAIN}_{config_entry.entry_id}"
        self._store = Store(hass, STORE_VERSION, store_key)
        self._unsub_time_listener: CALLBACK_TYPE | None = None
        # Pending retry after a failed nightly fetch. Must be cancelled on
        # unload — a stray timer on a discarded coordinator is exactly the
        # leak class that bit the 00:15 listener before v3.0.0-beta.3.
        self._unsub_retry: CALLBACK_TYPE | None = None
        self._retry_attempt = 0

    async def async_setup(self) -> None:
        """Set up the coordinator: load stored data, schedule daily fetch."""
        # Load persisted data (NotImplementedError = version mismatch, discard)
        try:
            stored = await self._store.async_load()
        except NotImplementedError:
            _LOGGER.info(
                "Stored data version incompatible with current version %d"
                " - discarding and refetching",
                STORE_VERSION,
            )
            await self._store.async_remove()
            stored = None
        if stored and isinstance(stored, dict):
            if stored.get("_tariff_zones") != self._zones_config:
                # The cached day was computed with a DIFFERENT zone
                # definition. Publishing its per-zone values would attach
                # them to the new zone names/sensors — plausible-looking but
                # mislabeled. Keep only the zone-independent values (total,
                # feed-in, date, closing readings) until a refetch succeeds;
                # dropping "_tariff_zones" keeps the refetch triggers armed.
                stored = {
                    key: value
                    for key, value in stored.items()
                    if not key.startswith(_ZONE_DEPENDENT_KEY_PREFIXES)
                    and key != "_tariff_zones"
                }
                _LOGGER.info(
                    "Tariff zones changed since the cached day - withholding "
                    "per-zone values until a refetch succeeds"
                )
            self.async_set_updated_data(stored)
            _LOGGER.debug(
                "Loaded stored data for date: %s",
                stored.get(SENSOR_DATE, "unknown"),
            )

        # Check if we need to fetch: either no data at all, or stale data,
        # or tariff time has changed since last fetch
        yesterday = dt_util.now().date() - timedelta(days=1)
        stored_date = self.data.get(SENSOR_DATE) if self.data else None
        needs_fetch = False

        if not self.data:
            _LOGGER.info(
                "No stored data found - attempting initial data fetch"
            )
            needs_fetch = True
        elif stored_date != yesterday.isoformat():
            _LOGGER.info(
                "Stored data is for %s but yesterday is %s - fetching update",
                stored_date,
                yesterday,
            )
            needs_fetch = True
        else:
            # Check if the tariff-zone definition has changed since the last
            # stored data (any change — a switch time or a zone name — alters
            # the computed slots, so it must trigger a refetch).
            stored_zones = self.data.get("_tariff_zones")
            if stored_zones != self._zones_config:
                _LOGGER.info(
                    "Tariff zones changed from %s to %s - refetching data",
                    stored_zones,
                    self._zones_config,
                )
                needs_fetch = True

        if needs_fetch:
            try:
                await self._async_do_daily_fetch()
            except UpdateFailed as err:
                if self.data:
                    # Cached values exist — keep running, the scheduled
                    # nightly fetch will retry.
                    _LOGGER.warning(
                        "Startup fetch failed (will retry at scheduled "
                        "time): %s",
                        err,
                    )
                else:
                    # No data at all: fail setup so HA retries with its own
                    # backoff instead of leaving empty sensors until the
                    # next nightly fetch. (SmgwNoDataError does NOT land
                    # here — the gateway answered fine and a repair issue
                    # was raised; auth errors propagate to trigger reauth.)
                    raise ConfigEntryNotReady(
                        f"Initial SMGW fetch failed: {err}"
                    ) from err

        # Register the daily time listener only after everything above
        # succeeded: an aborted setup (ConfigEntryNotReady / auth -> reauth)
        # must not leave an orphaned listener behind that keeps fetching on a
        # discarded coordinator instance across HA's setup retries.
        self._schedule_daily_fetch()

    def _configured_fetch_time(self) -> time:
        """Return the configured daily fetch time, falling back to 00:15."""
        time_str = self.config_entry.data.get(
            CONF_UPDATE_TIME, DEFAULT_UPDATE_TIME
        )
        try:
            return time.fromisoformat(time_str)
        except ValueError:
            _LOGGER.warning(
                "Invalid time format '%s', falling back to 00:15", time_str
            )
            return time(0, 15)

    def _schedule_daily_fetch(self) -> None:
        """Register a time-based trigger for the daily data fetch."""
        if self._unsub_time_listener:
            self._unsub_time_listener()

        fetch_time = self._configured_fetch_time()

        self._unsub_time_listener = async_track_time_change(
            self.hass,
            self._handle_daily_fetch,
            hour=fetch_time.hour,
            minute=fetch_time.minute,
            second=0,
        )

        _LOGGER.info(
            "Scheduled daily SMGW data fetch at %02d:%02d",
            fetch_time.hour,
            fetch_time.minute,
        )

    async def _handle_daily_fetch(self, now: datetime) -> None:
        """Handle the scheduled daily fetch.

        Both ConfigEntryAuthFailed and UpdateFailed are caught here because
        this callback runs outside of DataUpdateCoordinator's own update
        cycle (we use async_track_time_change instead of update_interval),
        so HA would not automatically translate auth failures into a reauth
        flow — we have to trigger it explicitly.
        """
        _LOGGER.info("Starting scheduled daily SMGW data fetch")
        # A retry from the previous day must never overlap the regular run.
        self._cancel_retry()
        self._retry_attempt = 0
        # Pin the target day for the whole retry chain. Recomputing
        # "yesterday" per attempt would silently switch days once a chain
        # crosses midnight (possible with a late configured fetch time), and
        # the originally missing day would then never be fetched at all.
        target_date = dt_util.as_local(now).date() - timedelta(days=1)
        await self._run_fetch_with_retry(target_date)

    async def _run_fetch_with_retry(self, target_date: date) -> None:
        """Run the daily fetch for ``target_date``, retrying on failure."""
        try:
            published = await self._async_do_daily_fetch(target_date)
        except ConfigEntryAuthFailed as err:
            # Never retry a rejected login: the credentials will not become
            # correct on their own, and repeated failed logins risk locking
            # the HAN account. The reauth flow is the only sane answer.
            _LOGGER.error(
                "Authentication failed during scheduled fetch, "
                "starting reauth flow: %s", err,
            )
            self.config_entry.async_start_reauth(self.hass)
        except UpdateFailed as err:
            self._schedule_retry(err, target_date)
        else:
            if self._retry_attempt:
                if published:
                    _LOGGER.info(
                        "SMGW data fetch for %s succeeded on retry %d",
                        target_date, self._retry_attempt,
                    )
                else:
                    # Nothing was fetched — the day is already covered or has
                    # been superseded by newer values. Reporting "succeeded"
                    # here would misrepresent what happened.
                    _LOGGER.info(
                        "Retry chain for %s stopped after %d attempts: "
                        "nothing left to fetch",
                        target_date, self._retry_attempt,
                    )
            self._retry_attempt = 0

    def _next_scheduled_fetch(self, now: datetime) -> datetime:
        """Return when the regular daily fetch is next due."""
        fetch_time = self._configured_fetch_time()
        candidate = now.replace(
            hour=fetch_time.hour,
            minute=fetch_time.minute,
            second=0,
            microsecond=0,
        )
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _schedule_retry(self, err: UpdateFailed, target_date: date) -> None:
        """Schedule the next attempt after a failed fetch, if one still fits.

        Retries stop once the regular fetch is due again — that run supersedes
        them, and it targets a different day anyway once midnight has passed.
        Note the data itself is never lost: the SMGW keeps 15-24 months, so a
        missed day stays retrievable through the export.
        """
        delays = RETRY_DELAYS_MINUTES
        delay = delays[min(self._retry_attempt, len(delays) - 1)]
        now = dt_util.now()

        if now + timedelta(minutes=delay) >= self._next_scheduled_fetch(now):
            _LOGGER.error(
                "SMGW data fetch for %s still failing after %d retries and "
                "the next scheduled fetch is due — giving up. That day stays "
                "missing from the sensors; the readings remain available from "
                "the gateway via the export service. Last error: %s",
                target_date, self._retry_attempt, err,
            )
            self._retry_attempt = 0
            return

        self._retry_attempt += 1
        # Only the first failure is loud: a gateway that is down all day would
        # otherwise fill the log with a dozen identical error lines.
        log = _LOGGER.error if self._retry_attempt == 1 else _LOGGER.debug
        log(
            "Daily fetch for %s failed (%s) — retry %d in %d minutes",
            target_date, err, self._retry_attempt, delay,
        )
        self._unsub_retry = async_call_later(
            self.hass,
            delay * 60,
            partial(self._handle_retry, target_date=target_date),
        )

    async def _handle_retry(
        self, _now: datetime, *, target_date: date
    ) -> None:
        """Run a retry attempt after the scheduled delay."""
        self._unsub_retry = None
        _LOGGER.debug(
            "Retrying SMGW data fetch for %s (attempt %d)",
            target_date, self._retry_attempt,
        )
        await self._run_fetch_with_retry(target_date)

    def _cancel_retry(self) -> None:
        """Drop a pending retry timer, if any."""
        if self._unsub_retry:
            self._unsub_retry()
            self._unsub_retry = None

    async def _async_do_daily_fetch(
        self, target_date: date | None = None
    ) -> bool:
        """Fetch one day's values — ``target_date``, or yesterday by default.

        The retry chain passes an explicit day so it keeps recovering the day
        it started for even if it runs past midnight.

        Returns whether values were actually published, so a caller can tell a
        real success from a skip instead of reporting both as "succeeded".
        """
        yesterday = target_date or dt_util.now().date() - timedelta(days=1)

        # Skip if we already have data for yesterday with the same zones
        zones_config = self._zones_config

        if (
            self.data
            and self.data.get(SENSOR_DATE) == yesterday.isoformat()
            and self.data.get("_tariff_zones") == zones_config
        ):
            _LOGGER.debug(
                "Already have data for %s, skipping fetch", yesterday
            )
            return False

        # Never move the sensors backwards in time. A retry chain pinned to an
        # older day can outlive a manual refresh that already published a newer
        # one (needs a late fetch time plus a midnight crossing). Publishing the
        # older day afterwards would make the TOTAL_INCREASING meter sensors
        # decrease, which Home Assistant reads as a meter swap and books the
        # difference as consumption in the long-term statistics.
        stored_date = self.data.get(SENSOR_DATE) if self.data else None
        if stored_date and stored_date > yesterday.isoformat():
            _LOGGER.info(
                "Sensors already hold %s, which is newer than the requested "
                "%s — skipping so the meter readings never go backwards",
                stored_date, yesterday,
            )
            return False

        # Pass the configured meter id so SMGWs that expose multiple meters
        # in their dropdown query the correct one. Legacy entries created
        # before multi-meter support still have CONF_METER_ID populated
        # (it was always written during setup), so behaviour is unchanged
        # for them: their meter id matches the only / first dropdown option.
        target_meter_id = self.config_entry.data.get(CONF_METER_ID)

        try:
            daily_data = await self._client.async_fetch_daily_data(
                yesterday,
                zones=self.tariff_zones,
                target_meter_id=target_meter_id,
            )
        except SmgwAuthError as err:
            raise ConfigEntryAuthFailed(
                f"Authentication failed: {err}"
            ) from err
        except SmgwNoDataError as err:
            # The gateway responded fine but has no (complete) daily values for
            # yesterday — e.g. a frozen meter after an account/meter swap. This
            # is NOT an auth or connection failure: do not trigger reauth, do not
            # crash, and keep any existing sensor values. Surface it as a
            # self-healing HA repair issue plus a clearly distinct log line.
            last_date = (
                self.data.get(SENSOR_DATE) if self.data else None
            ) or "—"
            _LOGGER.warning(
                "SMGW lieferte keine Tagesdaten für %s "
                "(zuletzt erfolgreich: %s): %s",
                yesterday, last_date, err,
            )
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._no_data_issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_NO_RECENT_DATA,
                translation_placeholders={
                    "date": yesterday.isoformat(),
                    "last_date": last_date,
                },
            )
            return False
        except SmgwClientError as err:
            raise UpdateFailed(
                f"Failed to fetch SMGW data for {yesterday}: {err}"
            ) from err

        # A successful fetch clears any prior "no recent data" repair issue
        # (idempotent: a no-op when none exists).
        ir.async_delete_issue(self.hass, DOMAIN, self._no_data_issue_id)

        data = self._daily_data_to_dict(daily_data)

        # Store the tariff-zone definition alongside data for change detection
        data["_tariff_zones"] = zones_config

        # Persist to store
        await self._store.async_save(dict(data))

        _LOGGER.info(
            "Successfully fetched SMGW data for %s: "
            "Import total=%.4f kWh (%s), Export total=%.4f kWh",
            yesterday,
            daily_data.daily_import_total,
            ", ".join(
                f"{name}={val:.4f}"
                for name, val in daily_data.zone_totals.items()
            ),
            daily_data.daily_export_total,
        )

        # Update coordinator data (triggers sensor updates)
        self.async_set_updated_data(data)
        return True

    async def _async_update_data(self) -> dict[str, Any]:
        """Called for manual refreshes from the UI."""
        await self._async_do_daily_fetch()
        return self.data if self.data is not None else {}

    @property
    def _no_data_issue_id(self) -> str:
        """Unique repair issue id for the 'no recent data' issue of this entry."""
        return no_data_issue_id(self.config_entry.entry_id)

    @property
    def target_meter_id(self) -> str | None:
        """The configured meter id for this entry (one meter per entry)."""
        return self.config_entry.data.get(CONF_METER_ID)

    @property
    def _zones_config(self) -> list[dict[str, str]]:
        """The raw tariff-zone definition from the config entry (JSON shape).

        Used for the store's change detection: this survives the Store's JSON
        round trip unchanged, so a plain ``==`` compares reliably.
        """
        raw = self.config_entry.data.get(CONF_TARIFF_ZONES, DEFAULT_TARIFF_ZONES)
        return [dict(zone) for zone in raw]

    @property
    def tariff_zones(self) -> TariffZones:
        """Configured tariff zones as parsed ``(time, name)`` pairs."""
        return [
            (time.fromisoformat(zone[ZONE_TIME]), zone[ZONE_NAME])
            for zone in self._zones_config
        ]

    async def async_download_cms(
        self, from_dt: datetime, to_dt: datetime
    ) -> tuple[bytes, str | None]:
        """Download the signed CMS export for an arbitrary range (read-only)."""
        try:
            return await self._client.async_download_cms(
                from_dt, to_dt, target_meter_id=self.target_meter_id
            )
        except SmgwServerError as err:
            # The SMGW returns an HTTP error for a range it has no data for
            # (e.g. before commissioning) -> surface as a clean localized
            # "no data", not a raw connection/HTTP error.
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_data_in_range"
            ) from err
        except SmgwAuthError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cms_auth_failed"
            ) from err
        except SmgwClientError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cms_download_failed"
            ) from err

    @staticmethod
    def _daily_data_to_dict(daily_data: DailyData) -> dict[str, Any]:
        """Convert DailyData to a flat dict for coordinator.data.

        Zone totals become ``daily_consumption_slot_{n}`` (n = order of first
        appearance in the config); the absolute readings at the inner
        boundaries become ``meter_consumption_switch_{n}``.
        """
        data = {
            SENSOR_DATE: daily_data.date.isoformat(),
            SENSOR_DAILY_CONSUMPTION_TOTAL: daily_data.daily_import_total,
            SENSOR_DAILY_FEEDIN_TOTAL: daily_data.daily_export_total,
            # "Endstand Vortag" is the CLOSING reading of the fetched day
            # (next-day midnight boundary), not its opening reading — the
            # opening boundary is the close of the day BEFORE the fetched
            # day (issue #35).
            SENSOR_METER_CONSUMPTION_PREV_DAY_CLOSE: (
                daily_data.import_boundaries[-1]
            ),
            SENSOR_METER_FEEDIN_PREV_DAY_CLOSE: (
                daily_data.export_next_midnight
            ),
        }
        for n, value in enumerate(daily_data.zone_totals.values(), 1):
            data[slot_key(n)] = value
        for n, value in enumerate(daily_data.import_boundaries[1:-1], 1):
            data[switch_key(n)] = value
        return data

    async def async_unload(self) -> None:
        """Clean up on unload."""
        self._cancel_retry()
        if self._unsub_time_listener:
            self._unsub_time_listener()
            self._unsub_time_listener = None
        await self._client.close()
