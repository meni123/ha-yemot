"""לקוח API של ימות המשיח.

כל התקשורת עם שרתי ימות עוברת דרך המחלקה הזו, כדי שלוגיקת ניתוח
התשובות והטיפול בשגיאות תישב במקום אחד.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_TIMEOUT,
    GET_CUSTOMER_DATA_URL,
    GET_IVR2_DIR_URL,
    GET_TEXT_FILE_URL,
    MANAGED_SIGNATURE,
    RUN_TZINTUK_URL,
    SEND_TTS_URL,
    UPDATE_EXTENSION_URL,
    UPLOAD_TEXT_FILE_URL,
)

_LOGGER = logging.getLogger(__name__)


class YemotApiError(HomeAssistantError):
    """שגיאה בתקשורת מול ימות המשיח או בתשובה שהתקבלה."""


class YemotClient:
    """עוטף את קריאות ה-API של ימות המשיח."""

    def __init__(self, hass: HomeAssistant, manager_token: str) -> None:
        """אתחול הלקוח."""
        self._hass = hass
        self._token = manager_token

    @property
    def token(self) -> str:
        """טוקן הניהול הנוכחי."""
        return self._token

    # ------------------------------------------------------------------
    # תשתית
    # ------------------------------------------------------------------

    async def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """שליחת בקשה ובדיקת התשובה."""
        session = async_get_clientsession(self._hass)
        data = {"token": self._token, **payload}

        try:
            async with session.post(
                url, data=data, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)
            ) as response:
                response.raise_for_status()
                body = await response.text()
        except asyncio.TimeoutError as err:
            raise YemotApiError("פג הזמן הקצוב לתגובה משרתי ימות המשיח") from err
        except aiohttp.ClientError as err:
            raise YemotApiError(f"שגיאת תקשורת מול ימות המשיח: {err}") from err

        try:
            parsed = json.loads(body)
        except ValueError:
            # לא כל הקריאות מחזירות JSON. אם ה-HTTP הצליח, נחשיב כהצלחה.
            return {"responseStatus": "OK", "raw": body}

        if not isinstance(parsed, dict):
            return {"responseStatus": "OK", "raw": parsed}

        status = str(parsed.get("responseStatus", "OK")).upper()
        if status != "OK":
            message = (
                parsed.get("message")
                or parsed.get("responseMessage")
                or parsed.get("responseStatus")
            )
            raise YemotApiError(f"ימות המשיח החזירה שגיאה: {message}")

        return parsed

    # ------------------------------------------------------------------
    # אימות
    # ------------------------------------------------------------------

    async def async_validate_token(self) -> None:
        """בדיקה שטוקן הניהול מתקבל על ידי ימות."""
        await self._post(GET_CUSTOMER_DATA_URL, {})

    # ------------------------------------------------------------------
    # ניהול שלוחות
    # ------------------------------------------------------------------

    @staticmethod
    def build_ext_content(api_link: str) -> str:
        """בניית תוכן קובץ ההגדרות של שלוחת API, כולל החתימה."""
        return (
            "type=api\n"
            f"api_link={api_link}\n"
            "api_url_post=yes\n"
            f"{MANAGED_SIGNATURE}\n"
        )

    async def async_ensure_parents(self, folder: str) -> list[str]:
        """יצירת שלוחות האב החסרות בדרך אל שלוחה מקוננת.

        בימות, כדי להגיע לשלוחה 1/2/9 המתקשר חייב לעבור דרך 1 ודרך 1/2.
        אם שלוחת ביניים אינה מוגדרת, הנתיב שבור והשלוחה אינה נגישה.
        לכן כל שלוחת אב חסרה נוצרת כתפריט.

        שלוחות אב שכבר קיימות אינן נגעות, כדי לא לדרוס תפריט של המשתמש.
        מחזיר את רשימת השלוחות שנוצרו.
        """
        parts = [p for p in folder.strip("/").split("/") if p]
        created: list[str] = []

        # כל האבות, בלי השלוחה עצמה
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if await self.async_folder_state(parent) != "free":
                # שלוחת אב שכבר מוגדרת נשארת בדיוק כפי שהיא.
                continue

            await self._post(
                UPDATE_EXTENSION_URL, {"path": f"ivr2:{parent}", "type": "menu"}
            )
            await self._post(
                UPLOAD_TEXT_FILE_URL,
                {"path": f"ivr2:{parent}/ext.ini", "contents": "type=menu\n"},
            )
            created.append(parent)
            _LOGGER.info("נוצרה שלוחת תפריט חסרה בנתיב %s", parent)

        return created

    async def async_write_extension(self, folder: str, api_link: str) -> None:
        """יצירת שלוחה מסוג API וכתיבת קובץ ההגדרות שלה."""
        await self.async_ensure_parents(folder)
        await self._post(
            UPDATE_EXTENSION_URL, {"path": f"ivr2:{folder}", "type": "api"}
        )
        # מתבצע רק אם השלב הראשון הצליח, ולכן אין צורך בהשהיה שרירותית.
        await self._post(
            UPLOAD_TEXT_FILE_URL,
            {
                "path": f"ivr2:{folder}/ext.ini",
                "contents": self.build_ext_content(api_link),
            },
        )

    async def async_release_extension(self, folder: str) -> None:
        """שחרור שלוחה בעת מחיקת תת-רשומה.

        שינוי הסוג בלבד אינו מספיק: הפקודה UpdateExtension מעדכנת את
        סוג השלוחה אך משאירה את קובץ ההגדרות על כנו, כולל api_link.
        התוצאה היא שלוחה שממשיכה להיראות מנוהלת. לכן הקובץ נדרס
        בתוכן נקי, ורק אחר כך מוחזר הסוג הרגיל.
        """
        await self._post(
            UPLOAD_TEXT_FILE_URL,
            {"path": f"ivr2:{folder}/ext.ini", "contents": "type=default\n"},
        )
        await self._post(
            UPDATE_EXTENSION_URL, {"path": f"ivr2:{folder}", "type": "default"}
        )

    # ------------------------------------------------------------------
    # סריקת מבנה השלוחות
    # ------------------------------------------------------------------

    async def async_get_dir(self, path: str = "") -> dict[str, Any] | None:
        """שליפת תוכן שלוחה: תת-שלוחות וקובץ ההגדרות המפוענח.

        התשובה מכילה בין השאר את המפתח dirs, ובו תת-השלוחות,
        ואת המפתח extIni, ובו קובץ ההגדרות כבר מפוענח כמילון.
        לכן קריאה אחת מספיקה גם לניווט וגם לזיהוי מצב השלוחה.
        """
        variants = [f"ivr2:{path}"] if path else ["ivr2:/", "ivr2:", "ivr2:."]

        for variant in variants:
            try:
                result = await self._post(GET_IVR2_DIR_URL, {"path": variant})
            except YemotApiError as err:
                _LOGGER.debug("שליפת הנתיב %r נכשלה: %s", variant, err)
                continue
            if isinstance(result, dict):
                return result

        return None

    @staticmethod
    def _extract_folder_names(payload: dict[str, Any]) -> list[str]:
        """חילוץ שמות תת-השלוחות מתוך המפתח dirs.

        חשוב שהמפתח dirs ייבדק לפני files: הראשון מכיל תיקיות,
        והשני מכיל קבצים שאינם שלוחות כלל.
        """
        entries: Any = None
        for key in ("dirs", "folders", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                entries = value
                break

        if entries is None:
            return []

        names: list[str] = []
        for item in entries:
            if isinstance(item, str):
                raw = item
            elif isinstance(item, dict):
                raw = str(
                    item.get("name")
                    or item.get("dirName")
                    or item.get("what")
                    or item.get("path")
                    or ""
                )
            else:
                continue

            # הערכים עשויים להגיע כנתיב מלא, למשל ivr2:1/2
            raw = raw.split(":")[-1].strip("/")
            name = raw.split("/")[-1]
            if name.isdigit():
                names.append(name)

        return sorted(set(names), key=lambda value: (len(value), int(value)))

    async def async_read_raw_ext_ini(self, folder: str) -> str | None:
        """קריאת קובץ ההגדרות של שלוחה כטקסט גולמי.

        זו מקור האמת לסיווג. המפתח extIni שמוחזר מ-GetIVR2Dir מכיל
        את ההגדרות האפקטיביות, כלומר גם ערכי ברירת מחדל שהמערכת
        יורשת, ולכן הוא לעולם אינו ריק ואי אפשר להסיק ממנו תפוסה.
        """
        try:
            result = await self._post(
                GET_TEXT_FILE_URL, {"what": f"ivr2:{folder}/ext.ini"}
            )
        except YemotApiError as err:
            _LOGGER.debug("קריאת הקובץ הגולמי של %s נכשלה: %s", folder, err)
            return None

        contents = result.get("contents")
        if contents is None:
            contents = result.get("raw")
        return str(contents) if contents is not None else None

    @staticmethod
    def _classify_raw(contents: str | None) -> str:
        """סיווג שלוחה לפי תוכן קובץ ההגדרות הגולמי."""
        if contents is None:
            return "free"

        meaningful: dict[str, str] = {}
        for line in contents.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")) or "=" not in line:
                continue
            key, value = line.split("=", 1)
            meaningful[key.strip().lower()] = value.strip()

        if not meaningful:
            return "free"

        if "/api/yemot/" in meaningful.get("api_link", ""):
            return "managed"

        # שלוחה שהוגדרה כרגילה ואין בה תוכן נוסף פנויה בפועל.
        # זה המצב של שלוחה ששוחררה לאחר מחיקה מהממשק.
        extras = {k: v for k, v in meaningful.items() if k != "type"}
        if not extras and meaningful.get("type", "") in ("", "default"):
            return "free"

        return "occupied"

    @staticmethod
    def _classify(ext_ini: Any) -> str:
        """סיווג לפי ההגדרות המפוענחות. משמש כגיבוי בלבד.

        אינו מסוגל להבחין בין שלוחה פנויה לתפוסה, משום שימות מוסיפה
        ערכי ברירת מחדל. לכן הוא מזהה רק שלוחות מנוהלות.
        """
        if not isinstance(ext_ini, dict):
            return "free"
        if "/api/yemot/" in str(ext_ini.get("api_link", "")):
            return "managed"
        return "occupied"

    async def async_folder_state(self, folder: str) -> str:
        """מצב שלוחה בודדת: managed, occupied או free."""
        raw = await self.async_read_raw_ext_ini(folder)
        if raw is not None:
            return self._classify_raw(raw)

        # גיבוי אם קריאת הקובץ הגולמי אינה זמינה
        result = await self.async_get_dir(folder)
        if result is None:
            return "free"
        return self._classify(result.get("extIni"))

    async def async_get_ext_ini(self, folder: str) -> dict[str, Any] | None:
        """קובץ ההגדרות המפוענח של שלוחה, או None אם אינה קיימת."""
        result = await self.async_get_dir(folder)
        if result is None:
            return None
        ext_ini = result.get("extIni")
        return ext_ini if isinstance(ext_ini, dict) else {}

    async def async_scan_extensions(
        self, depth: int, base: str = "", _listing: dict[str, Any] | None = None
    ) -> dict[str, str]:
        """סריקה רקורסיבית המחזירה מיפוי של נתיב שלוחה למצב שלה.

        הפרמטר _listing מעביר לרקורסיה את התוכן שכבר נשלף עבור אותה
        שלוחה, כדי לא לפנות לשרת פעמיים על אותו נתיב.
        """
        found: dict[str, str] = {}
        if depth <= 0:
            return found

        listing = _listing if _listing is not None else await self.async_get_dir(base)
        if listing is None:
            return found

        names = self._extract_folder_names(listing)
        if not names and not base:
            _LOGGER.warning(
                "בורר השלוחות לא זיהה תת-שלוחות בשורש. מפתחות התשובה: %s",
                list(listing),
            )

        for name in names:
            path = f"{base}/{name}" if base else name
            child = await self.async_get_dir(path)
            if child is None:
                continue

            found[path] = self._classify_raw(
                await self.async_read_raw_ext_ini(path)
            )

            # רקורסיה אמיתית: כל רמה מטפלת ברמה שמתחתיה, והעומק
            # הוא שקובע מתי לעצור. התוכן מועבר הלאה כדי לחסוך קריאה.
            found.update(
                await self.async_scan_extensions(depth - 1, path, _listing=child)
            )

        return found

    # ------------------------------------------------------------------
    # התראות יוצאות
    # ------------------------------------------------------------------

    async def async_send_tzintuk(
        self, phones: str, caller_id: str | None = None
    ) -> None:
        """שליחת צנתוק."""
        payload: dict[str, Any] = {"phones": phones}
        if caller_id:
            payload["callerId"] = caller_id
        await self._post(RUN_TZINTUK_URL, payload)

    async def async_send_tts(
        self,
        phones: str,
        message: str,
        caller_id: str | None = None,
        tts_voice: str | None = None,
        tts_rate: int | None = None,
    ) -> None:
        """הוצאת שיחה קולית עם הקראת טקסט."""
        payload: dict[str, Any] = {"phones": phones, "ttsMessage": message}
        if caller_id:
            payload["callerId"] = caller_id
        if tts_voice:
            payload["ttsVoice"] = tts_voice
        if tts_rate is not None:
            payload["ttsRate"] = tts_rate
        await self._post(SEND_TTS_URL, payload)
