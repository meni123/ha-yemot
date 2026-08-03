"""נקודת הקצה שמקבלת בקשות נכנסות משרתי ימות המשיח."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    ALLOWED_ACTIONS,
    ALLOWED_DOMAINS,
    DOMAIN,
    MAX_RESPONSE_LENGTH,
    STATE_CHANGE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


def _is_private_address(addr: str | None) -> bool:
    """האם הכתובת פנימית - סימן אפשרי ל-proxy שאינו מוגדר כמהימן."""
    try:
        ip = ipaddress.ip_address(addr or "")
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback



class YemotApiView(HomeAssistantView):
    """מטפל בבקשות משרתי ימות המשיח ומחזיר טקסט להקראה."""

    url = "/api/yemot/{token}/{entity_id}"
    extra_urls = ["/api/yemot/{token}/{entity_id}/{action}"]
    name = "api:yemot"
    # ימות אינה יכולה לשלוח כותרת Authorization של Home Assistant,
    # ולכן האימות מתבצע ידנית מטה: טוקן + סינון IP + סינון מספר מתקשר.
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """אתחול. ההגדרות נקראות דינמית כדי שטעינה מחדש תעבוד."""
        self.hass = hass

    # ------------------------------------------------------------------
    # נקודות כניסה
    # ------------------------------------------------------------------

    async def get(
        self,
        request: web.Request,
        token: str,
        entity_id: str,
        action: str | None = None,
    ) -> web.Response:
        """טיפול בבקשות GET."""
        return await self._process_request(request, token, entity_id, action)

    async def post(
        self,
        request: web.Request,
        token: str,
        entity_id: str,
        action: str | None = None,
    ) -> web.Response:
        """טיפול בבקשות POST."""
        return await self._process_request(request, token, entity_id, action)

    # ------------------------------------------------------------------
    # הלוגיקה המרכזית
    # ------------------------------------------------------------------

    async def _process_request(
        self,
        request: web.Request,
        token: str,
        entity_id: str,
        action: str | None,
    ) -> web.Response:
        """אימות הבקשה, ביצוע הפעולה והחזרת סטטוס להקראה."""
        config = self.hass.data.get(DOMAIN, {}).get("config")
        if not config:
            _LOGGER.warning("התקבלה בקשה מימות אך האינטגרציה אינה מוגדרת")
            return web.Response(status=503, text="Service unavailable")

        # --- שלב 1: סינון לפי כתובת IP ---
        # request.remote הוא כתובת ה-TCP האמיתית ולא ניתן לזייף אותה.
        # מאחורי reverse proxy יש להגדיר use_x_forwarded_for + trusted_proxies
        # ב-configuration.yaml, ואז HA מציב כאן את ה-IP האמיתי לאחר אימות.
        if not self._is_ip_allowed(request.remote, config["allowed_ips"]):
            if _is_private_address(request.remote):
                # כתובת פנימית מרמזת שה-proxy אינו מוגדר כמהימן,
                # ואז הסינון בודק את הכתובת הלא נכונה.
                _LOGGER.warning(
                    "נחסמה בקשה מכתובת פנימית %s. ככל הנראה חסרה הגדרת "
                    "use_x_forwarded_for ו-trusted_proxies ב-configuration.yaml",
                    request.remote,
                )
            else:
                _LOGGER.info(
                    "נחסמה בקשה מכתובת לא מורשית: %s", request.remote
                )
            return web.Response(status=403, text="Forbidden")

        # --- שלב 2: אימות הטוקן (השוואה בזמן קבוע) ---
        if not hmac.compare_digest(str(token), str(config["api_token"])):
            _LOGGER.warning("התקבל טוקן שגוי מכתובת %s", request.remote)
            await self._register_failed_login(request)
            return web.Response(status=401, text="Unauthorized")

        # --- שלב 3: סינון אופציונלי לפי מספר המתקשר ---
        allowed_phones = config.get("allowed_phones") or []
        if allowed_phones:
            caller = await self._get_caller_phone(request)
            if self._normalize_phone(caller) not in allowed_phones:
                _LOGGER.warning("נחסמה שיחה ממספר לא מורשה: %s", caller)
                return self._build_response("המספר שלך אינו מורשה לגשת למערכת")

        # --- שלב 4: בדיקת תקינות הישות ---
        if "." not in entity_id:
            return self._build_response("מזהה מכשיר שגוי")

        entity_domain = entity_id.split(".", 1)[0]
        state = self.hass.states.get(entity_id)
        if state is None:
            return self._build_response("המכשיר לא נמצא")

        # --- שלב 5: הקראת סטטוס בלבד ---
        if not action:
            return self._build_response(
                f"הסטטוס כרגע {self._translate_state(entity_domain, state)}"
            )

        # --- שלב 6: ביצוע פעולה, לאחר בדיקת רשימות ההיתר ---
        if entity_domain not in ALLOWED_DOMAINS or action not in ALLOWED_ACTIONS:
            _LOGGER.warning(
                "נחסם ניסיון להפעיל פעולה לא מורשית: %s.%s", entity_domain, action
            )
            return self._build_response("הפעולה המבוקשת אינה מורשית")

        if not self.hass.services.has_service(entity_domain, action):
            return self._build_response("הפעולה אינה נתמכת עבור מכשיר זה")

        try:
            new_state = await self._call_and_wait(entity_domain, action, entity_id)
        except Exception:  # noqa: BLE001 - חייבים להחזיר תשובה קולית תמיד
            _LOGGER.exception("שגיאה בהפעלת %s.%s על %s", entity_domain, action, entity_id)
            return self._build_response("אירעה שגיאה בביצוע הפעולה")

        if new_state is None:
            return self._build_response("הפעולה נשלחה אך לא ניתן לוודא את הסטטוס")

        return self._build_response(
            f"הפעולה בוצעה והמכשיר כרגע {self._translate_state(entity_domain, new_state)}"
        )

    # ------------------------------------------------------------------
    # עזרי אבטחה
    # ------------------------------------------------------------------

    @staticmethod
    def _is_ip_allowed(remote: str | None, allowed_networks: list) -> bool:
        """בדיקה האם הכתובת נמצאת באחד הטווחים המורשים."""
        if not allowed_networks:
            # רשימה ריקה = המשתמש בחר במפורש לא לסנן לפי IP.
            return True
        if not remote:
            return False
        try:
            client_ip = ipaddress.ip_address(remote)
        except ValueError:
            return False
        return any(client_ip in network for network in allowed_networks)

    async def _register_failed_login(self, request: web.Request) -> None:
        """דיווח על ניסיון כושל למנגנון חסימת ה-IP המובנה של HA."""
        try:
            from homeassistant.components.http.ban import process_wrong_login

            await process_wrong_login(request)
        except Exception:  # noqa: BLE001 - המנגנון אופציונלי ולא קריטי
            _LOGGER.debug("לא ניתן לדווח למנגנון חסימת ה-IP", exc_info=True)

    @staticmethod
    async def _get_caller_phone(request: web.Request) -> str:
        """שליפת מספר המתקשר שימות שולחת בפרמטר ApiPhone."""
        phone = request.query.get("ApiPhone", "")
        if not phone and request.method == "POST":
            try:
                form = await request.post()
                phone = str(form.get("ApiPhone", ""))
            except Exception:  # noqa: BLE001 - גוף לא תקין אינו קריטי
                phone = ""
        return phone

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """נרמול מספר טלפון להשוואה (הסרת תווים שאינם ספרות)."""
        return "".join(ch for ch in str(phone) if ch.isdigit())

    # ------------------------------------------------------------------
    # המתנה לשינוי מצב
    # ------------------------------------------------------------------

    async def _call_and_wait(self, entity_domain: str, action: str, entity_id: str):
        """הפעלת השירות והמתנה לשינוי מצב אמיתי, במקום sleep קבוע."""
        loop = self.hass.loop
        future: asyncio.Future = loop.create_future()

        @callback
        def _state_listener(event) -> None:
            if not future.done():
                future.set_result(event.data.get("new_state"))

        unsub = async_track_state_change_event(self.hass, [entity_id], _state_listener)

        try:
            await self.hass.services.async_call(
                entity_domain, action, {"entity_id": entity_id}, blocking=True
            )
            try:
                return await asyncio.wait_for(future, timeout=STATE_CHANGE_TIMEOUT)
            except asyncio.TimeoutError:
                # המכשיר לא דיווח על שינוי בזמן - מחזירים את המצב הידוע האחרון.
                return self.hass.states.get(entity_id)
        finally:
            unsub()
            if not future.done():
                future.cancel()

    # ------------------------------------------------------------------
    # תרגום מצבים לעברית מדוברת
    # ------------------------------------------------------------------

    _TRANSLATIONS = {
        "on": "דולק",
        "off": "כבוי",
        "locked": "נעול",
        "unlocked": "לא נעול",
        "locking": "ננעל כעת",
        "unlocking": "נפתח כעת",
        "jammed": "תקוע",
        "open": "פתוח",
        "closed": "סגור",
        "opening": "נפתח כעת",
        "closing": "נסגר כעת",
        "cool": "על קירור",
        "heat": "על חימום",
        "heat_cool": "על חימום וקירור",
        "fan_only": "על אוורור",
        "dry": "על ייבוש",
        "auto": "על אוטומטי",
        "cleaning": "מנקה",
        "docked": "בעמדת טעינה",
        "idle": "ממתין",
        "paused": "מושהה",
        "returning": "חוזר לעמדה",
        "error": "במצב שגיאה",
        "playing": "מנגן",
        "standby": "בהמתנה",
        "home": "בבית",
        "not_home": "מחוץ לבית",
        "armed_home": "דרוך במצב בית",
        "armed_away": "דרוך במצב יציאה",
        "disarmed": "מנוטרל",
        "triggered": "הופעל",
    }

    _TEMPERATURE_UNITS = frozenset({"°c", "c", "℃", "°f", "f", "℉"})

    def _translate_state(self, entity_domain: str, state_obj) -> str:
        """המרת מצב הישות לטקסט עברי מובן בהקראה."""
        raw_state = str(state_obj.state)
        lowered = raw_state.lower()

        if lowered in ("unavailable", "unknown", "none", ""):
            return "לא זמין"

        # תאריך ושעה - dt_util מזהה לבד, בלי היוריסטיקות שבירות.
        parsed_dt = dt_util.parse_datetime(raw_state)
        if parsed_dt is not None:
            local_dt = dt_util.as_local(parsed_dt)
            return (
                f"ה{local_dt.day} לחודש {local_dt.month} שנת {local_dt.year}, "
                f"בשעה {local_dt.strftime('%H:%M')}"
            )

        # ערכים מספריים - מעוגלים ועם יחידת מידה מדוברת.
        numeric = self._as_rounded_number(raw_state)
        if numeric is not None:
            unit = str(state_obj.attributes.get("unit_of_measurement", "")).strip().lower()
            if unit in self._TEMPERATURE_UNITS:
                return f"{numeric} מעלות"
            if unit == "%":
                return f"{numeric} אחוזים"
            if unit:
                return f"{numeric} {unit}"
            return str(numeric)

        translated = self._TRANSLATIONS.get(lowered)
        if translated:
            return translated

        # ברירת מחדל: להקריא את המצב הגולמי, עדיף על "לא מוכר".
        return raw_state

    @staticmethod
    def _as_rounded_number(value: str) -> int | float | None:
        """המרה למספר מעוגל, או None אם אינו מספרי."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        rounded = round(number, 1)
        return int(rounded) if rounded == int(rounded) else rounded

    # ------------------------------------------------------------------
    # בניית התגובה בפורמט ימות
    # ------------------------------------------------------------------

    @staticmethod
    def _build_response(text: str) -> web.Response:
        """בניית תגובה בפורמט שימות המשיח מצפה לו."""
        safe_text = (
            str(text)
            .replace("&", " ו")
            .replace("=", " שווה ")
            .replace("-", " ")
            .replace("\r", " ")
            .replace("\n", " ")
            .replace("%", " אחוז ")
        )
        safe_text = " ".join(safe_text.split())[:MAX_RESPONSE_LENGTH]
        body = f"id_list_message=t-{safe_text}&go_to_folder=/&"
        return web.Response(text=body, content_type="text/plain", charset="utf-8")
