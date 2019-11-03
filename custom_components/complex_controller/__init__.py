"""The complex_controller integration."""
import asyncio
import logging
import voluptuous as vol
import homeassistant.core
import homeassistant.helpers.config_validation as cv
from homeassistant.setup import setup_component
from homeassistant.const import (ATTR_ENTITY_ID, CONF_CONDITION,
                                 CONF_ENTITY_ID, CONF_TYPE, CONF_BASE)

from .const import *

BASE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_CONDITION):
        cv.CONDITION_SCHEMA,
        vol.Required(CONF_TYPE, default=CONF_DISPATCHER_DUMMY):
        vol.In((CONF_DISPATCHER_SIMPLE, CONF_DISPATCHER_DIM,
                CONF_DISPATCHER_DUMMY)),
        CONF_SCENE_ON:
        cv.entity_id,
        vol.Optional(CONF_SCENE_DIM):
        cv.entity_id,
        CONF_SCENE_OFF:
        cv.entity_id,
        CONF_DURATION_ON:
        vol.All(cv.time_period, cv.positive_timedelta),
        vol.Optional(CONF_DURATION_DIM):
        vol.All(cv.time_period, cv.positive_timedelta)
    },
    required=True)

OVERRIDER_SCHEMA = BASE_SCHEMA.extend({
    CONF_CONDITION: cv.CONDITION_SCHEMA,
    #vol.Optional(CONF_OVERRIDES): [vol.Self]
    CONF_OVERRIDES: [vol.Self]
})

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: {
            cv.string: {
                vol.Optional(CONF_TIMER):
                cv.entity_id,
                CONF_BASE:
                BASE_SCHEMA.extend(
                    {vol.Optional(CONF_OVERRIDES): [OVERRIDER_SCHEMA]})
            }
        }
    },
    extra=vol.ALLOW_EXTRA,
    required=True)

SERVICE_HANDLE_EVENT_SCHEMA = vol.Schema(
    {
        ATTR_ENTITY_ID: cv.entity_id,
        EVENT_TYPE: vol.In(ALL_EVENT_TYPES)
    },
    extra=vol.ALLOW_EXTRA,
    required=True)

controllers = dict()

_LOGGER = logging.getLogger(__name__)

def setup(hass, config):
    """Set up the complex_controller integration."""
    # try/catch?
    for name, controller_config in config[DOMAIN].items():
        controllers[f'{DOMAIN}.{name}'] = ComplexController(
            hass, name, controller_config)

    hass.services.register(DOMAIN,
                           SERVICE_HANDLE_EVENT,
                           async_on_handle_event,
                           schema=SERVICE_HANDLE_EVENT_SCHEMA)

    return True


#@homeassistant.core.callback
async def async_on_handle_event(event):
    controller_name = event.data[ATTR_ENTITY_ID]
    controller = controllers.get(controller_name)
    if controller is None:
        _LOGGER.error(
            f'Got service {SERVICE_HANDLE_EVENT} call with {ATTR_ENTITY_ID} = {controller_name} which is not set up!'
        )
        return
    await controller.dispatcher_tree.async_dispatch(event)


class ComplexController(object):
    def __init__(self, hass, name, config):
        self.hass = hass
        entity_id = f'{DOMAIN}.{name}'
        tree_context = TreeContext(
            hass, entity_id,
            HassTimerHelper(hass, config.get(CONF_TIMER, f'timer.{entity_id}'),
                            entity_id))
        asyncio.run_coroutine_threadsafe(
            tree_context.state_controller.async_set(DEFAULT_STATE), hass.loop)
        self.dispatcher_tree = DispatcherTreeNode(config[CONF_BASE],
                                                  tree_context)

    #@homeassistant.core.callback
    async def async_on_event(self, event):
        #self.hass.loop.create_task(self.dispatcher_tree.async_dispatch(event))
        await self.dispatcher_tree.async_dispatch(event)


