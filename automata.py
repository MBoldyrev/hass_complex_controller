import homeassistant.helpers.condition
import threading

from transitions import Machine


class DispatcherTreeNode(object):
    def __init__(self, config, is_root_node=True):
        if not is_root_node:
            self.condition = homeassistant.helpers.condition.async_from_config(
                hass, config.get(CONF_CONDITIONS))

        dispather_type = config.get(CONF_DISPATCHER)
        if dispather_type == CONF_DISPATCHER_LIGHT_DAY:
            self.dispatcher = LightControllerDay(config)
        elif dispather_type == CONF_DISPATCHER_LIGHT_NIGHT:
            self.dispatcher = LightControllerNight(config)

        self.children = list()
        for child in config.get(CONF_OVERRIDES, []):
            self.children.append(DispatcherTreeNode(child, False))

    def get_dispatcher(self):
        for child in children:
            if child.condition():
                return child.get_dispatcher()
        return self.dispatcher


class HassTimerHelper(object):
    def __init__(self, hass, entity_id, callback):
        self.hass = hass
        self.entity_id = entity_id
        self.domain,  = split_entity_id(entity_id)
        self.entity_id = entity_id
        self.callback = callback
        hass.bus.listen('timer.finished', self.on_timer_finish)

    def schedule(self, delay):
        self.hass.services.async_call(self.domain, 'timer.start', {
            'entity_id': self.entity_id,
            'duration': delay
        })

    def cancel(self):
        self.hass.services.async_call(self.domain, 'timer.cancel',
                                      {'entity_id': self.entity_id})

    def on_timer_finish(self, event):
        if event.data.get('entity_id') == self.entity_id:
            self.callback()


class ThreadingTimerHelper(object):
    def __init__(self, callback):
        self.callback = callback
        self.handle = None

    def schedule(self, delay):
        self.handle = threading.Timer(delay, self.callback)

    def cancel(self):
        self.handle.cancel()


class MovementController(object):
    def __init__(self, config):
        self.movement_event_name = config.get(CONF_MOVEMENT_EVENT)

    def on_movement(self):
        pass


class ManualController(object):
    def __init__(self, hass, config):
        manual_on_event_name = config.get(CONF_MANUAL_ON_EVENT)
        manual_off_event_name = config.get(CONF_MANUAL_OFF_EVENT)

        if manual_on_event_name is not None:
            hass.bus.listen(manual_on_event_name, {'entity_id': entity_id},
                            callback)
            # TODO use single event & different event data?

    def turn_on(self):
        pass

    def turn_off(self):
        pass


class BrightnessLightController(object):
    def __init__(self, entities, brightness):
        self.entities = entities
        self.brightness = brightness

    def call_on_entities(self, service, data):
        for entity_id in self.entities:
            domain, name = split_entity_id(entity_id)
            data_copy = data.copy()
            data_copy['entity_id'] = entity_id
            self.hass.services.async_call(domain, service, data_copy)

    def turn_on(self):
        self.call_on_entities('turn_on', {'brightness': self.brightness})

    def turn_off(self):
        self.call_on_entities('turn_off')


class LightControllerDim(MovementController, ManualController):
    def __init__(self, hass, config):
        MovementController.__init__(self, hass, config)
        ManualController.__init__(self, hass, config)

        self.hass = hass
        self.auto_on_duration = config.get(CONF_ON_DURATION, 90)
        self.auto_dim_duration = config.get(CONF_DIM_DURATION, 30)

        self.machine = Machine(
            model=self,
            states=['manual_on', 'auto_on', 'auto_dim', 'off'],
            init='off',
            ignore_invalid_triggers=True)
        self.machine.add_transition(
            'movement', ['auto_on', 'auto_dim', 'off'],
            'auto_on',
            after=['turn_light_on', 'schedule_auto_on_timer'])
        self.machine.add_transition(
            'timer_finished',
            'auto_on',
            'auto_dim',
            after=['turn_dim', 'schedule_auto_dim_timer'])
        self.machine.add_transition('timer_finished', 'auto_dim', 'off')
        self.machine.add_transition('manual_on',
                                    '*',
                                    'manual_on',
                                    after=['cancel_timer', 'turn_light_on'])
        self.machine.add_transition('manual_off',
                                    '*',
                                    'off',
                                    after='cancel_timer')
        self.machine.add_transition('manual_toggle', ['auto_on', 'manual_on'],
                                    'off',
                                    after='cancel_timer')
        self.machine.add_transition('manual_toggle', ['auto_dim', 'off'],
                                    'manual_on',
                                    after=['cancel_timer', 'turn_light_on'])

    def schedule_auto_on_timer(self):
        self.timer.schedule(self.auto_on_duration)

    def schedule_auto_dim_timer(self):
        self.timer.schedule(self.auto_dim_duration)

    def turn_dim(self):
        pass

    def turn_on(self):
        pass

    def turn_off(self):
        pass


class LightController(MovementController, ManualController):
    def __init__(self, hass, config):
        MovementController.__init__(self, hass, config)
        ManualController.__init__(self, hass, config)

        self.hass = hass
        self.auto_on_duration = config.get(CONF_ON_DURATION, 90)
        self.timer = TimerHelper(hass, config[CONF_TIMER], self.timer_finished)

        self.machine = Machine(model=self,
                               states=['manual_on', 'auto_on', 'off'],
                               init='off',
                               ignore_invalid_triggers=True)
        self.machine.add_transition(
            'movement', ['auto_on', 'off'],
            'auto_on',
            after=['turn_light_on', 'schedule_auto_on_timer'])
        self.machine.add_transition('timer_finished', 'auto_on', 'off')
        self.machine.add_transition('manual_on',
                                    '*',
                                    'manual_on',
                                    after=['cancel_timer', 'turn_light_on'])
        self.machine.add_transition('manual_off',
                                    '*',
                                    'off',
                                    after='cancel_timer')
        self.machine.add_transition('manual_toggle', ['auto_on', 'manual_on'],
                                    'off',
                                    after='cancel_timer')
        self.machine.add_transition('manual_toggle',
                                    'off',
                                    'manual_on',
                                    after=['cancel_timer', 'turn_light_on'])

    def schedule_auto_on_timer(self):
        self.timer.schedule(self.auto_on_duration)


class LightDispatcherDimScenes(LightControllerDim):
    class Scene(object):
        def __ini__(self, entity_id):
            assert(entity_id is not None)
            self.domain, self.name = split_entity_id(entity_id)[0], entity_id

    def __init__(self, hass, config):
        LightControllerDim.__init__(self, hass, config)

        def get_scene(conf_key):
            entity_id = config.get(conf_key)
            if entity_id is None:
                return None
            return Scene(entity_id)

        self.on_scene = get_domain_and_id(CONF_ON_SCENE)
        self.dim_scene = get_domain_and_id(CONF_DIM_SCENE)
        self.off_scene = get_domain_and_id(CONF_OFF_SCENE)

    def set_scene(self, scene):
        if scene is None:
            return
        self.hass.services.async_call(scene.domain, 'scene.turn_on',
                                      {'entity_id': scene.name})

    def turn_on(self):
        self.set_scene(self.on_scene)

    def turn_dim(self):
        self.set_scene(self.dim_scene)

    def turn_off(self):
        self.set_scene(self.off_scene)
