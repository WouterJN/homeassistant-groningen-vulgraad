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
from dataclasses import dataclass
from typing import Any

import aiohttp

from .discovery import PageDefinition

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://21burgerportaal.mendixcloud.com"
XAS_URL = f"{BASE_URL}/xas/"
PAGE_URL = f"{BASE_URL}/pages/nl_NL/"

# What the portal calls the things we need. These are data model names, which a
# rebuild leaves alone, unlike the operation ids below.
LINK_ENTITY = "Burger_Applicatie.link"
GROUP_ENTITY = "Burger_Applicatie.Group"
TEMPLATE_ENTITY = "Burger_Applicatie.Template"
COLLECTION_ENTITY = "Burger_Applicatie.Inzamelplek"

# Sent so the portal knows which municipality this is, which also makes it name
# its own landing page in the response. That is where discovery starts.
SESSION_PARAMS = {
    "offline": True,
    "referrer": "/groningen/landing/",
    "deviceType": "Desktop",
    "profile": "",
    "timezoneoffset": 0,
    "timezoneId": "Europe/Amsterdam",
    "preferredLanguages": ["nl-NL"],
    "version": 2,
}

# Opaque capability handles compiled into the app, regenerated on every
# redeploy. These are only a cache of the last known good set: when they stop
# working the client rediscovers them from the portal's own page definitions,
# so a redeploy no longer needs a new release. See discovery.py.
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
        allow_discovery: bool = True,
    ) -> None:
        self._session = session
        self._ops = dict(ops) if ops else dict(DEFAULT_OPS)
        self._allow_discovery = allow_discovery
        self.rediscovered = False
        self._headers: dict[str, str] = {}
        self._store: dict[str, dict[str, Any]] = {}
        self._changes: dict[str, dict[str, Any]] = {}

    @property
    def operation_ids(self) -> dict[str, str]:
        """The identifiers that actually worked, for the caller to remember."""
        return dict(self._ops)

    async def async_get_containers(
        self, postcode: str, huisnummer: str
    ) -> list[Container]:
        """Every container in the municipality, with fill levels.

        Tries the known identifiers first, which costs nothing extra. If the
        portal refuses them, which is what a redeploy looks like, the whole
        walk is repeated while reading each identifier out of the portal's own
        page definitions.
        """
        try:
            return await self._walk(postcode, huisnummer, discover=False)
        except VulgraadProtocolError as err:
            if not self._allow_discovery:
                raise
            _LOGGER.info(
                "portal rejected the known operation ids (%s), rediscovering", err
            )
            try:
                containers = await self._walk(postcode, huisnummer, discover=True)
            except VulgraadError as discovery_err:
                raise VulgraadProtocolError(
                    f"{err}; rediscovery also failed: {discovery_err}"
                ) from discovery_err
            self.rediscovered = True
            _LOGGER.info("rediscovered the portal's operation ids")
            return containers

    async def _walk(
        self, postcode: str, huisnummer: str, discover: bool
    ) -> list[Container]:
        self._store.clear()
        self._changes.clear()
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        landing = await self._async_start()
        if discover:
            page = await self._async_page(landing)
            self._learn("seed", page.microflow_datasource(LINK_ENTITY), page)
            self._learn(
                "address", page.call_with_arguments({"link", "helper"}), page
            )

        groups_guid, groups_page = await self._async_submit_address(
            postcode, huisnummer
        )
        if discover:
            page = await self._async_page(groups_page)
            self._learn(
                "groups_ds", page.entity_path_datasource(GROUP_ENTITY), page
            )
            self._learn("pick_tile", page.microflow_call("Group"), page)

        templates_guid, templates_page = await self._async_choose_tile(groups_guid)
        if discover:
            page = await self._async_page(templates_page)
            self._learn(
                "tmpl_ds", page.entity_path_datasource(TEMPLATE_ENTITY), page
            )
            self._learn("pick_tmpl", page.microflow_call("Template"), page)

        result_set, final_page = await self._async_choose_template(templates_guid)
        if discover:
            page = await self._async_page(final_page)
            self._learn(
                "retrieve", page.microflow_datasource(COLLECTION_ENTITY), page
            )

        return await self._async_retrieve(result_set)

    def _learn(
        self, name: str, operation: str | None, page: PageDefinition
    ) -> None:
        if not operation:
            raise VulgraadProtocolError(
                f"could not find the {name!r} operation in {page.path}"
            )
        if self._ops.get(name) != operation:
            _LOGGER.debug("%s: %s -> %s", name, self._ops.get(name), operation)
        self._ops[name] = operation

    # -- chain steps

    async def _async_start(self) -> str:
        """Open a session. The reply names the portal's own landing page."""
        payload = await self._async_post(
            {"action": "get_session_data", "params": SESSION_PARAMS}
        )
        token = payload.get("csrftoken")
        if not token:
            raise VulgraadProtocolError("no csrftoken in get_session_data")
        self._headers["X-Csrf-Token"] = token
        path, _ = _opened_form(payload)
        if not path:
            raise VulgraadProtocolError("bootstrap did not name a landing page")
        return path

    async def _async_submit_address(
        self, postcode: str, huisnummer: str
    ) -> tuple[str, str]:
        # The seed response already contains a fully-formed sessionData changes
        # block: token, municipality, and the association hashes. Echoing it
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
        path, parameters = _opened_form(response)
        if not path:
            message = _first_message(response)
            _LOGGER.debug("address rejected: %s", message)
            raise VulgraadAddressRejected(message or "address rejected")
        return parameters["$ResultSet_Groups"], path

    async def _async_choose_tile(self, groups_guid: str) -> tuple[str | None, str]:
        await self._async_call(
            "groups_ds", params={"CurrentObject": {"guid": groups_guid}}
        )
        tile = self._pick("Group", TILE)
        response = await self._async_call(
            "pick_tile", params={"Group": {"guid": tile["guid"]}}
        )
        path, parameters = _opened_form(response)
        templates = next(
            (v for k, v in parameters.items() if "Templates" in k), None
        )
        if not path:
            raise VulgraadProtocolError("choosing the tile opened no page")
        return templates, path

    async def _async_choose_template(
        self, templates_guid: str | None
    ) -> tuple[str, str]:
        if templates_guid:
            await self._async_call(
                "tmpl_ds", params={"CurrentObject": {"guid": templates_guid}}
            )
        template = self._pick("Template", TEMPLATE)
        response = await self._async_call(
            "pick_tmpl", params={"Template": {"guid": template["guid"]}}
        )
        path, _ = _opened_form(response)
        result_sets = self._of_type("ResultSet_Templates")
        if not result_sets:
            raise VulgraadProtocolError("no ResultSet_Templates after selection")
        if not path:
            raise VulgraadProtocolError("choosing the template opened no page")
        return result_sets[-1]["guid"], path

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

    async def _async_page(self, path: str) -> PageDefinition:
        """Fetch one page definition. Anonymous, plain HTTP, no session."""
        url = f"{PAGE_URL}{path}"
        try:
            async with self._session.get(url, timeout=REQUEST_TIMEOUT) as response:
                if response.status != 200:
                    raise VulgraadProtocolError(
                        f"page {path} returned HTTP {response.status}"
                    )
                return PageDefinition(path, await response.text())
        except asyncio.TimeoutError as err:
            raise VulgraadConnectionError(f"timed out fetching {path}") from err
        except aiohttp.ClientError as err:
            raise VulgraadConnectionError(str(err)) from err

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


def _opened_form(response: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """The page the portal just opened, and the parameters it passed to it."""
    for instruction in response.get("instructions", []):
        if instruction.get("type") == "open_form":
            args = instruction.get("args") or {}
            return args.get("FormPath"), args.get("FormParameters") or {}
    return None, {}


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
