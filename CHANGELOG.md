# Changelog

## 0.6.0

### Fixed

- A portal outage no longer looks like a redeploy. A server error from the
  portal (HTTP 5xx other than Mendix's 560 refusal) is now reported as the
  portal being unreachable. Before, every poll during an outage walked the
  portal twice and raised the "portal has been redeployed" repair notice.
- A connection problem during rediscovery stays a connection problem, and
  raises no repair notice either.
- Learning new operation identifiers no longer reloads the integration. The
  poll that learned them is kept, instead of being thrown away and repeated.
- A portal reply that lacks a field the client relies on is treated as a
  protocol change, so it is rediscovered and reported, instead of ending in an
  unhandled error.
- The setup screen no longer points to a repair notice that does not exist.

### Added

- Diagnostics, downloadable from the integration's page, for bug reports. The
  address, and the numbers, clusters and coordinates of the containers you
  follow, are redacted.
- Containers you deselect under Configure are now removed, along with their
  sensors and any cluster device left empty.
- The container picker shows each container's number, so two containers of the
  same kind in one cluster can be told apart.
- `country` is set to `NL` in `hacs.json`, as HACS requires for integrations
  that only work in some countries.

### Changed

- Needs Home Assistant 2024.11 or newer.
- The coordinator lives on the config entry (`runtime_data`) instead of in
  `hass.data`.
- Tests run on Python 3.14 with pinned dependencies, check the code with
  `ruff`, and cover the portal client end to end against a fake portal.

## 0.5.0

- A second sensor per container records when it was last emptied, inferred
  from a fall of at least 20 points between two polls.
