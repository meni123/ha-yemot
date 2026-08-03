"""תהליך ההגדרה של אינטגרציית ימות המשיח."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import (
    API_TIMEOUT,
    CONF_ALLOWED_IPS,
    CONF_ALLOWED_PHONES,
    CONF_API_TOKEN,
    CONF_EXTERNAL_URL,
    CONF_MANAGER_TOKEN,
    DEFAULT_ALLOWED_IPS,
    DOMAIN,
    GET_CUSTOMER_DATA_URL,
)

_LOGGER = logging.getLogger(__name__)


async def _validate_manager_token(hass: HomeAssistant, token: str) -> None:
    """בדיקה שטוקן הניהול תקין מול שרתי ימות.

    ימות תומכת בשני פורמטים של אימות ושניהם קבילים:
      1. מספר_מערכת:סיסמה  - למשל 0771234567:1234
      2. טוקן API ארוך מלשונית האבטחה, ללא נקודתיים

    זורק ValueError עם קוד שגיאה מתאים אם הבדיקה נכשלה.
    """
    token = token.strip()
    if len(token) < 6 or any(ch.isspace() for ch in token):
        raise ValueError("invalid_token_format")

    session = async_get_clientsession(hass)
    try:
        async with session.post(
            GET_CUSTOMER_DATA_URL,
            data={"token": token},
            timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
        ) as response:
            response.raise_for_status()
            body = await response.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        _LOGGER.debug("בדיקת הטוקן נכשלה: %s", err)
        raise ValueError("cannot_connect") from err

    try:
        data = json.loads(body)
    except ValueError:
        # אם התשובה אינה JSON, לא נוכל לאמת - נניח שתקין ולא נחסום את המשתמש.
        return

    if isinstance(data, dict):
        status = str(data.get("responseStatus", "OK")).upper()
        if status != "OK":
            message = str(
                data.get("message") or data.get("responseMessage") or ""
            ).lower()
            # רק שגיאת הרשאה חוסמת את ההגדרה. שגיאות אחרות מדווחות
            # ללוג בלבד, כדי לא לחסום את המשתמש בגלל שינוי ב-API של ימות.
            if any(word in message for word in ("token", "password", "login", "auth")):
                raise ValueError("invalid_auth")
            _LOGGER.warning(
                "ימות המשיח החזירה סטטוס לא צפוי בבדיקת הטוקן: %s", data
            )


def _default_external_url(hass: HomeAssistant) -> str:
    """ניחוש הכתובת החיצונית של השרת, אם קיימת."""
    try:
        return get_url(hass, allow_internal=False, prefer_external=True).rstrip("/")
    except NoURLAvailableError:
        return ""


class YemotConfigFlow(ConfigFlow, domain=DOMAIN):
    """טיפול בהוספת האינטגרציה."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """שלב ההגדרה הראשי."""
        # מונע הוספת שתי מופעים של האינטגרציה, שהייתה גורמת
        # להתנגשות ברישום נתיב ה-HTTP.
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
                        # טוקן אקראי וחזק נוצר אוטומטית. אין ברירת מחדל
                        # שהמשתמש עלול להשאיר כמו שהיא.
                        CONF_API_TOKEN: secrets.token_urlsafe(24),
                    },
                )

        suggested = user_input or {CONF_EXTERNAL_URL: _default_external_url(self.hass)}

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_EXTERNAL_URL,
                    description={"suggested_value": suggested.get(CONF_EXTERNAL_URL, "")},
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
                    CONF_ALLOWED_PHONES,
                    default=suggested.get(CONF_ALLOWED_PHONES, ""),
                ): str,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> YemotOptionsFlow:
        """מסך עריכת ההגדרות."""
        return YemotOptionsFlow(config_entry)


class YemotOptionsFlow(OptionsFlow):
    """עריכת הגדרות לאחר ההתקנה, בלי צורך למחוק ולהוסיף מחדש."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """שמירת ה-entry בשם פרטי, לתאימות עם כל גרסאות HA."""
        self._entry = config_entry

    def _current(self, key: str, default: str = "") -> str:
        """הערך הנוכחי: קודם מהאפשרויות, אחרת מההגדרה המקורית."""
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
                options = {
                    CONF_EXTERNAL_URL: external_url,
                    CONF_MANAGER_TOKEN: manager_token,
                    CONF_ALLOWED_IPS: user_input.get(CONF_ALLOWED_IPS, ""),
                    CONF_ALLOWED_PHONES: user_input.get(CONF_ALLOWED_PHONES, ""),
                }
                # טוקן ה-API נשמר ב-data ולא ב-options, כי הוא מזהה קבוע
                # של האינטגרציה. שינוי שלו מבטל את כל השלוחות הקיימות.
                new_token = str(user_input.get(CONF_API_TOKEN, "")).strip()
                if new_token and new_token != self._entry.data.get(CONF_API_TOKEN):
                    new_data = dict(self._entry.data)
                    new_data[CONF_API_TOKEN] = new_token
                    self.hass.config_entries.async_update_entry(
                        self._entry, data=new_data
                    )
                return self.async_create_entry(title="", data=options)

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
                    CONF_API_TOKEN,
                    default=self._entry.data.get(CONF_API_TOKEN, ""),
                ): str,
            }
        )

        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )
