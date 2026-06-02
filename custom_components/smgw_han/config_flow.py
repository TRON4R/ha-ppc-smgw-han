"""Config flow for SMGW HAN integration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.selector import (
    BooleanSelector,
    DateTimeSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
    TimeSelectorConfig,
)
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_DOWNLOAD_CMS,
    ATTR_FROM_DATETIME,
    ATTR_PERIOD,
    ATTR_TO_DATETIME,
    ATTR_WRITE_CSV,
    ATTR_WRITE_XLSX,
    CONF_DEVICE_NAME,
    CONF_INSTANCE_ID,
    CONF_METER_ID,
    CONF_PASSWORD,
    CONF_TARIFF_SWITCH_HOUR,
    CONF_TARIFF_SWITCH_MINUTE,
    CONF_UPDATE_TIME,
    CONF_URL,
    CONF_USERNAME,
    DEFAULT_TARIFF_SWITCH_HOUR,
    DEFAULT_TARIFF_SWITCH_MINUTE,
    DEFAULT_UPDATE_TIME,
    DEFAULT_URL,
    DOMAIN,
)
from .services import PERIOD_PRESETS, _period_range, run_export
from .smgw_client import (
    SmgwAuthError,
    SmgwClient,
    SmgwConnectionError,
    SmgwParseError,
)

_LOGGER = logging.getLogger(__name__)

# Hour options: 0-23
HOUR_OPTIONS = [
    {"value": str(h), "label": f"{h:02d}"} for h in range(24)
]

# Minute options: 0, 15, 30, 45 (matching 15-minute meter resolution)
MINUTE_OPTIONS = [
    {"value": "0", "label": "00"},
    {"value": "15", "label": "15"},
    {"value": "30", "label": "30"},
    {"value": "45", "label": "45"},
]


def _build_schema(
    defaults: dict[str, Any] | None = None,
) -> vol.Schema:
    """Build the data schema with optional defaults."""
    d = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_URL, default=d.get(CONF_URL, DEFAULT_URL)
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
            vol.Required(
                CONF_USERNAME, default=d.get(CONF_USERNAME, "")
            ): TextSelector(
                TextSelectorConfig(autocomplete="username")
            ),
            vol.Required(
                CONF_PASSWORD, default=d.get(CONF_PASSWORD, "")
            ): TextSelector(
                TextSelectorConfig(
                    type=TextSelectorType.PASSWORD,
                    autocomplete="current-password",
                )
            ),
            vol.Optional(
                CONF_TARIFF_SWITCH_HOUR,
                default=str(d.get(CONF_TARIFF_SWITCH_HOUR, DEFAULT_TARIFF_SWITCH_HOUR)),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=HOUR_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_TARIFF_SWITCH_MINUTE,
                default=str(d.get(CONF_TARIFF_SWITCH_MINUTE, DEFAULT_TARIFF_SWITCH_MINUTE)),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=MINUTE_OPTIONS,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_UPDATE_TIME,
                default=d.get(CONF_UPDATE_TIME, DEFAULT_UPDATE_TIME),
            ): TimeSelector(TimeSelectorConfig()),
            vol.Optional(
                CONF_DEVICE_NAME,
                default=d.get(CONF_DEVICE_NAME, ""),
            ): TextSelector(TextSelectorConfig()),
        }
    )


def _next_instance_id(used: set[int]) -> int:
    """Return the smallest positive integer not already in use."""
    n = 1
    while n in used:
        n += 1
    return n


def _expected_unique_id(data: dict[str, Any]) -> str:
    """The config-entry unique id implied by the current meter id + username.

    The unique id is the duplicate guard (one entry per physical meter + login).
    It is written once at setup but the settings and reauth flows can change the
    meter id (hardware swap) or username afterwards, so both re-sync it to this.
    """
    return f"{data[CONF_METER_ID]}:{data[CONF_USERNAME]}"


def _unique_id_collision(
    hass: HomeAssistant, unique_id: str, exclude_entry_id: str
) -> bool:
    """True if another configured entry already uses ``unique_id``.

    Guards the re-sync so it can never create a duplicate identity that the
    config flow's ``_abort_if_unique_id_configured`` would normally prevent.
    """
    return any(
        entry.unique_id == unique_id and entry.entry_id != exclude_entry_id
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


class SmgwTafConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SMGW HAN."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        # Carry-over from async_step_user to async_step_select_meter when the
        # SMGW exposes multiple meters in its dropdown.
        self._pending_user_input: dict[str, Any] | None = None
        self._available_meter_ids: list[str] = []

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> SmgwTafOptionsFlow:
        """Get the options flow for this handler."""
        return SmgwTafOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Convert string values from dropdowns to int for storage
            user_input[CONF_TARIFF_SWITCH_HOUR] = int(
                user_input.get(CONF_TARIFF_SWITCH_HOUR, DEFAULT_TARIFF_SWITCH_HOUR)
            )
            user_input[CONF_TARIFF_SWITCH_MINUTE] = int(
                user_input.get(CONF_TARIFF_SWITCH_MINUTE, DEFAULT_TARIFF_SWITCH_MINUTE)
            )

            client = SmgwClient(
                base_url=user_input[CONF_URL],
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
            )

            device_info = None
            try:
                device_info = await client.async_validate_and_get_device_info()
            except SmgwAuthError:
                errors["base"] = "invalid_auth"
            except SmgwConnectionError:
                errors["base"] = "cannot_connect"
            except SmgwParseError as err:
                _LOGGER.error("SMGW validation failed: %s", err)
                errors["base"] = "parse_error"
            except Exception:
                _LOGGER.exception("Unexpected error during connection test")
                errors["base"] = "unknown"
            finally:
                await client.close()

            if not errors and device_info is not None:
                if len(device_info.available_meter_ids) > 1:
                    # SMGW exposes multiple meters in its dropdown (e.g.
                    # Modul-2 installation with separate import and PV
                    # meters). Defer entry creation to a meter-selection
                    # step so the user can pick which one this entry shall
                    # represent.
                    self._pending_user_input = user_input
                    self._available_meter_ids = (
                        device_info.available_meter_ids
                    )
                    return await self.async_step_select_meter()

                return await self._create_entry_for_meter(
                    user_input, device_info.meter_id
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(),
            errors=errors,
        )

    async def async_step_select_meter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick one meter from a multi-meter SMGW."""
        errors: dict[str, str] = {}

        if user_input is not None and self._pending_user_input is not None:
            selected = user_input[CONF_METER_ID]
            return await self._create_entry_for_meter(
                self._pending_user_input, selected
            )

        meter_options = [
            {"value": meter_id, "label": meter_id}
            for meter_id in self._available_meter_ids
        ]

        return self.async_show_form(
            step_id="select_meter",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_METER_ID,
                        default=self._available_meter_ids[0],
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=meter_options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def _create_entry_for_meter(
        self, user_input: dict[str, Any], meter_id: str
    ) -> ConfigFlowResult:
        """Finalize a config entry for the given (verified) meter id."""
        # Unique id combines meter id and username so:
        #  - the same physical SMGW can be added once per distinct login
        #    (separate credentials for grid import vs. feed-in), and
        #  - a single SMGW with multiple meters in its dropdown can be added
        #    once per meter (same username, different meter id).
        await self.async_set_unique_id(
            f"{meter_id}:{user_input[CONF_USERNAME]}"
        )
        self._abort_if_unique_id_configured()

        data = {**user_input, CONF_METER_ID: meter_id}

        # Assign the lowest free positive integer as instance id.
        # Existing entries from pre-2.0 installations are treated as
        # instance 1 (matches their historical hardcoded slug).
        used_ids = {
            entry.data.get(CONF_INSTANCE_ID, 1)
            for entry in self._async_current_entries()
        }
        data[CONF_INSTANCE_ID] = _next_instance_id(used_ids)

        # Normalize empty device name to absent so sensor falls back to default.
        if not data.get(CONF_DEVICE_NAME, "").strip():
            data.pop(CONF_DEVICE_NAME, None)

        host = SmgwClient.parse_host_from_url(data[CONF_URL])
        return self.async_create_entry(
            title=f"PPC SMGW ({host})",
            data=data,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth trigger."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauth credential input."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            new_data = {**reauth_entry.data, **user_input}

            client = SmgwClient(
                base_url=new_data[CONF_URL],
                username=new_data[CONF_USERNAME],
                password=new_data[CONF_PASSWORD],
            )

            try:
                await client.async_validate_and_get_device_info()
            except SmgwAuthError:
                errors["base"] = "invalid_auth"
            except SmgwConnectionError:
                errors["base"] = "cannot_connect"
            except SmgwParseError as err:
                _LOGGER.error("SMGW reauth validation failed: %s", err)
                errors["base"] = "unknown"
            except Exception:
                _LOGGER.exception("Unexpected error during reauth")
                errors["base"] = "unknown"
            finally:
                await client.close()

            if not errors:
                # Reauth can change the username, which is part of the unique
                # id (meter:username). Re-sync it unless another entry already
                # claims that identity.
                new_unique_id = _expected_unique_id(new_data)
                if new_unique_id != reauth_entry.unique_id and (
                    _unique_id_collision(
                        self.hass, new_unique_id, reauth_entry.entry_id
                    )
                ):
                    errors["base"] = "duplicate_login"
                else:
                    return self.async_update_reload_and_abort(
                        reauth_entry,
                        data_updates=user_input,
                        unique_id=new_unique_id,
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=reauth_entry.data.get(CONF_USERNAME, ""),
                    ): TextSelector(
                        TextSelectorConfig(autocomplete="username")
                    ),
                    vol.Required(
                        CONF_PASSWORD,
                    ): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    ),
                }
            ),
            errors=errors,
        )


