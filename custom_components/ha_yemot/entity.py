"""מחלקת בסיס לישויות של אינטגרציית ימות המשיח."""

from __future__ import annotations

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ACTION, CONF_FOLDER, CONF_TARGET_ENTITY, DOMAIN
from .coordinator import YemotCoordinator


class YemotExtensionEntity(CoordinatorEntity[YemotCoordinator]):
    """בסיס לישות המשויכת לשלוחה בודדת.

    כל שלוחה מקבלת התקן משלה, המקושר להתקן הראשי של המערכת.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: YemotCoordinator,
        subentry: ConfigSubentry,
    ) -> None:
        """אתחול הישות."""
        super().__init__(coordinator)
        self._subentry = subentry
        self._data = dict(subentry.data)
        self.folder = str(self._data.get(CONF_FOLDER, "")).strip("/")
        self.target_entity = str(self._data.get(CONF_TARGET_ENTITY, ""))
        self.action = str(self._data.get(CONF_ACTION, "") or "")

        entry_id = coordinator.entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{subentry.subentry_id}")},
            name=subentry.title,
            manufacturer="ימות המשיח",
            model="שלוחת API",
            via_device=(DOMAIN, entry_id),
            configuration_url="https://www.call2all.co.il",
        )

    @property
    def _unique_prefix(self) -> str:
        """תחילית קבועה למזהה הייחודי של הישות."""
        return f"{self.coordinator.entry.entry_id}_{self._subentry.subentry_id}"

    @property
    def status(self):
        """מצב הסנכרון האחרון של השלוחה, אם נבדק."""
        return (self.coordinator.data or {}).get(self.folder)
