"""אינטגרציית ימות המשיח ל-Home Assistant."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_view import YemotApiView
from .const import (
    API_TIMEOUT,
    CONF_ALLOWED_IPS,
    CONF_ALLOWED_PHONES,
    CONF_API_TOKEN,
    CONF_EXTERNAL_URL,
    CONF_MANAGER_TOKEN,
    DOMAIN,
    RUN_TZINTUK_URL,
    SEND_TTS_URL,
    SERVICE_CREATE_EXTENSION,
    SERVICE_SEND_TTS,
    SERVICE_SEND_TZINTUK,
    UPDATE_EXTENSION_URL,
    UPLOAD_TEXT_FILE_URL,
)

_LOGGER = logging.getLogger(__name__)

FOLDER_PATTERN = re.compile(r"^\d+(/\d+)*$")


def _single_entity_id(value):
    """קבלת מזהה ישות בודד, גם אם HA עטף אותו ברשימה."""
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
    {
        vol.Required("phones"): cv.string,
        vol.Optional("caller_id"): cv.string,
    }
)

SEND_TTS_SCHEMA = vol.Schema(
    {
        vol.Required("phones"): cv.string,
        vol.Required("message"): cv.string,
        vol.Optional("caller_id"): cv.string,
        vol.Optional("tts_voice"): vol.In(["ymFemale", "ymMale", "Female1", "Male1"]),
        vol.Optional("tts_rate"): vol.All(vol.Coerce(int), vol.Range(min=-50, max=50)),
    }
)


# ----------------------------------------------------------------------
# מחזור החיים של האינטגרציה
# ----------------------------------------------------------------------


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """הגדרת האינטגרציה מתוך הממשק הגרפי."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    domain_data["config"] = {
        "external_url": _get_option(entry, CONF_EXTERNAL_URL, "").rstrip("/"),
        "api_token": entry.data[CONF_API_TOKEN],
        "manager_token": _get_option(entry, CONF_MANAGER_TOKEN, ""),
        "allowed_ips": parse_networks(_get_option(entry, CONF_ALLOWED_IPS, "")),
        "allowed_phones": parse_phones(_get_option(entry, CONF_ALLOWED_PHONES, "")),
    }

    # ה-View נרשם פעם אחת בלבד לכל אורך חיי התהליך.
    # aiohttp אינו מאפשר להסיר או לרשום מסלול פעמיים, ולכן הטוקן
    # וההגדרות נקראים דינמית מ-hass.data ולא נלכדים ב-closure.
    if not domain_data.get("view_registered"):
        hass.http.register_view(YemotApiView(hass))
        domain_data["view_registered"] = True

    if not domain_data.get("services_registered"):
        _register_services(hass)
        domain_data["services_registered"] = True

    # טעינה מחדש אוטומטית כשמשנים הגדרות במסך האפשרויות.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """הסרת האינטגרציה."""
    domain_data = hass.data.get(DOMAIN, {})

    for service in (
        SERVICE_CREATE_EXTENSION,
        SERVICE_SEND_TZINTUK,
        SERVICE_SEND_TTS,
    ):
        hass.services.async_remove(DOMAIN, service)

    domain_data.pop("services_registered", None)
    # ההגדרות נמחקות כדי שה-View יחזיר 503 עד לטעינה מחדש.
    domain_data.pop("config", None)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """טעינה מחדש לאחר שינוי הגדרות."""
    await hass.config_entries.async_reload(entry.entry_id)


def _get_option(entry: ConfigEntry, key: str, default: str = "") -> str:
    """קריאת ערך מהאפשרויות, עם נפילה חזרה לנתוני ההגדרה המקוריים."""
    if key in entry.options:
        return str(entry.options[key] or default)
    return str(entry.data.get(key, default) or default)


# ----------------------------------------------------------------------
# עזרי ניתוח הגדרות
# ----------------------------------------------------------------------


def parse_networks(raw: str) -> list:
    """המרת מחרוזת של כתובות/טווחים מופרדים בפסיקים לרשימת רשתות."""
    networks = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            _LOGGER.warning("ערך IP לא תקין בהגדרות ולכן התעלמנו ממנו: %s", part)
    return networks


def parse_phones(raw: str) -> list[str]:
    """המרת מחרוזת מספרי טלפון לרשימה מנורמלת (ספרות בלבד)."""
    phones = []
    for part in str(raw).split(","):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            phones.append(digits)
    return phones


# ----------------------------------------------------------------------
# תקשורת מול ה-API של ימות
# ----------------------------------------------------------------------


