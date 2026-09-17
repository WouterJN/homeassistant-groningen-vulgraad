"""Shared configuration and output handling for the Groningen container scrapers.

Two scrapers sit on top of this module -- vulgraad_http.py (fast, replays the
/xas/ protocol) and scrape_vulgraad.py (slow, drives the real client with
Playwright). They differ only in how they obtain the container objects; target
selection, filtering and output are identical and live here.

No address or container number appears in any source file. Values are resolved
at runtime, in descending precedence:

    1. command-line flags   --postcode / --huisnummer / --cluster / --container
    2. environment          VULGRAAD_POSTCODE, VULGRAAD_HUISNUMMER,
                            VULGRAAD_CLUSTER, VULGRAAD_CONTAINER
    3. a JSON config file   ./vulgraad.config.json, ~/.config/vulgraad/config.json,
                            or $VULGRAAD_CONFIG / --config

The address only unlocks the portal's address form; the retrieval returns every
container in the municipality whatever address is used. So it need not be
yours -- prefer a public building over your home.
"""

import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request

ENV_PREFIX = "VULGRAAD_"
FIELDS = ("postcode", "huisnummer", "cluster", "container")

SEARCH_PATHS = (
    pathlib.Path(__file__).with_name("vulgraad.config.json"),
    pathlib.Path.home() / ".config" / "vulgraad" / "config.json",
)

POSTCODE_RE = re.compile(r"^[1-9][0-9]{3}\s?[A-Za-z]{2}$")
HUISNUMMER_RE = re.compile(r"^[0-9]+$")

HELP = """\
No address configured. The scrapers need a Groningen postcode + house number to
get past the portal's address form. It does not filter anything, so it need not
be yours -- any valid address in the municipality works.

Set it in one of these ways:

  export VULGRAAD_POSTCODE=1234AB VULGRAAD_HUISNUMMER=1

  or write vulgraad.config.json next to the script:
      {"postcode": "1234AB", "huisnummer": "1",
       "cluster": "1234", "container": "567"}

  or pass --postcode 1234AB --huisnummer 1

See vulgraad.config.example.json."""


class ConfigError(Exception):
    pass


# --------------------------------------------------------------------- config


def _load_file(path=None):
    """Read the first config file that exists. Missing is fine; broken is not."""
    if path:
        candidates = [pathlib.Path(path)]
    else:
        candidates = list(SEARCH_PATHS)
        env_path = os.environ.get(ENV_PREFIX + "CONFIG")
        if env_path:
            candidates.insert(0, pathlib.Path(env_path))

    for p in candidates:
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text())
        except ValueError as e:
            raise ConfigError(f"{p} is not valid JSON: {e}")
        if not isinstance(data, dict):
            raise ConfigError(f"{p} must contain a JSON object")
        unknown = set(data) - set(FIELDS)
        if unknown:
            raise ConfigError(f"{p} has unknown key(s): {', '.join(sorted(unknown))}")
        return data, p

    if path:
        raise ConfigError(f"config file not found: {path}")
    return {}, None


def add_arguments(ap):
    """Register the flags shared by both scrapers."""
    ap.add_argument("--cluster", metavar="NNNN",
                    help="4-digit cluster number (default: from config)")
    ap.add_argument("--container", metavar="NNN",
                    help="container number, unique city-wide (default: from config)")
    ap.add_argument("--all-containers", action="store_true",
                    help="whole cluster, ignoring the configured container")
    ap.add_argument("--list", action="store_true",
                    help="every container in the municipality")
    ap.add_argument("--catalogue", action="store_true",
                    help="list containers from the municipality's open data "
                         "instead of the portal: no address needed, no fill levels")
    ap.add_argument("--sensors-only", action="store_true",
                    help="keep only containers that have a fill sensor")
    ap.add_argument("--postcode", metavar="1234AB",
                    help="any Groningen postcode (default: from config)")
    ap.add_argument("--huisnummer", metavar="N",
                    help="house number, digits only (default: from config)")
    ap.add_argument("--config", metavar="PATH", help="path to a JSON config file")
    ap.add_argument("--quiet", action="store_true", help="no progress on stderr")


WFS_URL = "https://maps.groningen.nl/geoserver/geo-data/wfs"
WFS_PARAMS = {
    "service": "wfs",
    "version": "2.0.0",
    "request": "GetFeature",
    "typeNames": "geo-data:CONTAINERS",
    "outputFormat": "application/json",
}


