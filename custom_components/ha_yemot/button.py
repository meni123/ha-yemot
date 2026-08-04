"""כפתורים עבור שלוחות ימות המשיח."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ALLOWED_ACTIONS,
    ALLOWED_DOMAINS,
    CONF_ACTION,
    DOMAIN,
    SUBENTRY_TYPE_EXTENSION,
)
from .coordinator import YemotCoordinator
from .entity import YemotExtensionEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """הקמת הכפתורים עבור כל שלוחה."""
    coordinator: YemotCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_EXTENSION:
            continue
        entities: list[ButtonEntity] = [YemotRewriteButton(coordinator, subentry)]

        # שלוחה להקראת סטטוס בלבד אינה מריצה שום פעולה, ולכן כפתור
        # הבדיקה אינו נוצר עבורה במקום להופיע כפקד מושבת לצמיתות.
        if str(subentry.data.get(CONF_ACTION, "") or ""):
            entities.insert(0, YemotTestButton(coordinator, subentry))

        async_add_entities(entities, config_subentry_id=subentry_id)


class YemotTestButton(YemotExtensionEntity, ButtonEntity):
    """מריץ את פעולת השלוחה מקומית, בלי להתקשר."""

    _attr_translation_key = "test"

    def __init__(self, coordinator: YemotCoordinator, subentry: ConfigSubentry) -> None:
        """אתחול."""
        super().__init__(coordinator, subentry)
        self._attr_unique_id = f"{self._unique_prefix}_test"

    async def async_press(self) -> None:
        """הפעלת הפעולה על המכשיר המקושר."""
        if "." not in self.target_entity:
            raise HomeAssistantError("מזהה המכשיר אינו תקין")

        domain = self.target_entity.split(".", 1)[0]
        if domain not in ALLOWED_DOMAINS or self.action not in ALLOWED_ACTIONS:
            raise HomeAssistantError("הפעולה אינה נמצאת ברשימת ההיתר")

        await self.hass.services.async_call(
            domain, self.action, {"entity_id": self.target_entity}, blocking=True
        )


class YemotRewriteButton(YemotExtensionEntity, ButtonEntity):
    """כותב מחדש את הגדרת השלוחה בימות.

    נועד לשימוש לאחר שחיישן הסנכרון זיהה חריגה. הכתיבה מתבצעת
    ביוזמת המשתמש בלבד, כדי לא לדרוס בשקט עריכות ידניות.
    """

    _attr_translation_key = "rewrite"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: YemotCoordinator, subentry: ConfigSubentry) -> None:
        """אתחול."""
        super().__init__(coordinator, subentry)
        self._attr_unique_id = f"{self._unique_prefix}_rewrite"

    async def async_press(self) -> None:
        """כתיבה מחדש של השלוחה."""
        await self.coordinator.async_rewrite(self.folder)
        _LOGGER.info("השלוחה %s נכתבה מחדש", self.folder)
