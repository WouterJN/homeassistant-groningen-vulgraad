"""The municipality's own open data service for container locations.

`https://maps.groningen.nl/geoserver/` publishes container locations as WFS,
anonymously and without an address. It is the authoritative source for what a
container *is*: cluster, number, fraction, coordinates.

It is not a substitute for the portal, for two measured reasons:

* it carries no fill level and no sensor flag, and
* it is staler. Compared against the portal on 2026-09-17 it was missing 42
  containers that do have a fill sensor, and listed 30 that the portal does not
  know at all.

Where the two overlap they agree exactly: 1927 shared container numbers, zero
cluster disagreements. So this is used as a fallback for labelling containers
when the portal cannot be reached, never as the primary list.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from .api import Container, VulgraadConnectionError, VulgraadProtocolError

_LOGGER = logging.getLogger(__name__)

WFS_URL = "https://maps.groningen.nl/geoserver/geo-data/wfs"

WFS_PARAMS = {
    "service": "wfs",
    "version": "2.0.0",
    "request": "GetFeature",
    "typeNames": "geo-data:CONTAINERS",
    "outputFormat": "application/json",
}

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)


async def async_get_catalogue(session: aiohttp.ClientSession) -> list[Container]:
    """Every container the municipality publishes, without fill levels.

    `has_sensor` comes back as None, meaning "unknown", which is distinct from
    the portal's False, meaning "known to have no sensor".
    """
    try:
        async with session.get(
            WFS_URL, params=WFS_PARAMS, timeout=REQUEST_TIMEOUT
        ) as response:
            if response.status != 200:
                raise VulgraadProtocolError(
                    f"WFS returned HTTP {response.status}"
                )
            payload = await response.json(content_type=None)
    except asyncio.TimeoutError as err:
        raise VulgraadConnectionError("timed out talking to the WFS") from err
    except aiohttp.ClientError as err:
        raise VulgraadConnectionError(str(err)) from err

    containers: dict[str, Container] = {}
    for feature in payload.get("features", []):
        properties = feature.get("properties") or {}
        number = str(properties.get("CONTAINERCODE") or "").strip()
        if not number or number in containers:
            continue
        containers[number] = Container(
            number=number,
            cluster_id=_text(properties.get("CLUSTERCODE")),
            cluster_name=_text(properties.get("CLUSTEROMSCHRIJVING")),
            vulgraad=None,
            has_sensor=None,
            fraction=_text(properties.get("FRACTIE")),
            warn=None,
            alarm=None,
            latitude=_number(properties.get("LATITUDE")),
            longitude=_number(properties.get("LONGITUDE")),
        )

    if not containers:
        raise VulgraadProtocolError("WFS returned no containers")
    _LOGGER.debug("WFS catalogue: %d containers", len(containers))
    return list(containers.values())


def _text(value) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _number(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
