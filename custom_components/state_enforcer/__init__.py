"""The complex_controller integration."""
import asyncio
import logging
import voluptuous as vol
import homeassistant.core
import homeassistant.helpers.config_validation as cv
from homeassistant.setup import async_setup_component
from homeassistant.const import (ATTR_ENTITY_ID, ATTR_SERVICE,
                                 ATTR_SERVICE_DATA, ATTR_STATE, CONF_ENTITY_ID,
                                 EVENT_STATE_CHANGED)

from .const import *

SLEEP_BASE = 3
SLEEP_MULTIPLIER = 1.5
SLEEP_MAX = 180

CONFIG_SCHEMA = vol.Schema({DOMAIN: [cv.entity_id]}, extra=vol.ALLOW_EXTRA)

SERVICE_SET_STATE_SCHEMA = vol.Schema(
    {
        ATTR_ENTITY_ID: cv.entity_id,
        ATTR_SERVICE: cv.entity_id,
        vol.Optional(ATTR_SERVICE_DATA): dict(),
        ATTR_STATE: cv.string,
        vol.Optional(ATTR_STATE_ATTRIBUTES): dict()
    },
    extra=vol.ALLOW_EXTRA,
    required=True)

state_enforcers = dict()

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass, config):
    """Set up the complex_controller integration."""
    # try/catch?
    for entity_id in config[DOMAIN]:
        state_enforcers[entity_id] = await StateEnforcer.create(
            hass, entity_id)

    hass.services.async_register(DOMAIN,
                                 SERVICE_SET_STATE,
                                 async_set_state,
                                 schema=SERVICE_SET_STATE_SCHEMA)

    hass.bus.async_listen(EVENT_STATE_CHANGED, async_on_state_changed)

    return True


async def async_set_state(event):
    entity_id = event.data[ATTR_ENTITY_ID]
    state_enforcer = state_enforcers.get(entity_id)
    if state_enforcer is None:
        _LOGGER.error(
            f'Got service {SERVICE_SET_STATE} call with {ATTR_ENTITY_ID} = {entity_id} which is not set up!'
        )
        return
    await state_enforcer.handle_enforce_state(event)


async def async_on_state_changed(event):
    state_enforcer = state_enforcers.get(event.data[ATTR_ENTITY_ID])
    if state_enforcer is None:
        return
    await state_enforcer.async_on_state_changed(event)


class StateEnforcer(object):
    @staticmethod
    async def create(hass, entity_id):
        new_obj = StateEnforcer()
        new_obj.hass = hass
        new_obj.entity_id = entity_id

        new_obj.service = None
        new_obj.service_data = dict()
        new_obj.state = None
        new_obj.state_attrs = dict()
        new_obj.retry_number = 0
        return new_obj

    async def async_on_state_changed(self, event):
        await self.check_current_state()

    async def handle_enforce_state(self, event):
        self.service = SplitId(event.data[ATTR_SERVICE])
        self.service_data = event.data.get(ATTR_SERVICE_DATA, dict())
        self.state = event.data[ATTR_STATE]
        self.state_attrs = event.data.get(ATTR_STATE_ATTRIBUTES, dict())
        self.retry_number = 0
        await self.enforce()

    async def check_current_state(self):
        current_state = self.hass.states.get(self.entity_id)
        if self.state is None:
            return
        if current_state.state != self.state or any(
                current_state.attributes.get(k) != v
                for k, v in self.state_attrs.items()):
            await self.enforce()

    async def enforce(self):
        if self.service is None:
            _LOGGER.error(
                f'Cannot enforce state {self.state} with attrs {self.state_attrs} because the service call is not set.'
            )
        await self.hass.services.async_call(self.service.domain,
                                            self.service.name,
                                            self.service_data)
        await asyncio.sleep(self.get_sleep_delay())
        await self.check_current_state()

    def get_sleep_delay(self):
        return min(SLEEP_BASE + self.retry_number * SLEEP_MULTIPLIER,
                   SLEEP_MAX)


class SplitId(object):
    def __init__(self, entity_id):
        assert (entity_id is not None)
        self.domain, self.name = homeassistant.core.split_entity_id(entity_id)
        self.full = entity_id
