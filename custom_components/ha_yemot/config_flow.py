"""תהליכי ההגדרה של אינטגרציית ימות המשיח."""

from __future__ import annotations

import logging
import re
import secrets
import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import YemotApiError, YemotClient
from .const import (
    ALLOWED_ACTIONS,
    CONF_ACTION,
    CONF_ALLOWED_IPS,
    CONF_ALLOWED_PHONES,
    CONF_API_TOKEN,
    CONF_EXTERNAL_URL,
    CONF_FOLDER,
    CONF_MANAGER_TOKEN,
    CONF_TARGET_ENTITY,
    DEFAULT_ALLOWED_IPS,
    DOMAIN,
    PICKER_CACHE_SECONDS,
    PICKER_SCAN_DEPTH,
    SUBENTRY_TYPE_EXTENSION,
)

_LOGGER = logging.getLogger(__name__)

FOLDER_PATTERN = re.compile(r"^\d+(/\d+)*$")

ACTION_LABELS: dict[str, str] = {
    "": "רק הקראת סטטוס",
    "turn_on": "הדלקה או הפעלה",
    "turn_off": "כיבוי",
    "toggle": "החלפת מצב",
    "open_cover": "פתיחת תריס",
    "close_cover": "סגירת תריס",
    "stop_cover": "עצירת תריס",
    "lock": "נעילה",
    "unlock": "פתיחת נעילה",
    "start": "הפעלה",
    "pause": "השהיה",
    "return_to_base": "חזרה לעמדת טעינה",
    "press": "לחיצת כפתור",
    "trigger": "הפעלת אוטומציה",
}


async def _validate_manager_token(hass: HomeAssistant, token: str) -> None:
    """בדיקת טוקן הניהול. זורק ValueError עם קוד שגיאה."""
    token = token.strip()
    if len(token) < 6 or any(ch.isspace() for ch in token):
        raise ValueError("invalid_token_format")

    client = YemotClient(hass, token)
    try:
        await client.async_validate_token()
    except YemotApiError as err:
        message = str(err).lower()
        if any(word in message for word in ("token", "password", "login", "auth")):
            raise ValueError("invalid_auth") from err
        _LOGGER.warning("בדיקת הטוקן החזירה תוצאה לא צפויה: %s", err)


def _default_external_url(hass: HomeAssistant) -> str:
    """ניחוש הכתובת החיצונית מהגדרות הרשת."""
    try:
        return get_url(hass, allow_internal=False, prefer_external=True).rstrip("/")
    except NoURLAvailableError:
        return ""


class YemotConfigFlow(ConfigFlow, domain=DOMAIN):
    """הוספת האינטגרציה."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """שלב ההגדרה הראשי."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}

        if user_input is not None:
            external_url = str(user_input[CONF_EXTERNAL_URL]).strip().rstrip("/")
            manager_token = str(user_input[CONF_MANAGER_TOKEN]).strip()

            if not external_url.startswith(("http://", "https://")):
                errors[CONF_EXTERNAL_URL] = "invalid_url"
            else:
                try:
                    await _validate_manager_token(self.hass, manager_token)
                except ValueError as err:
                    errors[CONF_MANAGER_TOKEN] = str(err)

            if not errors:
                return self.async_create_entry(
                    title="ימות המשיח",
                    data={
                        CONF_EXTERNAL_URL: external_url,
                        CONF_MANAGER_TOKEN: manager_token,
                        CONF_ALLOWED_IPS: user_input.get(
                            CONF_ALLOWED_IPS, DEFAULT_ALLOWED_IPS
                        ),
                        CONF_ALLOWED_PHONES: user_input.get(CONF_ALLOWED_PHONES, ""),
                        CONF_API_TOKEN: secrets.token_urlsafe(24),
                    },
                )

        suggested = user_input or {CONF_EXTERNAL_URL: _default_external_url(self.hass)}

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_EXTERNAL_URL,
                    description={
                        "suggested_value": suggested.get(CONF_EXTERNAL_URL, "")
                    },
                ): str,
                vol.Required(
                    CONF_MANAGER_TOKEN,
                    description={
                        "suggested_value": suggested.get(CONF_MANAGER_TOKEN, "")
                    },
                ): str,
                vol.Optional(
                    CONF_ALLOWED_IPS,
                    default=suggested.get(CONF_ALLOWED_IPS, DEFAULT_ALLOWED_IPS),
                ): str,
                vol.Optional(
                    CONF_ALLOWED_PHONES, default=suggested.get(CONF_ALLOWED_PHONES, "")
                ): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> YemotOptionsFlow:
        """מסך עריכת ההגדרות."""
        return YemotOptionsFlow(config_entry)

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """סוגי תת-הרשומות הנתמכים. כאן נוצר כפתור הוספת השלוחה."""
        return {SUBENTRY_TYPE_EXTENSION: ExtensionSubentryFlowHandler}


