"""Constants for the complex_controller integration."""

DOMAIN = 'complex_controller'

CONF_ACTIONS_ON = 'action_on'
CONF_ACTIONS_DIM = 'action_dim'
CONF_ACTIONS_OFF = 'action_off'
CONF_DISPATCHER_DIM = 'dim'
CONF_DISPATCHER_SIMPLE = 'simple'
CONF_DISPATCHER_DUMMY = 'dummy'
CONF_DURATION_ON = 'duration_on'
CONF_DURATION_DIM = 'duration_dim'
CONF_OVERRIDES = 'overrides'
CONF_SCENE = 'scene'
CONF_SERVICE = 'service'
CONF_SERVICE_DATA = 'service_data'
CONF_TIMER = 'timer'

STATE_AUTO_ON = 'auto_on'
STATE_MANUAL_ON = 'manual_on'
STATE_DIM = 'dim'
STATE_OFF = 'off'
ALL_STATES = [STATE_AUTO_ON, STATE_MANUAL_ON, STATE_DIM, STATE_OFF]
AUTO_CHANGEABLE_STATES = [STATE_AUTO_ON, STATE_DIM, STATE_OFF]
DEFAULT_STATE = STATE_OFF

SERVICE_HANDLE_EVENT = 'handle_event'

ATTR_CONTROLLER = 'controller'
ATTR_EVENT_TYPE = 'type'  # <-- key in service call data dict, and below come values:
EVENT_TYPE_TOGGLE = 'toggle'
EVENT_TYPE_MANUAL_ON = 'manual_on'
EVENT_TYPE_MANUAL_OFF = 'manual_off'
EVENT_TYPE_MOVEMENT = 'movement'
EVENT_TYPE_TIMER = 'timer'
ALL_EVENT_TYPES = (EVENT_TYPE_TOGGLE, EVENT_TYPE_MANUAL_ON,
                   EVENT_TYPE_MANUAL_OFF, EVENT_TYPE_MOVEMENT,
                   EVENT_TYPE_TIMER)
