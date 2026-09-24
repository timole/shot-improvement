"""Geometry of the IIHF ("euro") rink map used to pick where a shot is
taken from and what it hits (spec 137): the named shooting places, the two
targets (the goal, or the end boards) and how a place on the map turns into
a distance in metres. The map is only a shortcut for typing the distance.

Coordinates: x runs along the rink from the shooter's end boards (0 m) to
the far end boards (60 m), y across it (0..30 m). The shot goes towards the
far end. The puck hits either the goal (its goal line is at 56 m - the
distance core.claps measures to) or the end boards (60 m), and the sound
or picture of that hit is what ends the measured flight:

    distance = target x - shooter x

Pure logic, no Tk."""

from __future__ import annotations

import math
from typing import NamedTuple, Optional

RINK_LENGTH_M = 60.0
RINK_WIDTH_M = 30.0
CORNER_RADIUS_M = 8.5
GOAL_LINE_OFFSET_M = 4.0
FAR_GOAL_LINE_M = RINK_LENGTH_M - GOAL_LINE_OFFSET_M  # 56.0
BLUE_LINE_X_M = (22.5, 37.5)
CENTER_LINE_X_M = 30.0
FACEOFF_CIRCLE_RADIUS_M = 4.5
FACEOFF_SPOT_X_M = (10.0, 50.0)  # 6 m from each goal line
FACEOFF_SPOT_Y_M = (RINK_WIDTH_M / 2 - 7.0, RINK_WIDTH_M / 2 + 7.0)
GOAL_WIDTH_M = 1.83
GOAL_DEPTH_M = 1.12

# The shortest/longest distance a shot can sensibly be entered as: the
# rink is 60 m long, and the pairing window (core.claps.hit_delay_window)
# scales with distance, so a couple of metres is the practical minimum.
MIN_DISTANCE_M = 2.0
MAX_DISTANCE_M = 60.0

# What the puck hits.
GOAL = "goal"
END = "end"
DEFAULT_TARGET = GOAL
TARGETS = (GOAL, END)
TARGET_X_M = {GOAL: FAR_GOAL_LINE_M, END: RINK_LENGTH_M}
TARGET_NAMES = {GOAL: "Maali", END: "Päätylaita"}
_TARGET_TO = {GOAL: "maaliin", END: "päätyyn"}  # illative, for "... maaliin"

# A click within this many metres of a named place's line snaps to it.
SNAP_M = 1.5


def parse_distance_m(text: str) -> Optional[float]:
    """A distance typed by the user - "18,5", "18.5" or "18,5 m" - in
    metres, or None if it isn't a number in [MIN_DISTANCE_M,
    MAX_DISTANCE_M]."""
    cleaned = text.strip().lower()
    if cleaned.endswith("m"):
        cleaned = cleaned[:-1].strip()
    cleaned = cleaned.replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(value) or not (MIN_DISTANCE_M <= value <= MAX_DISTANCE_M):
        return None
    return value


def format_distance(distance_m: float) -> str:
    """18.5 -> "18,5", 6.0 -> "6" (Finnish decimal comma, no trailing zero)."""
    return f"{distance_m:.2f}".rstrip("0").rstrip(".").replace(".", ",")


class MapPlace(NamedTuple):
    key: str
    from_label: str  # elative, for "<from_label> maaliin"
    x_m: float  # where its line sits on the rink


# The named places a shot is taken from: a line across the rink each. The
# labels reuse core.claps.SHOT_POSITIONS' wording, so a goal-target
# description reads exactly like the preset it replaces.
MAP_PLACES: tuple[MapPlace, ...] = (
    MapPlace("end_to_end", "Päätyviivalta", GOAL_LINE_OFFSET_M),
    MapPlace("faceoff_dots", "Oman alueen aloituspisteiden linjalta", 10.0),
    MapPlace("other_blue_line", "Toisesta sinisestä viivasta", 22.5),
    MapPlace("red_line", "Keskiviivalta", 30.0),
    MapPlace("blue_line", "Sinisestä viivasta", 37.5),
    MapPlace("attack_dots", "Hyökkäysalueen aloituspisteiden välistä", 50.0),
)

# The original measurement (specs 098-127) is 57 m from the own goal line
# to the far end (a 61 m rink); on this 60 m rink the geometry gives 56.
# The named place keeps the 57 the user asked for.
_DISTANCE_OVERRIDE = {("end_to_end", END): 57.0}


def distance_for(place: MapPlace, target: str) -> float:
    """The shot distance from a named place to the target."""
    override = _DISTANCE_OVERRIDE.get((place.key, target))
    if override is not None:
        return override
    return round(TARGET_X_M[target] - place.x_m, 2)


def place_at(x_m: float, snap_m: float = SNAP_M) -> Optional[MapPlace]:
    """The named place whose line is within snap_m of x_m (the nearest one)."""
    near = [p for p in MAP_PLACES if abs(p.x_m - x_m) <= snap_m]
    return min(near, key=lambda p: abs(p.x_m - x_m)) if near else None


def identify_place(distance_m: float, target: str) -> Optional[MapPlace]:
    """The named place a distance to the target belongs to, if any."""
    for place in MAP_PLACES:
        if abs(distance_for(place, target) - distance_m) < 0.005:
            return place
    return None


def shooter_x(distance_m: float, target: str) -> float:
    """Where on the rink a distance to the target puts the shooter. May
    be outside the rink for a distance that does not fit."""
    place = identify_place(distance_m, target)
    return place.x_m if place is not None else TARGET_X_M[target] - distance_m


def distance_for_click(x_m: float, target: str, snap_m: float = SNAP_M) -> Optional[float]:
    """The distance for a click at x_m along the rink: snapped to a named
    place's distance when close to its line, otherwise the metres to the
    target rounded to 0.1 m. None when the click leaves no room for a
    shot (behind the target, or closer than MIN_DISTANCE_M)."""
    place = place_at(x_m, snap_m)
    if place is not None:
        return distance_for(place, target)
    distance = round(TARGET_X_M[target] - x_m, 1)
    if not (MIN_DISTANCE_M <= distance <= MAX_DISTANCE_M):
        return None
    return distance


def distance_after_target_change(distance_m: float, old: str, new: str) -> float:
    """Switching the target keeps the shooter where they are, so the
    distance changes by the gap between the two targets (goal -> end boards
    is 4 m more), clamped to what can be entered."""
    place = identify_place(distance_m, old)
    if place is not None:
        return distance_for(place, new)
    moved = round(distance_m + TARGET_X_M[new] - TARGET_X_M[old], 1)
    return min(max(moved, MIN_DISTANCE_M), MAX_DISTANCE_M)


def describe(distance_m: Optional[float], target: str) -> str:
    """"Sinisestä viivasta maaliin (18,5 m)" for a named place, "11 m
    maaliin" otherwise; "—" when unknown."""
    if distance_m is None:
        return "—"
    to = _TARGET_TO.get(target, _TARGET_TO[DEFAULT_TARGET])
    place = identify_place(distance_m, target)
    if place is None:
        return f"{format_distance(distance_m)} m {to}"
    return f"{place.from_label} {to} ({format_distance(distance_m)} m)"
