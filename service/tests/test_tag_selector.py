"""Tests for the deterministic tag pre-screen selector."""
from services.tag_selector import select_tags


def _catalog():
    return [
        {"name": "IsRunning",   "category": "line_state", "keywords": ["running"], "core": True},
        {"name": "LineSpeed",   "category": "speed",      "keywords": ["speed"],   "core": True},
        {"name": "StyleID",     "category": "recipe",     "keywords": ["style"],   "core": True},
        {"name": "OzPerSY",     "category": "coating_weight", "keywords": ["weight"], "core": False},
        {"name": "PanLevel",    "category": "puddle",     "keywords": ["pan"],     "core": False},
        {"name": "Pump1OutputPercentage", "category": "pump", "keywords": ["pump"], "core": False},
        {"name": "Zone1ProfileSetpoint",       "category": "oven_zone", "keywords": ["zone 1"], "core": False},
        {"name": "Zone1BottomTempActual",      "category": "oven_zone", "keywords": ["zone 1"], "core": False},
        {"name": "Zone3ProfileSetpoint",       "category": "oven_zone", "keywords": ["zone 3"], "core": False},
        {"name": "Zone3BottomTempActual",      "category": "oven_zone", "keywords": ["zone 3"], "core": False},
        {"name": "ExitTempCenter", "category": "oven_exit", "keywords": ["exit"], "core": False},
        {"name": "ShearDriveFault","category": "drive",    "keywords": ["fault"], "core": True},
    ]


def test_core_always_included():
    out = select_tags("hello", _catalog())
    assert "IsRunning" in out["selected_names"]
    assert "LineSpeed" in out["selected_names"]
    assert "StyleID" in out["selected_names"]
    assert "ShearDriveFault" in out["selected_names"]


def test_zone_specific_pulls_only_that_zone():
    out = select_tags("why is zone 3 overshooting?", _catalog())
    names = set(out["selected_names"])
    assert "Zone3ProfileSetpoint" in names
    assert "Zone3BottomTempActual" in names
    # zone 1 must NOT be pulled in
    assert "Zone1ProfileSetpoint" not in names
    assert 3 in out["matched_zones"]


def test_oven_word_alone_pulls_zone_category():
    out = select_tags("how is the oven looking?", _catalog())
    names = set(out["selected_names"])
    # No specific zone mentioned, so generic oven keyword should pull zone tags
    assert "Zone1ProfileSetpoint" in names
    assert "Zone3ProfileSetpoint" in names


def test_zone_specific_overrides_generic_oven():
    # When a specific zone is mentioned together with "oven", we should NOT
    # also dump every other zone.
    out = select_tags("oven zone 1 is too hot", _catalog())
    names = set(out["selected_names"])
    assert "Zone1ProfileSetpoint" in names
    assert "Zone3ProfileSetpoint" not in names


def test_coating_weight_category():
    out = select_tags("coating weight is off", _catalog())
    assert "OzPerSY" in out["selected_names"]


def test_keyword_fallback():
    out = select_tags("pump output low", _catalog())
    assert "Pump1OutputPercentage" in out["selected_names"]


def test_max_extra_cap():
    out = select_tags("oven", _catalog(), max_extra=2)
    extras = [n for n in out["selected_names"] if n not in
              ("IsRunning", "LineSpeed", "StyleID", "ShearDriveFault")]
    assert len(extras) <= 2


def test_unrelated_query_returns_core_only():
    out = select_tags("what's the weather?", _catalog())
    names = set(out["selected_names"])
    assert names == {"IsRunning", "LineSpeed", "StyleID", "ShearDriveFault"}
    assert out["reason"] == "core_only"
