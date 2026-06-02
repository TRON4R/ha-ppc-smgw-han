"""Constants for the SMGW HAN integration."""

DOMAIN = "smgw_han"

# Config flow keys
CONF_URL = "url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_UPDATE_TIME = "update_time"
CONF_TARIFF_SWITCH_HOUR = "tariff_switch_hour"
CONF_TARIFF_SWITCH_MINUTE = "tariff_switch_minute"
CONF_METER_ID = "meter_id"  # Parsed from SMGW during setup
CONF_INSTANCE_ID = "instance_id"  # Per-entry counter (1, 2, ...) for stable entity / device slug
CONF_DEVICE_NAME = "device_name"  # Optional user-defined device label, overrides default

# Defaults
DEFAULT_URL = "https://192.168.100.100/cgi-bin/hanservice.cgi"
DEFAULT_UPDATE_TIME = "00:15:00"
DEFAULT_TARIFF_SWITCH_HOUR = 5
DEFAULT_TARIFF_SWITCH_MINUTE = 0

# OBIS codes
OBIS_IMPORT = "1-0:1.8.0"  # Verbrauch / Grid import
OBIS_EXPORT = "1-0:2.8.0"  # Einspeisung / Grid export

# Export services
SERVICE_EXPORT_READINGS = "export_readings"
SERVICE_EXPORT_PERIOD = "export_period"
ATTR_DEVICE_ID = "device_id"
ATTR_PERIOD = "period"
ATTR_FROM_DATETIME = "from_datetime"
ATTR_TO_DATETIME = "to_datetime"
ATTR_DOWNLOAD_CMS = "download_cms"
ATTR_WRITE_CSV = "write_csv"
ATTR_WRITE_XLSX = "write_xlsx"
# Client-side plausibility guard for the export start date: reject requests
# further back than ~24 months + the running month. This is NOT the SMGW's true
# retention — the gateway returns whatever it actually has (PPC handbook
# guarantees billing data for at least 15 months; MsbG expects 24 months of
# consumption history), and the aggregation tolerates missing days. Generous on
# purpose, mainly to catch year typos.
SMGW_HISTORY_DAYS = 760  # ~24 months + ~1 month margin (leap-safe)
# NOTE: the "from_too_old" message in strings/en/de hard-codes "760" — update it
# too if this number changes.
# Files are written under <config>/www/<EXPORT_WWW_SUBDIR>/<token>/ so they are
# reachable as /local/<EXPORT_WWW_SUBDIR>/<token>/<file> download links.
EXPORT_WWW_SUBDIR = "smgw_han_exports"

# Store
STORE_VERSION = 4

# Sensor keys (used in coordinator.data dict)
SENSOR_DAILY_CONSUMPTION_TOTAL = "daily_consumption_total"
SENSOR_DAILY_CONSUMPTION_SLOT_1 = "daily_consumption_slot_1"
SENSOR_DAILY_CONSUMPTION_SLOT_2 = "daily_consumption_slot_2"
SENSOR_DAILY_FEEDIN_TOTAL = "daily_feedin_total"
SENSOR_METER_CONSUMPTION_PREV_DAY_CLOSE = "meter_consumption_prev_day_close"
SENSOR_METER_CONSUMPTION_SWITCH_1 = "meter_consumption_switch_1"
SENSOR_METER_FEEDIN_PREV_DAY_CLOSE = "meter_feedin_prev_day_close"
SENSOR_DATE = "date"
