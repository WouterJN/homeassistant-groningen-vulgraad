"""The portal client, walked against a fake portal that answers like the real one.

The fake keeps the parts of the protocol the client depends on: a CSRF token,
operations addressed by identifier, objects handed back and forth, and pages
that name the operation behind each widget. It refuses identifiers it does not
know with HTTP 560, which is what a redeploy looks like.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.groningen_vulgraad.api import (
    DEFAULT_OPS,
    PAGE_URL,
    BurgerportaalClient,
    VulgraadAddressRejected,
    VulgraadConnectionError,
    VulgraadProtocolError,
    _to_container,
)

LANDING = "groningen/landing.page.xml"
GROUPS = "groningen/groups.page.xml"
TEMPLATES = "groningen/templates.page.xml"
FINAL = "groningen/containers.page.xml"

# What a redeploy turns the identifiers into.
NEW_OPS = {name: f"new-{name}" for name in DEFAULT_OPS}


def open_form(path: str, **parameters: str) -> dict[str, Any]:
    return {
        "type": "open_form",
        "args": {"FormPath": path, "FormParameters": parameters},
    }


def obj(guid: str, kind: str, **attributes: Any) -> dict[str, Any]:
    return {
        "guid": guid,
        "objectType": f"Burger_Applicatie.{kind}",
        "attributes": {k: {"value": v} for k, v in attributes.items()},
    }


def locatie(number: str, vulgraad: str = "42", sensor: bool = True) -> dict[str, Any]:
    return obj(
        f"loc-{number}",
        "Locatie",
        ContainerNummer=number,
        ClusterID="1234",
        AdresVolledig="1234 - Teststraat",
        Vulgraad=vulgraad,
        HeeftSensor=sensor,
        FractieKleur="GREY",
        EersteGrensVulgraad="60",
        TweedeGrensVulgraad="80",
        Latitude="53.2",
        Longitude="6.5",
    )


def pages(ops: dict[str, str]) -> dict[str, str]:
    """Page definitions naming `ops`, in the shapes discovery.py reads."""
    return {
        LANDING: (
            '"datasource":{"type":"microflow","path":"Burger_Applicatie.link",'
            f'"operationId":"{ops["seed"]}"}}'
            '"argMap":{"link":{"widget":"a"},"helper":{"widget":"b"}},'
            f'"config":{{"operationId":"{ops["address"]}"}}'
        ),
        GROUPS: (
            '"datasource":{"type":"entityPath",'
            '"path":"Burger_Applicatie.Group_ResultSet_Groups/Burger_Applicatie.Group",'
            f'"operationId":"{ops["groups_ds"]}"}}'
            f'"type":"microflowCall","operationId":"{ops["pick_tile"]}",'
            '"parameters":[{"name":"Group"}]'
        ),
        TEMPLATES: (
            '"datasource":{"type":"entityPath",'
            '"path":"Burger_Applicatie.T_ResultSet/Burger_Applicatie.Template",'
            f'"operationId":"{ops["tmpl_ds"]}"}}'
            f'"type":"microflowCall","operationId":"{ops["pick_tmpl"]}",'
            '"parameters":[{"name":"Template"}]'
        ),
        FINAL: (
            '"datasource":{"type":"microflow",'
            '"path":"Burger_Applicatie.Inzamelplek",'
            f'"operationId":"{ops["retrieve"]}"}}'
        ),
    }


class FakeResponse:
    def __init__(self, status: int, payload: Any = None, text: str = "") -> None:
        self.status = status
        self._payload = payload
        self._text = text

    async def json(self, content_type: str | None = None) -> Any:
        return self._payload

    async def text(self) -> str:
        return self._text

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakePortal:
    """Just enough of the portal to walk the flow, and to break it on purpose."""

    def __init__(self, ops: dict[str, str] | None = None) -> None:
        self.ops = dict(ops or DEFAULT_OPS)
        self.by_id = {v: k for k, v in self.ops.items()}
        self.posts: list[dict[str, Any]] = []
        self.page_fetches: list[str] = []
        self.status: int | None = None  # forced HTTP status for every post
        self.reject_address = False
        self.address_parameters: dict[str, str] = {"$ResultSet_Groups": "rs-groups"}
        self.containers = [locatie("111"), locatie("111"), locatie("222", "7")]

    # -- aiohttp session surface

    def post(self, url: str, json: dict[str, Any], **kwargs: Any) -> FakeResponse:
        self.posts.append(json)
        if self.status is not None:
            return FakeResponse(self.status, text="forced")
        if json["action"] == "get_session_data":
            return FakeResponse(
                200, {"csrftoken": "token", "instructions": [open_form(LANDING)]}
            )
        name = self.by_id.get(json["operationId"])
        if name is None:
            return FakeResponse(560, text="operation refused")
        return FakeResponse(200, getattr(self, f"_op_{name}")(json))

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        path = url.removeprefix(PAGE_URL)
        self.page_fetches.append(path)
        page = pages(self.ops).get(path)
        if page is None:
            return FakeResponse(404)
        return FakeResponse(200, text=page)

    # -- operations

    def _op_seed(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "objects": [obj("link-1", "link"), obj("helper-1", "helper")],
            "changes": {"session-1": {"token": {"value": "abc"}}},
        }

    def _op_address(self, body: dict[str, Any]) -> dict[str, Any]:
        self.submitted = body["changes"]["helper-1"]
        if self.reject_address:
            return {
                "instructions": [
                    {"type": "text_message", "args": {"message": "buiten gebied"}}
                ]
            }
        return {"instructions": [open_form(GROUPS, **self.address_parameters)]}

    def _op_groups_ds(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "objects": [
                obj("g-1", "Group", Naam="Afval"),
                obj("g-2", "Group", Naam="Informatie"),
            ]
        }

    def _op_pick_tile(self, body: dict[str, Any]) -> dict[str, Any]:
        assert body["params"]["Group"]["guid"] == "g-2"
        return {"instructions": [open_form(TEMPLATES, **{"$Templates": "rs-t"})]}

    def _op_tmpl_ds(self, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "objects": [
                obj("t-1", "Template", Naam="Containerlocaties"),
                obj("rs-1", "ResultSet_Templates"),
            ]
        }

    def _op_pick_tmpl(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"instructions": [open_form(FINAL)]}

    def _op_retrieve(self, body: dict[str, Any]) -> dict[str, Any]:
        assert body["params"]["resultSet"]["guid"] == "rs-1"
        return {"objects": self.containers}


async def test_walks_the_flow_with_the_known_ids():
    portal = FakePortal()
    client = BurgerportaalClient(portal)

    containers = await client.async_get_containers("1234AB", "5")

    # desktop and mobile copies of one container collapse into one
    assert [c.number for c in containers] == ["111", "222"]
    first = containers[0]
    assert first.vulgraad == 42
    assert first.has_sensor is True
    assert (first.warn, first.alarm) == (60, 80)
    assert first.latitude == 53.2
    assert client.rediscovered is False
    assert portal.page_fetches == [], "known ids must not cost page fetches"
    assert portal.submitted == {
        "adres": {"value": "1234AB"},
        "housenummer": {"value": "5"},
    }
    # every operation after the bootstrap carries the CSRF token's session
    assert all(p.get("changes") is not None for p in portal.posts[1:])


async def test_a_redeploy_is_followed_by_rediscovery():
    portal = FakePortal(ops=NEW_OPS)
    client = BurgerportaalClient(portal)

    containers = await client.async_get_containers("1234AB", "5")

    assert len(containers) == 2
    assert client.rediscovered is True
    assert client.operation_ids == NEW_OPS
    assert portal.page_fetches == [LANDING, GROUPS, TEMPLATES, FINAL]


async def test_rediscovery_can_be_switched_off():
    client = BurgerportaalClient(FakePortal(ops=NEW_OPS), allow_discovery=False)
    with pytest.raises(VulgraadProtocolError):
        await client.async_get_containers("1234AB", "5")


async def test_a_portal_outage_is_not_a_redeploy():
    """A 503 must not trigger rediscovery or a repair notice."""
    portal = FakePortal()
    portal.status = 503
    client = BurgerportaalClient(portal)

    with pytest.raises(VulgraadConnectionError):
        await client.async_get_containers("1234AB", "5")
    assert portal.page_fetches == []
    assert len(portal.posts) == 1, "no second walk on an outage"


async def test_an_outage_during_rediscovery_stays_a_connection_error():
    portal = FakePortal(ops=NEW_OPS)

    real_get = portal.get

    def get(url: str, **kwargs: Any) -> FakeResponse:
        real_get(url, **kwargs)
        return FakeResponse(502)

    portal.get = get
    with pytest.raises(VulgraadConnectionError):
        await BurgerportaalClient(portal).async_get_containers("1234AB", "5")


async def test_a_rejected_address():
    portal = FakePortal()
    portal.reject_address = True
    with pytest.raises(VulgraadAddressRejected, match="buiten gebied"):
        await BurgerportaalClient(portal).async_get_containers("1011AB", "1")


async def test_an_unexpected_reply_shape_is_a_protocol_error():
    """A missing field takes the recovery path, not an unhandled KeyError."""
    portal = FakePortal()
    portal.address_parameters = {}
    with pytest.raises(VulgraadProtocolError):
        await BurgerportaalClient(portal).async_get_containers("1234AB", "5")


async def test_an_empty_retrieval_is_a_protocol_error():
    portal = FakePortal()
    portal.containers = []
    with pytest.raises(VulgraadProtocolError):
        await BurgerportaalClient(portal).async_get_containers("1234AB", "5")


def test_malformed_numbers_become_none():
    container = _to_container(
        obj(
            "x",
            "Locatie",
            ContainerNummer=333,
            Vulgraad="n/a",
            HeeftSensor=False,
            Latitude="",
        )
    )
    assert container.number == "333"
    assert container.vulgraad is None
    assert container.has_sensor is False
    assert container.latitude is None
    assert container.warn is None
