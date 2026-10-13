# RCT Bin Collections

An **unofficial** Home Assistant integration that shows your household waste and recycling collection
dates for **Rhondda Cynon Taf County Borough Council** (RCT), South Wales.

You enter a postcode, choose your address, and Home Assistant creates the following:

- sensors;
- reminder binary sensors;
- a calendar.

Dates come from the council's own "Find your recycling and waste collection day" page.

> This is a community project. It is **not affiliated with, endorsed by or supported by Rhondda
> Cynon Taf County Borough Council**. Please don't contact the council about problems with this
> integration; open an issue here instead.

---

## Features

- **Postcode and address lookup.** You don't need to find your UPRN, and there's no YAML to write.
  A manual UPRN option is available as a fallback.
- **Actual published dates**, including any Christmas or New Year changes once the council publishes
  them. Nothing is inferred from "every other Tuesday" rules.
- **Sensors**:
  - next collection;
  - days until the next collection;
  - a human-readable summary;
  - one sensor per waste type your property actually receives.
- **Reminder binary sensors** for today, tomorrow, and a configurable "due soon" window.
- **A native calendar** that works with the calendar dashboard and calendar triggers.
- **Updates at local midnight** (Europe/London, BST-aware) without contacting the council.
- **Gentle on the council's website:**
  - one request every 12 hours by default;
  - shared between all entities;
  - manual refreshes rate-limited;
  - `Retry-After` respected.
- **Keeps working through outages.** The last good schedule is cached locally and used for up to
  7 days, clearly marked as stale.
- **Multiple properties:** home, a relative's house, a rental, and so on, each with its own device.
- **Privacy-aware:**
  - no address or UPRN appears in entity attributes or logs;
  - diagnostics are redacted;
  - no telemetry.

## Requirements

| | Tested |
|---|---|
| Home Assistant | **2026.10.1** |
| Python | 3.14 (as bundled with Home Assistant 2026.10) |

Earlier Home Assistant versions have **not** been tested. The integration uses `probatio` for its
config-flow schemas, and that library was introduced with the 2026 releases.

## Installation

### HACS (custom repository)

1. In Home Assistant, open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/tamsininnit/rct-bins` with category **Integration**.
3. Search HACS for **RCT Bin Collections** and download it.
4. Restart Home Assistant.

### Manual

1. Download the latest release, or clone this repository.
2. Copy `custom_components/rct_bin_collections/` into your Home Assistant configuration folder, so
   that you have `config/custom_components/rct_bin_collections/manifest.json`.
3. Restart Home Assistant.

See [`docs/installation.md`](docs/installation.md) for more detail.

## Configuration

1. Go to **Settings → Devices & services → Add integration**.
2. Search for **RCT Bin Collections**.
3. Enter your **postcode**. Spaces and capitals are optional, so `cf37 1ab` works.
4. Optionally change the **property name** (default `Home`). It names the device and entity IDs.
5. Choose your **address** from the council's list.
6. The integration fetches your schedule, checks it belongs to that address, and creates the
   entities.

### Manual UPRN (fallback)

Tick **Enter a UPRN instead of choosing from the address list** on the first step if your address
is missing from the list. A UPRN (Unique Property Reference Number) is a number of up to 12
digits. You can find it on some council documents or on public lookup services such as
<https://uprn.uk/>. The integration only accepts it if the council confirms that property is at the
postcode you entered.

### Options

Open the integration's **Configure** button to change the following:

| Option | Default | Notes |
|---|---|---|
| Property name | `Home` | Renames the device. Existing entity IDs are kept. |
| Check for changes every | 12 hours | 6, 12 or 24 hours. Date-based entities still update at midnight. |
| Reminder window | 1 day | The "due soon" sensor is on when a collection is this many days away or fewer. |
| Collection types to show | All | Hidden types are removed from sensors, reminders and the calendar. |

You can't choose a data provider, because only one verified provider exists (see
[`docs/data-source-research.md`](docs/data-source-research.md)).

### Moving house

Use **⋮ → Reconfigure** on the integration entry to choose a new postcode and address. Your
entities, entity IDs and history are kept.

## Available entities

The examples below use a property named **Home**. Entity IDs start with `rct_bins_<property name>`.

| Entity | State | Key attributes |
|---|---|---|
| `sensor.rct_bins_home_next_collection` | Date, e.g. `2026-10-20` | `collection_types`, `days_until`, `property_name`, `source`, `data_stale`, `published_until` |
| `sensor.rct_bins_home_days_until_next_collection` | Number of days, e.g. `2` (`0` = today) | `next_date` |
| `sensor.rct_bins_home_collection_summary` | e.g. `Recycling, Food Waste and Garden Waste` | `next_date`, `days_until`, `following_collections` (next 4 dates) |
| `sensor.rct_bins_home_general_waste_next_collection` | Date | `category`, `description` (council label, e.g. `Black Bags`), `days_until`, `upcoming_dates` (up to 6), `last_successful_update` |
| `sensor.rct_bins_home_recycling_next_collection` | Date | as above |
| `sensor.rct_bins_home_food_waste_next_collection` | Date | as above |
| `sensor.rct_bins_home_garden_waste_next_collection` | Date | as above (only if your property has garden waste) |
| `sensor.rct_bins_home_last_successful_update` (diagnostic) | Timestamp | `data_stale`, `last_error`, `published_from`, `published_until`, `address_verified` |
| `binary_sensor.rct_bins_home_collection_today` | `on` / `off` / `unknown` | `date`, `collection_types`, `days_until` |
| `binary_sensor.rct_bins_home_collection_tomorrow` | `on` / `off` / `unknown` | as above |
| `binary_sensor.rct_bins_home_collection_due_soon` | `on` / `off` / `unknown` | as above |
| `calendar.rct_bins_home` | `on` on a collection day | One all-day event per waste type per date |

Per-type sensors are created **only for the types the council lists for your property**. If the
council publishes a type the integration doesn't recognise, it gets its own sensor, named after the
council's label.

### "Unknown" means unknown

The council publishes about three months ahead. When there's no published date, for example:

- a garden waste sensor after the last published date;
- "collection tomorrow" on the last day of the published period,

the state is `unknown`, **not** `off` or empty. A missing date is never presented as confirmation
that no collection will happen.

## Dashboard examples

Replace the example entity IDs with your own.

**1. Next bin collection**

```yaml
type: tile
entity: sensor.rct_bins_home_next_collection
name: Next bin day
icon: mdi:trash-can
```

**2. Days until collection**

```yaml
type: gauge
entity: sensor.rct_bins_home_days_until_next_collection
name: Days until bin day
min: 0
max: 7
severity:
  red: 0
  yellow: 1
  green: 2
```

**3. Calendar**

```yaml
type: calendar
entities:
  - calendar.rct_bins_home
initial_view: dayGridMonth
```

**4. Collection summary**

```yaml
type: markdown
title: Bins
content: >-
  {% set s = 'sensor.rct_bins_home_collection_summary' %}
  {% set days = state_attr(s, 'days_until') %}
  {% if days is none %}
  No collections published yet.
  {% else %}
  **{{ states(s) }}**
  {{ 'today' if days == 0 else 'tomorrow' if days == 1 else 'in ' ~ days ~ ' days' }}
  ({{ as_datetime(state_attr(s, 'next_date')).strftime('%A %-d %B') }})
  {% endif %}
```

**5. Separate waste types**

```yaml
type: entities
title: Collections by type
entities:
  - entity: sensor.rct_bins_home_general_waste_next_collection
    name: Black bags
  - entity: sensor.rct_bins_home_recycling_next_collection
    name: Recycling
  - entity: sensor.rct_bins_home_food_waste_next_collection
    name: Food waste
  - entity: sensor.rct_bins_home_garden_waste_next_collection
    name: Garden waste
```

## Automation examples

`notify.notify` is a placeholder. Replace it with your own notification action, such as
`notify.mobile_app_<your_phone>`.

**Evening before (6 PM)**

```yaml
alias: Bins - evening reminder
triggers:
  - trigger: time
    at: "18:00:00"
conditions:
  - condition: state
    entity_id: binary_sensor.rct_bins_home_collection_tomorrow
    state: "on"
actions:
  - action: notify.notify  # placeholder: use your own notify action
    data:
      title: Bins tomorrow
      message: >-
        Put out: {{ state_attr('binary_sensor.rct_bins_home_collection_tomorrow',
        'collection_types') | join(', ') }}.
mode: single
```

**Morning of (7 AM)**

```yaml
alias: Bins - morning reminder
triggers:
  - trigger: time
    at: "07:00:00"
conditions:
  - condition: state
    entity_id: binary_sensor.rct_bins_home_collection_today
    state: "on"
actions:
  - action: notify.notify  # placeholder: use your own notify action
    data:
      title: Bin day
      message: >-
        Collections today: {{ state_attr('binary_sensor.rct_bins_home_collection_today',
        'collection_types') | join(', ') }}. Have they gone out?
mode: single
```

More examples are in [`docs/automations.md`](docs/automations.md). They include:
- different messages for black-bag weeks;
- a calendar-trigger version.

## Privacy

**Stored locally in Home Assistant:**
- your postcode and UPRN (in the config entry);
- the friendly property name;
- a cached copy of the schedule (in `.storage/rct_bin_collections.<entry_id>`).

The cache holds:
- dates;
- waste types;
- council labels;
- a one-way hash of the UPRN that ties the cache to the property.

It holds no address and no plain UPRN. The cache is deleted when you remove the integration entry.

**Sent to the council website (`www.rctcbc.gov.uk`, HTTPS):**
- your postcode, during setup and reconfiguration only;
- your UPRN, on each refresh.

The integration doesn't use cookies or log in, and it sends an honest `User-Agent` naming the
integration.

**Not collected:** no telemetry, analytics or third-party services.

The address chosen during setup is not stored. Entity attributes do not include your address,
postcode or UPRN. Diagnostics downloads redact the following:
- postcode;
- UPRN;
- property name;
- entry title.

## Troubleshooting

See [`docs/troubleshooting.md`](docs/troubleshooting.md). It covers the following:
- property not found;
- missing types;
- wrong dates;
- failed refreshes;
- an empty calendar;
- enabling debug logging safely.

## Testing

All tests run offline, against sanitised fixtures captured from the live council site.

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements-test.txt
.venv/bin/pytest --cov
.venv/bin/ruff format --check . && .venv/bin/ruff check .
.venv/bin/mypy
```

These checks are run on every push and pull request by GitHub Actions:
- tests;
- linting;
- type checking;
- `hassfest`;
- HACS validation.

## Maintenance: when the council changes its website

All council-specific scraping lives in
`custom_components/rct_bin_collections/providers/rct_council_parser.py`, which is pure functions
with no I/O. If the site changes:

1. Capture the new page for a known UPRN. Use your browser's "Save page", or `curl` with a
   UPRN that is already public.
2. Remove addresses, postcodes and UPRNs, and save the result under `tests/fixtures/`.
3. Update the parser's selectors until `tests/test_providers.py` passes against both the old and new
   fixtures.
4. Bump the version in `manifest.json` and add a `CHANGELOG.md` entry.

[`docs/architecture.md`](docs/architecture.md) explains how the pieces fit together, and how to add
another data provider.

## Credits and licence

MIT licence. See [LICENSE](LICENSE).

The research for this integration benefited from the
[Waste Collection Schedule](https://github.com/mampfes/hacs_waste_collection_schedule) project's
`rctcbc_gov_uk` source (MIT). No code was copied: the parser here was written independently against
the live page.
