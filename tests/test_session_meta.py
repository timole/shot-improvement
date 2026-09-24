"""Pure-logic tests for core.session_meta and core.rink (spec 137): the
per-recording metadata sidecar, grouping a recordings folder into
sessions, parsing a typed distance, and the rink map's click -> metres."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core import rink
from core.rink import END, GOAL
from core.claps import DEFAULT_SHOT_POSITION, SHOT_POSITIONS
from core.session_meta import (
    format_distance,
    list_sessions,
    parse_distance_m,
    place_label,
    write_session_meta,
)


def _shot(index: int, speed: float | None) -> SimpleNamespace:
    return SimpleNamespace(index=index, speed_kmh=speed)


# --- typed distance ---------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [("18,5", 18.5), ("18.5", 18.5), (" 22,5 m ", 22.5), ("6", 6.0), ("60", 60.0), ("2", 2.0)],
)
def test_parse_distance_accepts_comma_dot_and_unit(text: str, expected: float) -> None:
    assert parse_distance_m(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "1", "61", "-5", "0", "nan", "inf", "18,5,2"])
def test_parse_distance_rejects_non_numbers_and_out_of_range(text: str) -> None:
    assert parse_distance_m(text) is None


def test_format_distance_uses_a_decimal_comma_and_no_trailing_zeros() -> None:
    assert format_distance(18.5) == "18,5"
    assert format_distance(6.0) == "6"
    assert format_distance(19.62) == "19,62"


# --- place names ----------------------------------------------------------


def test_place_label_names_every_preset_by_its_distance() -> None:
    for position in SHOT_POSITIONS:
        assert place_label(position.distance_m) == position.label


def test_place_label_falls_back_to_metres_and_a_dash_when_unknown() -> None:
    assert place_label(17.3) == "17,3 m"
    assert place_label(None) == "—"


# --- metadata file ----------------------------------------------------------


def test_write_session_meta_records_distance_duration_and_fastest(tmp_path) -> None:
    path = write_session_meta(tmp_path, "shot-improvement-20260921120000", 18.5, 10.0, [_shot(1, 98.4), _shot(2, None), _shot(3, 112.6)])

    assert path.name == "shot-improvement-20260921120000-session.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["distance_m"] == 18.5
    assert data["duration_s"] == 10.0
    assert data["fastest_kmh"] == 112.6
    assert data["shots"] == [
        {"index": 1, "speed_kmh": 98.4}, {"index": 2, "speed_kmh": None}, {"index": 3, "speed_kmh": 112.6},
    ]
    assert not list(tmp_path.glob("*.tmp"))


def test_write_session_meta_with_no_shots_has_no_fastest(tmp_path) -> None:
    path = write_session_meta(tmp_path, "shot-improvement-20260921120000", 6.0, 10.0, [])

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["fastest_kmh"] is None
    assert data["shots"] == []


# --- sessions ---------------------------------------------------------------


def _touch(folder, *names: str) -> None:
    for name in names:
        (folder / name).write_bytes(b"x")


def test_list_sessions_groups_a_recordings_files_and_reads_its_metadata(tmp_path) -> None:
    ts = "20260921120000"
    _touch(
        tmp_path, f"shot-improvement-{ts}.mp4", f"shot-improvement-{ts}-annotated.mp4",
        f"shot-improvement-{ts}-shot-01.jpg", f"shot-improvement-{ts}-shot-02.jpg", f"shot-improvement-{ts}-hands.json",
    )
    write_session_meta(tmp_path, f"shot-improvement-{ts}", DEFAULT_SHOT_POSITION.distance_m, 10.0, [_shot(1, 90.0), _shot(2, 101.0)])

    [session] = list_sessions(tmp_path)

    assert session.timestamp == ts
    assert session.raw == tmp_path / f"shot-improvement-{ts}.mp4"
    assert session.annotated == tmp_path / f"shot-improvement-{ts}-annotated.mp4"
    assert session.play_path == session.annotated
    assert [s.index for s in session.shots] == [1, 2]
    assert [s.speed_kmh for s in session.shots] == [90.0, 101.0]
    assert session.fastest_kmh == 101.0
    assert session.place == DEFAULT_SHOT_POSITION.label
    assert session.duration_s == 10.0
    assert session.created_label == "2026-09-21 12:00:00"
    assert len(session.files) == 6  # everything, so deleting the session leaves nothing behind


def test_list_sessions_orders_newest_first_and_plays_the_raw_clip_without_an_annotated_one(tmp_path) -> None:
    _touch(tmp_path, "shot-improvement-20260920100000.mp4", "shot-improvement-20260921100000.mp4")

    sessions = list_sessions(tmp_path)

    assert [s.timestamp for s in sessions] == ["20260921100000", "20260920100000"]
    assert sessions[0].play_path == tmp_path / "shot-improvement-20260921100000.mp4"


def test_a_recording_from_before_metadata_existed_has_unknown_place_but_lists_its_shots(tmp_path) -> None:
    ts = "20260901090000"
    _touch(tmp_path, f"shot-improvement-{ts}.mp4", f"shot-improvement-{ts}-shot-01.jpg", f"shot-improvement-{ts}-shot-02.jpg")

    [session] = list_sessions(tmp_path)

    assert session.place == "—"
    assert session.duration_s is None
    assert session.fastest_kmh is None
    assert [s.speed_kmh for s in session.shots] == [None, None]


def test_list_sessions_ignores_foreign_files_and_survives_a_corrupt_metadata_file(tmp_path) -> None:
    ts = "20260921120000"
    _touch(tmp_path, f"shot-improvement-{ts}.mp4", "notes.txt", "shot-improvement-abc.mp4")
    (tmp_path / f"shot-improvement-{ts}-session.json").write_text("{not json", encoding="utf-8")

    [session] = list_sessions(tmp_path)

    assert session.place == "—"
    assert list_sessions(tmp_path / "missing") == []


def test_a_session_with_only_shot_images_and_metadata_is_still_listed_and_has_no_clip(tmp_path) -> None:
    ts = "20260921120000"
    _touch(tmp_path, f"shot-improvement-{ts}-shot-01.jpg")
    write_session_meta(tmp_path, f"shot-improvement-{ts}", 26.0, 10.0, [_shot(1, 88.0)])

    [session] = list_sessions(tmp_path)

    assert session.play_path is None
    assert session.fastest_kmh == 88.0


# --- target stored with the session ----------------------------------------------


def test_the_target_is_stored_and_names_the_place_with_it(tmp_path) -> None:
    ts = "20260921120000"
    _touch(tmp_path, f"shot-improvement-{ts}.mp4")
    write_session_meta(tmp_path, f"shot-improvement-{ts}", 22.5, 10.0, [], target=END)

    [session] = list_sessions(tmp_path)

    assert session.target == END
    assert session.place == "Sinisestä viivasta päätyyn (22,5 m)"


def test_a_session_stored_without_a_target_is_named_the_old_way(tmp_path) -> None:
    ts = "20260921120000"
    _touch(tmp_path, f"shot-improvement-{ts}.mp4")
    (tmp_path / f"shot-improvement-{ts}-session.json").write_text(
        json.dumps({"distance_m": 18.5, "duration_s": 10.0, "shots": []}), encoding="utf-8",
    )

    [session] = list_sessions(tmp_path)

    assert session.target is None
    assert session.place == DEFAULT_SHOT_POSITION.label


def test_an_unknown_stored_target_is_ignored(tmp_path) -> None:
    ts = "20260921120000"
    _touch(tmp_path, f"shot-improvement-{ts}.mp4")
    (tmp_path / f"shot-improvement-{ts}-session.json").write_text(
        json.dumps({"distance_m": 11.0, "target": "roof", "shots": []}), encoding="utf-8",
    )

    [session] = list_sessions(tmp_path)

    assert session.target is None
    assert session.place == "11 m"


# --- rink geometry: shooting places and targets ------------------------------------


def test_the_goal_target_is_the_line_core_claps_measures_to() -> None:
    from core import claps

    assert rink.TARGET_X_M[GOAL] == claps._FAR_GOAL_LINE_M
    assert rink.TARGET_X_M[END] == rink.RINK_LENGTH_M


def test_place_distances_to_the_goal() -> None:
    to_goal = {p.key: rink.distance_for(p, GOAL) for p in rink.MAP_PLACES}

    assert to_goal == {
        "end_to_end": 52.0, "faceoff_dots": 46.0, "other_blue_line": 33.5,
        "red_line": 26.0, "blue_line": 18.5, "attack_dots": 6.0,
    }


def test_place_distances_to_the_end_boards() -> None:
    to_end = {p.key: rink.distance_for(p, END) for p in rink.MAP_PLACES}

    assert to_end == {
        "end_to_end": 57.0,  # the original 57 m measurement, kept as asked (the geometry says 56)
        "faceoff_dots": 50.0, "other_blue_line": 37.5, "red_line": 30.0,
        "blue_line": 22.5,  # the blue line to the end boards: the old "camera on the goal" preset
        "attack_dots": 10.0,
    }


def test_describing_a_goal_shot_reads_like_the_old_presets() -> None:
    for key in ("blue_line", "red_line", "other_blue_line", "faceoff_dots", "attack_dots"):
        preset = next(p for p in SHOT_POSITIONS if p.key == key)
        assert rink.describe(preset.distance_m, GOAL) == preset.label


def test_describe_names_place_and_target_or_falls_back_to_metres() -> None:
    assert rink.describe(22.5, END) == "Sinisestä viivasta päätyyn (22,5 m)"
    assert rink.describe(57.0, END) == "Päätyviivalta päätyyn (57 m)"
    assert rink.describe(11.0, GOAL) == "11 m maaliin"
    assert rink.describe(11.0, END) == "11 m päätyyn"
    assert rink.describe(None, GOAL) == "—"


def test_a_chosen_distance_puts_the_shooter_on_its_places_line_else_short_of_the_target() -> None:
    assert rink.shooter_x(18.5, GOAL) == 37.5
    assert rink.shooter_x(22.5, END) == 37.5
    assert rink.shooter_x(57.0, END) == 4.0  # the own goal line, not 3 m behind the boards
    assert rink.shooter_x(11.0, GOAL) == 45.0
    assert rink.shooter_x(11.0, END) == 49.0


def test_clicking_near_a_place_snaps_to_its_distance_to_the_target() -> None:
    assert rink.distance_for_click(37.0, GOAL) == 18.5
    assert rink.distance_for_click(37.0, END) == 22.5
    assert rink.distance_for_click(30.9, GOAL) == 26.0
    assert rink.distance_for_click(4.5, END) == 57.0
    assert rink.distance_for_click(4.5, GOAL) == 52.0


def test_clicking_elsewhere_gives_the_metres_to_the_target() -> None:
    assert rink.distance_for_click(45.0, GOAL) == 11.0
    assert rink.distance_for_click(45.0, END) == 15.0
    assert rink.distance_for_click(15.33, GOAL) == 40.7


def test_clicking_leaves_no_room_for_a_shot_behind_or_right_at_the_target() -> None:
    assert rink.distance_for_click(55.0, GOAL) is None  # 1 m
    assert rink.distance_for_click(58.0, GOAL) is None  # behind the goal line
    assert rink.distance_for_click(58.5, END) is None  # 1.5 m
    assert rink.distance_for_click(56.5, END) == 3.5  # still fits when the end boards are the target


def test_changing_the_target_keeps_the_shooter_where_they_stand() -> None:
    assert rink.distance_after_target_change(18.5, GOAL, END) == 22.5  # a named place: its own distance
    assert rink.distance_after_target_change(22.5, END, GOAL) == 18.5
    assert rink.distance_after_target_change(57.0, END, GOAL) == 52.0
    assert rink.distance_after_target_change(11.0, GOAL, END) == 15.0  # anywhere else: 4 m more
    assert rink.distance_after_target_change(15.0, END, GOAL) == 11.0


def test_changing_the_target_clamps_to_what_can_be_entered() -> None:
    assert rink.distance_after_target_change(58.0, GOAL, END) == rink.MAX_DISTANCE_M
    assert rink.distance_after_target_change(3.0, END, GOAL) == rink.MIN_DISTANCE_M
