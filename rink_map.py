"""The rink map for picking where a shot is taken from and what it hits
(spec 137): an IIHF rink drawn on a Tk canvas, the shooter's end on the
left and the target end on the right. The named places (blue line, red
line, ...) are dashed lines with a chip showing their distance to the
current target; a click on a chip or line sets that place, a click
anywhere else on the ice sets the distance to the target at that spot.
Clicking the goal or the end boards picks the target (highlighted in
orange). It only ever reports a distance in metres and a target - the
distance field beside it stays the one source of truth (core.rink has the
geometry, without any Tk)."""

from __future__ import annotations

import math
import tkinter as tk
from typing import Callable, Optional

from core import rink
from core.rink import format_distance

MARGIN_X = 14
MARGIN_TOP = 24
CHIP_HEIGHT = 16
MARGIN_BOTTOM = 8
SCALE = 5.6  # pixels per metre: the 60 m rink is 336 px wide

# Click zones for the targets, in rink metres: the goal (a little more
# than its own footprint, so it is easy to hit) and the end boards strip.
GOAL_ZONE_X_M = (rink.FAR_GOAL_LINE_M - 0.6, rink.FAR_GOAL_LINE_M + rink.GOAL_DEPTH_M + 0.3)
GOAL_ZONE_HALF_Y_M = 2.0
END_ZONE_X_M = rink.RINK_LENGTH_M - 1.0

ICE = "#eef6fb"
BOARDS = "#5b6770"
RED = "#c0392b"
BLUE = "#2f6fb3"
PLACE_LINE = "#1e8449"
SELECTED = "#e67e22"
GOAL_FILL = "#f8d7da"
GOAL_FILL_TARGET = "#f5b041"


