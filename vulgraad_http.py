#!/usr/bin/env python3
"""
Groningen burgerportaal -> container fill level (Vulgraad), without a browser.

Replays the Mendix /xas/ protocol directly: 8 POSTs, ~2 s per run, no Chromium.
Same configuration and output as scrape_vulgraad.py; see vulgraad_common.py.

    pip install requests

    ./vulgraad_http.py                    # target from config
    ./vulgraad_http.py --container 491    # by container number alone
    ./vulgraad_http.py --cluster 1063     # whole cluster, as a JSON array
    ./vulgraad_http.py --list             # every container in Groningen

Exit codes: 0 ok, 1 no match / no sensor, 2 configuration or protocol failure.

Fragile by design: the operationIds below are assigned when the Mendix app is
built and change on redeploy. When that happens this exits 2 and you fall back
to scrape_vulgraad.py, which only breaks on UI-label changes.
"""

import argparse
import json
import sys
from datetime import datetime, timezone

import requests

import vulgraad_common as common

XAS = "https://21burgerportaal.mendixcloud.com/xas/"

# The chain, in call order. The portal renders desktop and mobile copies of
# every widget, and each copy has its own operationId for the same underlying
# logic -- so each of these has an identical-payload twin that we simply never
# call. That is why this does one bulk retrieval where a browser does two.
OPS = {
    "seed":      "L2DRgbFgjVO3lpm5EHqv4A",  # mints link + helper + sessionData
    "address":   "4pSmQ7SgUlCQ8BbbusQIRg",  # submit postcode + house number
    "groups_ds": "wdCE9ugxFlWKOpwZ+pVM4w",  # datasource: tiles
    "pick_tile": "vmhd1Ab2ZF2sbyj0BI349Q",  # choose the Informatie tile
    "tmpl_ds":   "PHpJpnOIBFqfn1APctGdRg",  # datasource: templates of that tile
    "pick_tmpl": "7UcxGwslHVK5IysArKD6Tg",  # choose Containerlocaties
    "retrieve":  "9DR4gQCZF1yIjqtUq+iR7g",  # the bulk container payload
}

TILE = "Informatie"
TEMPLATE = "Containerlocaties"
LOCATIE = "Burger_Applicatie.Locatie"


class ProtocolError(RuntimeError):
    pass


