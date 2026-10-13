"""Config, reconfigure and options flows for RCT Bin Collections."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.util import dt as dt_util
import probatio

from .const import (
    CONF_EXCLUDED_CATEGORIES,
    CONF_INCLUDED_CATEGORIES,
    CONF_MANUAL_UPRN,
    CONF_POSTCODE,
    CONF_PROPERTY_NAME,
    CONF_PROVIDER,
    CONF_REMINDER_DAYS,
    CONF_SELECTED_ADDRESS,
    CONF_UPDATE_INTERVAL,
    CONF_UPRN,
    DEFAULT_PROPERTY_NAME,
    DEFAULT_REMINDER_DAYS,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
    MAX_REMINDER_DAYS,
    PROVIDER_RCT_COUNCIL,
    UPDATE_INTERVAL_CHOICES_HOURS,
)
from .coordinator import RctBinConfigEntry
from .exceptions import (
    ParseError,
    PostcodeNotFoundError,
    PropertyMismatchError,
    PropertyNotFoundError,
    RateLimitedError,
    ServiceUnavailableError,
    UnsupportedResponseError,
)
from .models import (
    CollectionSchedule,
    Property,
    category_display_name,
    normalise_postcode,
    normalise_uprn,
)
from .providers import CollectionProvider, ProviderCapability, create_provider

_LOGGER = logging.getLogger(__name__)

#: Data-layer exceptions mapped to config flow error keys, most specific first.
_ERROR_KEYS: tuple[tuple[type[Exception], str], ...] = (
    (RateLimitedError, "rate_limited"),
    (ServiceUnavailableError, "cannot_connect"),
    (PostcodeNotFoundError, "postcode_not_found"),
    (PropertyNotFoundError, "property_not_found"),
    (PropertyMismatchError, "property_mismatch"),
    (ParseError, "parse_error"),
    (UnsupportedResponseError, "unsupported_response"),
)


def _error_key(err: Exception) -> str:
    for exc_type, key in _ERROR_KEYS:
        if isinstance(err, exc_type):
            return key
    return "unknown"


class RctBinConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a property by postcode and address, or by UPRN."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialise flow state."""
        self._provider: CollectionProvider | None = None
        self._postcode: str = ""
        self._property_name: str = DEFAULT_PROPERTY_NAME
        self._properties: list[Property] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: RctBinConfigEntry) -> RctBinOptionsFlow:
        """Return the options flow."""
        return RctBinOptionsFlow()

    @property
    def provider(self) -> CollectionProvider:
        """Return the provider, creating it on first use."""
        if self._provider is None:
            self._provider = create_provider(
                PROVIDER_RCT_COUNCIL, async_get_clientsession(self.hass)
            )
        return self._provider

    # ----------------------------------------------------------------------------------
    # Step 1: postcode
    # ----------------------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the postcode, a friendly name and whether to enter a UPRN."""
        return await self._async_step_postcode("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address or UPRN of an existing property."""
        return await self._async_step_postcode("reconfigure", user_input)

    async def _async_step_postcode(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        reconfiguring = self.source == SOURCE_RECONFIGURE

        if user_input is not None:
            try:
                self._postcode = normalise_postcode(user_input[CONF_POSTCODE])
            except ValueError:
                errors[CONF_POSTCODE] = "invalid_postcode"
            else:
                if not reconfiguring:
                    name = str(user_input.get(CONF_PROPERTY_NAME) or "").strip()
                    self._property_name = name[:50] or DEFAULT_PROPERTY_NAME
                if user_input.get(CONF_MANUAL_UPRN) or not self.provider.supports(
                    ProviderCapability.POSTCODE_LOOKUP
                ):
                    return await self.async_step_manual_uprn()
                try:
                    self._properties = await self.provider.async_find_properties(self._postcode)
                except Exception as err:  # noqa: BLE001 - mapped to a user-facing key
                    errors["base"] = self._log_and_map(err)
                else:
                    return await self.async_step_select_address()

        if reconfiguring:
            default_postcode = self._get_reconfigure_entry().data.get(CONF_POSTCODE, "")
        else:
            default_postcode = ""
        fields: dict[probatio.Marker, Any] = {
            probatio.Required(
                CONF_POSTCODE, default=self._postcode or default_postcode
            ): TextSelector(),
        }
        if not reconfiguring:
            fields[probatio.Optional(CONF_PROPERTY_NAME, default=self._property_name)] = (
                TextSelector()
            )
        fields[probatio.Optional(CONF_MANUAL_UPRN, default=False)] = BooleanSelector()
        return self.async_show_form(
            step_id=step_id, data_schema=probatio.Schema(fields), errors=errors
        )

    # ----------------------------------------------------------------------------------
    # Step 2a: choose from the council's address list
    # ----------------------------------------------------------------------------------

    async def async_step_select_address(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick their property from the council's list."""
        errors: dict[str, str] = {}
        if user_input is not None:
            uprn = user_input[CONF_SELECTED_ADDRESS]
            if any(p.uprn == uprn for p in self._properties):
                result = await self._async_validate_and_finish(uprn, errors)
                if result is not None:
                    return result
            else:
                errors["base"] = "property_not_found"

        options = [SelectOptionDict(value=p.uprn, label=p.address) for p in self._properties]
        return self.async_show_form(
            step_id="select_address",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_SELECTED_ADDRESS): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN, sort=False
                        )
                    )
                }
            ),
            description_placeholders={
                "postcode": self._postcode,
                "count": str(len(self._properties)),
            },
            errors=errors,
        )

    # ----------------------------------------------------------------------------------
    # Step 2b: manual UPRN
    # ----------------------------------------------------------------------------------

    async def async_step_manual_uprn(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Accept a UPRN directly, verified against the postcode."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                uprn = normalise_uprn(user_input[CONF_UPRN])
            except ValueError:
                errors[CONF_UPRN] = "invalid_uprn"
            else:
                result = await self._async_validate_and_finish(
                    uprn, errors, require_address_match=True
                )
                if result is not None:
                    return result

        return self.async_show_form(
            step_id="manual_uprn",
            data_schema=probatio.Schema({probatio.Required(CONF_UPRN): TextSelector()}),
            description_placeholders={"postcode": self._postcode},
            errors=errors,
        )

    # ----------------------------------------------------------------------------------
    # Step 3: validate collections and create or update the entry
    # ----------------------------------------------------------------------------------

    async def _async_validate_and_finish(
        self,
        uprn: str,
        errors: dict[str, str],
        *,
        require_address_match: bool = False,
    ) -> ConfigFlowResult | None:
        """Fetch the schedule for ``uprn``; return a result, or None with ``errors`` set."""
        reconfigure_entry = (
            self._get_reconfigure_entry() if self.source == SOURCE_RECONFIGURE else None
        )
        if reconfigure_entry is None or uprn != reconfigure_entry.unique_id:
            await self.async_set_unique_id(uprn)
            self._abort_if_unique_id_configured()

        try:
            schedule = await self.provider.async_get_schedule(
                uprn, self._postcode, require_address_match=require_address_match
            )
        except Exception as err:  # noqa: BLE001 - mapped to a user-facing key
            errors["base"] = self._log_and_map(err)
            return None

        if not schedule.events:
            errors["base"] = "no_collections"
            return None
        if not schedule.upcoming(dt_util.now().date()):
            errors["base"] = "no_future_collections"
            return None

        # Hand the fresh schedule to the coordinator so setup does not fetch it again.
        self.hass.data.setdefault(DOMAIN, {})[uprn] = schedule
        data = {CONF_UPRN: uprn, CONF_POSTCODE: self._postcode, CONF_PROVIDER: PROVIDER_RCT_COUNCIL}

        if reconfigure_entry is not None:
            old_uprn = reconfigure_entry.data[CONF_UPRN]
            if old_uprn != uprn:
                _migrate_registry_identifiers(self.hass, reconfigure_entry, old_uprn, uprn)
            return self.async_update_reload_and_abort(
                reconfigure_entry, unique_id=uprn, data_updates=data
            )

        return self.async_create_entry(
            title=self._property_name,
            data=data,
            options={
                CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL_HOURS,
                CONF_REMINDER_DAYS: DEFAULT_REMINDER_DAYS,
                CONF_EXCLUDED_CATEGORIES: [],
            },
        )

    @staticmethod
    def _log_and_map(err: Exception) -> str:
        key = _error_key(err)
        if key == "unknown":
            _LOGGER.error("Unexpected error while talking to the council website", exc_info=err)
        else:
            _LOGGER.debug("Config flow lookup failed: %s (%s)", key, err)
        return key


def _migrate_registry_identifiers(
    hass: HomeAssistant, entry: RctBinConfigEntry, old_uprn: str, new_uprn: str
) -> None:
    """Move entities and the device to a new UPRN so entity IDs and history are kept."""
    entity_registry = er.async_get(hass)
    old_prefix = f"{old_uprn}_"
    for reg_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if reg_entry.unique_id.startswith(old_prefix):
            entity_registry.async_update_entity(
                reg_entry.entity_id,
                new_unique_id=f"{new_uprn}_{reg_entry.unique_id.removeprefix(old_prefix)}",
            )
    device_registry = dr.async_get(hass)
    if device := device_registry.async_get_device_by_identifier((DOMAIN, old_uprn), entry.entry_id):
        device_registry.async_update_device(device.id, new_identifiers={(DOMAIN, new_uprn)})


class RctBinOptionsFlow(OptionsFlowWithReload):
    """Change name, refresh interval, categories and reminder window."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show and save options."""
        entry = self.config_entry
        options = entry.options
        excluded = set(options.get(CONF_EXCLUDED_CATEGORIES, []))
        categories = sorted(
            excluded | _seen_categories(entry) | _registered_categories(self.hass, entry)
        )

        if user_input is not None:
            name = str(user_input.get(CONF_PROPERTY_NAME) or "").strip()[:50]
            name = name or DEFAULT_PROPERTY_NAME
            included = set(user_input.get(CONF_INCLUDED_CATEGORIES, categories))
            new_options = {
                CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                CONF_REMINDER_DAYS: int(user_input[CONF_REMINDER_DAYS]),
                CONF_EXCLUDED_CATEGORIES: sorted(set(categories) - included),
            }
            if name != entry.title:
                self.hass.config_entries.async_update_entry(entry, title=name)
            return self.async_create_entry(data=new_options)

        fields: dict[probatio.Marker, Any] = {
            probatio.Required(
                CONF_PROPERTY_NAME,
                default=entry.title,
            ): TextSelector(),
            probatio.Required(
                CONF_UPDATE_INTERVAL,
                default=str(options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_HOURS)),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[str(h) for h in UPDATE_INTERVAL_CHOICES_HOURS],
                    translation_key=CONF_UPDATE_INTERVAL,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            probatio.Required(
                CONF_REMINDER_DAYS,
                default=options.get(CONF_REMINDER_DAYS, DEFAULT_REMINDER_DAYS),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0, max=MAX_REMINDER_DAYS, step=1, mode=NumberSelectorMode.BOX
                )
            ),
        }
        if categories:
            fields[
                probatio.Optional(
                    CONF_INCLUDED_CATEGORIES,
                    default=[c for c in categories if c not in excluded],
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=c, label=_category_label(entry, c))
                        for c in categories
                    ],
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        return self.async_show_form(step_id="init", data_schema=probatio.Schema(fields))


def _seen_categories(entry: RctBinConfigEntry) -> set[str]:
    schedule = _loaded_schedule(entry)
    return set(schedule.categories_seen) if schedule else set()


def _registered_categories(hass: HomeAssistant, entry: RctBinConfigEntry) -> set[str]:
    """Return categories with a sensor in the entity registry (e.g. out-of-season ones)."""
    prefix = f"{entry.data[CONF_UPRN]}_category_"
    return {
        reg_entry.unique_id.removeprefix(prefix)
        for reg_entry in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if reg_entry.domain == "sensor" and reg_entry.unique_id.startswith(prefix)
    }


def _category_label(entry: RctBinConfigEntry, category: str) -> str:
    schedule = _loaded_schedule(entry)
    label = None
    if schedule is not None:
        label = next((e.label for e in schedule.events if e.category == category), None)
    return category_display_name(category, label)


def _loaded_schedule(entry: RctBinConfigEntry) -> CollectionSchedule | None:
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None or coordinator.data is None:
        return None
    schedule: CollectionSchedule = coordinator.data.schedule
    return schedule
