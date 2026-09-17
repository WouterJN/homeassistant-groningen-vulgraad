"""The municipality's open data service, and how it is used as a fallback."""

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.groningen_vulgraad.api import VulgraadProtocolError
from custom_components.groningen_vulgraad.config_flow import _container_options
from custom_components.groningen_vulgraad.wfs import async_get_catalogue

from .conftest import make_container

FEATURE = {
    "properties": {
        "CLUSTERCODE": "1235",
        "CLUSTEROMSCHRIJVING": "1235 - Teststraat",
        "CONTAINERCODE": "277",
        "FRACTIE": "RESTAFVAL",
        "LATITUDE": 53.2,
        "LONGITUDE": 6.5,
    }
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, payload, status=200):
        self._response = FakeResponse(payload, status)

    def get(self, *args, **kwargs):
        return self._response


async def test_catalogue_parses_features():
    rows = await async_get_catalogue(FakeSession({"features": [FEATURE]}))
    assert len(rows) == 1
    container = rows[0]
    assert container.number == "277"
    assert container.cluster_id == "1235"
    assert container.fraction == "RESTAFVAL"
    assert container.latitude == 53.2
    # The WFS carries no sensor flag: unknown, which is not the same as False.
    assert container.has_sensor is None
    assert container.vulgraad is None


async def test_catalogue_deduplicates():
    payload = {"features": [FEATURE, dict(FEATURE)]}
    rows = await async_get_catalogue(FakeSession(payload))
    assert len(rows) == 1


async def test_catalogue_rejects_empty_and_errors():
    with pytest.raises(VulgraadProtocolError):
        await async_get_catalogue(FakeSession({"features": []}))
    with pytest.raises(VulgraadProtocolError):
        await async_get_catalogue(FakeSession({}, status=503))


async def test_unknown_sensor_is_offered_only_when_allowed():
    """Unknown is excluded from the normal picker, allowed in the fallback."""
    catalogue = [
        make_container("277", vulgraad=None, has_sensor=None, fraction="RESTAFVAL")
    ]
    home = (53.2, 6.5)

    assert _container_options(catalogue, home) == []
    relaxed = _container_options(catalogue, home, require_sensor=False)
    assert [o["value"] for o in relaxed] == ["277"]
    assert "Restafval" in relaxed[0]["label"]


async def test_options_flow_falls_back_to_open_data(hass, containers):
    """When the portal is down, the picker still shows real names."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.groningen_vulgraad.const import (
        CONF_CONTAINERS,
        CONF_HUISNUMMER,
        CONF_POSTCODE,
        DOMAIN,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_POSTCODE: "1234AB", CONF_HUISNUMMER: "5"},
        options={CONF_CONTAINERS: ["277"]},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.groningen_vulgraad.coordinator."
        "BurgerportaalClient.async_get_containers",
        AsyncMock(return_value=containers),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    catalogue = [
        make_container("277", vulgraad=None, has_sensor=None, fraction="RESTAFVAL")
    ]
    with patch(
        "custom_components.groningen_vulgraad.config_flow._async_fetch",
        AsyncMock(side_effect=OSError("portal down")),
    ), patch(
        "custom_components.groningen_vulgraad.config_flow.async_get_catalogue",
        AsyncMock(return_value=catalogue),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)

    options = result["data_schema"].schema[CONF_CONTAINERS].config["options"]
    labels = [o["label"] for o in options]
    # A bare number would mean both sources failed.
    assert labels != ["277"]
    assert "Teststraat" in labels[0]
