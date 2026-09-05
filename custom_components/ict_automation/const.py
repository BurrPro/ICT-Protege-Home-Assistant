DOMAIN = "ict_automation"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_PASSWORD = "password"

CONF_DOORS = "doors"
CONF_AREAS = "areas"
CONF_INPUTS = "inputs"
CONF_OUTPUTS = "outputs"
# Trouble inputs are independent ICT records and must be configured explicitly.
CONF_TROUBLES = "troubles" 

# New Constants for Arming Modes
CONF_ENABLE_AWAY = "enable_arm_away"
CONF_ENABLE_STAY = "enable_arm_stay"
CONF_ENABLE_NIGHT = "enable_arm_night"
CONF_ENABLE_BYPASS = "enable_arm_bypass"

CONF_CHECKSUM = "checksum"
CHECKSUM_NONE = "none"
CHECKSUM_8_BIT_SUM = "8_bit_sum"
DEFAULT_CHECKSUM = CHECKSUM_8_BIT_SUM

# Door control behaviour; existing doors retain their normal lock controls.
CONF_DOOR_TYPES = "door_types"
DOOR_TYPE_LOCK = "lock"
DOOR_TYPE_TOGGLE = "toggle"


def get_door_type(options, door_id):
    modes = options.get(CONF_DOOR_TYPES, {})
    if not isinstance(modes, dict):
        raise ValueError("Door types must be a mapping")
    mode = modes.get(str(door_id), modes.get(door_id, DOOR_TYPE_LOCK))
    if mode not in (DOOR_TYPE_LOCK, DOOR_TYPE_TOGGLE):
        raise ValueError("Invalid door type")
    return mode
