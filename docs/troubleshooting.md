# Troubleshooting

## The integration doesn't appear in "Add integration"

- Check that the folder is `config/custom_components/rct_bin_collections/` and contains
  `manifest.json`. A common mistake is ending up with `custom_components/rct_bin_collections/rct_bin_collections/`.
- Restart Home Assistant after installing, then force-refresh the browser (Ctrl+F5).
- Look under **Settings → System → Logs** for errors mentioning `rct_bin_collections`.

## "The council found no addresses for this postcode"

- Check the postcode on the council's own
  [collection day page](https://www.rctcbc.gov.uk/EN/Resident/RecyclingandWasteServices/Findyourrecyclingandwastecollectionday.aspx).
- The council gives the same answer for "not an RCT postcode" and "postcode doesn't exist", so it
  can't tell them apart. Properties on a border may be served by a neighbouring council, such as
  Cardiff, Merthyr Tydfil, Bridgend or Caerphilly.
- New-build streets sometimes take a while to appear in the council's address list. If your address
  is missing but you know your UPRN, use the manual UPRN option.

## "The council does not recognise this property"

The council's page returned its error view for that UPRN. Check the UPRN, or go back and choose the
address from the list instead.

## "The council's record for this UPRN is not at the postcode you entered"

When you enter a UPRN manually, the integration checks that the council's address for it ends with
your postcode. This stops it from ever showing someone else's collection dates. Check both values.

If this error appears **after** setup, as a failed integration, the council has started returning
a different postcode for your UPRN. Use **Reconfigure** to choose the address again.

## A collection type is missing

- Sensors are only created for types the council lists **for your property**. Not every property
  receives every service: one of the properties checked during development had no garden waste.
  If the council website doesn't show a type for you, nor will the integration.
- Check you haven't hidden it under **Configure → Collection types to show**.
- If the council starts publishing a type later, its sensor is added automatically after the next
  refresh.

## A date looks wrong

1. Compare with the council's page for your address. The integration shows exactly what the
   council publishes.
2. If the council's page has changed, wait for the next refresh (12 hours by default), or trigger
   one now with the **Developer tools → Actions → `homeassistant.update_entity`** action on any
   RCT Bins entity. Manual refreshes are ignored within 5 minutes of a successful fetch, to protect
   the council's website.
3. Check `data_stale` on the entities. If it is `true`, the latest refresh failed and you're seeing
   the last good data. See `sensor.rct_bins_<name>_last_successful_update` for when that was.

## Failed refreshes or stale data

- `last_error` on the **Last successful update** diagnostic sensor tells you why the last
  refresh failed:

  | Value | Meaning |
  |---|---|
  | `service_unavailable` | Network problem or council server error. Retries hourly. |
  | `rate_limited` | The council asked us to wait. The integration waits as long as asked. |
  | `parse_error` | The page loaded but couldn't be read. The council may have changed its website. Please open an issue. |
  | `unsupported_response` | An unexpected response, such as an empty page, a redirect or the wrong content type. |
  | `property_not_found` | The council didn't recognise the UPRN this time. |

- Cached data is used for up to 7 days. After that the entities become **unavailable** rather than
  show dates that may be out of date.
- Recovery is automatic as soon as the council's site responds normally.

## No future events or "unknown" states

The council publishes roughly the current month plus the next two. Late in a month you may see:
- `unknown` for "collection tomorrow" on the last day of the published period;
- `unknown` for types without a date in that window.

This is deliberate: an unpublished date isn't the same as "no collection". It sorts itself out
once the council publishes the next month.

## The calendar is empty

- Open the calendar to a month inside the published period (the current month or the next two).
- Check you haven't hidden every collection type in the options.
- Check the integration entry isn't showing an error under **Settings → Devices & services**.

## Enabling debug logging safely

Add this to `configuration.yaml` and restart, or use **Enable debug logging** on the integration's
page:

```yaml
logger:
  default: warning
  logs:
    custom_components.rct_bin_collections: debug
```

Debug logs include counts, dates, HTTP status codes and error types. They **don't** include your
address, postcode or UPRN. Even so, check logs before sharing them publicly, and turn debug
logging off afterwards.

**Settings → Devices & services → RCT Bin Collections → ⋮ → Download diagnostics** produces a
redacted file that is safe to attach to a bug report. The postcode, UPRN, property name and entry
title are replaced with `**REDACTED**`.
