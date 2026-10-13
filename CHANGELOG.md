# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [0.1.0] - Unreleased

### Added
- UI setup by postcode and address list from the RCT Council website, with a manual UPRN
  fallback that is verified against the postcode.
- Reconfigure flow to change address; entities and history are kept.
- Options: property name, refresh interval (6/12/24 h), collection types, reminder window.
- Sensors: next collection, days until next collection, collection summary, last successful
  update (diagnostic), and one next-collection sensor per waste type the property receives.
- Binary sensors: collection today, tomorrow and due soon. These return `unknown` beyond the
  published horizon.
- Calendar with one all-day event per waste type per date.
- Local-midnight updates without network requests; Europe/London and BST aware.
- Last-known-good cache (versioned, atomic, up to 7 days) with stale-data indicators.
- Polite HTTP client:
  - timeouts, a 3 MiB response cap and no redirects;
  - retries with backoff, and `Retry-After` honoured;
  - a 5-minute floor between fetches and debounced manual refreshes.
- Redacted diagnostics.
- Integration icon shipped in `brand/` (served locally by Home Assistant 2026.10+).
