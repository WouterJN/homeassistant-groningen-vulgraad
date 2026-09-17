# Groningen container vulgraad

Read how full an underground waste container in Groningen is, and put it in
Home Assistant.

The municipality publishes fill levels only inside a Mendix portal with no API.
This repo works out what that portal does and exposes it three ways: a Home
Assistant integration, a fast command-line scraper, and a browser-driven
fallback. The reverse-engineering write-up is in
[`groningen-container-vulgraad_1.md`](groningen-container-vulgraad_1.md).

**No address or container number is stored in this repo.** Both are
configuration — in the integration's GUI, or via env vars / a gitignored JSON
file for the scripts.

## Home Assistant integration

`custom_components/groningen_vulgraad/` — a config-flow integration, set up
entirely from the UI.

1. Copy `custom_components/groningen_vulgraad/` into your HA `config/custom_components/`
   (or add this repo to HACS as a custom repository) and restart.
2. **Settings → Devices & Services → Add Integration → "Groningen container vulgraad"**.
3. Enter any Groningen postcode + house number. It only unlocks the portal and
   filters nothing, so it need not be your own — a public building is fine.
4. Pick your containers from a dropdown of the ~1650 real containers that have
   a fill sensor, **sorted nearest-first** from your Home Assistant location.
5. Set the poll interval (default and friendly minimum: 1 hour).

You get one sensor per container: `%`, `state_class: measurement`, grouped into
a device per cluster, with `warn_threshold` / `alarm_threshold` / `fraction` /
`status` (`ok` · `warn` · `alarm`) as attributes so cards and automations do
not hardcode 60 and 80.

A container whose `HeeftSensor` is false, or a failed poll, reports
`unavailable` — never `0`, which would read as "just emptied" and fire exactly
the automation you did not want.

One fetch serves every container you have configured: the portal's retrieval is
all-or-nothing, so watching ten costs what watching one costs.

### When the portal is redeployed

The integration calls the portal using operation identifiers compiled into the
Mendix app. A redeploy regenerates them and every poll fails. The integration
raises a **repair notice** telling you to re-capture them with
`scrape_vulgraad.py`, which drives the real client and survives redeploys.

## Command line

```bash
pip install requests
export VULGRAAD_POSTCODE=1234AB VULGRAAD_HUISNUMMER=1
./vulgraad_http.py --list --sensors-only      # find a container number
./vulgraad_http.py --container 567            # one container, as JSON
```

| | `vulgraad_http.py` | `scrape_vulgraad.py` |
|---|---|---|
| how | replays the portal's `/xas/` protocol | drives the real client with Playwright |
| needs | `requests` | `playwright` + Chromium (~94 MB) |
| speed | ~2 s | ~5.6 s |
| breaks when | the app is redeployed | the UI labels change |

Configuration precedence: CLI flags → environment → `vulgraad.config.json`
(copy `vulgraad.config.example.json`; it is gitignored). Exit codes: `0` ok,
`1` no match or no sensor, `2` config or protocol failure.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```

`custom_components/groningen_vulgraad/api.py` imports nothing from Home
Assistant, so it can be exercised standalone.
