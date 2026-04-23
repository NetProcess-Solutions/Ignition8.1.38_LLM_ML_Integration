# ai/config.py
# Configuration for the IgnitionChatbot gateway scripts.
# Jython 2.7 - DO NOT use f-strings, type hints, or other Python 3 syntax.

# -----------------------------------------------------------------------------
# Service endpoint
# -----------------------------------------------------------------------------
# URL the Ignition gateway uses to reach the FastAPI AI service.
AI_SERVICE_URL = "http://localhost:8000"
API_KEY        = "change_me_to_match_service_env"

# Base URL of the Ignition gateway itself (informational).
IGNITION_BASE_URL = "http://172.31.64.235:8088"

REQUEST_TIMEOUT_MS = 60000

# -----------------------------------------------------------------------------
# Plant context
# -----------------------------------------------------------------------------
LINE_ID = "coater1"

TAG_PROVIDER = "[UnifiedNamespace]"
COATER1_ROOT = TAG_PROVIDER + "Shaw/F0004/Coating/Coater1"

# -----------------------------------------------------------------------------
# Tag catalog
# -----------------------------------------------------------------------------
# Each entry is a dict so the pre-screen selector can filter by category.
# Required:
#   path, name, unit (or None), target (or None), category, keywords, core
# core=True means: ALWAYS include in every chat query, regardless of the
# selector. These are the tags the LLM needs for basic situational awareness.
#
# Categories (used by the selector):
#   line_state, speed, coating_weight, puddle, pump, applicator, width,
#   oven_zone, oven_exit, drive, accumulator, recipe, sewin
#
# NOTE: "ProfileSetponit" is intentionally misspelled to match the actual
# tag name in the UNS.
# -----------------------------------------------------------------------------

# Helper to keep oven zone entries compact.
def _zone(n, n_pad):
    return [
        {
            "path":     COATER1_ROOT + "/DryEnd/Maintenter/Oven/Zone" + n_pad + "/ProfileSetponit",
            "name":     "Zone" + str(n) + "ProfileSetpoint",
            "unit":     "F",
            "target":   None,
            "category": "oven_zone",
            "keywords": ["zone " + str(n), "zone" + str(n), "z" + str(n),
                         "oven", "temperature", "temp", "profile", "setpoint"],
            "core":     False,
        },
        {
            "path":     COATER1_ROOT + "/DryEnd/Maintenter/Oven/Zone" + n_pad + "/Zone" + str(n) + "BottomTempActual",
            "name":     "Zone" + str(n) + "BottomTempActual",
            "unit":     "F",
            "target":   None,
            "category": "oven_zone",
            "keywords": ["zone " + str(n), "zone" + str(n), "z" + str(n),
                         "oven", "temperature", "temp", "actual", "burner"],
            "core":     False,
        },
        {
            "path":     COATER1_ROOT + "/DryEnd/Maintenter/Oven/Zone" + n_pad + "/Zone" + str(n) + "BottomTempSetpoint",
            "name":     "Zone" + str(n) + "BottomTempSetpoint",
            "unit":     "F",
            "target":   None,
            "category": "oven_zone",
            "keywords": ["zone " + str(n), "zone" + str(n), "z" + str(n),
                         "oven", "temperature", "temp", "setpoint", "burner"],
            "core":     False,
        },
    ]


