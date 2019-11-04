"""The state_enforcer integration."""
import asyncio
import logging
import voluptuous as vol
import homeassistant.core
import homeassistant.helpers.config_validation as cv
from homeassistant.setup import async_setup_component
from homeassistant.const import (ATTR_ENTITY_ID, ATTR_SERVICE,
                                 ATTR_SERVICE_DATA, ATTR_STATE, CONF_ENTITY_ID,
                                 EVENT_STATE_CHANGED, STATE_ON, STATE_OFF)

from .const import *

SLEEP_BASE = 3
SLEEP_MULTIPLIER = 1.5
SLEEP_MAX = 180

CONFIG_SCHEMA = vol.Schema({DOMAIN: [cv.entity_id]}, extra=vol.ALLOW_EXTRA)

LIGHT_SERVICES = {STATE_ON: 'light.turn_on', STATE_OFF: 'light.turn_off'}

ONE_OR_MANY_ENTITIES_TO_LIST = vol.Any([cv.entity_id],
                                       vol.All(cv.entity_id, lambda e: [e]))

SERVICE_SET_STATE_SCHEMA = vol.Schema(
    {
        ATTR_ENTITY_ID: cv.entity_id,
        ATTR_SERVICE: ONE_OR_MANY_ENTITIES_TO_LIST,
        vol.Optional(ATTR_SERVICE_DATA): dict(),
        ATTR_STATE: cv.string,
        vol.Optional(ATTR_STATE_ATTRIBUTES): dict()
    },
    extra=vol.ALLOW_EXTRA,
    required=True)

SERVICE_SET_LIGHT_SCHEMA = vol.Schema(
    {
        ATTR_ENTITY_ID: ONE_OR_MANY_ENTITIES_TO_LIST,
        ATTR_STATE: vol.In(LIGHT_SERVICES),
        vol.Optional(ATTR_BRIGHTNESS): int
    },
    extra=vol.ALLOW_EXTRA,
    required=True)

state_enforcers = dict()

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass, config):
    """Set up the state_enforcer integration."""
    # try/catch?
    for entity_id in config[DOMAIN]:
        state_enforcers[entity_id] = await StateEnforcer.create(
            hass, entity_id)

    register_service_handler(hass, SERVICE_SET_STATE, async_set_state,
                             SERVICE_SET_STATE_SCHEMA)
    register_service_handler(hass, SERVICE_SET_LIGHT, async_set_light,
                             SERVICE_SET_LIGHT_SCHEMA)

    hass.bus.async_listen(EVENT_STATE_CHANGED, async_on_state_changed)

    return True


def register_service_handler(hass, service_name, handler, schema):
    async def service_handler_wrapper(event):
        selected_enforcers = []
        for entity_id in event.data[ATTR_ENTITY_ID]:
            state_enforcer = state_enforcers.get(entity_id)
            if state_enforcer is None:
                _LOGGER.error(f'Got service {service_name} call '
                              f'with {ATTR_ENTITY_ID} = {entity_id} '
                              'which is not set up!')
            else:
                selected_enforcers.append(state_enforcer)
        await asyncio.gather(*(handler(state_enforcer, event)
                               for state_enforcer in selected_enforcers))

    hass.services.async_register(DOMAIN,
                                 service_name,
                                 service_handler_wrapper,
                                 schema=schema)


async def async_set_state(state_enforcer, event):
    await state_enforcer.handle_enforce_state(
        service=event.data[ATTR_SERVICE],
        service_data=event.data.get(ATTR_SERVICE_DATA, dict()),
        state=event.data[ATTR_STATE],
        state_attrs=event.data.get(ATTR_STATE_ATTRIBUTES, dict()))


async def async_set_light(state_enforcer, event):
    on_off = event.data[ATTR_STATE]
    state_attrs = dict()
    if event.data.get(ATTR_BRIGHTNESS) is not None:
        state_attrs[ATTR_BRIGHTNESS] = event.data.get(ATTR_BRIGHTNESS)
    service_data = state_attrs.copy()
    service_data[ATTR_ENTITY_ID] = event.data[ATTR_ENTITY_ID]
    await state_enforcer.handle_enforce_state(service=LIGHT_SERVICES[on_off],
                                              service_data=service_data,
                                              state=on_off,
                                              state_attrs=state_attrs)


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

    async def handle_enforce_state(self, service, service_data, state, state_attrs):
        self.service = SplitId(service)
        self.service_data = service_data
        self.state = state
        self.state_attrs = state_attrs
        self.retry_number = 0
        await self.enforce()

    async def check_current_state(self):
        current_state = self.hass.states.get(self.entity_id)
        if self.state is None:
            return
        if current_state.state != self.state or any(
                current_state.attributes.get(k) != v
                for k, v in self.state_attrs.items()):
            self.retry_number += 1
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