def catalogue_rows(timeout=60):
    """Container locations from the municipality's own open data service.

    Anonymous, no address, and no load on the burgerportaal. It carries no fill
    level and no sensor flag, and it is slightly staler than the portal, so it
    is for discovering container numbers, not for reading values.
    """
    try:
        # requests ships a CA bundle; urllib relies on the system store, which
        # a python.org build on macOS does not have.
        import requests

        response = requests.get(WFS_URL, params=WFS_PARAMS, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except ImportError:
        url = f"{WFS_URL}?{urllib.parse.urlencode(WFS_PARAMS)}"
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))

    rows, seen = [], set()
    for feature in payload.get("features", []):
        p = feature.get("properties") or {}
        number = str(p.get("CONTAINERCODE") or "").strip()
        if not number or number in seen:
            continue
        seen.add(number)
        rows.append({
            "cluster_id": _str_or_none(p.get("CLUSTERCODE")),
            "cluster_name": _str_or_none(p.get("CLUSTEROMSCHRIJVING")),
            "container": number,
            "vulgraad": None,
            "has_sensor": None,      # unknown: the WFS has no sensor flag
            "fraction": _str_or_none(p.get("FRACTIE")),
            "warn": None,
            "alarm": None,
            "lat": _float_or_none(p.get("LATITUDE")),
            "lon": _float_or_none(p.get("LONGITUDE")),
            "guid": None,
            "source": "opendata-wfs",
        })
    return rows


def _str_or_none(value):
    return None if value in (None, "") else str(value)


def _float_or_none(value):
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def resolve(args):
    """Merge flags, environment and config file into one validated settings dict.

    Raises ConfigError with actionable guidance rather than falling back to a
    built-in address, so a misconfigured run never silently scrapes the wrong
    container.
    """
    file_cfg, source = _load_file(getattr(args, "config", None))

    out = {}
    for name in FIELDS:
        cli = getattr(args, name, None)
        env = os.environ.get(ENV_PREFIX + name.upper())
        value = cli if cli is not None else (env or file_cfg.get(name))
        out[name] = str(value).strip() if value not in (None, "") else None

    # The catalogue comes from open data, which needs no address at all.
    if getattr(args, "catalogue", False):
        out["_source"] = str(source) if source else None
        return out

    if not (out["postcode"] and out["huisnummer"]):
        raise ConfigError(HELP)
    if not POSTCODE_RE.match(out["postcode"]):
        raise ConfigError(f"postcode {out['postcode']!r} does not look like a "
                          f"Dutch postcode (1234AB)")
    if not HUISNUMMER_RE.match(out["huisnummer"]):
        raise ConfigError(f"huisnummer {out['huisnummer']!r} must be digits only "
                          f"(no letters or additions)")

    out["postcode"] = out["postcode"].replace(" ", "").upper()
    if getattr(args, "all_containers", False):
        out["container"] = None
    if not getattr(args, "list", False) and not (out["cluster"] or out["container"]):
        raise ConfigError("no target: set cluster and/or container in config, "
                          "or pass --cluster / --container / --list")
    out["_source"] = str(source) if source else None
    return out


def setup(ap):
    """Parse arguments and resolve config, or exit 2 with an explanation."""
    args = ap.parse_args()
    try:
        cfg = resolve(args)
    except ConfigError as e:
        print(e, file=sys.stderr)
        raise SystemExit(2)
    if not args.quiet and cfg["_source"]:
        print(f"  config: {cfg['_source']}", file=sys.stderr)
    return args, cfg


# --------------------------------------------------------------------- output


def dedupe(rows):
    """Collapse repeated containers.

    The portal renders desktop and mobile copies of every widget and each copy
    fires its own retrieval, so a browser session sees two Locatie objects
    (different guids, identical attributes) per physical container.
    """
    seen, out = set(), []
    for r in rows:
        key = (r["cluster_id"], r["container"], r["fraction"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def emit(rows, args, cfg, verbose=True):
    """Filter, print and return the process exit code.

    One JSON object when a single container was asked for and exactly one
    matched; a JSON array otherwise, so downstream parsers can rely on the
    shape following the request rather than the data.
    """
    cluster, container = cfg["cluster"], cfg["container"]

    if not args.list:
        rows = [r for r in rows
                if (not cluster or r["cluster_id"] == cluster)
                and (not container or r["container"] == container)]
    if args.sensors_only:
        # None means "unknown", as in the open data catalogue, so it is excluded
        # here along with a known-false flag.
        rows = [r for r in rows if r["has_sensor"]]
    rows = dedupe(rows)

    if not rows:
        target = "any container" if args.list else f"{cluster or '*'}/{container or '*'}"
        print(f"no match for {target}", file=sys.stderr)
        return 1

    if args.list or container is None or len(rows) > 1:
        if container and len(rows) > 1:
            print(f"warning: {len(rows)} containers numbered {container}; "
                  f"narrow it with --cluster", file=sys.stderr)
        rows.sort(key=lambda r: (str(r["cluster_id"]), str(r["container"])))
        print(json.dumps(rows, indent=2))
        if verbose:
            print(f"  {len(rows)} container(s)", file=sys.stderr)
        return 0

    row = rows[0]
    print(json.dumps(row, indent=2))
    # Without a sensor the Vulgraad is stale or meaningless; a caller feeding
    # Home Assistant should publish 'unavailable' rather than the number.
    if not row["has_sensor"]:
        print("warning: HeeftSensor is false, Vulgraad is meaningless",
              file=sys.stderr)
        return 1
    return 0
