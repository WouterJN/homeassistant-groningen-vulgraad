<img src="assets/icon.png" alt="" width="96" align="left">

# Groningen container vulgraad

Shows how full the underground waste containers in Groningen are, as Home
Assistant sensors.

<br clear="left">

The municipality only shows fill levels inside its burgerportaal, which has no
public API. This integration reads the portal for you.

## Installation

Requires Home Assistant 2024.11 or newer.

**With HACS (recommended).** Click the button below, or in HACS open the menu
with three dots, choose **Custom repositories**, and add this repository's URL
with type **Integration**. Then download **Groningen container vulgraad** and
restart Home Assistant.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=WouterJN&repository=homeassistant-groningen-vulgraad&category=integration)

**By hand.** Copy `custom_components/groningen_vulgraad/` into the
`custom_components/` folder of your configuration and restart. You will have to
repeat this for every update.

## Setup

1. Go to **Settings → Devices & services → Add integration** and search for
   **Groningen container vulgraad**.
2. Enter any Groningen postcode and house number. It only unlocks the portal and
   filters nothing, so a public building works as well as your own address.
3. Pick your containers. The list holds every container with a fill sensor,
   nearest to your Home Assistant location first.
4. Choose how often to poll. Hourly is the default and the minimum.

To follow more containers later, or fewer, choose **Configure** on the
integration. One entry handles any number of containers, because a single poll
returns the whole municipality.

## Sensors

Each container gets two sensors, grouped under a device for its cluster.

| Sensor | State | Attributes |
| --- | --- | --- |
| Fill level | Percentage | `status` (`ok`, `warn` or `alarm`), `warn_threshold`, `alarm_threshold`, `fraction`, `cluster_name` |
| Last emptied | Timestamp | `last_seen_level` |

- **The fill level is `unavailable` rather than 0** when a container has no
  working sensor or a poll fails, so a gap never looks like an emptying.
- **Last emptied is inferred.** The portal has no emptying date, so a fall of at
  least 20 points between two polls is recorded as an emptying. It is accurate to
  within one poll interval and reads `unknown` until the first one is seen. An
  emptying while Home Assistant was off is still caught after a restart.

Use `status` in automations rather than fixed numbers, since the thresholds
differ per container:

```yaml
triggers:
  - trigger: state
    entity_id: sensor.1234_teststraat_restafval_111
    attribute: status
    to: alarm
actions:
  - action: notify.notify
    data:
      message: "The restafval container is almost full."
```

## How it works

The portal is a Mendix app. The integration walks the same steps the website
does and receives every container in the municipality at once, so following ten
containers costs the same as following one.

Every portal request names internal identifiers that change whenever the
municipality rebuilds the portal. When the stored identifiers stop working, the
integration reads the new ones from the portal's own page definitions, within
the same poll, and remembers them. A repair notice appears only if that fails
too, which means the portal changed more than a rebuild would.

If the portal cannot be reached, the container picker falls back to the
municipality's open data service (`maps.groningen.nl/geoserver`) for names and
locations. That service has no fill levels, so it never replaces the portal.

## Problems

Open an [issue](https://github.com/WouterJN/homeassistant-groningen-vulgraad/issues)
and attach the diagnostics: on the integration's page, choose **Download
diagnostics** from the menu with three dots. They leave out your address, and
the numbers and locations of the containers you follow.

## Development

The tests need Python 3.14.

```bash
python3.14 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

```bash
.venv/bin/ruff check . && .venv/bin/python -m pytest tests -q
```

`api.py` imports nothing from Home Assistant, so the portal client can be run on
its own. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

MIT, see [LICENSE](LICENSE).
