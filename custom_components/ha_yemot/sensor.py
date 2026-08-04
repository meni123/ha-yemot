"""חיישנים עבור שלוחות ימות המשיח."""

from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_CALL_RECEIVED, SUBENTRY_TYPE_EXTENSION
from .coordinator import YemotCoordinator
from .entity import YemotExtensionEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """הקמת החיישנים עבור כל שלוחה מוגדרת."""
    coordinator: YemotCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_TYPE_EXTENSION:
            continue
        async_add_entities(
            [
                YemotLastCallSensor(coordinator, subentry),
                YemotCallCountSensor(coordinator, subentry),
            ],
            config_subentry_id=subentry_id,
        )


class _CallTrackingSensor(YemotExtensionEntity, SensorEntity):
    """בסיס לחיישן המתעדכן בעת קבלת שיחה לשלוחה.

    חשוב: אין להוסיף כאן את RestoreEntity. המחלקה RestoreSensor
    יורשת מ-SensorEntity ואחר כך מ-RestoreEntity, ולכן הוספה כאן
    בסדר ההפוך יוצרת סדר ירושה בלתי אפשרי וכשל בייבוא המודול.
    כל תת-מחלקה מצרפת את מחלקת השחזור המתאימה לה בעצמה.
    """

    async def async_added_to_hass(self) -> None:
        """הרשמה לאות השיחות הנכנסות."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CALL_RECEIVED, self._handle_call
            )
        )

    @callback
    def _handle_call(self, folder: str, entity_id: str, action: str) -> None:
        """טיפול בשיחה נכנסת, אם היא שייכת לשלוחה הזו."""
        # התאמה לפי נתיב השלוחה. קישורים ישנים אינם כוללים את הפרמטר,
        # ולכן קיימת נפילה חזרה להתאמה לפי מכשיר ופעולה.
        if folder:
            if folder.strip("/") != self.folder:
                return
        elif entity_id != self.target_entity or (action or "") != self.action:
            return

        self._on_call()
        self.async_write_ha_state()

    def _on_call(self) -> None:
        """מימוש בתת-המחלקות."""
        raise NotImplementedError


class YemotLastCallSensor(_CallTrackingSensor, RestoreSensor):
    """מועד השיחה האחרונה שהתקבלה לשלוחה."""

    _attr_translation_key = "last_call"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: YemotCoordinator, subentry: ConfigSubentry) -> None:
        """אתחול."""
        super().__init__(coordinator, subentry)
        self._attr_unique_id = f"{self._unique_prefix}_last_call"
        self._value: datetime | None = None

    async def async_added_to_hass(self) -> None:
        """שחזור הערך האחרון לאחר הפעלה מחדש."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            if isinstance(last.native_value, datetime):
                self._value = last.native_value

    @property
    def native_value(self) -> datetime | None:
        """הערך המוצג."""
        return self._value

    def _on_call(self) -> None:
        """עדכון חותמת הזמן."""
        self._value = dt_util.utcnow()


class YemotCallCountSensor(_CallTrackingSensor, RestoreEntity):
    """מספר השיחות שהתקבלו לשלוחה."""

    _attr_translation_key = "call_count"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "שיחות"

    def __init__(self, coordinator: YemotCoordinator, subentry: ConfigSubentry) -> None:
        """אתחול."""
        super().__init__(coordinator, subentry)
        self._attr_unique_id = f"{self._unique_prefix}_call_count"
        self._count = 0

    async def async_added_to_hass(self) -> None:
        """שחזור המונה לאחר הפעלה מחדש."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            try:
                self._count = int(float(last.state))
            except (TypeError, ValueError):
                self._count = 0

    @property
    def native_value(self) -> int:
        """הערך המוצג."""
        return self._count

    def _on_call(self) -> None:
        """הגדלת המונה."""
        self._count += 1
