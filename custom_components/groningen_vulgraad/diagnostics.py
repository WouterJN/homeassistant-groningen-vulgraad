"""Diagnostics: what a bug report needs, without where the user lives.

The address only unlocks the portal, but it is still an address. The
containers someone follows are the ones near their home, so their numbers,
clusters and coordinates are redacted too. What is left is enough to see
whether the portal answered, what it answered, and which identifiers worked.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_CONTAINERS, CONF_HUISNUMMER, CONF_POSTCODE
from .coordinator import VulgraadConfigEntry

TO_REDACT = {
    CONF_POSTCODE,
    CONF_HUISNUMMER,
    "number",
    "cluster_id",
    "cluster_name",
    "latitude",
    "longitude",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VulgraadConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data or {}
    selected = entry.options.get(CONF_CONTAINERS, [])
    error = coordinator.last_exception

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": {
                **{k: v for k, v in entry.options.items() if k != CONF_CONTAINERS},
                "container_count": len(selected),
            },
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(error) if error else None,
            "update_interval": str(coordinator.update_interval),
            "containers_in_portal": len(data),
            "with_sensor": sum(1 for c in data.values() if c.has_sensor),
        },
        "selected_containers": [
            async_redact_data(asdict(data[number]), TO_REDACT)
            if number in data
            else {"missing_from_portal": True}
            for number in selected
        ],
    }
