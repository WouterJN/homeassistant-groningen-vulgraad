"""The Groningen container vulgraad integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_CONTAINERS, DOMAIN
from .coordinator import VulgraadConfigEntry, VulgraadCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


def unique_ids(number: str) -> tuple[str, str]:
    """The unique ids of the two sensors that belong to one container."""
    return f"{DOMAIN}_{number}", f"{DOMAIN}_{number}_last_emptied"


async def async_setup_entry(hass: HomeAssistant, entry: VulgraadConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = VulgraadCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    _async_remove_unselected(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VulgraadConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: VulgraadConfigEntry
) -> None:
    """Reload when the container selection or interval changes.

    The coordinator also updates the entry when it learns new operation
    identifiers. That changes only its data, and reloading for it would throw
    away the poll that just succeeded and walk the portal again.
    """
    if dict(entry.options) == entry.runtime_data.options:
        return
    await hass.config_entries.async_reload(entry.entry_id)


def _async_remove_unselected(hass: HomeAssistant, entry: VulgraadConfigEntry) -> None:
    """Remove the sensors, and then the devices, of deselected containers.

    Without this, a container taken out in Configure would linger as two
    entities that are "no longer provided", under a device with nothing in it.
    """
    wanted = {
        uid
        for number in entry.options.get(CONF_CONTAINERS, [])
        for uid in unique_ids(number)
    }
    entities = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(entities, entry.entry_id):
        if entity.unique_id not in wanted:
            entities.async_remove(entity.entity_id)

    devices = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if not er.async_entries_for_device(
            entities, device.id, include_disabled_entities=True
        ):
            devices.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )
