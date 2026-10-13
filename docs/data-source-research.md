# Data source research

Research carried out on **13 October 2026** from a UK network connection, using `curl` and a
short Python script. All requests were made at low volume, a few seconds apart.

UPRNs used for live testing are the **public test cases already published** in the
Waste Collection Schedule repository (`10024274791`, `100100718352`, `100100733093`). Their
postcodes and addresses are not reproduced here (`{POSTCODE}` marks where one was used), and the
test fixtures use fictitious values throughout.

---

## Summary

| Candidate | Works for RCT? | Postcode → address list | Format | Selected |
|---|---|---|---|---|
| A. RCT Council website | **Yes, verified live** | **Yes, verified live** | HTML | **Primary** |
| B. Waste Collection Schedule `rctcbc_gov_uk` | Yes (it scrapes the same page as A) | No (UPRN only) | n/a | Reference only |
| C. UK Bin Day API | **No.** RCT is listed, but every schedule came back empty | Behind a browser challenge | JSON / iCal | Not used |
| D. Other sources | None found | n/a | n/a | n/a |

**Decision:** the integration talks directly to the council's own "Find your recycling and waste
collection day" page (option A). There is **no secondary provider** at this point, because none could be
validated. The provider layer is pluggable, so one can be added later without touching the entities.

---

## A. RCT Council website (selected)

### URL

```
https://www.rctcbc.gov.uk/EN/Resident/RecyclingandWasteServices/Findyourrecyclingandwastecollectionday.aspx
```

It is a Contensis CMS page running ASP.NET WebForms behind Varnish (`x-host: live-rctcbc.cloud.contensis.com`).

### How a browser uses it

1. The page shows a form with **House Number** (optional) and **Postcode** fields.
2. Submitting the form POSTs a WebForms postback, which answers with
   `302 Found` → `?PropertyNumber=&Postcode={POSTCODE}`.
3. That GET returns a `<select name="SelectedAddress">` whose `<option value="…">` values are
   **UPRNs**, and whose labels are full addresses.
4. Choosing an address loads `?uprn={UPRN}&month=0`. That page contains the address, a "next collection"
   table, a monthly calendar for the month at offset `month`, and a **printable calendar covering
   the current month plus the following two months**.

Both GET endpoints work **without the postback, cookies, ViewState, CSRF tokens or a session**.
The server sets an `ASP.NET_SessionId` cookie, but nothing needs it. There is no login and no
CAPTCHA, and nothing tries to detect bots.

### Postcode → address list (verified)

`GET …?PropertyNumber=&Postcode={POSTCODE}`, HTTP 200. Sanitised extract (see
`tests/fixtures/addresses-multiple.html`):

```html
<select class="form-control" name="SelectedAddress">
  <option>Please select an address</option>
  <option value="999000000001">1 Example Street, Exampletown, Pontyclun,  CF99 9ZZ</option>
  <option value="999000000002">2 Example Street, Exampletown, Pontyclun,  CF99 9ZZ</option>
  …
</select>
```

Observations:

- Postcode input is accepted in any case and with or without a space (e.g. `CF371AB`, `cf37 1ab`).
  All variants returned the same 41 addresses.
- `PropertyNumber=17` narrows the list to the one matching property.
- An unknown postcode, a postcode outside RCT (`SW1A 1AA`), or garbage input all return HTTP 200 with
  `<p>No Address Found</p>` (`tests/fixtures/addresses-none.html`). The council therefore
  **cannot tell "outside RCT" apart from "no such postcode"**, and the integration reports both as
  "no RCT addresses found for this postcode".

### UPRN → schedule (verified)

`GET …?uprn={UPRN}&month=0`, HTTP 200, about 360 KB of HTML. Sanitised extract (see
`tests/fixtures/schedule-page.html`):

```html
<div class="form-block-wrapper waste-collection-search">
  <div class="row"><div class="row"><div class="col-md-12">
    <p>17, Example Street, Exampletown, Pontyclun, CF99 9ZZ</p>
  …
  <div class="printableCalendar">
    <div class="calendar-wrap onlyPrint">
      <div class="calendar-month">October 2026</div>
      <table class="table" id="waste-calendar"> …
        <div class="card-body card-body-padding">
          <div class="card-title">6</div>
          <div class="card-text">
            <div class="alert alert-primary-recycling collection-day"><a href="…">Recycling</a></div>
            <div class="alert alert-primary-green collection-day"><a href="…">Garden Waste</a></div>
            <div class="alert alert-primary-black collection-day"><a href="…">Black Bags</a></div>
            <div class="alert alert-primary-food collection-day"><a href="…">Food Waste</a></div>
```

Parsed result for the first public test UPRN (Tuesday collections):

| Month | Dates with collections |
|---|---|
| October 2026 | 6 (all four), 13, 20, 27 (all four) |
| November 2026 | 3, 10, 17 (all four), 24 |
| December 2026 | 1, 8 (all four), 15, 22, 29 (all four) |

Recycling, Food Waste and Garden Waste are weekly. Black Bags are three-weekly. A second test UPRN
(Wednesday collections) has no Garden Waste at all, which shows the calendar is **property-specific**
and leaves out services the household doesn't receive.

### Observed collection labels

| Label | CSS class | Normalised category |
|---|---|---|
| `Black Bags` | `alert-primary-black` | `general_waste` |
| `Recycling` | `alert-primary-recycling` | `recycling` |
| `Food Waste` | `alert-primary-food` | `food_waste` |
| `Garden Waste` | `alert-primary-green` | `garden_waste` |

RCT also runs a registered hygiene/nappy collection. No test property showed it, so its label is
**unverified**. The parser maps labels that contain "nappy", "hygiene" or "absorbent" to `hygiene`,
and keeps any other unknown label as its own category, with the original text intact.

### Error responses (verified)

| Request | Response |
|---|---|
| Unknown UPRN (`99999999999`) or non-numeric UPRN | HTTP 200, no calendars, body contains `RAZOR VIEW ERROR … Object reference not set to an instance of an object` (`tests/fixtures/schedule-unknown-property.html`) |
| `month=12` | **HTTP 302 to the same URL over plain `http://`.** The client must not follow redirects. |
| `month=1..6` | HTTP 200; the monthly view moves forward by that many months; the printable calendar is unchanged |

On one occasion a valid `month=1` page came back without the address paragraph (the search form
was shown instead), yet it still contained the full printable calendar. Address verification is
therefore done on a best-effort basis in normal polling, and enforced only during setup.

### Horizon

- The printable calendar on `month=0` always covers **the current month plus the next two**. That is
  between roughly 2 and 3 months ahead, depending on the day of the month.
- The monthly view at `month=3` showed **January 2027** with dated entries. Months beyond that were
  not tested exhaustively. `month=12` redirects to HTTP, as noted above.
- The integration makes **one request per refresh** (`month=0`) and treats the last day of the
  last printed month as the **data horizon**. Entities never assume anything past the horizon.

### Bank holidays and exceptional changes

- The page itself says: *"Christmas and New Year collection days may change. There are no changes
  to collection days following other Bank Holidays."*
- On 13 October 2026 the December entries (22 and 29 December for Tuesday rounds, 23 and 30 December for
  Wednesday rounds) were still on the normal weekday. Either the Christmas changes **had not been
  published yet**, or they do not affect those rounds. It was **not possible to observe** how a
  rescheduled date appears.
- Because the calendar lists **actual dates**, not "every Tuesday", a rescheduled collection should
  simply show up on its new date. The integration never infers dates from a weekly rule. It
  re-fetches every 12 hours by default, so changes published later are picked up automatically.
- The page does **not** expose cancelled collections, or any "holiday" flag.

### Other page content

- `Your Recycling and Waste Collection is a Group C`: the round group. Not used; it isn't needed.
- A "Next Collection" table (`7 days' time, Tuesday 20th of October 2026`). On the afternoon of a
  collection day, it had already moved on to the following week, even though the calendar still
  listed that day. The integration uses the calendar, not this table.

### Terms, rate limits and robots

- `robots.txt` disallows only `/stellent/` and `/mobile/` for general user agents. This page is not
  disallowed.
- No API terms of use, and no rate-limit headers, were found. Responses are `no-cache`.
- To keep the load reasonable: default polling every 12 hours (6 hours minimum), one request per
  refresh, a 5-minute floor between real fetches, a 60-second debounce on manual refreshes,
  no concurrent requests per property, and `Retry-After` honoured on HTTP 429.
- The client sends an honest `User-Agent` that names the integration.

### Advantages
- First-party data: exactly what residents see on the council website.
- Property-specific, dated entries that include any holiday changes as they're published.
- Postcode → address list with UPRNs, so users never need to look up their UPRN.
- No authentication, cookies or anti-bot measures.

### Disadvantages and reliability concerns
- **HTML scraping.** A redesign could break the parser. This is mitigated by:
  - relying on class names (`printableCalendar`, `calendar-month`, `card-title`, `collection-day`);
  - a fallback to the monthly calendar block if the printable one disappears;
  - clear "structure changed" errors rather than silent empty schedules;
  - cached data being kept for up to 7 days while a fix is released.
- Page responses are large (about 360 KB). The client caps responses at 3 MiB.
- The address paragraph is not always present (see above).
- The council cannot tell "outside RCT" apart from "unknown postcode".

---

## B. Waste Collection Schedule `rctcbc_gov_uk` (reference only)

- File (checked 13 October 2026): `custom_components/waste_collection_schedule/waste_collection_schedule/source/rctcbc_gov_uk.py`
  in `mampfes/hacs_waste_collection_schedule`, licensed **MIT** (© 2020 Steffen Zimmermann).
- Mechanism: synchronous `requests`, then `GET …?uprn={uprn}&month={0..3}`, parsed with BeautifulSoup. It
  prefers `div.printableCalendar` → `div.calendar-wrap.onlyPrint` → `div.calendar-month`,
  plus `div.card-body.card-body-padding` → `div.card-title` and `<a>` text. If no printable
  calendar is found, it falls back to the monthly calendar.
- It parses the month header with `strptime("%d %B %Y")`, which **depends on the locale**.
- Identification is **UPRN only**; there is no postcode lookup.
- What this integration takes from it: the confirmation that the printable calendar is the
  richest block, and the idea of a monthly-calendar fallback. **No code was copied.** The parser here
  was written from scratch against the captured HTML. It uses a locale-independent month table,
  an async client, strict error classification, and the postcode lookup described above.
- It was not adopted as a dependency, for these reasons:
  - it is a whole integration, not a library;
  - it pulls in eight requirements, including `curl_cffi` and `pdfminer`;
  - it uses blocking `requests`;
  - its entity model is generic, not RCT-specific.

---

## C. UK Bin Day API (not used)

- Docs: <https://ukbinday.co.uk/api-docs>. OpenAPI: `https://ukbinday.co.uk/api/v2/openapi.json`.
- RCT appears in `GET /api/v2/councils` as `W06000016` ("Rhondda Cynon Taf County Borough Council").
- `GET /api/v2/W06000016/view/10024274791?postcode={POSTCODE}` returned:

  ```json
  {"uprn":"10024274791","council":"W06000016","cached":false,"cached_at":null,"dates":[],"deeplink":null}
  ```

  After a retry, the empty result came back **cached**
  (`"cached":true,"cached_at":"2026-10-13T15:48:17Z","dates":[]`). A second UPRN also returned
  `dates: []`. The iCal download (`/download/{uprn}`) returned a `VCALENDAR` with **no events**.
- The service's health endpoint reported "healthy" and showed no blocker for RCT.
- Its `/api/v2/find` address list (UPRNs) is behind a browser challenge and meant for the site's own
  frontend, so it cannot legitimately be used for postcode lookup.
- The service's docs say it is powered by `hacs_waste_collection_schedule` and
  `UKBinCollectionData`, so it scrapes the same council page and adds no independent data.

**Conclusion:** it does not currently return RCT data and adds a third-party dependency, so it is
**not implemented**. Implementing it would mean advertising a fallback that doesn't work, which the
brief forbids. If it starts returning RCT dates in future, it can be added as a provider module (see
`docs/architecture.md`).

---

## D. Other sources

- No council open-data feed, iCal feed or JSON API for RCT waste collections was found.
- `UKBinCollectionData` (another community scraper) works by scraping council websites, so it has
  no independent data source.

---

## Fallback strategy

1. **Cached last-known-good schedule.** It is stored with Home Assistant's `Store` helper (atomic
   writes, versioned), tied to a hash of the property's UPRN. It is used when the council is
   unreachable, and is marked **stale** in the entity attributes and the diagnostic sensor. It is
   discarded after 7 days without a successful fetch. Past dates are filtered out every time the
   schedule is read, so an old cache can never produce dates that look like future ones.
2. **Faster retry after a failure.** After a failed refresh, the next attempt comes after 1 hour
   rather than 12. If the council returns a longer `Retry-After`, that is used instead.
3. **No automatic provider switching.** If a second provider is ever added, a configured entry will
   not silently swap to it.

## Evidence and outstanding validation

Verified live on 13 October 2026:
- the postcode → address list, with UPRNs;
- the UPRN → three months of dated, typed collections;
- the error pages for an unknown postcode and an unknown UPRN;
- the redirect hazard.

The integration's own provider code (`RctCouncilProvider`, with its real `User-Agent`) was also run
against the live site at the end of the build, on 13 October 2026, using the first public test UPRN:

- postcode lookup returned 41 properties, including the test UPRN;
- the schedule had 44 events, from 1 October to 31 December 2026, in four categories;
- address verification passed (`require_address_match=True`);
- a non-RCT postcode (`SW1A 1AA`) raised `PostcodeNotFoundError` as expected.

Still unverified:
- how Christmas and New Year changes appear (to be checked once RCT publishes its December 2026
  changes);
- the exact label used for hygiene/nappy collections;
- whether the monthly-calendar fallback still matches the printable markup if the council ever
  removes the printable calendar.