class RinkMap(tk.Canvas):
    def __init__(
        self,
        parent,
        on_pick: Callable[[float], None],
        on_pick_target: Callable[[str], None] = lambda target: None,
        on_hover: Callable[[str], None] = lambda text: None,
        target: str = rink.DEFAULT_TARGET,
    ) -> None:
        width = int(rink.RINK_LENGTH_M * SCALE) + 2 * MARGIN_X
        height = int(rink.RINK_WIDTH_M * SCALE) + MARGIN_TOP + MARGIN_BOTTOM
        super().__init__(parent, width=width, height=height, highlightthickness=0, background="#f4f4f4")
        self._on_pick = on_pick
        self._on_pick_target = on_pick_target
        self._on_hover = on_hover
        self._target = target
        self._distance_m: Optional[float] = None
        self._draw_rink()
        self._draw_dynamic()
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._on_hover(""))

    # --- geometry ---------------------------------------------------------

    def _px(self, x_m: float) -> float:
        return MARGIN_X + x_m * SCALE

    def _py(self, y_m: float) -> float:
        return MARGIN_TOP + y_m * SCALE

    def x_m_at(self, px: float) -> float:
        return (px - MARGIN_X) / SCALE

    def y_m_at(self, py: float) -> float:
        return (py - MARGIN_TOP) / SCALE

    def _inside_rink(self, event) -> bool:
        return (
            MARGIN_X <= event.x <= MARGIN_X + rink.RINK_LENGTH_M * SCALE
            and MARGIN_TOP <= event.y <= MARGIN_TOP + rink.RINK_WIDTH_M * SCALE
        )

    # --- drawing: the rink itself -------------------------------------------

    def _outline_points(self) -> list[float]:
        """The rink outline: a rectangle with rounded corners."""
        r = rink.CORNER_RADIUS_M
        length, width = rink.RINK_LENGTH_M, rink.RINK_WIDTH_M
        corners = (  # (centre x, centre y, start angle in degrees), clockwise from top-left
            (r, r, 180), (length - r, r, 270), (length - r, width - r, 0), (r, width - r, 90),
        )
        points: list[float] = []
        for cx, cy, start in corners:
            for step in range(0, 10):
                angle = math.radians(start + step * 10)
                points += [self._px(cx + r * math.cos(angle)), self._py(cy + r * math.sin(angle))]
        return points

    def _draw_rink(self) -> None:
        self.create_polygon(self._outline_points(), fill=ICE, outline=BOARDS, width=2)
        mid_y = rink.RINK_WIDTH_M / 2
        top, bottom = self._py(0), self._py(rink.RINK_WIDTH_M)
        # centre and blue lines, goal lines (a goal line is only drawn between the boards' curves)
        self.create_line(self._px(rink.CENTER_LINE_X_M), top, self._px(rink.CENTER_LINE_X_M), bottom, fill=RED, width=3)
        for x in rink.BLUE_LINE_X_M:
            self.create_line(self._px(x), top, self._px(x), bottom, fill=BLUE, width=4)
        for x in (rink.GOAL_LINE_OFFSET_M, rink.FAR_GOAL_LINE_M):
            self.create_line(self._px(x), self._py(1.0), self._px(x), self._py(rink.RINK_WIDTH_M - 1.0), fill=RED, width=1)
        # the near goal (the goal shot at is drawn with the target, in _draw_dynamic)
        half = rink.GOAL_WIDTH_M / 2
        self.create_rectangle(
            self._px(rink.GOAL_LINE_OFFSET_M - rink.GOAL_DEPTH_M), self._py(mid_y - half),
            self._px(rink.GOAL_LINE_OFFSET_M), self._py(mid_y + half), outline=RED, fill=GOAL_FILL,
        )
        # centre circle and dot, end-zone face-off circles and spots
        radius = rink.FACEOFF_CIRCLE_RADIUS_M
        self._circle(rink.CENTER_LINE_X_M, mid_y, radius, BLUE)
        self._dot(rink.CENTER_LINE_X_M, mid_y, 1.0, BLUE)
        for x in rink.FACEOFF_SPOT_X_M:
            for y in rink.FACEOFF_SPOT_Y_M:
                self._circle(x, y, radius, RED)
                self._dot(x, y, 0.6, RED)

    def _circle(self, x_m: float, y_m: float, r_m: float, color: str) -> None:
        self.create_oval(
            self._px(x_m - r_m), self._py(y_m - r_m), self._px(x_m + r_m), self._py(y_m + r_m), outline=color,
        )

    def _dot(self, x_m: float, y_m: float, r_m: float, color: str) -> None:
        self.create_oval(
            self._px(x_m - r_m), self._py(y_m - r_m), self._px(x_m + r_m), self._py(y_m + r_m), fill=color, outline=color,
        )

    # --- drawing: what depends on the target and the chosen distance --------------

    def _draw_dynamic(self) -> None:
        """The target's highlight, the named places (their chips show the
        distance to the current target) and the chosen distance's marker.
        Redrawn whenever the target changes."""
        self.delete("dyn")
        mid_y = rink.RINK_WIDTH_M / 2
        half = rink.GOAL_WIDTH_M / 2
        goal_is_target = self._target == rink.GOAL
        self.create_rectangle(
            self._px(rink.FAR_GOAL_LINE_M), self._py(mid_y - half),
            self._px(rink.FAR_GOAL_LINE_M + rink.GOAL_DEPTH_M), self._py(mid_y + half),
            outline=SELECTED if goal_is_target else RED, fill=GOAL_FILL_TARGET if goal_is_target else GOAL_FILL,
            width=2 if goal_is_target else 1, tags="dyn",
        )
        if self._target == rink.END:
            # the end boards, along the straight part between the corners
            edge = rink.CORNER_RADIUS_M
            x = self._px(rink.RINK_LENGTH_M)
            self.create_line(x, self._py(edge), x, self._py(rink.RINK_WIDTH_M - edge), fill=SELECTED, width=5, tags="dyn")
        for place in rink.MAP_PLACES:
            x = self._px(place.x_m)
            self.create_line(
                x, self._py(0), x, self._py(rink.RINK_WIDTH_M), fill=PLACE_LINE, width=2, dash=(4, 3), tags="dyn",
            )
            # A green chip with the distance to the target: also clickable, like the line.
            label = format_distance(rink.distance_for(place, self._target))
            chip_half = 4 + 3 * len(label)
            self.create_rectangle(
                x - chip_half, 2, x + chip_half, 2 + CHIP_HEIGHT, fill=PLACE_LINE, outline=PLACE_LINE, tags="dyn",
            )
            self.create_text(
                x, 2 + CHIP_HEIGHT / 2, text=label, fill="white", font=("TkDefaultFont", 8, "bold"), tags="dyn",
            )
        self._draw_marker()

    def _draw_marker(self) -> None:
        self.delete("selected")
        if self._distance_m is None:
            return
        x_m = rink.shooter_x(self._distance_m, self._target)
        if not (0.0 <= x_m <= rink.RINK_LENGTH_M):
            return
        x, y = self._px(x_m), self._py(rink.RINK_WIDTH_M / 2)
        self.create_line(
            x, y, self._px(rink.TARGET_X_M[self._target]), y, fill=SELECTED, width=2, arrow=tk.LAST, tags=("dyn", "selected"),
        )
        self.create_oval(x - 6, y - 6, x + 6, y + 6, fill=SELECTED, outline="white", width=2, tags=("dyn", "selected"))

    def set_target(self, target: str) -> None:
        if target != self._target:
            self._target = target
            self._draw_dynamic()

    def set_distance(self, distance_m: Optional[float]) -> None:
        """Marks the chosen distance: the shooter's spot and an arrow to
        the target. None (or a distance that does not fit) shows no marker."""
        self._distance_m = distance_m
        self._draw_marker()

    # --- interaction --------------------------------------------------------

    def _on_chip(self, event) -> bool:
        return 2 <= event.y <= 2 + CHIP_HEIGHT

    def _target_zone(self, event) -> Optional[str]:
        """The target a click at this point would pick, if it is on the goal or the end boards."""
        x_m, y_m = self.x_m_at(event.x), self.y_m_at(event.y)
        if GOAL_ZONE_X_M[0] <= x_m <= GOAL_ZONE_X_M[1] and abs(y_m - rink.RINK_WIDTH_M / 2) <= GOAL_ZONE_HALF_Y_M:
            return rink.GOAL
        if x_m >= END_ZONE_X_M:
            return rink.END
        return None

    def _shooter_spot(self, event) -> tuple[Optional[float], Optional[rink.MapPlace]]:
        """(distance, named place) for a click that sets the shooting place."""
        x_m = self.x_m_at(event.x)
        if self._on_chip(event):
            place = rink.place_at(x_m)
            return (rink.distance_for(place, self._target), place) if place is not None else (None, None)
        if not self._inside_rink(event):
            return None, None
        return rink.distance_for_click(x_m, self._target), rink.place_at(x_m)

    def _click(self, event) -> None:
        if self._inside_rink(event) and not self._on_chip(event):
            zone = self._target_zone(event)
            if zone is not None:
                self._on_pick_target(zone)
                return
        distance, _place = self._shooter_spot(event)
        if distance is not None:
            self._on_pick(distance)

    def _motion(self, event) -> None:
        if self._inside_rink(event) and not self._on_chip(event):
            zone = self._target_zone(event)
            if zone is not None:
                self.config(cursor="hand2")
                self._on_hover(f"Kohde: {rink.TARGET_NAMES[zone].lower()}")
                return
        distance, place = self._shooter_spot(event)
        if distance is None:
            self.config(cursor="")
            self._on_hover("")
            return
        self.config(cursor="hand2")
        self._on_hover(rink.describe(distance, self._target))
