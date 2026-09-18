"""The GUI setup flow."""

from unittest.mock import AsyncMock, patch

from homeassistant.data_entry_flow import FlowResultType

from custom_components.groningen_vulgraad.api import (
    VulgraadAddressRejected,
    VulgraadConnectionError,
)
from custom_components.groningen_vulgraad.const import (
    CONF_CONTAINERS,
    CONF_HUISNUMMER,
    CONF_POSTCODE,
    CONF_SCAN_INTERVAL_HOURS,
    DOMAIN,
)

PATCH_FETCH = "custom_components.groningen_vulgraad.config_flow._async_fetch"


async def test_full_flow_creates_entry(hass, containers):
    """Address, then pick containers from the list the portal returned."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(PATCH_FETCH, AsyncMock(return_value=containers)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_POSTCODE: "1234 ab", CONF_HUISNUMMER: "5"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "containers"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CONTAINERS: ["111", "222"], CONF_SCAN_INTERVAL_HOURS: 2},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # normalised: whitespace stripped, upper-cased
    assert result["data"] == {CONF_POSTCODE: "1234AB", CONF_HUISNUMMER: "5"}
    assert result["options"][CONF_CONTAINERS] == ["111", "222"]
    assert result["options"][CONF_SCAN_INTERVAL_HOURS] == 2


async def test_picker_excludes_sensorless_and_sorts_by_distance(hass, containers):
    """The dropdown offers real containers, nearest first, sensors only."""
    hass.config.latitude, hass.config.longitude = 53.3, 6.6

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    with patch(PATCH_FETCH, AsyncMock(return_value=containers)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "1234AB", CONF_HUISNUMMER: "5"}
        )

    options = result["data_schema"].schema[CONF_CONTAINERS].config["options"]
    values = [o["value"] for o in options]
    assert "333" not in values          # no fill sensor
    assert values == ["222", "111"]     # 222 is at the configured home location
    assert "km" in options[0]["label"]


async def test_bad_postcode_is_caught_before_any_request(hass):
    """Shape is validated locally so a typo costs no traffic."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    with patch(PATCH_FETCH, AsyncMock()) as fetch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "banana", CONF_HUISNUMMER: "5"}
        )
    assert result["errors"] == {CONF_POSTCODE: "invalid_postcode"}
    fetch.assert_not_called()


async def test_address_outside_municipality(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    with patch(PATCH_FETCH, AsyncMock(side_effect=VulgraadAddressRejected("nope"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "1011AB", CONF_HUISNUMMER: "1"}
        )
    assert result["errors"] == {"base": "address_rejected"}


async def test_portal_unreachable(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    with patch(PATCH_FETCH, AsyncMock(side_effect=VulgraadConnectionError("down"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "1234AB", CONF_HUISNUMMER: "5"}
        )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_adding_containers_through_options(hass, containers):
    """The supported way to follow more than one container.

    A second config entry for the same address is refused, so the options
    screen is where containers are added. Submitting more of them must produce
    a sensor for each, on the one entry.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.groningen_vulgraad.const import CONF_SCAN_INTERVAL_HOURS

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_POSTCODE: "1234AB", CONF_HUISNUMMER: "5"},
        options={CONF_CONTAINERS: ["111"], CONF_SCAN_INTERVAL_HOURS: 1},
        unique_id="1234AB-5",
    )
    entry.add_to_hass(hass)

    patch_client = patch(
        "custom_components.groningen_vulgraad.coordinator."
        "BurgerportaalClient.async_get_containers",
        AsyncMock(return_value=containers),
    )
    with patch_client:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert len(hass.states.async_all("sensor")) == 1

    with patch(PATCH_FETCH, AsyncMock(return_value=containers)):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["step_id"] == "init"
        # both sensored containers are on offer; the third has no sensor
        offered = [
            o["value"]
            for o in result["data_schema"].schema[CONF_CONTAINERS].config["options"]
        ]
        assert set(offered) == {"111", "222"}

        with patch_client:
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {CONF_CONTAINERS: ["111", "222"], CONF_SCAN_INTERVAL_HOURS: 1},
            )
            await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_CONTAINERS] == ["111", "222"]
    assert len(hass.states.async_all("sensor")) == 2, "a sensor per container"


async def test_second_entry_for_the_same_address_is_refused(hass):
    """One entry already polls for every container, so a duplicate is waste."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    MockConfigEntry(
        domain=DOMAIN,
        data={CONF_POSTCODE: "1234AB", CONF_HUISNUMMER: "5"},
        options={CONF_CONTAINERS: ["111"]},
        unique_id="1234AB-5",
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    with patch(PATCH_FETCH, AsyncMock()) as fetch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_POSTCODE: "1234 ab", CONF_HUISNUMMER: "5"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    fetch.assert_not_called(), "no point calling the portal for a duplicate"
