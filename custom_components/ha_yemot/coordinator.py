"""מתאם הנתונים של אינטגרציית ימות המשיח.

אחראי על בדיקת הסנכרון: האם קובץ ההגדרות של כל שלוחה מנוהלת
עדיין תואם למה שהתוסף כתב, או שמישהו ערך אותו ידנית באתר ימות.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import YemotClient
from .const import (
    CONF_ACTION,
    CONF_FOLDER,
    CONF_TARGET_ENTITY,
    DOMAIN,
    SUBENTRY_TYPE_EXTENSION,
    SYNC_SCAN_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ExtensionStatus:
    """מצב הסנכרון של שלוחה בודדת."""

    folder: str
    in_sync: bool
    exists: bool
    expected_link: str
    actual_link: str | None
    error: str | None = None


class YemotCoordinator(DataUpdateCoordinator[dict[str, ExtensionStatus]]):
    """בודק מעת לעת שהשלוחות המנוהלות עדיין מוגדרות כראוי."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: YemotClient,
        external_url: str,
        api_token: str,
    ) -> None:
        """אתחול המתאם."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=SYNC_SCAN_INTERVAL_MINUTES),
        )
        self.entry = entry
        self.client = client
        self.external_url = external_url.rstrip("/")
        self.api_token = api_token

    # ------------------------------------------------------------------
    # בניית הקישור
    # ------------------------------------------------------------------

    def build_api_link(self, folder: str, entity_id: str, action: str) -> str:
        """בניית הקישור שיוטמע בשלוחה.

        פרמטר השלוחה מצורף כדי שהתוסף יוכל לזהות בוודאות איזו שלוחה
        הופעלה, גם כאשר שתי שלוחות מצביעות על אותו מכשיר ואותה פעולה.
        """
        suffix = f"/{action}" if action else ""
        return (
            f"{self.external_url}/api/yemot/{self.api_token}/"
            f"{entity_id}{suffix}?ext={folder}"
        )

    def managed_extensions(self) -> dict[str, dict[str, str]]:
        """מיפוי של נתיב שלוחה לנתוני תת-הרשומה שלה."""
        result: dict[str, dict[str, str]] = {}
        for subentry in self.entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_TYPE_EXTENSION:
                continue
            data = dict(subentry.data)
            folder = str(data.get(CONF_FOLDER, "")).strip("/")
            if folder:
                result[folder] = {
                    CONF_FOLDER: folder,
                    CONF_TARGET_ENTITY: str(data.get(CONF_TARGET_ENTITY, "")),
                    CONF_ACTION: str(data.get(CONF_ACTION, "") or ""),
                }
        return result

    # ------------------------------------------------------------------
    # מחזור העדכון
    # ------------------------------------------------------------------

    async def _async_read_settings(self, folder: str) -> dict[str, str] | None:
        """קריאת הגדרות השלוחה מהקובץ הגולמי.

        הקובץ הגולמי מכיל בדיוק את מה שנכתב, בעוד שההגדרות
        המפוענחות כוללות גם ערכי ברירת מחדל של המערכת.
        """
        raw = await self.client.async_read_raw_ext_ini(folder)
        if raw is None:
            return None

        settings: dict[str, str] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")) or "=" not in line:
                continue
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip()
        return settings

    async def _async_update_data(self) -> dict[str, ExtensionStatus]:
        """בדיקת מצב הסנכרון של כל השלוחות המנוהלות."""
        statuses: dict[str, ExtensionStatus] = {}

        for folder, info in self.managed_extensions().items():
            expected = self.build_api_link(
                folder, info[CONF_TARGET_ENTITY], info[CONF_ACTION]
            )
            try:
                ext_ini = await self._async_read_settings(folder)
            except Exception as err:  # noqa: BLE001 - כשל בשלוחה אחת לא יפיל את השאר
                _LOGGER.debug("בדיקת השלוחה %s נכשלה: %s", folder, err)
                statuses[folder] = ExtensionStatus(
                    folder=folder,
                    in_sync=False,
                    exists=False,
                    expected_link=expected,
                    actual_link=None,
                    error=str(err),
                )
                continue

            if not ext_ini:
                # השלוחה אינה קיימת, או שקובץ ההגדרות שלה ריק.
                statuses[folder] = ExtensionStatus(
                    folder=folder,
                    in_sync=False,
                    exists=False,
                    expected_link=expected,
                    actual_link=None,
                )
                continue

            actual = str(ext_ini.get("api_link", "")) or None
            statuses[folder] = ExtensionStatus(
                folder=folder,
                in_sync=actual == expected,
                exists=True,
                expected_link=expected,
                actual_link=actual,
            )

        return statuses

    async def async_rewrite(self, folder: str) -> None:
        """כתיבה מחדש של שלוחה, לאחר שזוהתה חריגה."""
        info = self.managed_extensions().get(folder)
        if not info:
            return
        link = self.build_api_link(
            folder, info[CONF_TARGET_ENTITY], info[CONF_ACTION]
        )
        await self.client.async_write_extension(folder, link)
        await self.async_request_refresh()
