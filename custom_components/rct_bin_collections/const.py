"""Constants for the RCT Bin Collections integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "rct_bin_collections"
VERSION: Final = "0.1.0"

# Config entry data
CONF_UPRN: Final = "uprn"
CONF_POSTCODE: Final = "postcode"
CONF_PROVIDER: Final = "provider"

# Config flow inputs
CONF_PROPERTY_NAME: Final = "property_name"
CONF_MANUAL_UPRN: Final = "manual_uprn"
CONF_SELECTED_ADDRESS: Final = "selected_address"

# Options
CONF_UPDATE_INTERVAL: Final = "update_interval"
CONF_INCLUDED_CATEGORIES: Final = "included_categories"
CONF_EXCLUDED_CATEGORIES: Final = "excluded_categories"
CONF_REMINDER_DAYS: Final = "reminder_days"

DEFAULT_PROPERTY_NAME: Final = "Home"
DEFAULT_UPDATE_INTERVAL_HOURS: Final = 12
UPDATE_INTERVAL_CHOICES_HOURS: Final = (6, 12, 24)
DEFAULT_REMINDER_DAYS: Final = 1
MAX_REMINDER_DAYS: Final = 14

PROVIDER_RCT_COUNCIL: Final = "rct_council"

# Refresh behaviour
RETRY_AFTER_FAILURE: Final = timedelta(hours=1)
MIN_FETCH_INTERVAL: Final = timedelta(minutes=5)
MANUAL_REFRESH_COOLDOWN_SECONDS: Final = 60
MAX_STALE_AGE: Final = timedelta(days=7)

# Persistent cache
STORAGE_VERSION: Final = 1

# Entity attribute limits
MAX_UPCOMING_DATES_IN_ATTRIBUTES: Final = 6
MAX_FOLLOWING_COLLECTIONS_IN_ATTRIBUTES: Final = 4
MAX_STATE_LENGTH: Final = 250
