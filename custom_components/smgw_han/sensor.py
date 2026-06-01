"""Sensor platform for SMGW HAN integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEVICE_NAME,
    CONF_INSTANCE_ID,
    CONF_METER_ID,
    DOMAIN,
    SENSOR_DAILY_CONSUMPTION_SLOT_1,
    SENSOR_DAILY_CONSUMPTION_SLOT_2,
    SENSOR_DAILY_CONSUMPTION_TOTAL,
    SENSOR_DAILY_FEEDIN_TOTAL,
    SENSOR_DATE,
    SENSOR_METER_CONSUMPTION_PREV_DAY_CLOSE,
    SENSOR_METER_CONSUMPTION_SWITCH_1,
    SENSOR_METER_FEEDIN_PREV_DAY_CLOSE,
)
from . import SmgwTafConfigEntry
from .coordinator import SmgwTafCoordinator

DEFAULT_DEVICE_NAME = "PPC SMGW"


@dataclass(frozen=True, kw_only=True)
class SmgwTafSensorEntityDescription(SensorEntityDescription):
    """Describe a SMGW HAN sensor."""

    data_key: str
    is_daily_value: bool = False


SENSOR_DESCRIPTIONS: tuple[SmgwTafSensorEntityDescription, ...] = (
    # --- Daily consumption values (for Energy Dashboard) ---
    SmgwTafSensorEntityDescription(
        key="daily_consumption_total",
        translation_key="daily_consumption_total",
        data_key=SENSOR_DAILY_CONSUMPTION_TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=4,
        is_daily_value=True,
        icon="mdi:home-lightning-bolt",
    ),
    SmgwTafSensorEntityDescription(
        key="daily_consumption_slot_1",
        translation_key="daily_consumption_slot_1",
        data_key=SENSOR_DAILY_CONSUMPTION_SLOT_1,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=4,
        is_daily_value=True,
        icon="mdi:home-lightning-bolt",
    ),
    SmgwTafSensorEntityDescription(
        key="daily_consumption_slot_2",
        translation_key="daily_consumption_slot_2",
        data_key=SENSOR_DAILY_CONSUMPTION_SLOT_2,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=4,
        is_daily_value=True,
        icon="mdi:home-lightning-bolt",
    ),
    SmgwTafSensorEntityDescription(
        key="daily_feedin_total",
        translation_key="daily_feedin_total",
        data_key=SENSOR_DAILY_FEEDIN_TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=4,
        is_daily_value=True,
        icon="mdi:solar-power",
    ),
    # --- Absolute meter readings (primary technical sensors) ---
    SmgwTafSensorEntityDescription(
        key="meter_consumption_prev_day_close",
        translation_key="meter_consumption_prev_day_close",
        data_key=SENSOR_METER_CONSUMPTION_PREV_DAY_CLOSE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=4,
        icon="mdi:meter-electric",
    ),
    SmgwTafSensorEntityDescription(
        key="meter_consumption_switch_1",
        translation_key="meter_consumption_switch_1",
        data_key=SENSOR_METER_CONSUMPTION_SWITCH_1,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=4,
        icon="mdi:meter-electric",
    ),
    SmgwTafSensorEntityDescription(
        key="meter_feedin_prev_day_close",
        translation_key="meter_feedin_prev_day_close",
        data_key=SENSOR_METER_FEEDIN_PREV_DAY_CLOSE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=4,
        icon="mdi:meter-electric-outline",
    ),
    # --- Date sensor (diagnostic, enabled by default) ---
    SmgwTafSensorEntityDescription(
        key="date",
        translation_key="date",
        data_key=SENSOR_DATE,
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:calendar",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: SmgwTafConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up SMGW HAN sensors from a config entry."""
    coordinator: SmgwTafCoordinator = config_entry.runtime_data

    entities: list[SensorEntity] = [
        SmgwTafSensor(coordinator, description, config_entry)
        for description in SENSOR_DESCRIPTIONS
    ]
    entities.append(SmgwDeviceIdSensor(config_entry))

    async_add_entities(entities)


class SmgwTafSensor(CoordinatorEntity[SmgwTafCoordinator], SensorEntity):
    """Sensor for SMGW HAN daily meter values."""

    entity_description: SmgwTafSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmgwTafCoordinator,
        description: SmgwTafSensorEntityDescription,
        config_entry: SmgwTafConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        # Identity is stable per config entry and independent of physical meter
        # hardware. If the meter is replaced (new meter_id), entities remain
        # unchanged. The instance counter is assigned at entry creation and
        # reused if an entry is deleted and re-added, so historical statistics
        # carry over after a physical meter swap.
        instance_id = config_entry.data.get(CONF_INSTANCE_ID, 1)
        device_slug = f"smgw_meter{instance_id}"
        self._attr_unique_id = f"{device_slug}_{description.key}"
        meter_id = config_entry.data.get(CONF_METER_ID)
        custom_name = (config_entry.data.get(CONF_DEVICE_NAME) or "").strip()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_slug)},
            name=custom_name or DEFAULT_DEVICE_NAME,
            manufacturer="PPC",
            model="Smart Meter Gateway",
            serial_number=meter_id,
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self.entity_description.data_key)
        if (
            self.entity_description.device_class == SensorDeviceClass.DATE
            and isinstance(value, str)
        ):
            try:
                return date_type.fromisoformat(value)
            except ValueError:
                return None
        return value

    @property
    def last_reset(self) -> datetime | None:
        """Return the time when the sensor was last reset.

        For daily value sensors, this is midnight of the measured date.
        Returns timezone-aware datetime using HA's timezone.
        """
        if not self.entity_description.is_daily_value:
            return None
        if self.coordinator.data is None:
            return None
        date_str = self.coordinator.data.get(SENSOR_DATE)
        if date_str:
            naive = datetime.fromisoformat(date_str + "T00:00:00")
            return dt_util.as_local(
                naive.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            )
        return None


class SmgwDeviceIdSensor(SensorEntity):
    """Diagnostic sensor exposing this device's internal device_id.

    Lets users copy the device id for the export services / dashboard tile
    when more than one SMGW is configured. Disabled by default to avoid
    clutter (single-SMGW setups don't need it — the services auto-detect the
    only device).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "device_id"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:identifier"

    def __init__(self, config_entry: SmgwTafConfigEntry) -> None:
        """Initialize the device-id sensor."""
        instance_id = config_entry.data.get(CONF_INSTANCE_ID, 1)
        device_slug = f"smgw_meter{instance_id}"
        self._attr_unique_id = f"{device_slug}_device_id"
        meter_id = config_entry.data.get(CONF_METER_ID)
        custom_name = (config_entry.data.get(CONF_DEVICE_NAME) or "").strip()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_slug)},
            name=custom_name or DEFAULT_DEVICE_NAME,
            manufacturer="PPC",
            model="Smart Meter Gateway",
            serial_number=meter_id,
        )

    async def async_added_to_hass(self) -> None:
        """Publish the device registry id once the entity is registered."""
        await super().async_added_to_hass()
        if self.registry_entry and self.registry_entry.device_id:
            self._attr_native_value = self.registry_entry.device_id
            self.async_write_ha_state()