class YemotOptionsFlow(OptionsFlow):
    """עריכת ההגדרות לאחר ההתקנה."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """שמירת הרשומה בשם פרטי, לתאימות עם כל הגרסאות."""
        self._entry = config_entry

    def _current(self, key: str, default: str = "") -> str:
        """הערך הנוכחי, קודם מהאפשרויות ואחר כך מנתוני ההגדרה."""
        if key in self._entry.options:
            return str(self._entry.options[key] or default)
        return str(self._entry.data.get(key, default) or default)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """מסך האפשרויות."""
        errors: dict[str, str] = {}

        if user_input is not None:
            external_url = str(user_input[CONF_EXTERNAL_URL]).strip().rstrip("/")
            manager_token = str(user_input[CONF_MANAGER_TOKEN]).strip()

            if not external_url.startswith(("http://", "https://")):
                errors[CONF_EXTERNAL_URL] = "invalid_url"
            elif len(str(user_input.get(CONF_API_TOKEN, "")).strip()) < 8:
                errors[CONF_API_TOKEN] = "token_too_short"
            else:
                try:
                    await _validate_manager_token(self.hass, manager_token)
                except ValueError as err:
                    errors[CONF_MANAGER_TOKEN] = str(err)

            if not errors:
                new_token = str(user_input.get(CONF_API_TOKEN, "")).strip()
                if new_token and new_token != self._entry.data.get(CONF_API_TOKEN):
                    new_data = dict(self._entry.data)
                    new_data[CONF_API_TOKEN] = new_token
                    self.hass.config_entries.async_update_entry(
                        self._entry, data=new_data
                    )
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_EXTERNAL_URL: external_url,
                        CONF_MANAGER_TOKEN: manager_token,
                        CONF_ALLOWED_IPS: user_input.get(CONF_ALLOWED_IPS, ""),
                        CONF_ALLOWED_PHONES: user_input.get(CONF_ALLOWED_PHONES, ""),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_EXTERNAL_URL, default=self._current(CONF_EXTERNAL_URL)
                ): str,
                vol.Required(
                    CONF_MANAGER_TOKEN, default=self._current(CONF_MANAGER_TOKEN)
                ): str,
                vol.Optional(
                    CONF_ALLOWED_IPS,
                    default=self._current(CONF_ALLOWED_IPS, DEFAULT_ALLOWED_IPS),
                ): str,
                vol.Optional(
                    CONF_ALLOWED_PHONES, default=self._current(CONF_ALLOWED_PHONES)
                ): str,
                vol.Required(
                    CONF_API_TOKEN, default=self._entry.data.get(CONF_API_TOKEN, "")
                ): str,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


class ExtensionSubentryFlowHandler(ConfigSubentryFlow):
    """הוספה ועריכה של שלוחה בודדת מתוך הממשק."""

    # ------------------------------------------------------------------
    # בורר השלוחות
    # ------------------------------------------------------------------

    async def _async_scan(self) -> dict[str, str]:
        """סריקת מבנה השלוחות, עם מטמון קצר.

        המטמון מונע סריקה חוזרת בכל פתיחה של הטופס, מה שהיה מוסיף
        השהיה מורגשת במערכות גדולות.
        """
        entry = self._get_entry()
        store = self.hass.data.setdefault(DOMAIN, {}).setdefault("picker_cache", {})
        cached = store.get(entry.entry_id)

        if cached and time.monotonic() - cached[0] < PICKER_CACHE_SECONDS:
            return cached[1]

        runtime = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not runtime:
            return {}

        client: YemotClient = runtime["client"]
        try:
            found = await client.async_scan_extensions(PICKER_SCAN_DEPTH)
        except Exception as err:  # noqa: BLE001 - הבורר אינו קריטי
            _LOGGER.debug("סריקת השלוחות נכשלה: %s", err)
            found = {}

        store[entry.entry_id] = (time.monotonic(), found)
        return found

    def _folder_selector(self, scanned: dict[str, str], current: str = ""):
        """בניית בורר השלוחות מתוך תוצאות הסריקה."""
        managed = self._managed_folders()
        options: list[SelectOptionDict] = []

        for path in sorted(scanned, key=lambda p: [int(x) for x in p.split("/")]):
            state = scanned[path]
            if path in managed and path != current:
                label = f"{path} — מנוהלת כבר"
            elif state == "managed":
                label = f"{path} — שלוחה מנוהלת"
            elif state == "occupied":
                label = f"{path} — תפוסה"
            else:
                label = f"{path} — פנויה"
            options.append(SelectOptionDict(value=path, label=label))

        if current and current not in scanned:
            options.insert(0, SelectOptionDict(value=current, label=current))

        return SelectSelector(
            SelectSelectorConfig(
                options=options,
                mode=SelectSelectorMode.DROPDOWN,
                # מאפשר הקלדה ידנית כאשר הסריקה לא החזירה את השלוחה.
                custom_value=True,
                sort=False,
            )
        )

    def _managed_folders(self) -> set[str]:
        """נתיבי השלוחות שכבר מנוהלות על ידי התוסף."""
        return {
            str(sub.data.get(CONF_FOLDER, "")).strip("/")
            for sub in self._get_entry().subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_EXTENSION
        }

    @staticmethod
    def _action_selector():
        """בורר הפעולה."""
        options = [
            SelectOptionDict(value=value, label=label)
            for value, label in ACTION_LABELS.items()
            if value == "" or value in ALLOWED_ACTIONS
        ]
        return SelectSelector(
            SelectSelectorConfig(
                options=options, mode=SelectSelectorMode.DROPDOWN, sort=False
            )
        )

    async def _async_build_schema(
        self, current: dict[str, Any] | None = None
    ) -> vol.Schema:
        """בניית סכמת הטופס."""
        current = current or {}
        scanned = await self._async_scan()
        folder = str(current.get(CONF_FOLDER, ""))

        folder_field: Any
        if scanned:
            folder_field = self._folder_selector(scanned, folder)
        else:
            # נפילה חזרה להקלדה חופשית אם הסריקה נכשלה.
            folder_field = str

        return vol.Schema(
            {
                vol.Required(
                    CONF_FOLDER, description={"suggested_value": folder}
                ): folder_field,
                vol.Required(
                    CONF_TARGET_ENTITY,
                    description={
                        "suggested_value": current.get(CONF_TARGET_ENTITY, "")
                    },
                ): EntitySelector(EntitySelectorConfig(multiple=False)),
                vol.Optional(
                    CONF_ACTION, default=str(current.get(CONF_ACTION, "") or "")
                ): self._action_selector(),
            }
        )

    # ------------------------------------------------------------------
    # שלבי התהליך
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """הוספת שלוחה חדשה."""
        errors: dict[str, str] = {}

        if user_input is not None:
            folder = str(user_input[CONF_FOLDER]).strip().strip("/")
            entity_id = str(user_input[CONF_TARGET_ENTITY])
            action = str(user_input.get(CONF_ACTION, "") or "")

            if not FOLDER_PATTERN.match(folder):
                errors[CONF_FOLDER] = "invalid_folder"
            elif folder in self._managed_folders():
                errors[CONF_FOLDER] = "folder_in_use"
            elif occupied := await self._async_check_free(folder):
                errors[CONF_FOLDER] = occupied
            else:
                try:
                    await self._async_write(folder, entity_id, action)
                except YemotApiError as err:
                    _LOGGER.error("כתיבת השלוחה נכשלה: %s", err)
                    errors["base"] = "write_failed"

            if not errors:
                return self.async_create_entry(
                    title=self._build_title(folder, entity_id),
                    data={
                        CONF_FOLDER: folder,
                        CONF_TARGET_ENTITY: entity_id,
                        CONF_ACTION: action,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=await self._async_build_schema(user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """עריכת שלוחה קיימת."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        previous = str(subentry.data.get(CONF_FOLDER, "")).strip("/")

        if user_input is not None:
            folder = str(user_input[CONF_FOLDER]).strip().strip("/")
            entity_id = str(user_input[CONF_TARGET_ENTITY])
            action = str(user_input.get(CONF_ACTION, "") or "")

            if not FOLDER_PATTERN.match(folder):
                errors[CONF_FOLDER] = "invalid_folder"
            elif folder != previous and folder in self._managed_folders():
                errors[CONF_FOLDER] = "folder_in_use"
            elif occupied := await self._async_check_free(folder, previous):
                errors[CONF_FOLDER] = occupied
            else:
                try:
                    await self._async_write(folder, entity_id, action)
                    if folder != previous and previous:
                        # השלוחה עברה מקום, ולכן הישנה משוחררת.
                        await self._async_release(previous)
                except YemotApiError as err:
                    _LOGGER.error("עדכון השלוחה נכשל: %s", err)
                    errors["base"] = "write_failed"

            if not errors:
                return self.async_update_and_abort(
                    self._get_entry(),
                    subentry,
                    title=self._build_title(folder, entity_id),
                    data={
                        CONF_FOLDER: folder,
                        CONF_TARGET_ENTITY: entity_id,
                        CONF_ACTION: action,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=await self._async_build_schema(
                user_input or dict(subentry.data)
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # עזרים
    # ------------------------------------------------------------------

    def _runtime(self) -> dict[str, Any] | None:
        """נתוני הריצה של הרשומה."""
        return self.hass.data.get(DOMAIN, {}).get(self._get_entry().entry_id)

    async def _async_check_free(self, folder: str, previous: str = "") -> str | None:
        """בדיקה שהשלוחה אינה תפוסה על ידי תפריט או הקלטה קיימים.

        פועלת גם כאשר סריקת הבורר נכשלה, ולכן היא ההגנה האמיתית
        מפני דריסה של תוכן קיים.
        """
        if folder == previous:
            return None
        runtime = self._runtime()
        if not runtime:
            return None
        try:
            state = await runtime["client"].async_folder_state(folder)
        except YemotApiError as err:
            _LOGGER.debug("בדיקת תפוסת השלוחה %s נכשלה: %s", folder, err)
            return None
        return "folder_occupied" if state == "occupied" else None

    async def _async_write(self, folder: str, entity_id: str, action: str) -> None:
        """כתיבת השלוחה בימות."""
        runtime = self._runtime()
        if not runtime:
            raise YemotApiError("האינטגרציה אינה טעונה")

        link = runtime["coordinator"].build_api_link(folder, entity_id, action)
        await runtime["client"].async_write_extension(folder, link)
        # ביטול מטמון הבורר, כדי שהשלוחה החדשה תופיע בפתיחה הבאה.
        self.hass.data.get(DOMAIN, {}).get("picker_cache", {}).pop(
            self._get_entry().entry_id, None
        )

    async def _async_release(self, folder: str) -> None:
        """שחרור שלוחה שאינה בשימוש עוד."""
        runtime = self._runtime()
        if runtime:
            try:
                await runtime["client"].async_release_extension(folder)
            except YemotApiError as err:
                _LOGGER.warning("שחרור השלוחה %s נכשל: %s", folder, err)

    def _build_title(self, folder: str, entity_id: str) -> str:
        """כותרת ידידותית לתת-הרשומה ולהתקן."""
        state = self.hass.states.get(entity_id)
        name = (
            state.attributes.get("friendly_name") if state else None
        ) or entity_id
        return f"שלוחה {folder} — {name}"