async def async_yemot_post(hass: HomeAssistant, url: str, payload: dict) -> dict:
    """שליחת בקשה לימות המשיח, כולל בדיקת התשובה.

    מחזיר את גוף התשובה כמילון, וזורק HomeAssistantError אם משהו נכשל.
    """
    session = async_get_clientsession(hass)
    try:
        async with session.post(
            url, data=payload, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)
        ) as response:
            response.raise_for_status()
            body = await response.text()
    except asyncio.TimeoutError as err:
        raise HomeAssistantError("פג הזמן הקצוב לתגובה משרתי ימות המשיח") from err
    except aiohttp.ClientError as err:
        raise HomeAssistantError(f"שגיאת תקשורת מול ימות המשיח: {err}") from err

    try:
        data = json.loads(body)
    except ValueError:
        # חלק מהקריאות אינן מחזירות JSON. אם ה-HTTP הצליח, נחשיב כהצלחה.
        return {"responseStatus": "OK", "raw": body}

    if not isinstance(data, dict):
        return {"responseStatus": "OK", "raw": data}

    status = data.get("responseStatus")
    if status and str(status).upper() != "OK":
        message = data.get("message") or data.get("responseMessage") or status
        raise HomeAssistantError(f"ימות המשיח החזירה שגיאה: {message}")

    return data


# ----------------------------------------------------------------------
# השירותים
# ----------------------------------------------------------------------


def _register_services(hass: HomeAssistant) -> None:
    """רישום שלושת השירותים."""

    def _config() -> dict:
        config = hass.data.get(DOMAIN, {}).get("config")
        if not config:
            raise HomeAssistantError("אינטגרציית ימות המשיח אינה מוגדרת")
        return config

    async def handle_create_extension(call: ServiceCall) -> None:
        """יצירת שלוחה בימות המשיח המקושרת לישות ב-Home Assistant."""
        config = _config()
        folder = str(call.data["folder"]).strip().strip("/")
        entity_id = call.data["entity_id"]
        action = str(call.data.get("action") or "").strip()

        if not FOLDER_PATTERN.match(folder):
            raise HomeAssistantError(
                f"נתיב שלוחה לא תקין: {folder}. הפורמט הנדרש הוא למשל 1/5"
            )

        if not config["external_url"]:
            raise HomeAssistantError("לא הוגדרה כתובת חיצונית לשרת")

        action_suffix = f"/{action}" if action else ""
        api_link = (
            f"{config['external_url']}/api/yemot/"
            f"{config['api_token']}/{entity_id}{action_suffix}"
        )
        ext_content = f"type=api\napi_link={api_link}\napi_url_post=yes"

        # שלב א': יצירת השלוחה כשלוחת API.
        await async_yemot_post(
            hass,
            UPDATE_EXTENSION_URL,
            {
                "token": config["manager_token"],
                "path": f"ivr2:{folder}",
                "type": "api",
            },
        )

        # שלב ב': כתיבת קובץ ההגדרות. מתבצע רק אם שלב א' הצליח,
        # ולכן אין צורך ב-sleep שרירותי.
        await async_yemot_post(
            hass,
            UPLOAD_TEXT_FILE_URL,
            {
                "token": config["manager_token"],
                "path": f"ivr2:{folder}/ext.ini",
                "contents": ext_content,
            },
        )

        _LOGGER.info("שלוחה %s הוגדרה בהצלחה עבור %s", folder, entity_id)

    async def handle_send_tzintuk(call: ServiceCall) -> None:
        """שליחת צנתוק."""
        config = _config()
        payload = {
            "token": config["manager_token"],
            "phones": call.data["phones"],
        }
        if caller_id := call.data.get("caller_id"):
            payload["callerId"] = caller_id

        await async_yemot_post(hass, RUN_TZINTUK_URL, payload)

    async def handle_send_tts(call: ServiceCall) -> None:
        """הוצאת שיחה קולית עם הקראת טקסט."""
        config = _config()
        payload = {
            "token": config["manager_token"],
            "phones": call.data["phones"],
            "ttsMessage": call.data["message"],
        }
        if caller_id := call.data.get("caller_id"):
            payload["callerId"] = caller_id
        if tts_voice := call.data.get("tts_voice"):
            payload["ttsVoice"] = tts_voice
        if (tts_rate := call.data.get("tts_rate")) is not None:
            payload["ttsRate"] = tts_rate

        await async_yemot_post(hass, SEND_TTS_URL, payload)

    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_EXTENSION, handle_create_extension,
        schema=CREATE_EXTENSION_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_TZINTUK, handle_send_tzintuk,
        schema=SEND_TZINTUK_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_TTS, handle_send_tts, schema=SEND_TTS_SCHEMA
    )
