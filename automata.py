import asyncio
import homeassistant.helpers.condition
#import threading

#from transitions import Machine

STATE_AUTO_ON = 'auto_on'
STATE_MANUAL_ON = 'manual_on'
STATE_DIM = 'dim'
STATE_OFF = 'off'
ALL_STATES = [STATE_AUTO_ON, STATE_MANUAL_ON, STATE_DIM, STATE_OFF]
AUTO_CHANGEABLE_STATES = [STATE_AUTO_ON, STATE_DIM, STATE_OFF]

EVENT_TYPE = 'type'  # <--event.data key, and below come values:
EVENT_TYPE_TOGGLE = 'toggle'
EVENT_TYPE_MANUAL_ON = 'manual_on'
EVENT_TYPE_MANUAL_OFF = 'manual_off'
EVENT_TYPE_MOVEMENT = 'movement'
EVENT_TYPE_TIMER = 'timer'


class ComplexController(object):
    def __init__(self, hass, config):
        tree_context = TreeContext(
            hass, config[CONF_ENTITY_ID],
            HassTimerHelper(hass, config[CONF_TIMER], self.on_event))
        self.dispatcher_tree = DispatcherTreeNode(hass, config[CONF_ROOT],
                                                  tree_context)
        #hass.async_block_till_done()
        hass.bus.listen(config[CONF_EVENT], self.on_event)

    def on_event(self, event):
        self.dispatcher_tree.get_dispatcher().dispatch(event)


class DispatcherTreeNode(object):
    def __init__(self, config, tree_context):
        condition_config = config.get(CONF_CONDITION)
        self.condition = homeassistant.helpers.condition.async_from_config(
            tree_context.hass,
            condition_config) if condition_config is not None else lambda: true

        dispatcher_config = config[CONF_CONFIG]
        handler_context = HandlerContext(
            tree_context, SceneController(hass, dispatcher_config))

        dispather_type = config[CONF_TYPE]
        if dispather_type == CONF_DISPATCHER_DIM:
            self.dispatcher = make_dim_dispatcher(config, handler_context)
        elif dispather_type == CONF_DISPATCHER_SIMPLE:
            self.dispatcher = make_simple_dispatcher(config, handler_context)
        else:
            raise (BaseException(
                'Unknown dispatcher type: {}'.format(dispather_type)))

        self.children = list()
        for child in config.get(CONF_OVERRIDES, []):
            self.children.append(DispatcherTreeNode(hass, child, tree_context))

    def get_dispatcher(self):
        for child in children:
            if child.condition():
                return child.get_dispatcher()
        return self.dispatcher


class HassTimerHelper(object):
    def __init__(self, hass, entity_id, callback):
        self.hass = hass
        self.entity_id = SplitId(entity_id)
        self.callback = callback
        assert hass.setup_component(hass, 'timer', entity_id)
        self.remove_listener = hass.bus.listen('timer.finished',
                                               self.on_timer_finished)

    def __del__(self):
        self.remove_listener()

    def schedule(self, delay):
        self.hass.services.async_call(self.entity_id.domain, 'timer.start', {
            ATTR_ENTITY_ID: self.entity_id.full,
            'duration': delay
        })

    def cancel(self):
        self.hass.services.async_call(self.entity_i.domain, 'timer.cancel',
                                      {ATTR_ENTITY_ID: self.entity_id.full})

    def on_timer_finished(self, event):
        if event.data[ATTR_ENTITY_ID] == self.entity_id.full:
            self.callback(
                homeassistant.core.Event(event.event_type,
                                         data={EVENT_TYPE: EVENT_TYPE_TIMER}))


