"""Reading operation identifiers out of the portal's own page definitions."""

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.groningen_vulgraad.api import (
    BurgerportaalClient,
    VulgraadProtocolError,
)
from custom_components.groningen_vulgraad.const import (
    CONF_HUISNUMMER,
    CONF_OPERATION_IDS,
    CONF_POSTCODE,
    DOMAIN,
)
from custom_components.groningen_vulgraad.discovery import PageDefinition

from .conftest import make_container

# Shapes taken from the live page definitions.
PAGE = (
    '<m:page>'
    '"datasource":{"type":"microflow","path":"Burger_Applicatie.link",'
    '"operationId":"SEEDSEEDSEEDSEEDSEEDaa","argMap":{}}'
    '"datasource":{"type":"microflow","path":"Burger_Applicatie.Inzamelplek",'
    '"operationId":"RETRIEVERETRIEVEREaa","argMap":{}}'
    '"datasource":{"type":"entityPath",'
    '"path":"Burger_Applicatie.Group_ResultSet_Groups/Burger_Applicatie.Group",'
    '"sourceVariable":"$ResultSet_Groups","operationId":"GROUPSDSGROUPSDSaaaa"}'
    '"type":"microflowCall","operationId":"PICKTILEPICKTILEaaaa",'
    '"parameters":[{"name":"Group","value":{"type":"variable"}}]'
    '"argMap":{"link":{"widget":"dataView1"},"helper":{"widget":"dataView2"}},'
    '"config":{"operationId":"ADDRESSADDRESSADaaaa","validate":"view"}'
)


def test_finds_each_operation_by_what_it_does():
    page = PageDefinition("some.page.xml", PAGE)
    assert page.microflow_datasource("Burger_Applicatie.link") == "SEEDSEEDSEEDSEEDSEEDaa"
    assert (
        page.microflow_datasource("Burger_Applicatie.Inzamelplek")
        == "RETRIEVERETRIEVEREaa"
    )
    assert page.entity_path_datasource("Burger_Applicatie.Group") == "GROUPSDSGROUPSDSaaaa"
    assert page.microflow_call("Group") == "PICKTILEPICKTILEaaaa"
    assert page.call_with_arguments({"link", "helper"}) == "ADDRESSADDRESSADaaaa"


def test_missing_operations_return_none_rather_than_a_wrong_one():
    page = PageDefinition("some.page.xml", PAGE)
    assert page.microflow_datasource("Burger_Applicatie.Nope") is None
    assert page.entity_path_datasource("Burger_Applicatie.Template") is None
    assert page.microflow_call("Template") is None
    assert page.call_with_arguments({"link"}) is None


def test_entity_path_matches_the_end_of_the_path_only():
    """Group_ResultSet_Groups must not be mistaken for the Group entity."""
    page = PageDefinition("some.page.xml", PAGE)
    assert page.entity_path_datasource("Burger_Applicatie.Group_ResultSet_Groups") is None


async def test_stale_ids_trigger_one_rediscovery():
    client = BurgerportaalClient(session=None)
    attempts = []

    async def fake_walk(postcode, huisnummer, discover):
        attempts.append(discover)
        if not discover:
            raise VulgraadProtocolError("HTTP 560")
        return [make_container("111")]

    client._walk = fake_walk
    containers = await client.async_get_containers("1234AB", "5")

    assert attempts == [False, True], "fast path first, then discovery"
    assert client.rediscovered is True
    assert len(containers) == 1


async def test_rediscovery_failure_reports_both_causes():
    client = BurgerportaalClient(session=None)

    async def fake_walk(postcode, huisnummer, discover):
        raise VulgraadProtocolError("discovery broke" if discover else "HTTP 560")

    client._walk = fake_walk
    with pytest.raises(VulgraadProtocolError) as err:
        await client.async_get_containers("1234AB", "5")
    assert "HTTP 560" in str(err.value)
    assert "discovery broke" in str(err.value)


async def test_discovery_can_be_turned_off():
    client = BurgerportaalClient(session=None, allow_discovery=False)
    attempts = []

    async def fake_walk(postcode, huisnummer, discover):
        attempts.append(discover)
        raise VulgraadProtocolError("HTTP 560")

    client._walk = fake_walk
    with pytest.raises(VulgraadProtocolError):
        await client.async_get_containers("1234AB", "5")
    assert attempts == [False]


async def test_rediscovered_ids_are_remembered_on_the_entry(hass, containers):
    """So a redeploy costs one rediscovery, not one per restart."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_POSTCODE: "1234AB", CONF_HUISNUMMER: "5"},
        options={"containers": ["111"]},
    )
    entry.add_to_hass(hass)

    learned = {"seed": "NEWSEED", "retrieve": "NEWRETRIEVE"}

    async def fetch(self, postcode, huisnummer):
        self.rediscovered = True
        self._ops = dict(learned)
        return containers

    with patch(
        "custom_components.groningen_vulgraad.coordinator."
        "BurgerportaalClient.async_get_containers",
        fetch,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.data[CONF_OPERATION_IDS] == learned


async def test_remembered_ids_are_passed_back_to_the_client(hass, containers):
    stored = {"seed": "STORED", "retrieve": "STORED2"}
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_POSTCODE: "1234AB",
            CONF_HUISNUMMER: "5",
            CONF_OPERATION_IDS: stored,
        },
        options={"containers": ["111"]},
    )
    entry.add_to_hass(hass)
    seen = {}

    original_init = BurgerportaalClient.__init__

    def spy(self, session, ops=None, allow_discovery=True):
        seen["ops"] = ops
        original_init(self, session, ops, allow_discovery)

    with patch.object(BurgerportaalClient, "__init__", spy), patch(
        "custom_components.groningen_vulgraad.coordinator."
        "BurgerportaalClient.async_get_containers",
        AsyncMock(return_value=containers),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert seen["ops"] == stored
