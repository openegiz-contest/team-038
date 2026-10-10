import pytest
from fastapi import HTTPException

from twinvision.setup.setup_twins import all_definitions
from twinvision.web import app as web


class FakeDitto:
    """In-memory stand-in for the Ditto HTTP API paths used by the web backend."""

    def __init__(self):
        self.things = {tid: body for tid, body in all_definitions(42).items()}
        self.puts = []

    def request(self, method, path, body=None, timeout=30):
        if path.startswith("/api/2/search/things"):
            items = [{"thingId": t, **b} for t, b in self.things.items()]
            return 200, {"items": items}
        if path.startswith("/api/2/things?ids="):
            ids = path.split("ids=")[1].split("&")[0].split(",")
            return 200, [{"thingId": i, "features": self.things[i]["features"]} for i in ids if i in self.things]
        if path == f"/api/2/things/{web.CONTROLLER}/features":
            return 200, self.things[web.CONTROLLER]["features"]
        if method == "PUT" and path.startswith(f"/api/2/things/{web.CONTROLLER}/features/"):
            name = path.split("/features/")[1].split("/")[0]
            self.things[web.CONTROLLER]["features"][name] = {"properties": body}
            self.puts.append((name, body))
            return 204, None
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture
def store(monkeypatch):
    fake = FakeDitto()
    s = web.Store(fake)
    s.discover()
    monkeypatch.setattr(web, "store", s)
    monkeypatch.setattr(web, "ditto", fake)
    return s


def active(store):
    return {k: v["properties"].get("active") for k, v in store.controller().items()}


def test_discovery_and_snapshot(store):
    assert len(store.ids) == 75
    state = store.build(store.fetch())
    assert "truck-01" in state["live"] and "truck-01" in state["base"]
    assert "scenario-controller" not in state["live"]
    keys = [k["key"] for k in state["kpis"]]
    assert "plant_feed_tph" in keys and "diesel_l_t" in keys
    for row in state["kpis"]:
        assert row["verdict"] == "same"                         # identical copies at start


def test_kpi_verdicts(store):
    things = store.fetch()
    things["mine-baseline"]["tph_ore"], things["mine"]["tph_ore"] = 3000.0, 2400.0
    things["mine-baseline"]["diesel_l_t"], things["mine"]["diesel_l_t"] = 0.20, 0.26
    rows = {r["key"]: r for r in store.build(things)["kpis"]}
    assert rows["tph_ore"]["verdict"] == "worse" and rows["tph_ore"]["delta"] < 0
    assert rows["diesel_l_t"]["verdict"] == "worse"


def test_activate_validate_and_undo(store):
    web.activate("road_closure", {"segments": ["road-s06"]})
    assert store.controller()["road_closure"]["properties"] == {"active": True, "segments": ["s06"]}
    with pytest.raises(HTTPException):
        web.activate("road_closure", {"segments": ["s99"]})
    with pytest.raises(HTTPException):
        web.activate("no_such_scenario", {})
    web.activate("energy_cap", {"cap_kw": 1})
    assert store.controller()["energy_cap"]["properties"]["cap_kw"] == 5000.0   # clamped
    web.undo_last()
    assert active(store)["energy_cap"] is False and active(store)["road_closure"] is True
    web.deactivate("road_closure")
    assert active(store)["road_closure"] is False
    assert store.controller()["road_closure"]["properties"]["segments"] == ["s06"]  # parameters kept


def test_reset_is_one_undo_step(store):
    web.activate("crusher_failure", {})
    web.activate("fuel_shortage", {"resupply_lph": 100, "bay_stock_l": 5000})
    web.reset_all()
    assert not any(active(store).values())
    web.undo_last()
    a = active(store)
    assert a["crusher_failure"] and a["fuel_shortage"]
    assert store.controller()["fuel_shortage"]["properties"]["bay_stock_l"] == 5000.0


def test_module_switch_and_undo(store):
    web.set_module("maintenance", {"enabled": False})
    assert store.controller()["maintenance_module"]["properties"] == {"enabled": False}
    with pytest.raises(HTTPException):
        web.set_module("maintenance", {"enabled": "no"})
    with pytest.raises(HTTPException):
        web.set_module("vision", {"enabled": True})
    web.undo_last()
    assert store.controller()["maintenance_module"]["properties"] == {"enabled": True}


def test_reset_switches_modules_back_on(store):
    web.set_module("energy", {"enabled": False})
    web.activate("crusher_failure", {})
    out = web.reset_all()
    assert out == {"deactivated": ["crusher_failure"], "modules_on": ["energy"]}
    assert store.controller()["energy_module"]["properties"] == {"enabled": True}
    web.undo_last()
    assert store.controller()["energy_module"]["properties"] == {"enabled": False}
    assert store.controller()["crusher_failure"]["properties"]["active"] is True
