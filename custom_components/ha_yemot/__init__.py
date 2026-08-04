"""אינטגרציית ימות המשיח ל-Home Assistant."""

from __future__ import annotations

import ipaddress
import logging
import re

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .api import YemotApiError, YemotClient
from .api_view import YemotApiView
from .const import (
    CONF_ALLOWED_IPS,
    CONF_ALLOWED_PHONES,
    CONF_API_TOKEN,
    CONF_EXTERNAL_URL,
    CONF_FOLDER,
    CONF_MANAGER_TOKEN,
    DOMAIN,
    SERVICE_CREATE_EXTENSION,
    SERVICE_SEND_TTS,
    SERVICE_SEND_TZINTUK,
    SUBENTRY_TYPE_EXTENSION,
)
from .coordinator import YemotCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

FOLDER_PATTERN = re.compile(r"^\d+(/\d+)*$")


def _single_entity_id(value):
    """קבלת מזהה ישות בודד, גם אם נעטף ברשימה."""
    if isinstance(value, list):
        if len(value) != 1:
            raise vol.Invalid("יש לבחור מכשיר אחד בלבד")
        value = value[0]
    return cv.entity_id(value)


CREATE_EXTENSION_SCHEMA = vol.Schema(
    {
        vol.Required("folder"): cv.string,
        vol.Required("entity_id"): _single_entity_id,
        vol.Optional("action", default=""): cv.string,
    }
)

SEND_TZINTUK_SCHEMA = vol.Schema(
    {vol.Required("phones"): cv.string, vol.Optional("caller_id"): cv.string}
)

SEND_TTS_SCHEMA = vol.Schema(
    {
        vol.Required("phones"): cv.string,
        vol.Required("message"): cv.string,
        vol.Optional("caller_id"): cv.string,
        vol.Optional("tts_voice"): vol.In(["ymFemale", "ymMale"]),
        vol.Optional("tts_rate"): vol.All(vol.Coerce(int), vol.Range(min=-50, max=50)),
    }
)


# ----------------------------------------------------------------------
# מחזור החיים
# ----------------------------------------------------------------------


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """הגדרת האינטגרציה."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    external_url = _get_option(entry, CONF_EXTERNAL_URL, "").rstrip("/")
    api_token = entry.data[CONF_API_TOKEN]
    manager_token = _get_option(entry, CONF_MANAGER_TOKEN, "")

    client = YemotClient(hass, manager_token)
    coordinator = YemotCoordinator(hass, entry, client, external_url, api_token)

    domain_data[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "external_url": external_url,
    }
    # ההגדרות שה-View קורא בכל בקשה. נשמרות בנפרד כדי שטעינה
    # מחדש תעדכן אותן בלי לרשום את הנתיב פעם נוספת.
    domain_data["config"] = {
        "api_token": api_token,
        "allowed_ips": parse_networks(_get_option(entry, CONF_ALLOWED_IPS, "")),
        "allowed_phones": parse_phones(_get_option(entry, CONF_ALLOWED_PHONES, "")),
    }

    # שחרור שלוחות שתת-הרשומה שלהן נמחקה מאז הטעינה הקודמת.
    await _async_release_removed(hass, entry, client, domain_data)

    _register_hub_device(hass, entry)

    if not domain_data.get("view_registered"):
        hass.http.register_view(YemotApiView(hass))
        domain_data["view_registered"] = True

    if not domain_data.get("services_registered"):
        _register_services(hass)
        domain_data["services_registered"] = True

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """הסרת האינטגרציה."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False

    domain_data = hass.data.get(DOMAIN, {})
    domain_data.pop(entry.entry_id, None)
    domain_data.pop("config", None)
    domain_data.get("picker_cache", {}).pop(entry.entry_id, None)

    for service in (
        SERVICE_CREATE_EXTENSION,
        SERVICE_SEND_TZINTUK,
        SERVICE_SEND_TTS,
    ):
        hass.services.async_remove(DOMAIN, service)
    domain_data.pop("services_registered", None)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """טעינה מחדש לאחר שינוי הגדרות או תת-רשומות."""
    await hass.config_entries.async_reload(entry.entry_id)