class SmgwTafOptionsFlow(OptionsFlow):
    """Handle options flow for SMGW HAN."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        # Carries the step-1 export choices over to the step-2 date form.
        self._export_input: dict[str, Any] = {}
        self._export_from: datetime | None = None
        self._export_to: datetime | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Top-level menu: change settings or export data."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "export"],
        )

    # ------------------------------------------------------------------
    # Data export (period preset -> prefilled, editable date range)
    # ------------------------------------------------------------------

    async def async_step_export(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: pick a period preset and the file outputs."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not (
                user_input[ATTR_DOWNLOAD_CMS]
                or user_input[ATTR_WRITE_CSV]
                or user_input[ATTR_WRITE_XLSX]
            ):
                errors["base"] = "no_outputs"
            else:
                self._export_input = user_input
                self._export_from, self._export_to = _period_range(
                    user_input[ATTR_PERIOD]
                )
                return await self.async_step_export_dates()

        d = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(
                    ATTR_PERIOD, default=d.get(ATTR_PERIOD, "last_month")
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(PERIOD_PRESETS),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="export_period",
                    )
                ),
                vol.Required(
                    ATTR_DOWNLOAD_CMS, default=d.get(ATTR_DOWNLOAD_CMS, True)
                ): BooleanSelector(),
                vol.Required(
                    ATTR_WRITE_CSV, default=d.get(ATTR_WRITE_CSV, True)
                ): BooleanSelector(),
                vol.Required(
                    ATTR_WRITE_XLSX, default=d.get(ATTR_WRITE_XLSX, True)
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="export", data_schema=schema, errors=errors
        )

    async def async_step_export_dates(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: confirm/edit the prefilled date range, then run the export."""
        errors: dict[str, str] = {}

        if user_input is not None:
            from_dt = self._coerce_dt(user_input.get(ATTR_FROM_DATETIME))
            to_dt = self._coerce_dt(user_input.get(ATTR_TO_DATETIME))
            if from_dt is None or to_dt is None or from_dt >= to_dt:
                errors["base"] = "invalid_range"
            else:
                try:
                    result = await run_export(
                        self.hass,
                        self.config_entry.runtime_data,
                        from_dt,
                        to_dt,
                        download_cms=self._export_input[ATTR_DOWNLOAD_CMS],
                        do_csv=self._export_input[ATTR_WRITE_CSV],
                        do_xlsx=self._export_input[ATTR_WRITE_XLSX],
                    )
                except ServiceValidationError:
                    # run_export raises this only for an empty/out-of-range CMS.
                    errors["base"] = "no_data_in_range"
                except Exception:  # noqa: BLE001 - surface as a form error
                    _LOGGER.exception("Export via options flow failed")
                    errors["base"] = "export_failed"
                else:
                    links = self._links_markdown(result)
                    self._notify_export(result, links)
                    return self.async_abort(
                        reason="export_done",
                        description_placeholders={"links": links},
                    )

        # Prefill with the submitted values (on error) or the preset range.
        submitted = user_input or {}
        suggested = {
            ATTR_FROM_DATETIME: submitted.get(ATTR_FROM_DATETIME)
            or self._fmt(self._export_from),
            ATTR_TO_DATETIME: submitted.get(ATTR_TO_DATETIME)
            or self._fmt(self._export_to),
        }
        schema = self.add_suggested_values_to_schema(
            vol.Schema(
                {
                    vol.Required(ATTR_FROM_DATETIME): DateTimeSelector(),
                    vol.Required(ATTR_TO_DATETIME): DateTimeSelector(),
                }
            ),
            suggested,
        )
        return self.async_show_form(
            step_id="export_dates", data_schema=schema, errors=errors
        )

    @staticmethod
    def _fmt(value: datetime | None) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""

    @staticmethod
    def _coerce_dt(value: Any) -> datetime | None:
        """Parse a datetime selector value (str or datetime) to local naive."""
        if value is None:
            return None
        dt = value if isinstance(value, datetime) else dt_util.parse_datetime(value)
        if dt is None:
            return None
        if dt.tzinfo is not None:
            dt = dt_util.as_local(dt).replace(tzinfo=None)
        return dt

    @staticmethod
    def _links_markdown(result: dict[str, Any]) -> str:
        """Build a markdown line of the download links (CMS, CSV, Excel)."""
        files = result.get("files", {})
        parts = []
        if files.get("cms"):
            parts.append(f"[CMS]({files['cms']})")
        if files.get("csv"):
            parts.append(f"[CSV]({files['csv']})")
        if files.get("xlsx"):
            parts.append(f"[Excel]({files['xlsx']})")
        return " · ".join(parts) if parts else "_(keine Dateien)_"

    def _notify_export(self, result: dict[str, Any], links: str) -> None:
        """Post a persistent notification as a lasting copy of the links."""
        message = (
            f"{result['reading_count']} Werte, "
            f"{len(result['daily_summary'])} Tage.\n\n{links}"
        )
        persistent_notification.async_create(
            self.hass,
            message,
            title="SMGW Export",
            notification_id=f"smgw_export_{self.config_entry.entry_id}",
        )

    # ------------------------------------------------------------------
    # Connection / tariff settings (the former single options step)
    # ------------------------------------------------------------------

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the connection and tariff settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Convert string values from dropdowns to int for storage
            user_input[CONF_TARIFF_SWITCH_HOUR] = int(
                user_input.get(CONF_TARIFF_SWITCH_HOUR, DEFAULT_TARIFF_SWITCH_HOUR)
            )
            user_input[CONF_TARIFF_SWITCH_MINUTE] = int(
                user_input.get(CONF_TARIFF_SWITCH_MINUTE, DEFAULT_TARIFF_SWITCH_MINUTE)
            )

            new_data = {**self.config_entry.data, **user_input}

            # Normalize empty device name to absent so sensor falls back to default.
            if not new_data.get(CONF_DEVICE_NAME, "").strip():
                new_data.pop(CONF_DEVICE_NAME, None)

            client = SmgwClient(
                base_url=new_data[CONF_URL],
                username=new_data[CONF_USERNAME],
                password=new_data[CONF_PASSWORD],
            )

            old_meter_id = self.config_entry.data.get(CONF_METER_ID)

            try:
                # Always inspect the *full* dropdown so we can decide whether
                # the configured meter is still present, was swapped, or
                # vanished from a multi-meter SMGW. Passing no target makes
                # device_info.meter_id default to the first dropdown option,
                # but we *only* adopt that as the entry's meter id in the
                # narrow "single-meter SMGW + hardware swap" case below.
                device_info = await client.async_validate_and_get_device_info()
                available = device_info.available_meter_ids

                if old_meter_id and old_meter_id in available:
                    # Configured meter still present — keep it. This is the
                    # common case and prevents silent corruption of entries
                    # that point to a non-first meter on a multi-meter SMGW.
                    new_data[CONF_METER_ID] = old_meter_id
                elif old_meter_id and len(available) == 1:
                    # Old id gone and only one meter is currently visible —
                    # *might* be a single-meter hardware swap, but only if
                    # the user is not running this SMGW in a multi-meter
                    # setup. Otherwise the "one option left" could just mean
                    # the second meter is temporarily missing from the
                    # SMGW's dropdown (PLC glitch, firmware update, ...),
                    # and adopting it would silently re-point this entry at
                    # the wrong meter.
                    other_entries = [
                        e
                        for e in self.hass.config_entries.async_entries(DOMAIN)
                        if e.entry_id != self.config_entry.entry_id
                    ]
                    same_smgw_other_entry_exists = any(
                        e.data.get(CONF_URL) == new_data[CONF_URL]
                        and e.data.get(CONF_USERNAME) == new_data[CONF_USERNAME]
                        for e in other_entries
                    )
                    remaining_used_by_other = any(
                        e.data.get(CONF_METER_ID) == device_info.meter_id
                        for e in other_entries
                    )
                    if (
                        same_smgw_other_entry_exists
                        or remaining_used_by_other
                    ):
                        # Multi-meter context is detected (either by a
                        # sibling entry on the same SMGW or by the remaining
                        # id already being claimed by another entry). Refuse
                        # the auto-swap and surface as error.
                        _LOGGER.error(
                            "Configured meter %s missing from SMGW dropdown. "
                            "Only %s remains, but this looks like a "
                            "multi-meter setup (sibling entries detected). "
                            "Refusing to silently re-point this entry. "
                            "Remove and re-add it instead.",
                            old_meter_id, device_info.meter_id,
                        )
                        errors["base"] = "configured_meter_missing"
                    else:
                        # Genuine single-meter SMGW: classic hardware swap.
                        _LOGGER.info(
                            "Meter ID changed from %s to %s - hardware "
                            "replacement detected on single-meter SMGW. "
                            "Updating stored meter ID, entities unchanged.",
                            old_meter_id, device_info.meter_id,
                        )
                        new_data[CONF_METER_ID] = device_info.meter_id
                elif old_meter_id:
                    # Multi-meter SMGW and configured meter vanished — we
                    # can't pick a replacement blindly. Surface as error so
                    # the user explicitly removes and re-adds the entry.
                    _LOGGER.error(
                        "Configured meter %s no longer in SMGW dropdown. "
                        "Available: %s. Remove this entry and add it again "
                        "to pick a meter.",
                        old_meter_id, available,
                    )
                    errors["base"] = "configured_meter_missing"
                else:
                    # No stored meter id (defensive fallback — should not
                    # occur on existing entries since CONF_METER_ID has been
                    # written during setup since v1.x).
                    new_data[CONF_METER_ID] = device_info.meter_id
            except SmgwAuthError:
                errors["base"] = "invalid_auth"
            except SmgwConnectionError:
                errors["base"] = "cannot_connect"
            except SmgwParseError as err:
                _LOGGER.error("SMGW validation failed: %s", err)
                errors["base"] = "parse_error"
            except Exception:
                _LOGGER.exception("Unexpected error during connection test")
                errors["base"] = "unknown"
            finally:
                await client.close()

            if not errors:
                # Re-sync the config-entry unique id when meter id (hardware
                # swap) or username changed, so the duplicate guard keeps
                # matching reality. Entities/devices ride on instance_id, not
                # this unique_id, so history is unaffected (see test
                # test_meter_swap_keeps_identity). Refuse if the new identity
                # would collide with another configured entry.
                new_unique_id = _expected_unique_id(new_data)
                if new_unique_id != self.config_entry.unique_id and (
                    _unique_id_collision(
                        self.hass, new_unique_id, self.config_entry.entry_id
                    )
                ):
                    errors["base"] = "duplicate_login"
                else:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data=new_data,
                        unique_id=new_unique_id,
                    )
                    await self.hass.config_entries.async_reload(
                        self.config_entry.entry_id
                    )
                    return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="settings",
            data_schema=_build_schema(dict(self.config_entry.data)),
            errors=errors,
        )
