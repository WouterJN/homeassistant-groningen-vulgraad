"""Sensors per configured container: fill level, and when it was last emptied."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import Container
from .const import (
    ATTR_ALARM,
    ATTR_CLUSTER_ID,
    ATTR_CLUSTER_NAME,
    ATTR_CONTAINER,
    ATTR_FRACTION,
    ATTR_LAST_SEEN_LEVEL,
    ATTR_STATUS,
    ATTR_WARN,
    CONF_CONTAINERS,
    DOMAIN,
    EMPTIED_MIN_DROP,
    FRACTION_NAMES,
)
from .coordinator import VulgraadCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up two sensors per configured container."""
    coordinator: VulgraadCoordinator = hass.data[DOMAIN][entry.entry_id]
    numbers: list[str] = entry.options.get(CONF_CONTAINERS, [])
    entities: list[SensorEntity] = []
    for number in numbers:
        entities.append(VulgraadSensor(coordinator, number))
        entities.append(VulgraadLastEmptiedSensor(coordinator, number))
    async_add_entities(entities)


class VulgraadEntity(CoordinatorEntity[VulgraadCoordinator]):
    """One container, named after its fraction and grouped under its cluster."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VulgraadCoordinator, number: str) -> None:
        super().__init__(coordinator)
        self._number = number
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
        self._attr_translation_placeholders = {
            "fraction": fraction,
            "number": self._number,
        }

        cluster_id = container.cluster_id if container else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, cluster_id or f"container-{self._number}")},
            name=(container.cluster_name if container else None)
            or f"Cluster {cluster_id or self._number}",
            manufacturer="Gemeente Groningen",
            model="Ondergrondse container",
            entry_type=None,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        # The first poll is what tells us the cluster and fraction, so identity
        # is refreshed rather than fixed at construction.
        self._apply_identity()
        super()._handle_coordinator_update()


class VulgraadSensor(VulgraadEntity, SensorEntity):
    """How full one container is, in percent."""

    _attr_translation_key = "vulgraad"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:trash-can"

    def __init__(self, coordinator: VulgraadCoordinator, number: str) -> None:
        super().__init__(coordinator, number)
        self._attr_unique_id = f"{DOMAIN}_{number}"

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


class VulgraadLastEmptiedSensor(VulgraadEntity, RestoreSensor):
    """When the fill level was last seen to fall, which is when it was emptied.

    The portal has no emptying date, so this is inferred: a drop of at least
    EMPTIED_MIN_DROP points between two polls counts as an emptying, stamped
    with the time of the poll that saw it. It is therefore accurate to within
    one poll interval.

    Unknown until the first emptying is seen. The level it compares against is
    kept across restarts, so an emptying during downtime is still caught.
    """

    _attr_translation_key = "last_emptied"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:delete-empty"

    def __init__(self, coordinator: VulgraadCoordinator, number: str) -> None:
        super().__init__(coordinator, number)
        self._attr_unique_id = f"{DOMAIN}_{number}_last_emptied"
        self._attr_native_value: datetime | None = None
        self._last_vulgraad: int | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (data := await self.async_get_last_sensor_data()) is not None:
            if isinstance(data.native_value, datetime):
                self._attr_native_value = data.native_value
        if (state := await self.async_get_last_state()) is not None:
            previous = state.attributes.get(ATTR_LAST_SEEN_LEVEL)
            if isinstance(previous, int):
                self._last_vulgraad = previous
        # The coordinator polled before this entity existed, so compare that
        # poll against the restored level now rather than waiting an interval.
        self._observe()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._observe()
        super()._handle_coordinator_update()

    @callback
    def _observe(self) -> None:
        container = self._container
        level = container.vulgraad if container and container.has_sensor else None
        if level is None:
            # A failed poll or a missing reading must not reset the baseline,
            # or an emptying across the gap would go unnoticed.
            return
        if (
            self._last_vulgraad is not None
            and self._last_vulgraad - level >= EMPTIED_MIN_DROP
        ):
            self._attr_native_value = dt_util.utcnow()
        self._last_vulgraad = level

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {ATTR_LAST_SEEN_LEVEL: self._last_vulgraad}
