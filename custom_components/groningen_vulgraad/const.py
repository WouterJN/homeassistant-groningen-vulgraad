"""Constants for the Groningen container vulgraad integration."""

from __future__ import annotations

import re
from typing import Final

DOMAIN: Final = "groningen_vulgraad"

CONF_POSTCODE: Final = "postcode"
CONF_HUISNUMMER: Final = "huisnummer"
CONF_CONTAINERS: Final = "containers"
CONF_SCAN_INTERVAL_HOURS: Final = "scan_interval_hours"

# The portal rebuilds a 2.7 MB payload for every poll, so be a good citizen.
DEFAULT_SCAN_INTERVAL_HOURS: Final = 1
MIN_SCAN_INTERVAL_HOURS: Final = 1
MAX_SCAN_INTERVAL_HOURS: Final = 24

POSTCODE_RE: Final = re.compile(r"^[1-9][0-9]{3}\s?[A-Za-z]{2}$")
HUISNUMMER_RE: Final = re.compile(r"^[0-9]+$")

# FractieKleur -> what the container is actually for.
FRACTION_NAMES: Final[dict[str, str]] = {
    "GREY": "Restafval",
    "GREEN": "Glas",
    "BLUE": "Papier",
    "ORANGE": "Plastic",
    "YELLOW": "Textiel",
}

ATTR_CLUSTER_ID: Final = "cluster_id"
ATTR_CLUSTER_NAME: Final = "cluster_name"
ATTR_CONTAINER: Final = "container_number"
ATTR_FRACTION: Final = "fraction"
ATTR_WARN: Final = "warn_threshold"
ATTR_ALARM: Final = "alarm_threshold"
ATTR_STATUS: Final = "status"

# Raised as a repair issue when the operationIds go stale after a redeploy.
ISSUE_STALE_OPS: Final = "stale_operation_ids"
