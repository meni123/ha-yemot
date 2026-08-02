import logging
import asyncio
import aiohttp
from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.http import HomeAssistantView
from homeassistant.util import dt as dt_util

# ייבוא הקבועים מקובץ const.py
from .const import (
    DOMAIN,
    UPDATE_EXTENSION_URL,
    UPLOAD_TEXT_FILE_URL,
    RUN_TZINTUK_URL,
    SEND_TTS_URL
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """הגדרת התוסף מתוך הממשק הגרפי (Config Flow)."""
    
    external_url = entry.data.get("external_url")
    api_token = entry.data.get("api_token")
    yemot_manager_token = entry.data.get("yemot_manager_token")
    
    allowed_ips_str = entry.data.get("allowed_ips", "")
    allowed_ips = [ip.strip() for ip in allowed_ips_str.split(",")] if allowed_ips_str else []

    hass.http.register_view(YemotApiView(hass, api_token, allowed_ips))

    # --- 1. השירות ליצירת שלוחות בימות המשיח ---
    async def handle_create_extension(call):
        """יצירת שלוחה בימות המשיח והגדרתה כ-API."""
        folder = call.data.get("folder")
        entity_id = call.data.get("entity_id")
        action = call.data.get("action", "")
        
        action_url = f"/{action}" if action else ""
        api_link = f"{external_url}/api/yemot/{api_token}/{entity_id}{action_url}"
        
        ext_content = f"type=api\napi_link={api_link}\napi_url_post=yes"
        
        try:
            async with aiohttp.ClientSession() as session:
                # שלב א': יצירת התיקייה
                await session.post(
                    UPDATE_EXTENSION_URL, 
                    data={"token": yemot_manager_token, "path": f"ivr2:{folder}"}
                )
                
                await asyncio.sleep(0.5)
                
                # שלב ב': הזרקת קובץ ההגדרות לתוך התיקייה
                await session.post(
                    UPLOAD_TEXT_FILE_URL, 
                    data={"token": yemot_manager_token, "path": f"ivr2:{folder}/ext.ini", "contents": ext_content}
                )
        except aiohttp.ClientError as err:
            _LOGGER.error(f"שגיאת תקשורת מול ימות המשיח בעת יצירת שלוחה: {err}")

    # --- 2. שירות לשליחת צנתוקים ---
    async def handle_send_tzintuk(call):
        """שליחת צנתוק למספרים נבחרים."""
        phones = call.data.get("phones")
        caller_id = call.data.get("caller_id")
        
        payload = {"token": yemot_manager_token, "phones": phones}
        if caller_id:
            payload["callerId"] = caller_id
            
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(RUN_TZINTUK_URL, data=payload)
        except aiohttp.ClientError as err:
            _LOGGER.error(f"שגיאת תקשורת בעת שליחת צנתוק: {err}")

    # --- 3. שירות להוצאת שיחה קולית (TTS) ---
    async def handle_send_tts(call):
        """שליחת הודעת טקסט לדיבור (TTS) למספרים נבחרים."""
        phones = call.data.get("phones")
        message = call.data.get("message")
        caller_id = call.data.get("caller_id")
        
        payload = {
            "token": yemot_manager_token, 
            "phones": phones,
            "ttsMessage": message
        }
        if caller_id:
            payload["callerId"] = caller_id
            
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(SEND_TTS_URL, data=payload)
        except aiohttp.ClientError as err:
            _LOGGER.error(f"שגיאת תקשורת בעת שליחת שיחת TTS: {err}")

    hass.services.async_register(DOMAIN, "create_extension", handle_create_extension)
    hass.services.async_register(DOMAIN, "send_tzintuk", handle_send_tzintuk)
    hass.services.async_register(DOMAIN, "send_tts", handle_send_tts)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """הסרת התוסף מ-Home Assistant מתוך הממשק."""
    hass.services.async_remove(DOMAIN, "create_extension")
    hass.services.async_remove(DOMAIN, "send_tzintuk")
    hass.services.async_remove(DOMAIN, "send_tts")
    return True


class YemotApiView(HomeAssistantView):
    """תצוגת ה-API לקבלת בקשות נכנסות משרתי ימות המשיח."""
    
    url = "/api/yemot/{token}/{entity_id}"
    extra_urls = ["/api/yemot/{token}/{entity_id}/{action}"]
    name = "api:yemot"
    requires_auth = False 

    def __init__(self, hass: HomeAssistant, api_token: str, allowed_ips: list):
        """אתחול מחלקת התצוגה."""
        self.hass = hass
        self.api_token = api_token
        self.allowed_ips = allowed_ips

    async def get(self, request: web.Request, token: str, entity_id: str, action: str = None) -> web.Response:
        """טיפול בבקשות GET נכנסות."""
        await request.read()
        return await self._process_request(request, token, entity_id, action)

    async def post(self, request: web.Request, token: str, entity_id: str, action: str = None) -> web.Response:
        """טיפול בבקשות POST נכנסות."""
        await request.read()
        return await self._process_request(request, token, entity_id, action)

    async def _process_request(self, request: web.Request, token: str, entity_id: str, action: str) -> web.Response:
        """עיבוד הבקשה, הפעלת פעולות (אם נדרש) והחזרת סטטוס המכשיר."""
        client_ip_header = request.headers.get("X-Forwarded-For", request.remote)
        client_ip = client_ip_header.split(',')[0].strip() if client_ip_header else ""
        
        if self.allowed_ips and client_ip not in self.allowed_ips:
            return web.Response(text="Access Denied", status=403, content_type="text/plain")

        if token != self.api_token:
            return self._build_response("שגיאת הרשאה")

        domain = entity_id.split(".")[0]

        if action:
            try:
                await self.hass.services.async_call(domain, action, {"entity_id": entity_id}, blocking=True)
                await asyncio.sleep(2.5) 
                
                new_state = self.hass.states.get(entity_id)
                if new_state:
                    ans = self._translate_state(domain, new_state)
                    return self._build_response(f"הפעולה בוצעה והמכשיר כרגע {ans}")
                else:
                    return self._build_response("הפעולה נשלחה אך לא ניתן לוודא את הסטטוס החדש")
            except Exception as err:
                _LOGGER.error(f"שגיאה בהפעלת שירות {action} על ישות {entity_id}: {err}")
                return self._build_response("שגיאה בביצוע הפעולה")

        state = self.hass.states.get(entity_id)
        if not state:
            return self._build_response("המכשיר לא נמצא")

        ans = self._translate_state(domain, state)
        return self._build_response(f"הסטטוס כרגע {ans}")

    def _translate_state(self, domain: str, state_obj) -> str:
        """תרגום מצב הישות לאנגלית/מספרים לטקסט מדובר בעברית."""
        raw_st = str(state_obj.state) 
        st = raw_st.lower()
        
        if st in ["unavailable", "unknown"]:
            return "לא זמין"
            
        if "t" in raw_st and len(raw_st) > 15 and raw_st[4] == "-":
            try:
                parsed_dt = dt_util.parse_datetime(raw_st)
                if parsed_dt:
                    local_dt = dt_util.as_local(parsed_dt)
                    day = local_dt.day
                    month = local_dt.month
                    year = local_dt.year
                    time_str = local_dt.strftime("%H:%M")
                    return f"ה{day} לחודש {month} שנת {year}, בשעה {time_str}"
            except Exception:
                pass

        if domain == "sensor":
            try:
                if '.' in st:
                    st = str(round(float(st)))
            except ValueError:
                pass
                
            unit = str(state_obj.attributes.get("unit_of_measurement", "")).lower()
            if "c" in unit or "מעלות" in unit or "°" in unit:
                return f"{st} מעלות"
            elif "%" in unit:
                return f"{st} אחוזים"
            else:
                return st
                
        translations = {
            "on": "דולק",
            "off": "כבוי",
            "locked": "נעול",
            "unlocked": "פתוח",
            "cool": "על קירור",
            "heat": "על חימום",
            "fan_only": "על אוורור",
            "dry": "על ייבוש",
            "auto": "על אוטומטי",
            "open": "פתוח",
            "closed": "סגור",
            "opening": "נפתח כעת",
            "closing": "נסגר כעת",
            "cleaning": "מנקה",
            "docked": "בעמדת טעינה",
            "idle": "ממתין",
            "paused": "מושהה",
            "returning": "חוזר לעמדה",
            "error": "במצב שגיאה"
        }
        
        return translations.get(st, "במצב לא מוכר")

    def _build_response(self, text: str) -> web.Response:
        """בניית תגובת שרת בפורמט התואם לדרישות ימות המשיח."""
        safe_text = text.replace("-", " ").replace("&", " ו-").replace("=", " שווה ")
        response_text = f"id_list_message=t-{safe_text}&go_to_folder=/&"
        return web.Response(text=response_text, content_type="text/plain", charset="utf-8")
