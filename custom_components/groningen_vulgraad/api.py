"""Async client for the Groningen burgerportaal container data.

Deliberately free of Home Assistant imports: it takes an aiohttp session and
returns plain dataclasses, so it can be exercised from a plain script.

The portal is a Mendix 10 single-page app with no REST API. Everything moves
over POST /xas/, and generic client actions are locked down: only the
operations the app's own pages invoke will run. So this walks the same chain
the real client walks, in eight requests.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

XAS_URL = "https://21burgerportaal.mendixcloud.com/xas/"

# Opaque capability handles compiled into the Mendix app. They identify a call
# site, not a microflow, and are regenerated whenever the app is redeployed --
# the one real fragility here. When they go stale every call returns HTTP 560;
# re-capture them with scrape_vulgraad.py, which survives a redeploy.
DEFAULT_OPS: dict[str, str] = {
    "seed":      "L2DRgbFgjVO3lpm5EHqv4A",  # mints link + helper + sessionData
    "address":   "4pSmQ7SgUlCQ8BbbusQIRg",  # submit postcode + house number
    "groups_ds": "wdCE9ugxFlWKOpwZ+pVM4w",  # datasource: tiles
    "pick_tile": "vmhd1Ab2ZF2sbyj0BI349Q",  # choose the Informatie tile
    "tmpl_ds":   "PHpJpnOIBFqfn1APctGdRg",  # datasource: that tile's templates
    "pick_tmpl": "7UcxGwslHVK5IysArKD6Tg",  # choose Containerlocaties
    "retrieve":  "9DR4gQCZF1yIjqtUq+iR7g",  # the bulk container payload
}

TILE = "Informatie"
TEMPLATE = "Containerlocaties"
LOCATIE = "Burger_Applicatie.Locatie"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=90)


class VulgraadError(Exception):
    """Base error."""


class VulgraadConnectionError(VulgraadError):
    """The portal could not be reached."""


class VulgraadAddressRejected(VulgraadError):
    """The portal refused the postcode / house number."""


class VulgraadProtocolError(VulgraadError):
    """The protocol no longer behaves as expected -- most likely a redeploy."""


@dataclass(frozen=True)
class Container:
    """One physical container. `number` is unique municipality-wide."""

    number: str
    cluster_id: str | None
    cluster_name: str | None
    vulgraad: int | None
    # True / False from the portal; None when the source does not know, as with
    # the open data WFS, which carries no sensor flag at all.
    has_sensor: bool | None
    fraction: str | None
    warn: int | None
    alarm: int | None
    latitude: float | None
    longitude: float | None

    @property
    def label(self) -> str:
        """Human-readable one-liner for the config-flow picker."""
        parts = [self.cluster_name or f"cluster {self.cluster_id}", self.number]
        if self.fraction:
            parts.append(self.fraction.title())
        return " · ".join(parts)


class BurgerportaalClient:
    """Just enough of a Mendix client to walk one flow.

    Every runtimeOperation must echo back the client's view of the objects it
    has been handed, plus the `changes` block holding the hashes the server
    minted for them. Trimming either is rejected with HTTP 560, so both are
    accumulated as the chain progresses.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        ops: dict[str, str] | None = None,
    ) -> None:
        self._session = session
        self._ops = dict(ops or DEFAULT_OPS)
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._store: dict[str, dict[str, Any]] = {}
        self._changes: dict[str, dict[str, Any]] = {}

    async def async_get_containers(
        self, postcode: str, huisnummer: str
    ) -> list[Container]:
        """Run the full chain and return every container in the municipality."""
        await self._async_start()
        await self._async_submit_address(postcode, huisnummer)
        result_set = await self._async_reach_container_view()
        return await self._async_retrieve(result_set)

    # -- chain steps

    async def _async_start(self) -> None:
        payload = await self._async_post({"action": "get_session_data", "params": {}})
        token = payload.get("csrftoken")
        if not token:
            raise VulgraadProtocolError("no csrftoken in get_session_data")
        self._headers["X-Csrf-Token"] = token

    async def _async_submit_address(self, postcode: str, huisnummer: str) -> None:
        # The seed response already contains a fully-formed sessionData changes
        # block -- token, municipality, and the association hashes. Echoing it
        # back untouched is what makes a browserless submit work; building it by
        # hand is refused as "postcode outside the service area".
        await self._async_call("seed")
        link = self._of_type("link")[0]
        helper = self._of_type("helper")[0]

        response = await self._async_call(
            "address",
            params={
                "link": {"guid": link["guid"]},
                "helper": {"guid": helper["guid"]},
            },
            extra_changes={
                helper["guid"]: {
                    "adres": {"value": postcode},
                    "housenummer": {"value": huisnummer},
                }
            },
        )

        for instruction in response.get("instructions", []):
            if instruction.get("type") == "open_form":
                self._groups_guid = instruction["args"]["FormParameters"][
                    "$ResultSet_Groups"
                ]
                return

        message = _first_message(response)
        _LOGGER.debug("address rejected: %s", message)
        raise VulgraadAddressRejected(message or "address rejected")

    async def _async_reach_container_view(self) -> str:
        """Informatie tile -> Containerlocaties template -> a resultSet guid."""
        await self._async_call(
            "groups_ds", params={"CurrentObject": {"guid": self._groups_guid}}
        )
        tile = self._pick("Group", TILE)
        response = await self._async_call(
            "pick_tile", params={"Group": {"guid": tile["guid"]}}
        )

        templates_guid = None
        for instruction in response.get("instructions", []):
            parameters = (instruction.get("args") or {}).get("FormParameters") or {}
            for key, value in parameters.items():
                if "Templates" in key:
                    templates_guid = value
        if templates_guid:
            await self._async_call(
                "tmpl_ds", params={"CurrentObject": {"guid": templates_guid}}
            )

        template = self._pick("Template", TEMPLATE)
        await self._async_call(
            "pick_tmpl", params={"Template": {"guid": template["guid"]}}
        )

        result_sets = self._of_type("ResultSet_Templates")
        if not result_sets:
            raise VulgraadProtocolError("no ResultSet_Templates after selection")
        return result_sets[-1]["guid"]

    async def _async_retrieve(self, result_set: str) -> list[Container]:
        response = await self._async_call(
            "retrieve", params={"resultSet": {"guid": result_set}}
        )
        objects = [
            o for o in response.get("objects", []) if o.get("objectType") == LOCATIE
        ]
        if not objects:
            raise VulgraadProtocolError("retrieval returned no Locatie objects")

        containers: dict[str, Container] = {}
        for obj in objects:
            container = _to_container(obj)
            # The portal renders desktop and mobile copies of every widget, so
            # a session can hold two identical Locatie objects per container.
            if container.number and container.number not in containers:
                containers[container.number] = container
        _LOGGER.debug("retrieved %d containers", len(containers))
        return list(containers.values())

    # -- protocol plumbing

    async def _async_post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._session.post(
                XAS_URL, json=body, headers=self._headers, timeout=REQUEST_TIMEOUT
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    # 560 is the Mendix "operation refused" catch-all; from a
                    # previously working chain it means the ids went stale.
                    raise VulgraadProtocolError(
                        f"HTTP {response.status}: {text[:200]}"
                    )
                return await response.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise VulgraadConnectionError("timed out talking to the portal") from err
        except aiohttp.ClientError as err:
            raise VulgraadConnectionError(str(err)) from err
        except json.JSONDecodeError as err:
            raise VulgraadProtocolError(f"non-JSON response: {err}") from err

    async def _async_call(
        self,
        op: str,
        params: dict[str, Any] | None = None,
        extra_changes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        changes = json.loads(json.dumps(self._changes))
        for guid, fields in (extra_changes or {}).items():
            changes.setdefault(guid, {}).update(fields)

        response = await self._async_post(
            {
                "action": "runtimeOperation",
                "operationId": self._ops[op],
                "params": params or {},
                "validationGuids": [],
                "options": {},
                "changes": changes,
                "objects": list(self._store.values()),
            }
        )

        for obj in response.get("objects", []):
            self._store[obj["guid"]] = obj
        for guid, change in (response.get("changes") or {}).items():
            self._changes.setdefault(guid, {}).update(change)
        return response

    # -- object helpers

    def _of_type(self, suffix: str) -> list[dict[str, Any]]:
        found = [
            o
            for o in self._store.values()
            if o.get("objectType", "").endswith(f".{suffix}")
        ]
        if not found:
            raise VulgraadProtocolError(f"no {suffix} object in the session")
        return found

    @staticmethod
    def _label(obj: dict[str, Any]) -> str | None:
        attributes = obj.get("attributes", {})
        for key in ("Naam", "Caption", "Titel"):
            value = (attributes.get(key) or {}).get("value")
            if value:
                return str(value)
        return None

    def _pick(self, suffix: str, wanted: str) -> dict[str, Any]:
        """Find an object by its visible label, so a reordered menu still works."""
        for obj in self._of_type(suffix):
            if wanted in (self._label(obj) or ""):
                return obj
        raise VulgraadProtocolError(f"no {suffix} named {wanted!r}")


def _first_message(response: dict[str, Any]) -> str | None:
    for instruction in response.get("instructions", []):
        if instruction.get("type") == "text_message":
            args = instruction.get("args") or {}
            for value in args.values():
                if isinstance(value, str) and value:
                    return value
    return None


def _to_container(obj: dict[str, Any]) -> Container:
    attributes = {k: (v or {}).get("value") for k, v in obj["attributes"].items()}

    def as_int(key: str) -> int | None:
        value = attributes.get(key)
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def as_float(key: str) -> float | None:
        value = attributes.get(key)
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return Container(
        number=str(attributes.get("ContainerNummer") or ""),
        cluster_id=attributes.get("ClusterID"),
        cluster_name=attributes.get("AdresVolledig"),
        vulgraad=as_int("Vulgraad"),
        has_sensor=bool(attributes.get("HeeftSensor")),
        fraction=attributes.get("FractieKleur"),
        warn=as_int("EersteGrensVulgraad"),
        alarm=as_int("TweedeGrensVulgraad"),
        latitude=as_float("Latitude"),
        longitude=as_float("Longitude"),
    )
