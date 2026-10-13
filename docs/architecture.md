# Architecture

```text
custom_components/rct_bin_collections/
├── __init__.py          Config entry setup/unload/remove
├── manifest.json        Integration metadata (version, requirements)
├── const.py             Keys, defaults and limits
├── exceptions.py        Data-layer exceptions (no HA imports)
├── models.py            Dataclasses, postcode/UPRN normalisation, category mapping (no HA imports)
├── api.py               Polite async HTTP client (timeouts, size cap, retries, 429)
├── providers/
│   ├── __init__.py      Provider registry
│   ├── base.py          CollectionProvider interface and capabilities
│   ├── rct_council.py   Council provider: HTTP + parse + validate
│   └── rct_council_parser.py  Pure HTML parsing functions
├── coordinator.py       DataUpdateCoordinator: fetch, cache, staleness, midnight tick
├── entity.py            Shared base entity and device info
├── sensor.py            Next/days/summary/last-update and per-category sensors
├── binary_sensor.py     Today / tomorrow / due-soon reminders
├── calendar.py          CalendarEntity
├── config_flow.py       User, select address, manual UPRN, reconfigure and options flows
├── diagnostics.py       Redacted diagnostics
├── strings.json, translations/en.json, icons.json
```

## Data flow

```text
Council website ──HTTPS──▶ HttpClient ──HTML──▶ parser ──ParsedSchedule──▶ RctCouncilProvider
                                                                              │ validates postcode,
                                                                              │ builds CollectionSchedule
                                                                              ▼
                                Store (.storage) ◀──save/load── RctBinCoordinator
                                                                              │ CoordinatorData
                              ┌───────────────┬──────────────┬────────────────┤
                              ▼               ▼              ▼                ▼
                           sensors     binary_sensors     calendar       diagnostics
```

- **Layers.** The HTTP client, parser and provider have no Home Assistant imports apart from the
  shared `aiohttp` session. They are tested directly.
- **Parsing off the event loop.** The parser is CPU-bound: the council page is about 360 KB of HTML. It
  runs in the default executor, so it never blocks the event loop.
- **Normalised schedules.** `CollectionSchedule.build` removes duplicate events and sorts them into
  display order: general waste, recycling, food, garden, hygiene, then unknown types. Each schedule
  records the **horizon** (the first and last day the council published), so entities can tell "no
  collection" apart from "not published yet".

## Coordinator behaviour

| Situation | Behaviour |
|---|---|
| Normal | Fetch every 12 h (configurable to 6 or 24 h). One request per refresh. |
| Config flow just fetched the schedule | The coordinator reuses it and skips the immediate re-fetch. |
| Manual refresh (`homeassistant.update_entity`) | Debounced (60 s cooldown). Skipped entirely within 5 min of a successful fetch. |
| Concurrent refreshes | Share one in-flight request. |
| Network error, timeout, HTTP 5xx, parse error, unknown property | Keep the last good schedule, marked `data_stale: true`, with `last_error` set. Retry after 1 h. |
| HTTP 429 | Same, but wait for `Retry-After`, capped at the normal interval. |
| Postcode in the response doesn't match the configured one | `ConfigEntryError`: entities go unavailable, because the dates might be for someone else. |
| No successful fetch for over 7 days | `UpdateFailed`: entities go unavailable rather than showing old data. |
| Startup with the council down | Load the cache if it is under 7 days old and for the same property. Otherwise `ConfigEntryNotReady`. |
| Local midnight | `async_track_time_change` (local time) calls `async_update_listeners()`. No network request. |

The cache is a versioned `Store` with atomic writes, kept per config entry. It contains no address
and no plain UPRN, only a truncated SHA-256 tag of the UPRN. Past events are filtered whenever
data is read, so an old cache can never yield dates that look like future ones.

## Entities and identifiers

- Unique IDs are `<uprn>_<key>`, for example `100100123456_category_recycling`.
- The device identifier is `(rct_bin_collections, <uprn>)`.
- Reconfiguring to a new UPRN migrates the entity and device registry entries, so entity IDs and
  history survive a house move.
- Per-category sensors are created dynamically. They are created for categories in the current
  schedule, and for categories seen before (taken from the entity registry), so seasonal services
  don't disappear. A category that appears in a later refresh gets its entity added immediately.

## Adding another provider

1. Implement `CollectionProvider` in `providers/<name>.py`, and declare its capabilities. Don't
   implement `async_find_properties` unless the source really supports postcode lookup.
2. Return a `CollectionSchedule` with an honest horizon, and raise the exceptions in
   `exceptions.py`.
3. Register it in `providers/__init__.py`.
4. Add fixtures and tests that show it returns RCT data **for the same property**.
5. Only then expose a provider choice in the options flow. A configured entry must never switch
   provider silently.
