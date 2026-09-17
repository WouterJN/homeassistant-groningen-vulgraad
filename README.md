# Groningen container vulgraad

Read how full an underground waste container in Groningen is, and put it in
Home Assistant.

The municipality publishes fill levels only inside a Mendix portal that has no
API. This repo works out what that portal does and exposes it three ways: a
Home Assistant integration, a fast command line scraper, and a browser driven
fallback.

**No address or container number is stored in this repo.** Both are
configuration, either in the integration's own interface or through environment
variables and a gitignored JSON file for the scripts.

## Installation

### With HACS (recommended)

[HACS](https://hacs.xyz) is the Home Assistant Community Store. It installs
this integration for you and tells you when a new version is released, which is
why it is the preferred route.

1. Install HACS first if you do not have it yet, by following the guide at
   [hacs.xyz](https://hacs.xyz/docs/use/download/download/).
2. In Home Assistant, open **HACS** from the sidebar.
3. Open the menu at the top right, the one with three dots, and choose
   **Custom repositories**.
4. Paste `https://github.com/WouterJN/homeassistant-groningen-vulgraad` into the
   repository field, set **Type** to **Integration**, and select **Add**.
5. Search HACS for **Groningen container vulgraad**, open it, and select
   **Download**.
6. Restart Home Assistant.

Once this integration is listed in the HACS default store, steps 3 and 4 will
no longer be needed.

### By hand

Copy the folder `custom_components/groningen_vulgraad/` into the
`custom_components/` folder of your Home Assistant configuration, then restart
Home Assistant. You will have to repeat this for every update, which is what
HACS saves you from.

## Setting it up

1. Go to **Settings**, then **Devices and Services**, then **Add Integration**,
   and search for **Groningen container vulgraad**.
2. Enter any Groningen postcode and house number. It only unlocks the portal
   and filters nothing, so it need not be your own. A public building is fine.
3. Pick your containers from a dropdown of the roughly 1650 real containers
   that have a fill sensor, sorted by distance from your Home Assistant
   location, closest first.
4. Set the poll interval. The default, and the friendly minimum, is one hour.

You get one sensor per container, reported as a percentage with
`state_class: measurement`, grouped into a device per cluster. Each sensor
carries `warn_threshold`, `alarm_threshold`, `fraction` and `status` as
attributes, where status is `ok`, `warn` or `alarm`, so cards and automations
never hardcode 60 and 80.

A container whose `HeeftSensor` flag is false, or a poll that fails, reports
`unavailable`. It never reports 0, which would read as "just emptied" and fire
exactly the automation you did not want.

One fetch serves every container you configure. The portal's retrieval returns
the whole municipality or nothing, so watching ten containers costs what
watching one costs.

### Where the data comes from

Fill levels exist only inside the portal. The municipality does publish an open
data service for container locations, clusters and fractions, and this project
uses it to keep the container picker readable when the portal cannot be
reached, and for `--catalogue` on the command line. It carries no fill level
and no sensor flag, and it is slightly staler than the portal, so it never
replaces it.

### When the portal is redeployed

The integration calls the portal using operation identifiers compiled into the
Mendix app. A redeploy regenerates them and every poll then fails. The
integration raises a repair notice telling you to capture the new identifiers
with `scrape_vulgraad.py`, which drives the real client and survives a
redeploy.

## Command line

```bash
pip install requests
./vulgraad_http.py --catalogue                # container list from open data
export VULGRAAD_POSTCODE=1234AB VULGRAAD_HUISNUMMER=1
./vulgraad_http.py --container 567            # one container, as JSON
```

`--catalogue` reads the municipality's own open data service at
`maps.groningen.nl/geoserver`, which needs no address and puts no load on the
portal. It is the polite way to look up a container number. It publishes
locations only, so fill levels still come from the portal.

`vulgraad_http.py` replays the portal's `/xas/` protocol. It needs only
`requests`, finishes in about 2 seconds, and stops working when the app is
redeployed.

`scrape_vulgraad.py` drives the real client with Playwright. It needs Chromium,
about 94 MB, takes roughly 6 seconds, and stops working only when the interface
labels change.

Configuration precedence runs from command line flags, to environment
variables, to `vulgraad.config.json`. Copy `vulgraad.config.example.json` to
create one; it is gitignored. Exit codes are 0 for success, 1 when nothing
matched or the container has no sensor, and 2 for a configuration or protocol
failure.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```

`custom_components/groningen_vulgraad/api.py` imports nothing from Home
Assistant, so you can exercise it on its own.
