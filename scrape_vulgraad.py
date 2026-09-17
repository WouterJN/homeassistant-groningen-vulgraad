#!/usr/bin/env python3
"""
Groningen burgerportaal -> container fill level (Vulgraad), via the real client.

Drives the Mendix SPA with Playwright up to the container map, then reads the
values straight out of the client-side object cache. Slower than
vulgraad_http.py and needs Chromium, but it only breaks when the UI labels
change -- not when the app is redeployed. Use it as the fallback, and to
re-capture operationIds when the fast path stops working.

Same configuration and output as vulgraad_http.py; see vulgraad_common.py.

    pip install playwright && playwright install chromium

    ./scrape_vulgraad.py                    # target from config
    ./scrape_vulgraad.py --container 491    # by container number alone
    ./scrape_vulgraad.py --cluster 1063     # whole cluster, as a JSON array
    ./scrape_vulgraad.py --list             # every container in Groningen
    ./scrape_vulgraad.py --headful --stats  # watch it, and report call counts

Exit codes: 0 ok, 1 no match / no sensor, 2 configuration or navigation failure.
"""

import argparse
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

import vulgraad_common as common

URL = "https://21burgerportaal.mendixcloud.com/p/groningen/landing/"
LOCATIE = "Burger_Applicatie.Locatie"

# The portal renders desktop and mobile copies of every widget and each copy
# fires its own datasource operations, so the 2.68 MB container payload is
# fetched twice under different operationIds. We read the object cache, which
# the first copy already filled, so the second is pure waste -- for us and for
# their server. These are the retrieval ops seen so far; others are learned.
KNOWN_BULK_OPS = {
    "9DR4gQCZF1yIjqtUq+iR7g",
    "F1xsBcBTqVugNcJ+nuC8sQ",
}

# Wire size above which a response counts as a bulk retrieval. Ordinary
# datasource calls are 1-9 KB gzipped and the container payload is two orders
# of magnitude larger, so this threshold is not delicate.
BULK_WIRE_BYTES = 50_000

STATE_FILE = pathlib.Path(__file__).with_name(".xas_bulk_ops.json")

# Runs in the page: walk the Mendix object cache and return plain objects.
# `sel` is null for --list; either half of it may be null.
EXTRACT_JS = """
(sel) => {
  if (!window.mx || !mx.data || !mx.data.objectCache) return null;
  const cache = mx.data.objectCache.objectCache;
  if (!cache) return null;
  const objs = (typeof cache.values === 'function')
    ? Array.from(cache.values())
    : Object.values(cache);

  const num = (v) => (v === null || v === undefined || v === '') ? null : Number(v);
  const read = (o) => ({
    cluster_id:   o.get('ClusterID'),
    cluster_name: o.get('AdresVolledig'),
    container:    o.get('ContainerNummer'),
    vulgraad:     num(o.get('Vulgraad')),
    has_sensor:   o.get('HeeftSensor'),
    fraction:     o.get('FractieKleur'),
    warn:         num(o.get('EersteGrensVulgraad')),
    alarm:        num(o.get('TweedeGrensVulgraad')),
    lat:          num(o.get('Latitude')),
    lon:          num(o.get('Longitude')),
    guid:         o.getGuid ? o.getGuid() : null
  });

  const out = [];
  for (const o of objs) {
    let t;
    try { t = o.getEntity(); } catch (e) { continue; }
    if (t !== 'Burger_Applicatie.Locatie') continue;
    if (sel) {
      if (sel.cluster && o.get('ClusterID') !== sel.cluster) continue;
      if (sel.container && o.get('ContainerNummer') !== sel.container) continue;
    }
    out.push(read(o));
    if (sel && sel.cluster && sel.container) break;
  }
  return out.length ? out : null;
}
"""


