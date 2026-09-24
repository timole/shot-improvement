"""Per-recording session metadata (spec 137): where the shots were taken
from, how long the recording was and how fast each shot was, stored next
to the recording as "shot-improvement-<ts>-session.json", plus the
grouping of a recordings folder into sessions (raw clip, annotated clip,
shot images, sidecars) that the GUI's recordings list shows.

Pure logic - no Tk, no cv2 - so it is testable without a display."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .claps import SHOT_POSITIONS
from .log_setup import get_logger
# parse_distance_m / format_distance / the distance limits live in core.rink
# now; re-exported here, where they first lived.
from .rink import (  # noqa: F401
    DEFAULT_TARGET, MAX_DISTANCE_M, MIN_DISTANCE_M, TARGETS, describe, format_distance, parse_distance_m,
)

logger = get_logger("session_meta")

SESSION_SUFFIX = "-session.json"
META_VERSION = 1

_NAME_RE = re.compile(
    r"^shot-improvement-(?P<ts>\d{14})"
    r"(?P<kind>-annotated\.mp4|\.mp4|-shot-(?P<n>\d+)\.jpg|-session\.json|-hands\.json)$"
)


def place_label(distance_m: Optional[float]) -> str:
    """The name of the shooting place for a distance: a named preset from
    core.claps.SHOT_POSITIONS when the distance is (within a few cm) that
    preset's, else just "N m". "—" when unknown (a recording made before
    session metadata existed)."""
    if distance_m is None:
        return "—"
    for position in SHOT_POSITIONS:
        if abs(position.distance_m - distance_m) < 0.005:
            return position.label
    return f"{format_distance(distance_m)} m"


def _fastest(speeds: Iterable[Optional[float]]) -> Optional[float]:
    known = [s for s in speeds if s is not None]
    return max(known) if known else None


def write_session_meta(
    out_dir: Path, filename_stem: str, distance_m: float, duration_s: float, shots: Iterable,
    target: str = DEFAULT_TARGET,
) -> Path:
    """Writes "<filename_stem>-session.json". `shots` are objects with
    .index (1-based, the "-shot-NN.jpg" suffix) and .speed_kmh (None when
    the shot had no audible hit) - core.compose.ShotImage fits. Written
    via a temp file so a crash never leaves half a JSON behind."""
    shot_list = [{"index": s.index, "speed_kmh": s.speed_kmh} for s in shots]
    data = {
        "version": META_VERSION,
        "distance_m": distance_m,
        "target": target,  # what the puck hits: "goal" or "end" (core.rink)
        "duration_s": round(duration_s, 3),
        "fastest_kmh": _fastest(s["speed_kmh"] for s in shot_list),
        "shots": shot_list,
    }
    out_path = out_dir / f"{filename_stem}{SESSION_SUFFIX}"
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp_path.replace(out_path)
    return out_path


def read_session_meta(path: Path) -> Optional[dict]:
    """The parsed JSON, or None if missing/unreadable/not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@dataclass
class SessionShot:
    index: int
    path: Path
    speed_kmh: Optional[float]


@dataclass
class Session:
    timestamp: str  # 14 digits, YYYYmmddHHMMSS
    raw: Optional[Path] = None
    annotated: Optional[Path] = None
    shots: list[SessionShot] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)  # everything that belongs to it, for deleting
    distance_m: Optional[float] = None
    duration_s: Optional[float] = None
    target: Optional[str] = None  # None: recorded before the target was stored (it was the goal)

    @property
    def fastest_kmh(self) -> Optional[float]:
        return _fastest(s.speed_kmh for s in self.shots)

    @property
    def play_path(self) -> Optional[Path]:
        """What double-click plays: the annotated clip when there is one."""
        return self.annotated or self.raw

    @property
    def created_label(self) -> str:
        try:
            return datetime.strptime(self.timestamp, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return self.timestamp

    @property
    def place(self) -> str:
        if self.target is not None and self.distance_m is not None:
            return describe(self.distance_m, self.target)
        return place_label(self.distance_m)


def list_sessions(recordings_dir: Path) -> list[Session]:
    """One Session per recording timestamp found in recordings_dir, newest
    first. A recording made before this metadata existed has no
    distance/speeds (place "—"); its shot images are still listed."""
    by_ts: dict[str, Session] = {}
    meta_paths: dict[str, Path] = {}
    if not recordings_dir.is_dir():
        return []
    for path in sorted(recordings_dir.iterdir()):
        match = _NAME_RE.match(path.name)
        if match is None:
            continue
        session = by_ts.setdefault(match["ts"], Session(timestamp=match["ts"]))
        session.files.append(path)
        kind = match["kind"]
        if kind == ".mp4":
            session.raw = path
        elif kind == "-annotated.mp4":
            session.annotated = path
        elif kind == SESSION_SUFFIX:
            meta_paths[match["ts"]] = path
        elif match["n"] is not None:
            session.shots.append(SessionShot(index=int(match["n"]), path=path, speed_kmh=None))

    for ts, session in by_ts.items():
        session.shots.sort(key=lambda s: s.index)
        meta_path = meta_paths.get(ts)
        meta = read_session_meta(meta_path) if meta_path else None
        if meta is None:
            continue
        distance = meta.get("distance_m")
        duration = meta.get("duration_s")
        session.distance_m = float(distance) if isinstance(distance, (int, float)) else None
        session.duration_s = float(duration) if isinstance(duration, (int, float)) else None
        target = meta.get("target")
        session.target = target if target in TARGETS else None
        speeds = {
            s.get("index"): s.get("speed_kmh")
            for s in meta.get("shots", [])
            if isinstance(s, dict)
        }
        for shot in session.shots:
            speed = speeds.get(shot.index)
            shot.speed_kmh = float(speed) if isinstance(speed, (int, float)) else None
    return sorted(by_ts.values(), key=lambda s: s.timestamp, reverse=True)
