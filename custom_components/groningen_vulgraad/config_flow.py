"""Config flow: enter an address, then pick containers from a real list."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util.location import distance

from .api import (
    BurgerportaalClient,
    Container,
    VulgraadAddressRejected,
    VulgraadConnectionError,
    VulgraadProtocolError,
)
from .const import (
    CONF_CONTAINERS,
    CONF_HUISNUMMER,
    CONF_POSTCODE,
    CONF_SCAN_INTERVAL_HOURS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    FRACTION_NAMES,
    HUISNUMMER_RE,
    MAX_SCAN_INTERVAL_HOURS,
    MIN_SCAN_INTERVAL_HOURS,
    POSTCODE_RE,
)

_LOGGER = logging.getLogger(__name__)

ADDRESS_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_POSTCODE): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT, autocomplete="postal-code")
        ),
        vol.Required(CONF_HUISNUMMER): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT)
        ),
    }
)


def _interval_selector() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=MIN_SCAN_INTERVAL_HOURS,
            max=MAX_SCAN_INTERVAL_HOURS,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="h",
        )
    )


def _validate_address(user_input: dict[str, Any]) -> dict[str, str]:
    """Check the shape of the address before spending a request on it."""
    errors: dict[str, str] = {}
    postcode = str(user_input.get(CONF_POSTCODE, "")).strip()
    huisnummer = str(user_input.get(CONF_HUISNUMMER, "")).strip()
    if not POSTCODE_RE.match(postcode):
        errors[CONF_POSTCODE] = "invalid_postcode"
    if not HUISNUMMER_RE.match(huisnummer):
        errors[CONF_HUISNUMMER] = "invalid_huisnummer"
    return errors


def _container_options(
    containers: list[Container], home: tuple[float | None, float | None]
) -> list[SelectOptionDict]:
    """Sensored containers, nearest to home first.

    The portal hands back the whole municipality, so the picker can show real
    containers instead of asking the user to know a number. Sorting by distance
    from the Home Assistant location puts theirs at the top.
    """
    home_lat, home_lon = home
    usable = [c for c in containers if c.has_sensor and c.number]

    def sort_key(container: Container) -> tuple[float, str]:
        if (
            home_lat is not None
            and home_lon is not None
            and container.latitude is not None
            and container.longitude is not None
        ):
            metres = distance(
                home_lat, home_lon, container.latitude, container.longitude
            )
            if metres is not None:
                return (metres, container.number)
        return (float("inf"), container.number)

    options: list[SelectOptionDict] = []
    for container in sorted(usable, key=sort_key):
        fraction = FRACTION_NAMES.get(
            (container.fraction or "").upper(), container.fraction or "?"
        )
        label = f"{container.cluster_name or container.cluster_id} · {fraction}"
        metres, _ = sort_key(container)
        if metres != float("inf"):
            label = f"{label} · {metres / 1000:.1f} km"
        options.append(SelectOptionDict(value=container.number, label=label))
    return options


async def _async_fetch(hass, postcode: str, huisnummer: str) -> list[Container]:
    client = BurgerportaalClient(async_get_clientsession(hass))
    return await client.async_get_containers(postcode, huisnummer)


class VulgraadConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    def __init__(self) -> None:
        self._postcode: str = ""
        self._huisnummer: str = ""
        self._containers: list[Container] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for an address and verify it against the portal."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = _validate_address(user_input)
            postcode = str(user_input[CONF_POSTCODE]).replace(" ", "").upper()
            huisnummer = str(user_input[CONF_HUISNUMMER]).strip()

            if not errors:
                await self.async_set_unique_id(f"{postcode}-{huisnummer}")
                self._abort_if_unique_id_configured()
                try:
                    self._containers = await _async_fetch(
                        self.hass, postcode, huisnummer
                    )
                except VulgraadAddressRejected:
                    errors["base"] = "address_rejected"
                except VulgraadConnectionError:
                    errors["base"] = "cannot_connect"
                except VulgraadProtocolError:
                    errors["base"] = "protocol_changed"
                except Exception:  # noqa: BLE001 - surfaced as a form error
                    _LOGGER.exception("unexpected error validating the address")
                    errors["base"] = "unknown"
                else:
                    self._postcode = postcode
                    self._huisnummer = huisnummer
                    return await self.async_step_containers()

        return self.async_show_form(
            step_id="user",
            data_schema=ADDRESS_SCHEMA,
            errors=errors,
            description_placeholders={"count": ""},
        )

    async def async_step_containers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick containers from the list the portal just returned."""
        options = _container_options(
            self._containers, (self.hass.config.latitude, self.hass.config.longitude)
        )
        if not options:
            return self.async_abort(reason="no_containers")

        if user_input is not None:
            selected = user_input[CONF_CONTAINERS]
            return self.async_create_entry(
                title=f"Containers ({len(selected)})",
                data={
                    CONF_POSTCODE: self._postcode,
                    CONF_HUISNUMMER: self._huisnummer,
                },
                options={
                    CONF_CONTAINERS: selected,
                    CONF_SCAN_INTERVAL_HOURS: int(
                        user_input.get(
                            CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
                        )
                    ),
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_CONTAINERS): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                vol.Optional(
                    CONF_SCAN_INTERVAL_HOURS, default=DEFAULT_SCAN_INTERVAL_HOURS
                ): _interval_selector(),
            }
        )
        return self.async_show_form(
            step_id="containers",
            data_schema=schema,
            description_placeholders={"count": str(len(options))},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return VulgraadOptionsFlow(entry)


class VulgraadOptionsFlow(OptionsFlow):
    """Change the container selection or the poll interval."""

    def __init__(self, entry: ConfigEntry) -> None:
        # Stored privately: assigning self.config_entry is deprecated.
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_CONTAINERS: user_input[CONF_CONTAINERS],
                    CONF_SCAN_INTERVAL_HOURS: int(
                        user_input[CONF_SCAN_INTERVAL_HOURS]
                    ),
                }
            )

        current = self._entry.options.get(CONF_CONTAINERS, [])
        try:
            containers = await _async_fetch(
                self.hass,
                self._entry.data[CONF_POSTCODE],
                self._entry.data[CONF_HUISNUMMER],
            )
            options = _container_options(
                containers,
                (self.hass.config.latitude, self.hass.config.longitude),
            )
        except Exception:  # noqa: BLE001 - fall back to what is configured
            _LOGGER.debug("could not refresh the container list", exc_info=True)
            options = [SelectOptionDict(value=n, label=n) for n in current]

        schema = vol.Schema(
            {
                vol.Required(CONF_CONTAINERS, default=current): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                vol.Required(
                    CONF_SCAN_INTERVAL_HOURS,
                    default=self._entry.options.get(
                        CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
                    ),
                ): _interval_selector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
