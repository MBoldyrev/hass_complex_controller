"""The complex_controller integration."""
import asyncio
import logging
import voluptuous as vol
import homeassistant.core
import homeassistant.helpers.config_validation as cv
from homeassistant.setup import async_setup_component
from homeassistant.const import (ATTR_ENTITY_ID, CONF_CONDITION,
                                 CONF_ENTITY_ID, CONF_TYPE, CONF_BASE)

from .const import *

ACTION_SERVICE_SCHEMA = vol.Schema(
    {
        CONF_SERVICE:
        cv.entity_id,
        vol.Required(CONF_SERVICE_DATA, default=dict()):
        vol.Schema({}, extra=vol.ALLOW_EXTRA)
    },
    required=True)

ACTION_SCENE_SCHEMA = vol.Schema({CONF_SCENE: cv.entity_id}, required=True)

ACTION_SCHEMA = vol.Any(ACTION_SERVICE_SCHEMA, ACTION_SCENE_SCHEMA)

ONE_OR_MANY_ACTIONS_TO_LIST = vol.Any([ACTION_SCHEMA],
                                      vol.All(ACTION_SCHEMA, lambda a: [a]))

BASE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_CONDITION):
        cv.CONDITION_SCHEMA,
        vol.Required(CONF_TYPE, default=CONF_DISPATCHER_DUMMY):
        vol.In((CONF_DISPATCHER_SIMPLE, CONF_DISPATCHER_DIM,
                CONF_DISPATCHER_DUMMY)),
        vol.Required(CONF_ACTIONS_ON, default=[]):
        ONE_OR_MANY_ACTIONS_TO_LIST,
        vol.Required(CONF_ACTIONS_DIM, default=[]):
        ONE_OR_MANY_ACTIONS_TO_LIST,
        vol.Required(CONF_ACTIONS_OFF, default=[]):
        ONE_OR_MANY_ACTIONS_TO_LIST,
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
        ATTR_CONTROLLER: cv.string,
        ATTR_EVENT_TYPE: vol.In(ALL_EVENT_TYPES)
    },
    extra=vol.ALLOW_EXTRA,
    required=True)

controllers = dict()

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass, config):
    """Set up the complex_controller integration."""
    # try/catch?
    for name, controller_config in config[DOMAIN].items():
        controllers[name] = await ComplexController.create(
            hass, name, controller_config)

    hass.services.async_register(DOMAIN,
                                 SERVICE_HANDLE_EVENT,
                                 async_on_handle_event,
                                 schema=SERVICE_HANDLE_EVENT_SCHEMA)

    return True


#@homeassistant.core.callback
async def async_on_handle_event(event):
    controller_name = event.data[ATTR_CONTROLLER]
    controller = controllers.get(controller_name)
    if controller is None:
        _LOGGER.error(
            f'Got service {SERVICE_HANDLE_EVENT} call with {ATTR_CONTROLLER} = {controller_name} which is not set up!'
        )
        return
    await controller.dispatcher_tree.async_dispatch(event)


class ComplexController(object):
    @staticmethod
    async def create(hass, name, config):
        new_controller = ComplexController()
        new_controller.hass = hass
        entity_id = f'{DOMAIN}.{name}'
        tree_context = TreeContext(
            hass, entity_id, await HassTimerHelper.create(
                hass, config.get(CONF_TIMER, f'timer.{entity_id}'), name))
        await tree_context.state_controller.async_set(DEFAULT_STATE)
        new_controller.dispatcher_tree = await DispatcherTreeNode.create(
            config[CONF_BASE], tree_context)
        return new_controller