class XasMonitor:
    """Accounts for /xas/ traffic and drops the duplicate bulk retrieval.

    A request must be allowed or aborted before its size is known, so
    operationIds observed returning a bulk payload are remembered in a state
    file. KNOWN_BULK_OPS seeds it; after a redeploy one run pays the extra
    download, relearns, and the next run is lean again.

    Byte counts come from the Chrome DevTools Protocol and are wire sizes
    (gzipped). They only include requests that finish before the browser is
    closed, which is deliberate -- we close as soon as the data is readable --
    so treat them as a lower bound, not a billing statement.
    """

    def __init__(self, skip_duplicates=True, state_file=STATE_FILE):
        self.skip_duplicates = skip_duplicates
        self.state_file = state_file
        self.bulk_ops = set(KNOWN_BULK_OPS) | self._load()
        self.learned = set()
        self.calls = 0
        self.wire_bytes = 0
        self.allowed_bulk = 0
        self.aborted_bulk = 0
        self._op_by_request = {}
        self._xas_requests = set()

    def _load(self):
        try:
            return set(json.loads(self.state_file.read_text())["bulk_ops"])
        except Exception:
            return set()

    def save(self):
        if not self.learned:
            return
        try:
            self.state_file.write_text(json.dumps({
                "bulk_ops": sorted(self.bulk_ops),
                "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }, indent=2))
        except OSError:
            pass  # read-only deployment: relearn every run

    def route(self, route):
        op = _operation_id(route.request.post_data)
        if op and op in self.bulk_ops:
            if self.allowed_bulk and self.skip_duplicates:
                self.aborted_bulk += 1
                return route.abort()
            self.allowed_bulk += 1
        route.continue_()

    def attach(self, page):
        """Subscribe to CDP network events. Observational only."""
        try:
            cdp = page.context.new_cdp_session(page)
            cdp.send("Network.enable")
        except Exception:
            return False
        cdp.on("Network.requestWillBeSent", self._on_request)
        cdp.on("Network.loadingFinished", self._on_finished)
        return True

    def _on_request(self, event):
        if "/xas/" not in event["request"].get("url", ""):
            return
        request_id = event["requestId"]
        self._xas_requests.add(request_id)
        self.calls += 1
        op = _operation_id(event["request"].get("postData"))
        if op:
            self._op_by_request[request_id] = op

    def _on_finished(self, event):
        request_id = event["requestId"]
        if request_id not in self._xas_requests:
            return
        self._xas_requests.discard(request_id)
        size = event.get("encodedDataLength", 0)
        self.wire_bytes += size
        op = self._op_by_request.pop(request_id, None)
        if op and size >= BULK_WIRE_BYTES and op not in self.bulk_ops:
            self.bulk_ops.add(op)
            self.learned.add(op)


def _operation_id(post_data):
    if not post_data:
        return None
    try:
        return json.loads(post_data).get("operationId")
    except (ValueError, TypeError):
        return None


def one(locator):
    """First *visible* match.

    The portal ships duplicated responsive markup, so every field and label
    exists twice with one copy hidden at the current viewport. Element ids are
    regenerated each session, leaving visibility as the only stable choice.
    """
    return locator.filter(visible=True).first


def navigate_to_map(page, postcode, huisnummer, verbose):
    """Address form -> Informatie tile -> Containerlocaties."""

    def step(msg):
        if verbose:
            print(f"  {msg}", file=sys.stderr)

    step("loading landing page")
    page.goto(URL, wait_until="networkidle")

    step("entering address")
    one(page.get_by_placeholder("uw postcode")).fill(postcode)
    one(page.get_by_placeholder("uw huisnummer")).fill(huisnummer)
    one(page.get_by_role("button", name="Volgende")).click()

    step("choosing tile 'Informatie'")
    one(page.get_by_text("Informatie", exact=True)).click()

    step("opening 'Containerlocaties'")
    one(page.get_by_text("Containerlocaties", exact=True)).click()


def poll_cache(page, selector, timeout_s, verbose):
    """Poll the object cache until the bulk retrieval has landed."""
    started = time.monotonic()
    deadline = started + timeout_s
    while time.monotonic() < deadline:
        result = page.evaluate(EXTRACT_JS, selector)
        if result:
            if verbose:
                print(f"  cache filled after {time.monotonic() - started:.1f}s",
                      file=sys.stderr)
            return result
        page.wait_for_timeout(250)
    return None


def main():
    ap = argparse.ArgumentParser(
        description="Read a Groningen container fill level (real client).")
    common.add_arguments(ap)
    ap.add_argument("--skip-duplicate", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="abort the duplicated bulk retrieval (default: on)")
    ap.add_argument("--stats", action="store_true",
                    help="report /xas/ call count and wire bytes on stderr")
    ap.add_argument("--timeout", type=float, default=45.0,
                    help="seconds to wait for the dataset (default: 45)")
    ap.add_argument("--headful", action="store_true", help="show the browser")
    args, cfg = common.setup(ap)
    verbose = not args.quiet

    selector = None if args.list else {"cluster": cfg["cluster"],
                                       "container": cfg["container"]}
    monitor = XasMonitor(skip_duplicates=args.skip_duplicate)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headful)
        page = browser.new_page()
        page.set_default_timeout(30_000)
        monitor.attach(page)
        page.route("**/xas/", monitor.route)
        try:
            navigate_to_map(page, cfg["postcode"], cfg["huisnummer"], verbose)
            if verbose:
                print("  waiting for container dataset", file=sys.stderr)
            rows = poll_cache(page, selector, args.timeout, verbose)
        except PWTimeout as e:
            print(f"navigation failed (UI labels changed?): {e}", file=sys.stderr)
            return 2
        finally:
            browser.close()

    monitor.save()
    if args.stats or verbose:
        print(f"  {monitor.calls} /xas/ calls, >={monitor.wire_bytes:,} bytes on "
              f"the wire ({monitor.allowed_bulk} bulk retrieval(s), "
              f"{monitor.aborted_bulk} skipped)", file=sys.stderr)
    if monitor.learned:
        print(f"  learned new bulk operationId(s): "
              f"{', '.join(sorted(monitor.learned))} -- app redeployed?",
              file=sys.stderr)

    if not rows:
        print(f"nothing in the object cache after {args.timeout:.0f}s",
              file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for row in rows:
        row["scraped_at"] = now
    return common.emit(rows, args, cfg, verbose)


if __name__ == "__main__":
    sys.exit(main())