def _get_option(entry: ConfigEntry, key: str, default: str = "") -> str:
    """קריאת ערך מהאפשרויות, עם נפילה חזרה לנתוני ההגדרה."""
    if key in entry.options:
        return str(entry.options[key] or default)
    return str(entry.data.get(key, default) or default)


def _register_hub_device(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """יצירת ההתקן הראשי המייצג את מערכת ימות."""
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="ימות המשיח",
        name="מערכת ימות המשיח",
        model="מערכת טלפוניה",
        configuration_url="https://www.call2all.co.il",
    )


async def _async_release_removed(
    hass: HomeAssistant,
    entry: ConfigEntry,
    client: YemotClient,
    domain_data: dict,
) -> None:
    """החזרת שלוחות שנמחקו מהממשק לסוג רגיל בימות.

    להסרת תת-רשומה אין התקשרות חוזרת ייעודית, ולכן ההשוואה נעשית
    מול רשימת הנתיבים שנשמרה בטעינה הקודמת.
    """
    current = {
        str(sub.data.get(CONF_FOLDER, "")).strip("/")
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_TYPE_EXTENSION
    }
    current.discard("")

    known_key = f"known_folders_{entry.entry_id}"
    previous: set[str] = domain_data.get(known_key, set())

    for folder in previous - current:
        try:
            await client.async_release_extension(folder)
            _LOGGER.info("השלוחה %s שוחררה בעקבות מחיקה מהממשק", folder)
        except YemotApiError as err:
            _LOGGER.warning("שחרור השלוחה %s נכשל: %s", folder, err)

    domain_data[known_key] = current


# ----------------------------------------------------------------------
# עזרי ניתוח
# ----------------------------------------------------------------------


def parse_networks(raw: str) -> list:
    """המרת מחרוזת כתובות מופרדות בפסיקים לרשימת רשתות."""
    networks = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            _LOGGER.warning("ערך כתובת לא תקין בהגדרות, ולכן התעלמנו ממנו: %s", part)
    return networks


def parse_phones(raw: str) -> list[str]:
    """המרת מחרוזת מספרי טלפון לרשימה מנורמלת."""
    phones = []
    for part in str(raw).split(","):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            phones.append(digits)
    return phones


# ----------------------------------------------------------------------
# השירותים
# ----------------------------------------------------------------------


def _register_services(hass: HomeAssistant) -> None:
    """רישום השירותים."""

    def _runtime() -> dict:
        for key, value in hass.data.get(DOMAIN, {}).items():
            if isinstance(value, dict) and "client" in value:
                return value
        raise HomeAssistantError("אינטגרציית ימות המשיח אינה מוגדרת")

    async def handle_create_extension(call: ServiceCall) -> None:
        """יצירת שלוחה. שירות מיושן, מוחלף בהוספת שלוחה מהממשק."""
        _LOGGER.warning(
            "השירות %s.%s מיושן. מומלץ להוסיף שלוחות דרך כפתור הוספת "
            "השלוחה שבכרטיס האינטגרציה, כדי לקבל גם התקן וחיישנים",
            DOMAIN,
            SERVICE_CREATE_EXTENSION,
        )
        runtime = _runtime()
        folder = str(call.data["folder"]).strip().strip("/")
        if not FOLDER_PATTERN.match(folder):
            raise HomeAssistantError(
                f"נתיב שלוחה לא תקין: {folder}. הפורמט הנדרש הוא למשל 1/5"
            )

        link = runtime["coordinator"].build_api_link(
            folder, call.data["entity_id"], str(call.data.get("action") or "")
        )
        await runtime["client"].async_write_extension(folder, link)

    async def handle_send_tzintuk(call: ServiceCall) -> None:
        """שליחת צנתוק."""
        await _runtime()["client"].async_send_tzintuk(
            call.data["phones"], call.data.get("caller_id")
        )

    async def handle_send_tts(call: ServiceCall) -> None:
        """הוצאת שיחה קולית."""
        await _runtime()["client"].async_send_tts(
            call.data["phones"],
            call.data["message"],
            call.data.get("caller_id"),
            call.data.get("tts_voice"),
            call.data.get("tts_rate"),
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_EXTENSION,
        handle_create_extension,
        schema=CREATE_EXTENSION_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_TZINTUK, handle_send_tzintuk, schema=SEND_TZINTUK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_TTS, handle_send_tts, schema=SEND_TTS_SCHEMA
    )
