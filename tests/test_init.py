"""Entry lifecycle: reloads, cleanup of deselected containers, diagnostics."""

from unittest.mock import AsyncMock, patch

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.groningen_vulgraad.api import VulgraadConnectionError
from custom_components.groningen_vulgraad.const import (
    CONF_CONTAINERS,
    CONF_HUISNUMMER,
    CONF_OPERATION_IDS,
    CONF_POSTCODE,
    CONF_SCAN_INTERVAL_HOURS,
    DOMAIN,
)
from custom_components.groningen_vulgraad.diagnostics import (
    async_get_config_entry_diagnostics,
)

PATCH_CLIENT = "custom_components.groningen_vulgraad.coordinator.BurgerportaalClient"


class FakeClient:
    """Stands in for the portal client; rediscovers when told to."""

    calls = 0
    rediscover_on: set[int] = set()
    containers: list = []

    def __init__(self, session, ops=None):
        self.rediscovered = False
        self.operation_ids = {"seed": "fresh"}

    async def async_get_containers(self, postcode, huisnummer):
        type(self).calls += 1
        self.rediscovered = type(self).calls in type(self).rediscover_on
        return type(self).containers


def clusters(hass, entry):
    """The clusters that have a device for this entry."""
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return {i[1] for device in devices for i in device.identifiers}


def make_entry(hass, numbers):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_POSTCODE: "1234AB", CONF_HUISNUMMER: "5"},
        options={CONF_CONTAINERS: list(numbers), CONF_SCAN_INTERVAL_HOURS: 1},
        unique_id="1234AB-5",
    )
    entry.add_to_hass(hass)
    return entry


async def test_learning_new_ids_does_not_reload(hass, containers):
    """Storing rediscovered ids must not throw the poll away and walk again."""
    FakeClient.calls = 0
    FakeClient.rediscover_on = {2}
    FakeClient.containers = containers
    entry = make_entry(hass, ["111"])

    with patch(PATCH_CLIENT, FakeClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert entry.data[CONF_OPERATION_IDS] == {"seed": "fresh"}
    assert FakeClient.calls == 2, "a reload would have polled a third time"
    assert hass.states.get("sensor.1234_teststraat_restafval_111").state == "51"


async def test_changing_options_still_reloads(hass, containers):
    FakeClient.calls = 0
    FakeClient.rediscover_on = set()
    FakeClient.containers = containers
    entry = make_entry(hass, ["111"])

    with patch(PATCH_CLIENT, FakeClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_SCAN_INTERVAL_HOURS: 3}
        )
        await hass.async_block_till_done()

    assert FakeClient.calls == 2
    assert entry.runtime_data.update_interval.total_seconds() == 3 * 3600


async def test_deselected_containers_are_removed(hass, containers):
    FakeClient.containers = containers
    entry = make_entry(hass, ["111", "222"])
    with patch(PATCH_CLIENT, FakeClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entities = er.async_get(hass)
    assert len(er.async_entries_for_config_entry(entities, entry.entry_id)) == 4
    assert clusters(hass, entry) == {"1234", "5678"}

    with patch(PATCH_CLIENT, FakeClient):
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_CONTAINERS: ["111"]}
        )
        await hass.async_block_till_done()

    left = er.async_entries_for_config_entry(entities, entry.entry_id)
    assert sorted(e.unique_id for e in left) == [
        f"{DOMAIN}_111",
        f"{DOMAIN}_111_last_emptied",
    ]
    assert clusters(hass, entry) == {"1234"}


async def test_an_outage_raises_no_repair_issue(hass):
    from homeassistant.helpers import issue_registry as ir

    entry = make_entry(hass, ["111"])
    with patch(
        f"{PATCH_CLIENT}.async_get_containers",
        AsyncMock(side_effect=VulgraadConnectionError("503")),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, "stale_operation_ids") is None


async def test_diagnostics_leave_out_where_the_user_lives(hass, containers):
    FakeClient.containers = containers
    entry = make_entry(hass, ["111", "999"])
    with patch(PATCH_CLIENT, FakeClient):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    text = repr(diagnostics)

    for secret in ("1234AB", "Teststraat", "53.2", "'111'"):
        assert secret not in text, secret
    assert diagnostics["entry"]["options"]["container_count"] == 2
    assert diagnostics["coordinator"]["containers_in_portal"] == 3
    first, missing = diagnostics["selected_containers"]
    assert first["vulgraad"] == 51
    assert first["number"] == "**REDACTED**"
    assert missing == {"missing_from_portal": True}