class MendixClient:
    """Just enough of a Mendix client to walk one flow.

    Every runtimeOperation must echo back the client's view of the objects it
    has been handed, plus the `changes` block carrying the hashes the server
    minted for them. Trimming either is rejected with HTTP 560, so both are
    accumulated across the chain.
    """

    def __init__(self, timeout=60):
        self.s = requests.Session()
        self.s.headers.update({"Content-Type": "application/json",
                               "Accept": "application/json"})
        self.timeout = timeout
        self.store = {}      # guid -> object as last seen
        self.changes = {}    # guid -> field changes, including server hashes
        self.calls = 0
        self.decoded_bytes = 0   # responses are gzipped; this is after decoding

    def start(self):
        resp = self._post({"action": "get_session_data", "params": {}})
        token = resp.get("csrftoken")
        if not token:
            raise ProtocolError("no csrftoken in get_session_data")
        self.s.headers["X-Csrf-Token"] = token

    def _post(self, body):
        try:
            r = self.s.post(XAS, json=body, timeout=self.timeout)
        except requests.RequestException as e:
            raise ProtocolError(f"request failed: {e}")
        self.calls += 1
        self.decoded_bytes += len(r.content)
        if r.status_code != 200:
            raise ProtocolError(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            raise ProtocolError(f"non-JSON response: {r.text[:200]}")

    def call(self, op, params=None, extra_changes=None):
        changes = json.loads(json.dumps(self.changes))
        for guid, fields in (extra_changes or {}).items():
            changes.setdefault(guid, {}).update(fields)

        resp = self._post({
            "action": "runtimeOperation", "operationId": OPS[op],
            "params": params or {}, "validationGuids": [], "options": {},
            "changes": changes, "objects": list(self.store.values()),
        })
        for o in resp.get("objects", []):
            self.store[o["guid"]] = o
        for guid, ch in (resp.get("changes") or {}).items():
            self.changes.setdefault(guid, {}).update(ch)
        return resp

    def of_type(self, suffix):
        return [o for o in self.store.values()
                if o.get("objectType", "").endswith("." + suffix)]

    @staticmethod
    def label(obj):
        attrs = obj.get("attributes", {})
        for key in ("Naam", "Caption", "Titel"):
            value = (attrs.get(key) or {}).get("value")
            if value:
                return value
        return None

    def pick(self, suffix, wanted):
        """Find an object by its visible label rather than by position."""
        for o in self.of_type(suffix):
            if wanted in str(self.label(o) or ""):
                return o
        raise ProtocolError(f"no {suffix} named {wanted!r}; saw "
                            f"{[self.label(o) for o in self.of_type(suffix)]}")


def fetch_locations(postcode, huisnummer, verbose=False):
    """Walk session -> address -> tile -> template -> containers."""

    def step(msg):
        if verbose:
            print(f"  {msg}", file=sys.stderr)

    c = MendixClient()
    c.start()
    step("session established")

    # The seed response already contains a fully-formed sessionData changes
    # block -- token, municipality, and the association hashes. Echoing it back
    # untouched is what makes a browserless address submit work; building it by
    # hand is rejected as "postcode outside the service area".
    c.call("seed")
    link = c.of_type("link")[0]
    helper = c.of_type("helper")[0]

    resp = c.call("address",
                  params={"link": {"guid": link["guid"]},
                          "helper": {"guid": helper["guid"]}},
                  extra_changes={helper["guid"]: {
                      "adres": {"value": postcode},
                      "housenummer": {"value": huisnummer}}})
    form = next((i for i in resp.get("instructions", [])
                 if i.get("type") == "open_form"), None)
    if not form:
        messages = [i.get("args") for i in resp.get("instructions", [])]
        raise ProtocolError(f"address rejected: {json.dumps(messages)[:200]}")
    step("address accepted")

    groups = form["args"]["FormParameters"]["$ResultSet_Groups"]
    c.call("groups_ds", params={"CurrentObject": {"guid": groups}})
    tile = c.pick("Group", TILE)
    resp = c.call("pick_tile", params={"Group": {"guid": tile["guid"]}})

    templates = None
    for i in resp.get("instructions", []):
        for key, value in ((i.get("args") or {}).get("FormParameters") or {}).items():
            if "Templates" in key:
                templates = value
    if templates:
        c.call("tmpl_ds", params={"CurrentObject": {"guid": templates}})
    template = c.pick("Template", TEMPLATE)
    step(f"reached {TILE} / {TEMPLATE}")

    c.call("pick_tmpl", params={"Template": {"guid": template["guid"]}})
    result_sets = c.of_type("ResultSet_Templates")
    if not result_sets:
        raise ProtocolError("no ResultSet_Templates after template selection")

    resp = c.call("retrieve",
                  params={"resultSet": {"guid": result_sets[-1]["guid"]}})
    locations = [o for o in resp.get("objects", []) if o.get("objectType") == LOCATIE]
    if not locations:
        raise ProtocolError("retrieval returned no Locatie objects")
    step(f"{c.calls} calls, {c.decoded_bytes:,} bytes decoded, "
         f"{len(locations)} locations")
    return locations


def to_row(obj, scraped_at):
    """Flatten a Mendix Locatie into the output record."""
    attrs = {k: (v or {}).get("value") for k, v in obj["attributes"].items()}

    def as_int(key):
        value = attrs.get(key)
        return None if value in (None, "") else int(value)

    def as_float(key):
        value = attrs.get(key)
        return None if value in (None, "") else float(value)

    return {
        "cluster_id":   attrs.get("ClusterID"),
        "cluster_name": attrs.get("AdresVolledig"),
        "container":    attrs.get("ContainerNummer"),
        "vulgraad":     as_int("Vulgraad"),
        "has_sensor":   attrs.get("HeeftSensor"),
        "fraction":     attrs.get("FractieKleur"),
        "warn":         as_int("EersteGrensVulgraad"),
        "alarm":        as_int("TweedeGrensVulgraad"),
        "lat":          as_float("Latitude"),
        "lon":          as_float("Longitude"),
        "guid":         obj["guid"],
        "scraped_at":   scraped_at,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Read a Groningen container fill level (no browser).")
    common.add_arguments(ap)
    args, cfg = common.setup(ap)
    verbose = not args.quiet

    try:
        locations = fetch_locations(cfg["postcode"], cfg["huisnummer"], verbose)
    except ProtocolError as e:
        print(f"protocol failure (app redeployed? try scrape_vulgraad.py): {e}",
              file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return common.emit([to_row(o, now) for o in locations], args, cfg, verbose)


if __name__ == "__main__":
    sys.exit(main())