class DispatcherTreeNode(object):
    def __init__(self, config, tree_context):
        def get_condition_from_config(condition_config):
            return asyncio.run_coroutine_threadsafe(
                homeassistant.helpers.condition.async_from_config(
                    tree_context.hass,
                    condition_config,
                    config_validation=False), tree_context.hass.loop).result()

        condition_config = config.get(CONF_CONDITION)
        self._condition = get_condition_from_config(
            condition_config) if condition_config is not None else lambda: true

        self.tree_context = tree_context

        handler_context = HandlerContext(
            tree_context, SceneController(tree_context.hass, config))

        dispather_type = config[CONF_TYPE]
        if dispather_type == CONF_DISPATCHER_DIM:
            self.dispatcher = make_dim_dispatcher(config, handler_context)
        elif dispather_type == CONF_DISPATCHER_SIMPLE:
            self.dispatcher = make_simple_dispatcher(config, handler_context)
        elif dispather_type == CONF_DISPATCHER_DUMMY:
            self.dispatcher = make_dummy_dispatcher(config, handler_context)
        else:
            raise (BaseException(
                'Unknown dispatcher type: {}'.format(dispather_type)))

        self.children = list()
        for child in config.get(CONF_OVERRIDES, []):
            self.children.append(DispatcherTreeNode(child, tree_context))

    def check_condition(self):
        return self._condition(self.tree_context.hass)

    async def async_dispatch(self, event):
        if self.check_condition():
            if not any(await asyncio.gather(*(child.async_dispatch(event)
                                              for child in self.children))):
                await self.dispatcher.async_dispatch(event)
            return True
        return False


class HassTimerHelper(object):
    def __init__(self, hass, entity_id, callback_service):
        self.hass = hass
        self.entity_id = SplitId(entity_id)
        self.callback_service = SplitId(callback_service)
        assert setup_component(hass, 'timer',
                               {'timer': {
                                   self.entity_id.name: {}
                               }})
        self.remove_listener = hass.bus.async_listen('timer.finished',
                                                     self.on_timer_finished)

    def __del__(self):
        self.remove_listener()

    async def async_schedule(self, delay):
        await self.hass.services.async_call('timer', 'start', {
            ATTR_ENTITY_ID: self.entity_id.full,
            'duration': delay
        })

    async def async_cancel(self):
        await self.hass.services.async_call(
            'timer', 'cancel', {ATTR_ENTITY_ID: self.entity_id.full})

    #@homeassistant.core.callback
    async def on_timer_finished(self, event):
        if event.data[ATTR_ENTITY_ID] == self.entity_id.full:
            await self.hass.services.async_call(
                DOMAIN, SERVICE_HANDLE_EVENT, {
                    ATTR_ENTITY_ID: self.callback_service.full,
                    EVENT_TYPE: EVENT_TYPE_TIMER
                })
            #await self.callback({EVENT_TYPE: EVENT_TYPE_TIMER})


def get_event_type(event):
    return event.data[EVENT_TYPE]


class TreeContext(object):
    def __init__(self, hass, entity_id, timer):
        self.hass = hass
        self.entity_id = entity_id
        self.timer = timer
        self.state_controller = StateController(hass, entity_id)


class HandlerContext(object):
    def __init__(self, tree_context, scene_controller):
        self.tree_context = tree_context
        self.scene_controller = scene_controller


class Dispatcher(object):
    def __init__(self, handler_context):
        self.handler_context = handler_context
        self.strategies = list()

    def add_strategy(self, strategy):
        self.strategies.append(strategy)

    async def async_dispatch(self, event):
        current_state = self.handler_context.tree_context.state_controller.get(
        ).state
        event_type = get_event_type(event)
        for strategy in self.strategies:
            handler = strategy.get_handler(current_state, event_type)
            if handler is not None:
                await self.handler_context.tree_context.timer.async_cancel()
                await handler(self.handler_context, current_state, event)
                return
        _LOGGER.debug(
            'No handler registered for transition from {} on {}'.format(
                state, get_event_type(event)))


class HandlerStrategyBase(object):
    def __init__(self):
        self.handler_map = dict()

    def set_handler(self, states, event_types, handler):
        if not isinstance(states, list):
            states = [states]
        if not isinstance(event_types, list):
            event_types = [event_types]
        for state in states:
            for event_type in event_types:
                self.handler_map[state, event_type] = handler

    def get_handler(self, state, event_type):
        return self.handler_map.get((state, event_type))


