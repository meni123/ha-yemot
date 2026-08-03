import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

DOMAIN = "ha_yemot"

class YemotConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            return self.async_create_entry(title="ימות המשיח", data=user_input)

        data_schema = vol.Schema({
            vol.Required("external_url", default="https://YOUR_HA_DOMAIN"): str,
            vol.Required("api_token", default="123456"): str,
            vol.Required("yemot_manager_token", default="0771234567:123456"): str,
            vol.Optional("allowed_ips", default="2a13:8140:1::/48"): str
        })

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )
