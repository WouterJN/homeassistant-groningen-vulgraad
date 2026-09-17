"""Polling coordinator: one fetch feeds every configured container."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BurgerportaalClient,
    Container,
    VulgraadAddressRejected,
    VulgraadConnectionError,
    VulgraadProtocolError,
)
from .const import (
    CONF_HUISNUMMER,
    CONF_OPERATION_IDS,
    CONF_POSTCODE,
    CONF_SCAN_INTERVAL_HOURS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    ISSUE_STALE_OPS,
)

_LOGGER = logging.getLogger(__name__)


class VulgraadCoordinator(DataUpdateCoordinator[dict[str, Container]]):
    """Fetch the whole municipality once per interval, keyed by container number.

    The retrieval is all-or-nothing -- there is no way to ask for one
    container -- so watching ten costs exactly what watching one costs.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        hours = entry.options.get(
            CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=hours),
        )
        self.entry = entry
        # A private cookie jar, not Home Assistant's shared client session.
        #
        # The portal is session based: it sets a session cookie and issues a
        # CSRF token bound to it. Home Assistant keeps ONE shared session, and
        # therefore one cookie jar, for the whole instance. If anything else
        # starts a portal chain while ours is in flight, for example the config
        # flow during a poll or a second config entry, the newer
        # get_session_data rotates the session underneath us and our next call
        # comes back HTTP 401. Reproduced against the live portal.
        #
        # Created during config entry setup, so Home Assistant detaches it when
        # the entry is unloaded.
        self._session = async_create_clientsession(hass)
        # Whatever worked last time. Empty on a fresh install, in which case the
        # client starts from the identifiers it ships with.
        self._ops: dict[str, str] = dict(entry.data.get(CONF_OPERATION_IDS) or {})

    async def _async_update_data(self) -> dict[str, Container]:
        postcode = self.entry.data[CONF_POSTCODE]
        huisnummer = self.entry.data[CONF_HUISNUMMER]

        try:
            # A fresh client per poll, because the client accumulates Mendix
            # object state that belongs to one walk of the flow. The cookie jar
            # is deliberately reused: the portal is happy to start a new
            # session on it, and the coordinator never overlaps its own polls.
            client = BurgerportaalClient(self._session, ops=self._ops or None)
            containers = await client.async_get_containers(postcode, huisnummer)
            if client.rediscovered:
                self._async_remember(client.operation_ids)
        except VulgraadProtocolError as err:
            # The client already tried to rediscover the identifiers from the
            # portal's own page definitions, so reaching here means the portal
            # changed in a way this integration cannot follow on its own.
            self._async_raise_stale_issue()
            raise UpdateFailed(f"portal protocol changed: {err}") from err
        except VulgraadAddressRejected as err:
            raise UpdateFailed(f"address rejected by the portal: {err}") from err
        except VulgraadConnectionError as err:
            raise UpdateFailed(f"cannot reach the portal: {err}") from err

        self._async_clear_stale_issue()
        return {c.number: c for c in containers if c.number}

    def _async_remember(self, ops: dict[str, str]) -> None:
        """Store rediscovered identifiers on the config entry."""
        if ops == self._ops:
            return
        self._ops = dict(ops)
        _LOGGER.info("storing rediscovered portal operation ids")
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**self.entry.data, CONF_OPERATION_IDS: self._ops},
        )

    def _async_raise_stale_issue(self) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_STALE_OPS,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_STALE_OPS,
        )

    def _async_clear_stale_issue(self) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_STALE_OPS)