class ManualHandlerStrategy(HandlerStrategyBase):
    def __init__(self):
        HandlerStrategyBase.__init__(self)

        self.set_handler([STATE_OFF, STATE_DIM], EVENT_TYPE_TOGGLE,
                         self.async_turn_on)
        self.set_handler([STATE_AUTO_ON, STATE_MANUAL_ON], EVENT_TYPE_TOGGLE,
                         self.async_turn_off)
        self.set_handler(ALL_STATES, EVENT_TYPE_MANUAL_ON, self.async_turn_on)
        self.set_handler(ALL_STATES, EVENT_TYPE_MANUAL_OFF, self.async_turn_off)

    async def async_turn_on(self, context, current_state, event):
        await context.scene_controller.async_turn_on()
        await context.tree_context.state_controller.async_set(STATE_MANUAL_ON)

    async def async_turn_off(self, context, current_state, event):
        await context.scene_controller.async_turn_off()
        await context.tree_context.state_controller.async_set(STATE_OFF)


class SimpleMovementHandlerStrategy(HandlerStrategyBase):
    def __init__(self, config):
        HandlerStrategyBase.__init__(self)
        self.duration_on = config[CONF_DURATION_ON]

        self.set_handler(AUTO_CHANGEABLE_STATES, EVENT_TYPE_MOVEMENT,
                         self.async_turn_on)
        self.set_handler(AUTO_CHANGEABLE_STATES, EVENT_TYPE_TIMER,
                         self.async_turn_off)

    async def async_turn_on(self, context, current_state, event):
        await context.scene_controller.async_turn_on()
        await context.tree_context.timer.async_schedule(self.duration_on)
        await context.tree_context.state_controller.async_set(STATE_AUTO_ON)

    async def async_turn_off(self, context, current_state, event):
        await context.scene_controller.async_turn_off()
        await context.tree_context.state_controller.async_set(STATE_OFF)


class DimMovementHandlerStrategy(SimpleMovementHandlerStrategy):
    def __init__(self, config):
        SimpleMovementHandlerStrategy.__init__(self, config)
        self.duration_dim = config.get(CONF_DURATION_DIM, self.duration_on)

        self.set_handler(AUTO_CHANGEABLE_STATES, EVENT_TYPE_MOVEMENT,
                         self.async_turn_on)
        self.set_handler(STATE_AUTO_ON, EVENT_TYPE_TIMER, self.async_turn_dim)
        self.set_handler(STATE_DIM, EVENT_TYPE_TIMER, self.async_turn_off)

    async def async_turn_dim(self, context, current_state, event):
        await context.scene_controller.async_turn_dim()
        await context.tree_context.timer.async_schedule(self.duration_dim)
        await context.tree_context.state_controller.async_set(STATE_DIM)


def make_simple_dispatcher(config, handler_context):
    dispatcher = Dispatcher(handler_context)
    dispatcher.add_strategy(ManualHandlerStrategy())
    dispatcher.add_strategy(SimpleMovementHandlerStrategy(config))
    return dispatcher


def make_dim_dispatcher(config, handler_context):
    dispatcher = Dispatcher(handler_context)
    dispatcher.add_strategy(ManualHandlerStrategy())
    dispatcher.add_strategy(DimMovementHandlerStrategy(config))
    return dispatcher


def make_dummy_dispatcher(config, handler_context):
    dispatcher = Dispatcher(handler_context)
    return dispatcher


class StateController(object):
    def __init__(self, hass, entity_id):
        self.hass = hass
        self.entity_id = entity_id

    async def async_set(self, state):
        self.hass.states.async_set(self.entity_id, state)

    def get(self):
        return self.hass.states.get(self.entity_id)


class SceneController(object):
    def __init__(self, hass, config):
        self.hass = hass

        def get_scene(conf_key):
            entity_id = config.get(conf_key)
            if entity_id is None:
                return None
            return SplitId(entity_id)

        self.scene_on = get_scene(CONF_SCENE_ON)
        self.scene_dim = get_scene(CONF_SCENE_DIM)
        self.scene_off = get_scene(CONF_SCENE_OFF)

    async def async_set_scene(self, scene_id):
        if scene_id is None:
            return
        await self.hass.services.async_call('scene', 'turn_on',
                                            {ATTR_ENTITY_ID: scene_id.full})

    async def async_turn_on(self):
        await self.async_set_scene(self.scene_on)

    async def async_turn_dim(self):
        await self.async_set_scene(self.scene_dim)

    async def async_turn_off(self):
        await self.async_set_scene(self.scene_off)


class SplitId(object):
    def __init__(self, entity_id):
        assert (entity_id is not None)
        self.domain, self.name = homeassistant.core.split_entity_id(entity_id)
        self.full = entity_id
