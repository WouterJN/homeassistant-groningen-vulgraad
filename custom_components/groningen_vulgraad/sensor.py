"""Fill-level sensors, one per configured container."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Container
from .const import (
    ATTR_ALARM,
    ATTR_CLUSTER_ID,
    ATTR_CLUSTER_NAME,
    ATTR_CONTAINER,
    ATTR_FRACTION,
    ATTR_STATUS,
    ATTR_WARN,
    CONF_CONTAINERS,
    DOMAIN,
    FRACTION_NAMES,
)
from .coordinator import VulgraadCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one sensor per configured container."""
    coordinator: VulgraadCoordinator = hass.data[DOMAIN][entry.entry_id]
    numbers: list[str] = entry.options.get(CONF_CONTAINERS, [])
    async_add_entities(
        VulgraadSensor(coordinator, number) for number in numbers
    )


class VulgraadSensor(CoordinatorEntity[VulgraadCoordinator], SensorEntity):
    """How full one container is, in percent."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:trash-can"

    def __init__(self, coordinator: VulgraadCoordinator, number: str) -> None:
        super().__init__(coordinator)
        self._number = number
        self._attr_unique_id = f"{DOMAIN}_{number}"
        self._apply_identity()

    @property
    def _container(self) -> Container | None:
        return (self.coordinator.data or {}).get(self._number)

    @callback
    def _apply_identity(self) -> None:
        """Name the entity and group it under its cluster."""
        container = self._container
        fraction = FRACTION_NAMES.get(
            (container.fraction or "").upper() if container else "", "Container"
        )
        self._attr_name = f"{fraction} {self._number}"

        cluster_id = container.cluster_id if container else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, cluster_id or f"container-{self._number}")},
            name=(container.cluster_name if container else None)
            or f"Cluster {cluster_id or self._number}",
            manufacturer="Gemeente Groningen",
            model="Ondergrondse container",
            entry_type=None,
        )

    @property
    def available(self) -> bool:
        """Unavailable beats a wrong number.

        A container without a sensor reports a stale Vulgraad, and a 0 would
        read as 'just emptied' -- which is exactly the automation you do not
        want firing.
        """
        container = self._container
        return (
            super().available
            and container is not None
            and container.has_sensor
            and container.vulgraad is not None
        )

    @property
    def native_value(self) -> int | None:
        container = self._container
        return container.vulgraad if container else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        container = self._container
        if container is None:
            return {}

        status = "ok"
        if container.vulgraad is not None:
            if container.alarm is not None and container.vulgraad >= container.alarm:
                status = "alarm"
            elif container.warn is not None and container.vulgraad >= container.warn:
                status = "warn"

        return {
            ATTR_CONTAINER: container.number,
            ATTR_CLUSTER_ID: container.cluster_id,
            ATTR_CLUSTER_NAME: container.cluster_name,
            ATTR_FRACTION: container.fraction,
            ATTR_WARN: container.warn,
            ATTR_ALARM: container.alarm,
            ATTR_STATUS: status,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        # The first poll is what tells us the cluster and fraction, so identity
        # is refreshed rather than fixed at construction.
        self._apply_identity()
        super()._handle_coordinator_update()
