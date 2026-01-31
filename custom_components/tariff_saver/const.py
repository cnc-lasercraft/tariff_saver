"""Constants for Tariff Saver."""
from __future__ import annotations

DOMAIN = "tariff_saver"

# Core options / config keys
CONF_PUBLISH_TIME = "publish_time"
DEFAULT_PUBLISH_TIME = "18:15"

CONF_TARIFF_NAME = "tariff_name"
CONF_BASELINE_TARIFF_NAME = "baseline_tariff_name"

# Consumption (energy total_increasing)
CONF_CONSUMPTION_ENERGY_ENTITY = "consumption_energy_entity"

# Grade thresholds (deviation vs daily avg, percent)
# Grade mapping: 1 (best) ... 5 (worst)
CONF_GRADE_T1 = "grade_t1_percent"
CONF_GRADE_T2 = "grade_t2_percent"
CONF_GRADE_T3 = "grade_t3_percent"
CONF_GRADE_T4 = "grade_t4_percent"

DEFAULT_GRADE_T1 = -20.0
DEFAULT_GRADE_T2 = -10.0
DEFAULT_GRADE_T3 = 10.0
DEFAULT_GRADE_T4 = 25.0

# Toggles
CONF_ENABLE_COST_TRACKING = "enable_cost_tracking"
DEFAULT_ENABLE_COST_TRACKING = True