class ThreadingTimerHelper(object):
    def __init__(self, callback):
        self.callback = callback
        self.handle = None

    def schedule(self, delay):
        self.handle = threading.Timer(delay, self.callback)

    def cancel(self):
        self.handle.cancel()


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

    def dispatch(self, event):
        current_state = self.handler_context.tree_context.state_controller.get(
        ).state
        event_type = get_event_type(event)
        for strategy in self.strategies:
            handler = strategy.get_handler(current_state, event_type)
            if handler is not None:
                timer.cancel()
                handler(self.handler_context, current_state, event)
                return
        print('No handler registered for transition from {} on {}'.format(
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
                self.handler_map[state, get_event_type(event)] = handler

    def get_handler(self, state, event):
        return handler_map.get((state, get_event_type(event)))


class ManualHandlerStrategy(HandlerStrategyBase):
    def __init__(self):
        HandlerStrategyBase.__init__(self)

        self.set_handler([STATE_OFF, STATE_DIM], EVENT_TYPE_TOGGLE,
                         self.turn_on)
        self.set_handler([STATE_ON, STATE_AUTO_ON], EVENT_TYPE_TOGGLE,
                         self.turn_off)
        self.set_handler(ALL_STATES, EVENT_TYPE_MANUAL_ON, self.turn_on)
        self.set_handler(ALL_STATES, EVENT_TYPE_MANUAL_OFF, self.turn_off)

    def turn_on(self, context, current_state, event):
        context.controller.turn_on()
        context.tree_context.state_controller.set(STATE_MANUAL_ON)

    def turn_off(self):
        context.controller.turn_off()
        context.tree_context.state_controller.set(STATE_OFF)


class SimpleMovementHandlerStrategy(HandlerStrategyBase):
    def __init__(self, config):
        HandlerStrategyBase.__init__(self)
        self.duration_on = config[CONF_DURATION_ON]

        self.set_handler(AUTO_CHANGEABLE_STATES, EVENT_TYPE_MOVEMENT,
                         self.turn_on)
        self.set_handler(AUTO_CHANGEABLE_STATES, EVENT_TYPE_TIMER,
                         self.turn_off)

    def turn_on(self, context, current_state, event):
        context.scene_controller.turn_on()
        context.tree_context.timer.schedule(duration_on)
        context.tree_context.state_controller.set(STATE_AUTO_ON)

    def turn_off(self, context, current_state, event):
        context.scene_controller.turn_off()
        context.tree_context.state_controller.set(STATE_OFF)


class DimMovementHandlerStrategy(SimpleMovementHandlerStrategy):
    def __init__(self, config):
        SimpleMovementHandlerStrategy.__init__(self, config)
        self.duration_dim = config.get(CONF_DURATION_DIM, self.duration_on)

        self.set_handler(AUTO_CHANGEABLE_STATES, EVENT_TYPE_MOVEMENT,
                         self.turn_on)
        self.set_handler(STATE_AUTO_ON, EVENT_TYPE_TIMER, self.turn_dim)
        self.set_handler(STATE_DIM, EVENT_TYPE_TIMER, self.turn_off)

    def turn_dim(self, context, current_state, event):
        context.scene_controller.turn_dim()
        context.tree_context.timer.schedule(duration_dim)
        context.tree_context.state_controller.set(STATE_DIM)


def make_simple_dispatcher(config, handler_context):
    dispatcher = Dispatcher(handler_context)
    dispatcher.add_strategy(ManualHandlerStrategy(config))
    dispatcher.add_strategy(SimpleMovementHandlerStrategy(config))


def make_dim_dispatcher(config, handler_context):
    dispatcher = Dispatcher(handler_context)
    dispatcher.add_strategy(ManualHandlerStrategy(config))
    dispatcher.add_strategy(DimMovementHandlerStrategy(config))


class StateController(object):
    def __init__(self, hass, entity_id):
        self.hass = hass
        self.entity_id = entity_id

    def set(self, state):
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

    def set_scene(self, scene_id):
        if scene_id is None:
            return
        self.hass.services.async_call(scene_id.domain, 'scene.turn_on',
                                      {ATTR_ENTITY_ID: scene_id.full})

    def turn_on(self):
        self.set_scene(self.scene_on)

    def turn_dim(self):
        self.set_scene(self.scene_dim)

    def turn_off(self):
        self.set_scene(self.scene_off)


class SplitId(object):
    def __ini__(self, entity_id):
        assert (entity_id is not None)
        self.domain, self.name = homeassistant.core.split_entity_id(entity_id)
        self.full = entity_id
