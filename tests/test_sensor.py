"""Entity behaviour, including the failure modes that matter."""

from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.groningen_vulgraad.api import VulgraadProtocolError
from custom_components.groningen_vulgraad.const import (
    CONF_CONTAINERS,
    CONF_HUISNUMMER,
    CONF_POSTCODE,
    DOMAIN,
)

from .conftest import make_container

PATCH_CLIENT = (
    "custom_components.groningen_vulgraad.coordinator."
    "BurgerportaalClient.async_get_containers"
)


async def setup_entry(hass, containers, numbers=("111", "222", "333")):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_POSTCODE: "1234AB", CONF_HUISNUMMER: "5"},
        options={CONF_CONTAINERS: list(numbers)},
        unique_id="1234AB-5",
    )
    entry.add_to_hass(hass)
    with patch(PATCH_CLIENT, AsyncMock(return_value=containers)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_sensor_state_and_attributes(hass, containers):
    await setup_entry(hass, containers)

    state = hass.states.get("sensor.1234_teststraat_restafval_111")
    assert state is not None, [s.entity_id for s in hass.states.async_all()]
    assert state.state == "51"
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["state_class"] == "measurement"
    assert state.attributes["warn_threshold"] == 60
    assert state.attributes["alarm_threshold"] == 80
    assert state.attributes["status"] == "ok"
    assert state.attributes["cluster_id"] == "1234"


async def test_status_crosses_thresholds(hass, containers):
    await setup_entry(hass, containers)
    state = hass.states.get("sensor.5678_teststraat_papier_222")
    assert state.state == "85"
    assert state.attributes["status"] == "alarm"


async def test_container_without_sensor_is_unavailable_not_zero(hass, containers):
    """A 0 would read as 'just emptied' and fire the wrong automation."""
    await setup_entry(hass, containers)
    state = hass.states.get("sensor.9999_teststraat_restafval_333")
    assert state.state == "unavailable"


async def test_one_fetch_serves_every_container(hass, containers):
    """The retrieval is all-or-nothing, so N containers must cost one call."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_POSTCODE: "1234AB", CONF_HUISNUMMER: "5"},
        options={CONF_CONTAINERS: ["111", "222", "333"]},
    )
    entry.add_to_hass(hass)
    with patch(PATCH_CLIENT, AsyncMock(return_value=containers)) as fetch:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert fetch.call_count == 1


async def test_redeploy_raises_repair_issue(hass, containers):
    """Stale operationIds should tell the user how to recover."""
    from homeassistant.helpers import issue_registry as ir

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_POSTCODE: "1234AB", CONF_HUISNUMMER: "5"},
        options={CONF_CONTAINERS: ["111"]},
    )
    entry.add_to_hass(hass)
    with patch(PATCH_CLIENT, AsyncMock(side_effect=VulgraadProtocolError("560"))):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, "stale_operation_ids") is not None


async def test_unload(hass, containers):
    entry = await setup_entry(hass, containers)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_portal_gets_a_private_cookie_jar(hass, containers):
    """Regression: the portal must not ride Home Assistant's shared session.

    The portal binds its CSRF token to a session cookie. Home Assistant keeps
    one cookie jar for the whole instance, so sharing it lets a config flow or
    a second entry rotate our session and earn an HTTP 401 mid chain.
    """
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    shared = async_get_clientsession(hass)
    entry = await setup_entry(hass, containers, numbers=("111",))
    coordinator = hass.data[DOMAIN][entry.entry_id]

    assert coordinator._session is not shared
    assert coordinator._session.cookie_jar is not shared.cookie_jar


async def test_config_flow_fetch_uses_its_own_session(hass):
    """The same isolation, for the flow that runs before an entry exists."""
    from unittest.mock import AsyncMock, patch

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from custom_components.groningen_vulgraad.config_flow import _async_fetch

    shared = async_get_clientsession(hass)
    seen = {}

    class FakeClient:
        def __init__(self, session):
            seen["session"] = session

        async def async_get_containers(self, postcode, huisnummer):
            return []

    with patch(
        "custom_components.groningen_vulgraad.config_flow.BurgerportaalClient",
        FakeClient,
    ):
        await _async_fetch(hass, "1234AB", "5")

    assert seen["session"] is not shared
    assert seen["session"].closed, "the throwaway session must be closed"
