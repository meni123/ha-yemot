"""Constants for the Yemot HaMashiach integration."""

from typing import Final

DOMAIN: Final = "ha_yemot"

# --- מפתחות הגדרה ---
CONF_EXTERNAL_URL: Final = "external_url"
CONF_API_TOKEN: Final = "api_token"
CONF_MANAGER_TOKEN: Final = "yemot_manager_token"
CONF_ALLOWED_IPS: Final = "allowed_ips"
CONF_ALLOWED_PHONES: Final = "allowed_phones"

# --- כתובות ה-API של ימות המשיח ---
YEMOT_API_BASE_URL: Final = "https://www.call2all.co.il/ym/api"
UPDATE_EXTENSION_URL: Final = f"{YEMOT_API_BASE_URL}/UpdateExtension"
UPLOAD_TEXT_FILE_URL: Final = f"{YEMOT_API_BASE_URL}/UploadTextFile"
RUN_TZINTUK_URL: Final = f"{YEMOT_API_BASE_URL}/RunTzintuk"
SEND_TTS_URL: Final = f"{YEMOT_API_BASE_URL}/SendTTS"
GET_CUSTOMER_DATA_URL: Final = f"{YEMOT_API_BASE_URL}/GetCustomerData"

# --- קריאות API נוספות, לסריקה ולבדיקת סנכרון ---
GET_IVR2_DIR_URL: Final = f"{YEMOT_API_BASE_URL}/GetIVR2Dir"
GET_TEXT_FILE_URL: Final = f"{YEMOT_API_BASE_URL}/GetTextFile"

# --- תת-רשומות ---
SUBENTRY_TYPE_EXTENSION: Final = "extension"
CONF_FOLDER: Final = "folder"
CONF_TARGET_ENTITY: Final = "entity_id"
CONF_ACTION: Final = "action"

# החתימה שמסמנת שלוחה כמנוהלת על ידי התוסף.
# בלעדיה אי אפשר להבחין בין שלוחה שלנו לבין תפריט של המשתמש,
# ולכן היא תנאי מקדים לזיהוי, לסריקה ולבדיקת הסנכרון.
MANAGED_SIGNATURE: Final = "# managed-by-ha-yemot"

# --- תזמונים ---
# בדיקת סנכרון מול ימות. אין לקצר את זה משמעותית:
# כל בדיקה עולה קריאת API אחת לכל שלוחה מנוהלת.
SYNC_SCAN_INTERVAL_MINUTES: Final = 30
# תוקף המטמון של סריקת השלוחות עבור הבורר בטופס.
# קצר בכוונה, כדי ששינוי או מחיקה ישתקפו בבורר כמעט מיד.
PICKER_CACHE_SECONDS: Final = 45
# עומק הסריקה עבור הבורר. שלוש רמות מכסות מבנים כמו 1/2/3.
# כל רמה נוספת מכפילה בערך את מספר קריאות ה-API בפתיחת הטופס,
# ולכן התוצאה נשמרת במטמון לפי PICKER_CACHE_SECONDS.
PICKER_SCAN_DEPTH: Final = 3

# --- אותות פנימיים ---
SIGNAL_CALL_RECEIVED: Final = f"{DOMAIN}_call_received"
EVENT_CALL_RECEIVED: Final = f"{DOMAIN}_call_received"


# טווח ה-IP של שרתי ימות המשיח (נכון למועד הכתיבה).
# אם ימות משנים טווחים - זה המקום היחיד לעדכן.
DEFAULT_ALLOWED_IPS: Final = "2a13:8140:1::/48"

# --- שמות שירותים ---
SERVICE_CREATE_EXTENSION: Final = "create_extension"
SERVICE_SEND_TZINTUK: Final = "send_tzintuk"
SERVICE_SEND_TTS: Final = "send_tts"

# --- רשימות היתר לפעולות מרחוק ---
# רק דומיינים ופעולות שמופיעים כאן ניתנים להפעלה דרך שיחת טלפון.
# זו שכבת ההגנה האחרונה: גם מי שהשיג את הטוקן לא יוכל לקרוא
# ל-shell_command, homeassistant.restart, hassio.host_shutdown וכדומה.
ALLOWED_DOMAINS: Final = frozenset(
    {
        "automation",
        "button",
        "climate",
        "cover",
        "fan",
        "humidifier",
        "input_boolean",
        "light",
        "lock",
        "media_player",
        "scene",
        "script",
        "siren",
        "switch",
        "vacuum",
        "water_heater",
    }
)

ALLOWED_ACTIONS: Final = frozenset(
    {
        "turn_on",
        "turn_off",
        "toggle",
        "open_cover",
        "close_cover",
        "stop_cover",
        "lock",
        "unlock",
        "start",
        "pause",
        "stop",
        "return_to_base",
        "press",
        "trigger",
    }
)

# --- מגבלות והגדרות זמן ---
# כמה זמן להמתין לעדכון מצב הישות לפני שמקריאים תשובה למתקשר.
STATE_CHANGE_TIMEOUT: Final = 3.0
# מגבלת אורך של הטקסט המוקרא, כדי לא לשבור את פורמט התגובה של ימות.
MAX_RESPONSE_LENGTH: Final = 250
# timeout לקריאות יוצאות מול שרתי ימות.
API_TIMEOUT: Final = 30
