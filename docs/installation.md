# Installation

## Before you start

- You need Home Assistant **2026.10.1** or later. That is the version this release was tested against.
- You need a property in Rhondda Cynon Taf. Postcodes are mostly CF37–CF45 and CF72, but the
  council's lookup decides, not the postcode prefix.
- You don't need an account, API key or UPRN.

## Option 1: HACS custom repository

1. Install [HACS](https://hacs.xyz/) if you haven't already.
2. Open **HACS**, select **⋮** (top right) and then **Custom repositories**.
3. Enter the repository URL `https://github.com/tamsininnit/rct-bins`, choose the type
   **Integration**, and select **Add**.
4. Search HACS for **RCT Bin Collections**, open it, and select **Download**.
5. Restart Home Assistant (**Settings → System → ⋮ → Restart Home Assistant**).

HACS will offer updates when new releases are published.

## Option 2: manual copy

1. Download the `rct_bin_collections.zip` asset from the latest GitHub release, or clone the
   repository.
2. Create `custom_components` inside your Home Assistant configuration directory if it doesn't
   exist. That is the folder that contains `configuration.yaml`.
3. Copy the integration folder so the result looks like this:

   ```text
   config/
   └── custom_components/
       └── rct_bin_collections/
           ├── __init__.py
           ├── manifest.json
           └── …
   ```

4. Restart Home Assistant.

To update, replace the folder with the newer version and restart.

## Adding a property

1. Go to **Settings → Devices & services → Add integration**.
2. Search for **RCT Bin Collections**. If it doesn't appear, refresh the browser (Ctrl+F5) after the
   restart.
3. Enter the postcode and, optionally, a name for the property.
4. Pick your address from the list and select **Submit**.

Setup fetches your schedule straight away. If the council's website is down, setup tells you, and
you can try again later.

To add more properties, repeat these steps. Each property gets its own device and entities.

## Removing

**Settings → Devices & services → RCT Bin Collections → ⋮ → Delete** removes the property, its
entities and its cached schedule. To uninstall completely, also remove the integration in HACS, or
delete `custom_components/rct_bin_collections`, and then restart.