class DispatcherTreeNode(object):
    @staticmethod
    async def create(config, tree_context):
        new_obj = DispatcherTreeNode()

        async def get_condition_from_config(condition_config):
            return await homeassistant.helpers.condition.async_from_config(
                tree_context.hass, condition_config, config_validation=False)

        condition_config = config.get(CONF_CONDITION)
        new_obj._condition = await get_condition_from_config(
            condition_config
        ) if condition_config is not None else lambda hass: True

        new_obj.tree_context = tree_context

        handler_context = HandlerContext(
            tree_context, ActionController(tree_context.hass, config))

        dispather_type = config[CONF_TYPE]
        if dispather_type == CONF_DISPATCHER_DIM:
            new_obj.dispatcher = make_dim_dispatcher(config, handler_context)
        elif dispather_type == CONF_DISPATCHER_SIMPLE:
            new_obj.dispatcher = make_simple_dispatcher(
                config, handler_context)
        elif dispather_type == CONF_DISPATCHER_DUMMY:
            new_obj.dispatcher = make_dummy_dispatcher(config, handler_context)
        else:
            raise (BaseException(
                'Unknown dispatcher type: {}'.format(dispather_type)))

        new_obj.children = list()
        for child in config.get(CONF_OVERRIDES, []):
            new_obj.children.append(await DispatcherTreeNode.create(
                child, tree_context))
        return new_obj

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
    @staticmethod
    async def create(hass, entity_id, controller_name):
        new_obj = HassTimerHelper()
        new_obj.hass = hass
        new_obj.entity_id = SplitId(entity_id)
        new_obj.controller_name = controller_name
        assert await async_setup_component(
            hass, 'timer', {'timer': {
                new_obj.entity_id.name: {}
            }})
        new_obj.remove_listener = hass.bus.async_listen(
            'timer.finished', new_obj.on_timer_finished)
        return new_obj

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
                    ATTR_CONTROLLER: self.controller_name,
                    ATTR_EVENT_TYPE: EVENT_TYPE_TIMER
                })


def get_event_type(event):
    return event.data[ATTR_EVENT_TYPE]


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
                current_state, get_event_type(event)))


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
        self.set_handler(ALL_STATES, EVENT_TYPE_MANUAL_OFF,
                         self.async_turn_off)

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


class ActionController(object):
    class ServiceCaller(object):
        def __init__(self, hass, config):
            self.hass = hass
            self.service = SplitId(config[CONF_SERVICE])
            self.service_data = config[CONF_SERVICE_DATA]

        async def act(self):
            await self.hass.services.async_call(self.service.domain,
                                                self.service.name,
                                                self.service_data)

    class SceneTurner(object):
        def __init__(self, hass, config):
            self.hass = hass
            self.scene = config[CONF_SCENE]

        async def act(self):
            await self.hass.services.async_call('scene', 'turn_on',
                                                {ATTR_ENTITY_ID: self.scene})

    def __init__(self, hass, config):
        self.hass = hass

        def get_actions(conf_key):
            action_configs = config.get(conf_key)
            actions = []
            for action_config in action_configs:
                if check_schema(ACTION_SERVICE_SCHEMA, action_config):
                    actions.append(
                        ActionController.ServiceCaller(hass, action_config))
                elif check_schema(ACTION_SCENE_SCHEMA, action_config):
                    actions.append(
                        ActionController.SceneTurner(hass, action_config))
                else:
                    _LOGGER.error(
                        f'Cound not initialize action for {conf_key}.')
            return actions

        self.actions_on = get_actions(CONF_ACTIONS_ON)
        self.actions_dim = get_actions(CONF_ACTIONS_DIM)
        self.actions_off = get_actions(CONF_ACTIONS_OFF)

    async def async_do_actions(self, actions):
        await asyncio.gather(*(action.act() for action in actions))

    async def async_turn_on(self):
        await self.async_do_actions(self.actions_on)

    async def async_turn_dim(self):
        await self.async_do_actions(self.actions_dim)

    async def async_turn_off(self):
        await self.async_do_actions(self.actions_off)


def check_schema(schema, value):
    try:
        schema(value)
        return True
    except vol.Invalid:
        return False

class SplitId(object):
    def __init__(self, entity_id):
        assert (entity_id is not None)
        self.domain, self.name = homeassistant.core.split_entity_id(entity_id)
        self.full = entity_id
