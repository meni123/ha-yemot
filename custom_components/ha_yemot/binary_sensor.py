"""חיישן בינארי המדווח על חריגה בהגדרת השלוחה."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SUBENTRY_TYPE_EXTENSION
from .coordinator import YemotCoordinator
from .entity import YemotExtensionEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """הקמת חיישן הסנכרון עבור כל שלוחה."""
    coordinator: YemotCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_EXTENSION:
            continue
        async_add_entities(
            [YemotSyncProblemSensor(coordinator, subentry)],
            config_subentry_id=subentry_id,
        )


class YemotSyncProblemSensor(YemotExtensionEntity, BinarySensorEntity):
    """דולק כאשר ההגדרה בימות אינה תואמת למה שהתוסף כתב.

    זה תופס שלושה מצבים: עריכה ידנית של השלוחה באתר ימות,
    החלפת טוקן האבטחה, ומחיקה של השלוחה.
    """

    _attr_translation_key = "sync_problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: YemotCoordinator, subentry: ConfigSubentry) -> None:
        """אתחול."""
        super().__init__(coordinator, subentry)
        self._attr_unique_id = f"{self._unique_prefix}_sync_problem"

    @property
    def is_on(self) -> bool | None:
        """האם קיימת חריגה."""
        status = self.status
        if status is None:
            return None
        return not status.in_sync

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """פירוט לצורכי אבחון."""
        status = self.status
        if status is None:
            return {}

        if not status.exists:
            reason = "השלוחה אינה קיימת בימות או שאין בה קובץ הגדרות"
        elif status.in_sync:
            reason = "תקין"
        elif status.actual_link is None:
            reason = "קובץ ההגדרות אינו מכיל קישור"
        else:
            reason = "הקישור בימות שונה מהקישור הצפוי"

        attributes = {"שלוחה": status.folder, "מצב": reason}
        if status.error:
            attributes["שגיאה"] = status.error
        return attributes