KEY_TAGS = [
    # --- Line state (CORE: always sent) --------------------------------------
    {"path": COATER1_ROOT + "/IsRunning",         "name": "IsRunning",         "unit": None,  "target": None,
     "category": "line_state", "keywords": ["running", "stopped", "down", "state", "status"], "core": True},
    {"path": COATER1_ROOT + "/IsStopped",         "name": "IsStopped",         "unit": None,  "target": None,
     "category": "line_state", "keywords": ["stopped", "down", "state", "status", "stop"],    "core": True},
    {"path": COATER1_ROOT + "/RunStopTime",       "name": "RunStopTime",       "unit": "s",   "target": None,
     "category": "line_state", "keywords": ["downtime", "stop time", "duration"],             "core": True},
    {"path": COATER1_ROOT + "/TandemOperation",   "name": "TandemOperation",   "unit": None,  "target": None,
     "category": "line_state", "keywords": ["tandem"],                                        "core": True},

    # --- Speed (CORE) --------------------------------------------------------
    {"path": COATER1_ROOT + "/DryEnd/Maintenter/LineSpeed",  "name": "LineSpeed",
     "unit": "fpm", "target": None,
     "category": "speed", "keywords": ["speed", "fpm", "rate", "line speed"], "core": True},
    {"path": COATER1_ROOT + "/ShiftAverageSpeed",            "name": "ShiftAverageSpeed",
     "unit": "fpm", "target": None,
     "category": "speed", "keywords": ["speed", "shift", "average"],          "core": True},
    {"path": COATER1_ROOT + "/DryEnd/Inspect/InspectionTableSpeed", "name": "InspectionTableSpeed",
     "unit": "fpm", "target": None,
     "category": "speed", "keywords": ["inspect", "inspection", "table", "speed"], "core": False},
    {"path": COATER1_ROOT + "/DryEnd/Shearing/Speed",        "name": "ShearSpeed",
     "unit": "fpm", "target": None,
     "category": "speed", "keywords": ["shear", "shearing", "speed"],         "core": False},

    # --- Recipe (CORE) -------------------------------------------------------
    {"path": COATER1_ROOT + "/StyleID",   "name": "StyleID",   "unit": None, "target": None,
     "category": "recipe", "keywords": ["style", "product", "recipe", "spec"], "core": True},
    {"path": COATER1_ROOT + "/FrontStep", "name": "FrontStep", "unit": None, "target": None,
     "category": "recipe", "keywords": ["step", "front", "stage", "phase"],    "core": True},

    # --- Coating weight ------------------------------------------------------
    {"path": COATER1_ROOT + "/WetEnd/Froth/Adhesive/OzPerSY",       "name": "OzPerSY",
     "unit": "oz/yd2", "target": None,
     "category": "coating_weight", "keywords": ["weight", "oz", "ozpersy", "coating", "adhesive"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Adhesive/OzPerSYTarget", "name": "OzPerSYTarget",
     "unit": "oz/yd2", "target": None,
     "category": "coating_weight", "keywords": ["weight", "target", "oz", "coating", "adhesive"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/OzPerSY",       "name": "PreCoatOzPerSY",
     "unit": "oz/yd2", "target": None,
     "category": "coating_weight", "keywords": ["weight", "precoat", "pre-coat", "oz", "coating"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/OzPerSYTarget", "name": "PreCoatOzPerSYTarget",
     "unit": "oz/yd2", "target": None,
     "category": "coating_weight", "keywords": ["weight", "precoat", "target", "oz"],              "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/Cupweight",         "name": "Cupweight",
     "unit": None, "target": None,
     "category": "coating_weight", "keywords": ["cup", "weight", "cupweight"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/CupweightSetpoint", "name": "CupweightSetpoint",
     "unit": None, "target": None,
     "category": "coating_weight", "keywords": ["cup", "weight", "setpoint", "target"], "core": False},

    # --- Puddle / pan --------------------------------------------------------
    {"path": COATER1_ROOT + "/WetEnd/Froth/Adhesive/PanLevel",       "name": "PanLevel",
     "unit": None, "target": None,
     "category": "puddle", "keywords": ["pan", "level", "adhesive"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Adhesive/PanLevelTarget", "name": "PanLevelTarget",
     "unit": None, "target": None,
     "category": "puddle", "keywords": ["pan", "level", "target", "setpoint"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/PuddleHeightAlley",       "name": "PuddleHeightAlley",
     "unit": None, "target": None,
     "category": "puddle", "keywords": ["puddle", "alley", "height", "level"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/PuddleHeightAlleyCenter", "name": "PuddleHeightAlleyCenter",
     "unit": None, "target": None,
     "category": "puddle", "keywords": ["puddle", "alley", "center", "height"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/PuddleHeightWall",        "name": "PuddleHeightWall",
     "unit": None, "target": None,
     "category": "puddle", "keywords": ["puddle", "wall", "height", "level"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/PuddleHeightWallCenter",  "name": "PuddleHeightWallCenter",
     "unit": None, "target": None,
     "category": "puddle", "keywords": ["puddle", "wall", "center", "height"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/PuddleHeightTarget",      "name": "PuddleHeightTarget",
     "unit": None, "target": None,
     "category": "puddle", "keywords": ["puddle", "target", "setpoint", "height"], "core": False},

    # --- Pumps / air ---------------------------------------------------------
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/Pump1OutputPercentage", "name": "Pump1OutputPercentage",
     "unit": "%", "target": None,
     "category": "pump", "keywords": ["pump", "pump 1", "pump1", "output"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/Pump2OutputPercentage", "name": "Pump2OutputPercentage",
     "unit": "%", "target": None,
     "category": "pump", "keywords": ["pump", "pump 2", "pump2", "output"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/InjectionAirFlowrate",  "name": "InjectionAirFlowrate",
     "unit": None, "target": None,
     "category": "pump", "keywords": ["air", "flow", "injection", "flowrate"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/Backpressure",          "name": "Backpressure",
     "unit": None, "target": None,
     "category": "pump", "keywords": ["pressure", "back", "backpressure"], "core": False},

    # --- Applicator ----------------------------------------------------------
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/ApplicatorGapSetpoint",    "name": "ApplicatorGapSetpoint",
     "unit": None, "target": None,
     "category": "applicator", "keywords": ["applicator", "gap", "setpoint"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/ApplicatorGapProfileSpec", "name": "ApplicatorGapProfileSpec",
     "unit": None, "target": None,
     "category": "applicator", "keywords": ["applicator", "gap", "profile", "spec"], "core": False},

    # --- Width ---------------------------------------------------------------
    {"path": COATER1_ROOT + "/DryEnd/Inspect/FinishedCarpetWidth", "name": "FinishedCarpetWidth",
     "unit": "in", "target": None,
     "category": "width", "keywords": ["width", "carpet", "finished", "inspect"], "core": False},
    {"path": COATER1_ROOT + "/DryEnd/Maintenter/CarpetNapWidth",   "name": "CarpetNapWidth",
     "unit": "in", "target": None,
     "category": "width", "keywords": ["width", "nap", "carpet", "tenter"], "core": False},
    {"path": COATER1_ROOT + "/DryEnd/Maintenter/TrimKnifeWidth",   "name": "TrimKnifeWidth",
     "unit": "in", "target": None,
     "category": "width", "keywords": ["width", "trim", "knife"], "core": False},
    {"path": COATER1_ROOT + "/DryEnd/Maintenter/WingPinningWidth", "name": "WingPinningWidth",
     "unit": "in", "target": None,
     "category": "width", "keywords": ["width", "wing", "pinning", "tenter"], "core": False},
    {"path": COATER1_ROOT + "/WetEnd/Froth/Precoat/ApplicationWidth", "name": "ApplicationWidth",
     "unit": "in", "target": None,
     "category": "width", "keywords": ["width", "application", "applicator"], "core": False},

    # --- Drives / mechanical (CORE for fault/running) ------------------------
    {"path": COATER1_ROOT + "/DryEnd/Maintenter/MaintenterLoadAmps", "name": "MaintenterLoadAmps",
     "unit": "A", "target": None,
     "category": "drive", "keywords": ["amps", "load", "current", "tenter", "maintenter", "drive"], "core": False},
    {"path": COATER1_ROOT + "/DryEnd/Shearing/Drive_Fault", "name": "ShearDriveFault",
     "unit": None, "target": None,
     "category": "drive", "keywords": ["shear", "drive", "fault", "alarm"], "core": True},
    {"path": COATER1_ROOT + "/DryEnd/Shearing/Running",     "name": "ShearDriveRunning",
     "unit": None, "target": None,
     "category": "drive", "keywords": ["shear", "drive", "running"], "core": True},

    # --- Accumulators --------------------------------------------------------
    {"path": COATER1_ROOT + "/DryEnd/Maintenter/LevelAccumulator1", "name": "LevelAccumulator1",
     "unit": None, "target": None,
     "category": "accumulator", "keywords": ["accumulator", "level"], "core": False},

    # --- SewIn ---------------------------------------------------------------
    {"path": COATER1_ROOT + "/WetEnd/SewIn/JBox3/SlatGuiderAutoManual", "name": "SlatGuiderAutoManual",
     "unit": None, "target": None,
     "category": "sewin", "keywords": ["sewin", "sew", "slat", "guider", "jbox"], "core": False},

    # --- Oven exit temps -----------------------------------------------------
    {"path": COATER1_ROOT + "/DryEnd/Maintenter/Oven/ExitTempAlley",  "name": "ExitTempAlley",
     "unit": "F", "target": None,
     "category": "oven_exit", "keywords": ["exit", "temp", "alley", "oven"], "core": False},
    {"path": COATER1_ROOT + "/DryEnd/Maintenter/Oven/ExitTempCenter", "name": "ExitTempCenter",
     "unit": "F", "target": None,
     "category": "oven_exit", "keywords": ["exit", "temp", "center", "oven"], "core": False},
    {"path": COATER1_ROOT + "/DryEnd/Maintenter/Oven/ExitTempWall",   "name": "ExitTempWall",
     "unit": "F", "target": None,
     "category": "oven_exit", "keywords": ["exit", "temp", "wall", "oven"], "core": False},
]

# Append zones 1..15 (folder is zero-padded, friendly names are not).
_ZONE_PADS = [
    (1, "01"), (2, "02"), (3, "03"), (4, "04"), (5, "05"),
    (6, "06"), (7, "07"), (8, "08"), (9, "09"), (10, "10"),
    (11, "11"), (12, "12"), (13, "13"), (14, "14"), (15, "15"),
]
for _n, _pad in _ZONE_PADS:
    KEY_TAGS = KEY_TAGS + _zone(_n, _pad)


# -----------------------------------------------------------------------------
# Backward-compat tuple form. Other modules (context.py) read this.
# (path, friendly_name, unit, target_or_None)
# -----------------------------------------------------------------------------
KEY_TAG_PATHS = [(t["path"], t["name"], t["unit"], t["target"]) for t in KEY_TAGS]

# -----------------------------------------------------------------------------
# Recipe / current spec tags (kept for RecipeContext.product_style)
# -----------------------------------------------------------------------------
RECIPE_TAGS = [
    (COATER1_ROOT + "/StyleID",   "product_style"),
    (COATER1_ROOT + "/FrontStep", "front_step"),
]

# -----------------------------------------------------------------------------
# Pre-screen selector
# -----------------------------------------------------------------------------
# When True, ai/context.py first calls /api/select_tags with the user's
# question to get a small subset of relevant tags, and only those + core
# tags are read and historian-queried. Drops payload size and historian load
# dramatically.
USE_TAG_SELECTOR = True

# Cap on number of non-core tags the selector may include.
SELECTOR_MAX_TAGS = 20

# -----------------------------------------------------------------------------
# Historian
# -----------------------------------------------------------------------------
HISTORIAN_WINDOW_MINUTES   = 60
HISTORIAN_INTERVAL_MINUTES = 5
HISTORIAN_AGGREGATION_MODE = "Average"

DEVIATION_SIGMA_THRESHOLD = 2.0

# -----------------------------------------------------------------------------
# Alarms
# -----------------------------------------------------------------------------
ALARM_SOURCE_FILTER = [
    "*Shaw/F0004/Coating/Coater1*",
    "*Plt_04_C1_*",
]

LOGGER_NAME = "ai.chatbot"
