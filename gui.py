"""Native Tkinter GUI for shot-improvement.

Shows a live, continuously palm-box-annotated camera preview from
startup, in the same area natively plays back a saved recording (no
external player), lets you pick which camera/microphone/speaker to
use, and lists saved recordings with delete.

Run: venv\\Scripts\\python gui.py
"""

from __future__ import annotations

import re
import threading
import time
import tkinter as tk
import winsound
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Optional

import cv2
from PIL import Image, ImageTk

from core import azure_sync, cloud_sync, devices, pending
from core.status_publish import StatusPublisher
from core.claps import DEFAULT_SHOT_POSITION, SHOT_POSITIONS
from core.compose import ShotImage, select_shot_frame_range
from core.log_setup import get_logger, setup_logging
from core.playback import SKIP_SECONDS, SPEED_OPTIONS, ClipPlayer, format_time
from core.recorder import FRAME_FILE_EXTENSION, FRAME_HEIGHT, FRAME_WIDTH, RECORDINGS_DIR
from core.segmentation import BackgroundRemover
from core.session import LiveSession, RecordingResult, ShotSource

setup_logging()
logger = get_logger("gui")

# Frames are always shown resized to this fixed on-screen size, NOT
# the camera's native capture size (1280x720, FRAME_WIDTH/FRAME_HEIGHT
# - unaffected by this, still what's actually recorded/encoded) - a
# smaller preview window is easier to fit alongside the recordings
# list on a small laptop screen. 640x360 keeps the same 16:9 aspect
# ratio as the native 1280x720 capture (exactly half each dimension).
# When the source frame already matches this size, _show_frame skips
# resizing entirely; a mismatched source (any size, including an
# annotated frame's taller spectrogram-panel composite) gets a plain
# cv2.resize rather than the arbitrary-ratio PIL resize this used to
# do on every single displayed frame (live preview, played frame, or a
# single stepped frame alike).
DISPLAY_SIZE = (640, 360)
# Visible rows in the recordings list (the rest scroll).
RECORDINGS_LIST_ROWS = 6
DEFAULT_DURATION_S = 10
PREVIEW_POLL_MS = 10  # self-paced anyway - actual cadence follows the work each tick does
# Spec 091: a 3-2-1 countdown before capture actually starts, one
# second per step, with a short beep on each step so the user (who is
# presumably in front of the camera, not looking at the screen) knows
# when to get ready without watching the status label.
COUNTDOWN_SECONDS = 3
COUNTDOWN_BEEP_FREQ_HZ = 880
COUNTDOWN_BEEP_MS = 150
# _tick() runs the whole GUI's live preview/playback loop; one call
# taking much longer than that is worth a log line (see LiveSession's
# own, tighter, SLOW_FRAME_WARN_THRESHOLD_S for the read_frame()-level
# version of this same idea).
SLOW_TICK_WARN_THRESHOLD_S = 1.0

_FILENAME_TIMESTAMP_RE = re.compile(r"shot-improvement-(\d{14})(?:-annotated|-shot-\d+)?\.(?:mp4|jpg)$")

# Spec 108: raw shot preview's own speed choices - deliberately not
# core.playback.SPEED_OPTIONS (which includes 1.5x) - the user asked
# for exactly these four.
RAW_SHOT_SPEED_OPTIONS = (0.25, 0.5, 1.0, 2.0)
RAW_PLAY_TEXT = "▶ Toista raakakuvaa"
RAW_PAUSE_TEXT = "❚❚ Pysäytä"


def _device_choices(raw_devices: list[dict]) -> list[str]:
    return [f"{i}: {info['name']}" for i, info in enumerate(raw_devices)]


def _speed_label(speed: float) -> str:
    return f"{speed:g}x"


_SPEED_LABELS = [_speed_label(s) for s in SPEED_OPTIONS]


def _speed_from_label(label: str) -> float:
    return SPEED_OPTIONS[_SPEED_LABELS.index(label)]


_RAW_SHOT_SPEED_LABELS = [_speed_label(s) for s in RAW_SHOT_SPEED_OPTIONS]


def _raw_shot_speed_from_label(label: str) -> float:
    return RAW_SHOT_SPEED_OPTIONS[_RAW_SHOT_SPEED_LABELS.index(label)]


def _recording_timestamp(path: Path) -> str:
    """The 14-digit timestamp in a recording file's name - lets a
    recording's mp4s and shot images sort together (stable sort keeps
    name order within one recording)."""
    match = re.search(r"(\d{14})", path.name)
    return match.group(1) if match else ""


def _created_label(path: Path) -> str:
    """The recording's creation time, parsed from its own filename
    timestamp (shot-improvement-YYYYMMDDHHMMSS[-annotated].mp4) rather
    than filesystem mtime, which a copy/move could change."""
    match = _FILENAME_TIMESTAMP_RE.search(path.name)
    if not match:
        return ""
    try:
        dt = datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Shot improvement")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.session: Optional[LiveSession] = None
        self._mode = "live"  # "live" | "playback" | "shots" - see _on_shots_ready/_exit_shots_mode
        self._player: Optional[ClipPlayer] = None
        self._scrubbing = False  # user is dragging the timeline slider - suppress auto position updates
        self._next_frame_due = 0.0  # time.monotonic() value; paces _playback_tick() by player.frame_interval_s()
        self._background_remover: Optional[BackgroundRemover] = None  # lazy - model load is slow, reused after first use
        self._segmenting = False  # a background-removal worker thread is currently running
        self._photo_image = None  # keep a reference alive - Tkinter/PhotoImage gotcha
        self._counting_down = False  # the 3-2-1 countdown is running, before actual capture starts
        # Spec 093: "Vain nauhoitus" (recording only) mode - see
        # _build_record_row for the checkbox and on_process_pending_click
        # for the queue this list feeds.
        self._pending_items: list[pending.PendingRecording] = []
        self._processing_pending = False
        # Spec 106: the shot list shown right after a recording (or a
        # processed pending item) finishes - see _on_shots_ready,
        # _show_shot, _exit_shots_mode.
        self._shots: list[ShotImage] = []
        # Spec 107/108: where this shot list's raw frames live, for
        # playing a per-shot raw preview directly - see
        # on_raw_shot_play_pause_click. _shot_frame_cache avoids
        # re-globbing raw_dir on a repeat play of the same shot.
        self._shot_source: Optional[ShotSource] = None
        self._shot_frame_cache: dict[int, tuple[list[Path], list[float], int]] = {}
        self._raw_playing = False
        self._raw_play_after_id: Optional[str] = None
        self._default_device_labels: Optional[tuple[str, str, str]] = None
        self._raw_frame_pos = 0
        self._raw_window: Optional[tuple[list[Path], list[float]]] = None  # selected shot's frames (paths, times)
        self._raw_scrubbing = False  # user is dragging the raw slider
        self._raw_scale_setting = False  # slider is being moved by code, not the user

        # Two columns: left_frame holds every control (camera/device
        # pickers, record row, transport buttons, speed/volume/
        # loop/background-removal tools, status) except the timeline,
        # and - below all of that - the recordings list; center_frame
        # holds only the video (shown at DISPLAY_SIZE, 640x360 - see
        # that constant's docstring) and, right under it, the timeline
        # scrubber. Kept as separate containers so each can be built/
        # packed independently in _apply_layout().
        main = ttk.Frame(root)
        main.pack(fill="both", expand=True)
        self.left_frame = ttk.Frame(main)
        self.center_frame = ttk.Frame(main)

        self._build_device_row(self.left_frame)

        self.preview_label = tk.Label(self.center_frame, background="#000")
        self.preview_label.pack(padx=8, pady=8)
        # Spec 125: shown only while a still image is open from the list.
        self.image_back_button = ttk.Button(
            self.center_frame, text="Takaisin livekuvaan", command=lambda: self._exit_image_mode(),
        )

        self._build_record_row(self.left_frame)
        self._build_playback_controls(self.left_frame, self.center_frame)
        self._build_shots_list(self.left_frame)
        self._build_raw_controls(self.center_frame)

        self.status_var = tk.StringVar(value="Käynnistetään kameraa…")
        self.status_label = ttk.Label(self.left_frame, textvariable=self.status_var)
        self.status_label.pack(pady=(0, 8))

        # Spec 105: shows the most recent recording's temp folder
        # (raw/annotated frames + audio.wav, spec 105 temporarily kept
        # on disk instead of auto-deleted - see core.session.
        # KEEP_TEMP_DIR_FOR_INSPECTION) - empty/hidden whenever that
        # flag is off, or before any recording has finished yet.
        self.tmp_dir_var = tk.StringVar(value="")
        self.tmp_dir_label = ttk.Label(
            self.left_frame, textvariable=self.tmp_dir_var, foreground="#555", wraplength=280, justify="left",
        )
        self.tmp_dir_label.pack(pady=(0, 8))

        self._build_list(self.left_frame)
        self._apply_layout()

        self._video_paths: list[Path] = []
        self.refresh_recordings()
        # Independent of the camera/session (spec 093: processing a
        # pending queue never touches the camera) - shown immediately,
        # even before _init_session runs, so a backlog from a previous
        # session is visible and processable even if the camera fails
        # to open this time.
        self.refresh_pending()

        # Spec 087/094/095: uploads new clips (raw+annotated+preview) to
        # Azure Blob Storage and deletes ones removed locally (spec 123:
        # Azure is the only backend). Runs on its own background thread
        # (see cloud_sync.SyncWorker) - starting it never blocks camera
        # startup below.
        self._sync_worker = cloud_sync.SyncWorker(
            backends=[azure_sync.AzureBlobBackend()],
            dispatch=lambda fn: self.root.after(0, fn),
        )
        self._sync_worker.start_reconcile(on_done=self._on_sync_reconciled)
        # Spec 120: live status for the website (countdown, fastest shot).
        self._status = StatusPublisher()
        self._current_rec_id = ""
        self._recording_deferred = False

        self.root.after(0, self._init_session)

    # --- layout ------------------------------------------------------

    def _build_device_row(self, root: tk.Tk) -> None:
        frame = ttk.Frame(root)
        frame.pack(fill="x", padx=8, pady=(8, 0))

        ttk.Label(frame, text="Kamera:").grid(row=0, column=0, sticky="w")
        self.camera_var = tk.StringVar()
        self.camera_combo = ttk.Combobox(frame, textvariable=self.camera_var, state="readonly", width=32)
        self.camera_combo.grid(row=0, column=1, sticky="w", padx=(4, 12))
        self.camera_combo.bind("<<ComboboxSelected>>", self.on_camera_selected)

        ttk.Label(frame, text="Mikrofoni:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.mic_var = tk.StringVar()
        self.mic_combo = ttk.Combobox(frame, textvariable=self.mic_var, state="readonly", width=32)
        self.mic_combo.grid(row=1, column=1, sticky="w", padx=(4, 12), pady=(4, 0))
        self.mic_combo.bind("<<ComboboxSelected>>", self.on_mic_selected)

        ttk.Label(frame, text="Kaiutin:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.speaker_var = tk.StringVar()
        self.speaker_combo = ttk.Combobox(frame, textvariable=self.speaker_var, state="readonly", width=32)
        self.speaker_combo.grid(row=2, column=1, sticky="w", padx=(4, 12), pady=(4, 0))
        self.speaker_combo.bind("<<ComboboxSelected>>", self.on_speaker_selected)

        self.camera_info_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.camera_info_var, foreground="#555").grid(
            row=0, column=2, sticky="w", padx=(8, 0)
        )

    def _build_record_row(self, root: tk.Tk) -> None:
        frame = ttk.Frame(root)
        frame.pack(pady=(0, 4))
        ttk.Label(frame, text="Kesto (s):").pack(side="left", padx=(0, 4))
        self.duration_var = tk.StringVar(value=str(DEFAULT_DURATION_S))
        self.duration_spinbox = ttk.Spinbox(
            frame, from_=1, to=60, increment=1, textvariable=self.duration_var, width=5
        )
        self.duration_spinbox.pack(side="left")
        self.record_button = ttk.Button(frame, text="Tallenna", command=self.on_record_click, state="disabled")
        self.record_button.pack(side="left", padx=(8, 0))
        # Spec 129: the button says how long the recording will be.
        self.duration_var.trace_add("write", lambda *_: self._update_record_button_text())
        self._update_record_button_text()

        # Spec 128: where the shot is taken from - sets the distance the
        # puck's speed is computed over (core.claps.SHOT_POSITIONS).
        position_row = ttk.Frame(root)
        position_row.pack(pady=(0, 4))
        ttk.Label(position_row, text="Ammuntapaikka:").pack(side="left", padx=(0, 4))
        self.shot_position_var = tk.StringVar(value=DEFAULT_SHOT_POSITION.label)
        ttk.Combobox(
            position_row, textvariable=self.shot_position_var, state="readonly", width=44,
            values=[p.label for p in SHOT_POSITIONS],
        ).pack(side="left")

        # Spec 093: post-capture processing (pose annotation,
        # spectrogram, ffmpeg encoding) is CPU-heavy enough on modest
        # hardware to noticeably slow down a NEW recording started while
        # it runs, even with spec 091's own priority handling - this
        # checkbox lets the user skip that contention entirely: capture
        # only, nothing else, until "Käsittele odottavat" is clicked.
        # Spec 114: defaults to checked - measured directly on this
        # hardware (3.83GB RAM) that starting a new recording while a
        # previous one's background annotation pass is still running
        # isn't just slower, it can fail outright (one real test
        # recording captured 0 of ~330 expected frames, every camera
        # read failing for the full 10s, free memory measured as low as
        # ~97MB during a 3-recording burst) - deferred processing avoids
        # that contention entirely, so it's the safer default here.
        defer_row = ttk.Frame(root)
        defer_row.pack(pady=(0, 4))
        self.defer_processing_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            defer_row, text="Vain nauhoitus (käsittele myöhemmin)", variable=self.defer_processing_var,
        ).pack(side="left")

        pending_row = ttk.Frame(root)
        pending_row.pack(pady=(0, 4))
        self.pending_var = tk.StringVar(value="")
        ttk.Label(pending_row, textvariable=self.pending_var, foreground="#555").pack(side="left")
        self.process_pending_button = ttk.Button(
            pending_row, text="Käsittele odottavat", command=self.on_process_pending_click, state="disabled",
        )
        self.process_pending_button.pack(side="left", padx=(8, 0))

        # Spec 133: back to the state of a fresh start.
        reset_row = ttk.Frame(root)
        reset_row.pack(pady=(0, 4))
        ttk.Button(reset_row, text="Palauta oletukset", command=self.on_reset_click).pack(side="left")

    def _build_playback_controls(self, controls_root: tk.Tk, timeline_root: tk.Tk) -> None:
        # Both hidden (not packed) while mode == "live"; shown together
        # for as long as a clip is loaded, whether it's actually
        # playing or paused - see spec 084. Built once, up front, so
        # on_play_selected() etc. only ever need to pack/unpack these
        # two frames. playback_controls_frame holds everything except
        # the timeline (lives in the left column); timeline_frame holds
        # just the scrubber + time label (lives under the video, in the
        # center column).
        self.playback_controls_frame = ttk.Frame(controls_root)
        self.timeline_frame = ttk.Frame(timeline_root)

        transport = ttk.Frame(self.playback_controls_frame)
        transport.pack(pady=(4, 0))
        # -1/+1 ruutu are plain text, not icons: unlike play/pause/stop,
        # there's no universal single-glyph symbol for "step one frame"
        # - the previous "⏮"/"⏭" (conventionally "skip to start/end" or
        # "previous/next track") worked identically but were confusing
        # enough that this functionality went undiscovered.
        ttk.Button(transport, text="-1 ruutu", width=8, command=self.on_prev_frame_click).pack(side="left", padx=1)
        ttk.Button(transport, text="⏪5s", width=4, command=self.on_skip_back_click).pack(side="left", padx=1)
        self.play_pause_button = ttk.Button(transport, text="▶", width=3, command=self.on_play_pause_click)
        self.play_pause_button.pack(side="left", padx=1)
        ttk.Button(transport, text="■", width=3, command=self.on_stop_click).pack(side="left", padx=1)
        ttk.Button(transport, text="5s⏩", width=4, command=self.on_skip_forward_click).pack(side="left", padx=1)
        ttk.Button(transport, text="+1 ruutu", width=8, command=self.on_next_frame_click).pack(side="left", padx=1)
        ttk.Button(transport, text="Takaisin livekuvaan", command=self.on_back_to_live_click).pack(
            side="left", padx=(12, 0)
        )

        self.scrub_var = tk.DoubleVar(value=0.0)
        self.scrub_scale = ttk.Scale(
            self.timeline_frame, from_=0.0, to=1.0, variable=self.scrub_var, orient="horizontal"
        )
        self.scrub_scale.pack(side="left", fill="x", expand=True)
        self.scrub_scale.bind("<ButtonPress-1>", self.on_scrubber_press)
        self.scrub_scale.bind("<ButtonRelease-1>", self.on_scrubber_release)
        self.time_var = tk.StringVar(value="0:00.0 / 0:00.0")
        ttk.Label(self.timeline_frame, textvariable=self.time_var, width=14).pack(side="left", padx=(8, 0))

        options_row = ttk.Frame(self.playback_controls_frame)
        options_row.pack(pady=(4, 4))
        ttk.Label(options_row, text="Nopeus:").pack(side="left")
        self.speed_var = tk.StringVar(value=_speed_label(1.0))
        speed_combo = ttk.Combobox(
            options_row, textvariable=self.speed_var, state="readonly", width=6,
            values=_SPEED_LABELS,
        )
        speed_combo.pack(side="left", padx=(4, 12))
        speed_combo.bind("<<ComboboxSelected>>", self.on_speed_changed)

        ttk.Label(options_row, text="Äänenvoimakkuus:").pack(side="left")
        self.volume_var = tk.DoubleVar(value=1.0)
        volume_scale = ttk.Scale(
            options_row, from_=0.0, to=1.0, variable=self.volume_var, orient="horizontal", length=100,
            command=self.on_volume_changed,
        )
        volume_scale.pack(side="left", padx=(4, 4))
        self.mute_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_row, text="Mykistä", variable=self.mute_var, command=self.on_mute_toggle).pack(
            side="left", padx=(0, 12)
        )

        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_row, text="Toista silmukassa", variable=self.loop_var, command=self.on_loop_toggle).pack(
            side="left"
        )

        frame_tools_row = ttk.Frame(self.playback_controls_frame)
        frame_tools_row.pack(pady=(0, 4))
        ttk.Button(frame_tools_row, text="Häivytä tausta", command=self.on_remove_background_click).pack(side="left")
        ttk.Label(
            frame_tools_row,
            text="(vain nykyinen kuva - tunnistaa vain ihmisen, ei mailaa)",
            foreground="#555",
        ).pack(side="left", padx=(6, 0))

    def _build_list(self, root: tk.Tk) -> None:
        list_frame = self.list_frame = ttk.Frame(root)
        list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        ttk.Label(list_frame, text="Tallenteet (kaksoisnapsauta toistaaksesi):").pack(anchor="w")
        inner = ttk.Frame(list_frame)
        inner.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(inner, columns=("created",), show="tree headings", height=RECORDINGS_LIST_ROWS, selectmode="browse")
        self.tree.heading("#0", text="Nimi")
        self.tree.heading("created", text="Luotu")
        self.tree.column("#0", width=300, anchor="w")
        self.tree.column("created", width=160, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-Button-1>", self.on_play_selected)
        scrollbar = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.config(yscrollcommand=scrollbar.set)

        button_row = ttk.Frame(root)
        button_row.pack(pady=(0, 8))
        ttk.Button(button_row, text="Toista", command=self.on_play_selected).pack(side="left", padx=4)
        ttk.Button(button_row, text="Poista", command=self.on_delete_selected).pack(side="left", padx=4)

    def _build_shots_list(self, timeline_root: tk.Tk) -> None:
        # Spec 106: hidden (not packed) until a recording produces at
        # least one detected shot - see _on_shots_ready. Lives in the
        # left column, above the recordings list (the video preview
        # stays in the right/center column).
        self.shots_frame = ttk.Frame(timeline_root)
        ttk.Label(self.shots_frame, text="Laukaukset:").pack(anchor="w", padx=8)

        # Spec 107/108/125: the currently-shown shot's speed as text; the
        # raw-frame player's controls live under the video (see
        # _build_raw_controls).
        speed_row = ttk.Frame(self.shots_frame)
        speed_row.pack(fill="x", padx=8, pady=(0, 4))
        self.shot_speed_var = tk.StringVar(value="")
        ttk.Label(speed_row, textvariable=self.shot_speed_var).pack(side="left")

        inner = ttk.Frame(self.shots_frame)
        inner.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.shots_tree = ttk.Treeview(
            inner, columns=("speed",), show="tree headings", height=5, selectmode="browse"
        )
        self.shots_tree.heading("#0", text="Laukaus")
        self.shots_tree.heading("speed", text="Nopeus")
        self.shots_tree.column("#0", width=120, anchor="w")
        self.shots_tree.column("speed", width=100, anchor="w")
        self.shots_tree.pack(side="left", fill="both", expand=True)
        self.shots_tree.bind("<<TreeviewSelect>>", self.on_shot_selected)
        scrollbar = ttk.Scrollbar(inner, orient="vertical", command=self.shots_tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.shots_tree.config(yscrollcommand=scrollbar.set)
        ttk.Button(self.shots_frame, text="Takaisin livekuvaan", command=self._exit_shots_mode).pack(
            anchor="w", padx=8, pady=(0, 8)
        )

    def _build_raw_controls(self, root) -> None:
        """Spec 125: controls for stepping through / playing the selected
        shot's raw frames, under the video on the right: -1/+1 frame,
        play/pause ("Toista raakakuvaa"), a slider over the shot's
        frames, the frame number + time, and the speed. Packed only in
        shots mode (see _on_shots_ready)."""
        self.raw_controls_frame = ttk.Frame(root)
        buttons = ttk.Frame(self.raw_controls_frame)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="-1 ruutu", width=8, command=lambda: self._raw_step(-1)).pack(side="left", padx=1)
        self.shot_play_button = ttk.Button(
            buttons, text=RAW_PLAY_TEXT, command=self.on_raw_shot_play_pause_click,
        )
        self.shot_play_button.pack(side="left", padx=1)
        ttk.Button(buttons, text="+1 ruutu", width=8, command=lambda: self._raw_step(1)).pack(side="left", padx=1)
        ttk.Label(buttons, text="Nopeus:").pack(side="left", padx=(12, 0))
        self.raw_speed_var = tk.StringVar(value=_speed_label(1.0))
        ttk.Combobox(
            buttons, textvariable=self.raw_speed_var, state="readonly", width=6,
            values=_RAW_SHOT_SPEED_LABELS,
        ).pack(side="left", padx=(4, 0))

        slider_row = ttk.Frame(self.raw_controls_frame)
        slider_row.pack(fill="x", pady=(4, 0))
        self.raw_scrub_var = tk.DoubleVar(value=0.0)
        self.raw_scale = ttk.Scale(
            slider_row, from_=0.0, to=1.0, variable=self.raw_scrub_var, orient="horizontal",
            command=self._on_raw_scale_moved,
        )
        self.raw_scale.pack(side="left", fill="x", expand=True)
        self.raw_scale.bind("<ButtonPress-1>", lambda _e: setattr(self, "_raw_scrubbing", True))
        self.raw_scale.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_raw_scrubbing", False))
        self.raw_time_var = tk.StringVar(value="")
        ttk.Label(slider_row, textvariable=self.raw_time_var, width=22).pack(side="left", padx=(8, 0))

    def _apply_layout(self) -> None:
        """Two columns side by side: controls, then the recordings
        list below them, on the left; the video (shown at
        DISPLAY_SIZE) with its timeline right under it, on the
        right/center."""
        self.left_frame.pack(side="left", fill="y")
        self.center_frame.pack(side="left", fill="both", expand=True)

    # --- session / device setup ---------------------------------------

    def _init_session(self) -> None:
        try:
            self.session = LiveSession()
        except Exception as exc:
            logger.exception("_init_session: failed to open LiveSession")
            self.status_var.set(f"Kameran avaaminen epäonnistui: {exc}")
            messagebox.showerror("Virhe", str(exc))
            return

        self._populate_device_combos()
        # Spec 133: what a fresh start picked - "Palauta oletukset" returns to it.
        self._default_device_labels = (self.camera_var.get(), self.mic_var.get(), self.speaker_var.get())
        self._update_camera_info()
        self.status_var.set("Valmis.")
        self.record_button.config(state="normal")
        self.root.after(PREVIEW_POLL_MS, self._tick)

    def _populate_device_combos(self) -> None:
        camera_names = devices.list_video_devices()
        self.camera_combo["values"] = [f"{i}: {name}" for i, name in enumerate(camera_names)]
        self.camera_var.set(f"{self.session.video_index}: {self.session.video_name}")

        self._mic_devices = devices.list_input_audio_devices()
        self.mic_combo["values"] = ["(järjestelmän oletus)"] + _device_choices(self._mic_devices)
        self.mic_var.set(self._label_for(self.session.audio_index, self.session.audio_name))

        self._speaker_devices = devices.list_output_audio_devices()
        self.speaker_combo["values"] = ["(järjestelmän oletus)"] + _device_choices(self._speaker_devices)
        self.speaker_var.set(self._label_for(self.session.output_index, self.session.output_name))

    @staticmethod
    def _label_for(index: Optional[int], name: Optional[str]) -> str:
        if index is None:
            return "(järjestelmän oletus)"
        return f"{index}: {name}"

    def _update_camera_info(self) -> None:
        if self.session is None:
            return
        width, height, fps = self.session.camera_info()
        self.camera_info_var.set(f"{width}x{height}, {fps:.1f} fps")

    def on_camera_selected(self, _event=None) -> None:
        if self.session is None or self._mode != "live":
            return
        index = int(self.camera_var.get().split(":", 1)[0])
        name = devices.list_video_devices()[index]
        logger.info("User selected camera %d: %r", index, name)
        try:
            self.session.switch_video_device(index, name)
        except Exception as exc:
            logger.exception("on_camera_selected: switch failed")
            messagebox.showerror("Virhe", str(exc))
            return
        self._update_camera_info()

    def on_mic_selected(self, _event=None) -> None:
        if self.session is None:
            return
        value = self.mic_var.get()
        if value.startswith("("):
            self.session.set_audio_device(None, None)
            return
        index = int(value.split(":", 1)[0])
        self.session.set_audio_device(index, self._mic_devices[index]["name"])

    def on_speaker_selected(self, _event=None) -> None:
        if self.session is None:
            return
        value = self.speaker_var.get()
        if value.startswith("("):
            self.session.set_output_device(None, None)
            return
        index = int(value.split(":", 1)[0])
        self.session.set_output_device(index, self._speaker_devices[index]["name"])

    # --- main loop: live preview or playback, whichever is active ----

    def _tick(self) -> None:
        # The re-schedule below MUST always run, even if this tick's
        # work raises - an uncaught exception here previously meant the
        # whole preview/recording loop silently stopped rescheduling
        # itself, freezing the app with no error shown anywhere. Now it
        # gets logged and the loop keeps going.
        start = time.monotonic()
        try:
            if self._segmenting:
                # A real, measured bug: this loop normally fires every
                # PREVIEW_POLL_MS=10ms (100/s) forever, live preview or
                # not. Confirmed via an isolated script that creating
                # BackgroundRemover() takes ~30ms alone but took 12+
                # minutes (and counting) inside the running GUI with
                # this loop still ticking - the background thread was
                # starved, not genuinely deadlocked. Skipping the tick's
                # actual work (not the reschedule) while segmentation
                # runs gives that thread the CPU/GIL time it needs.
                pass
            elif self._mode == "playback":
                self._playback_tick()
            elif self._mode in ("shots", "image"):
                # Nothing to do each tick - the displayed shot image is
                # static until the user picks another row or leaves
                # shots mode (see _exit_shots_mode), same idea as
                # "playback" leaving the live feed alone while paused.
                pass
            else:
                self._live_tick()
        except Exception:
            logger.exception("_tick: unhandled exception in mode=%r", self._mode)
        finally:
            elapsed = time.monotonic() - start
            if elapsed > SLOW_TICK_WARN_THRESHOLD_S:
                logger.warning("_tick: took %.2fs (mode=%r)", elapsed, self._mode)
            # While recording, reschedule with after_idle (no fixed
            # delay) instead of PREVIEW_POLL_MS - that 10ms pacing exists
            # to avoid redrawing the live preview faster than needed, but
            # during recording there's no redraw (_live_tick skips it),
            # and the flat 10ms added on top of each frame's real
            # capture time was itself capping throughput well below what
            # a tight loop reaches (measured: ~45fps vs ~58fps at
            # 1280x720 on this camera).
            if self.session is not None and self.session.is_recording:
                self.root.after_idle(self._tick)
            else:
                self.root.after(PREVIEW_POLL_MS, self._tick)

    def _live_tick(self) -> None:
        if self.session is None:
            return
        frame = self.session.read_frame()
        # Skip the display update while a recording is in progress -
        # cv2.cvtColor + PIL resize + PhotoImage/Tkinter update is real
        # per-frame CPU cost with nothing to do with capturing, and at
        # 1280x720 it was measured to cap real captured fps around 16-17
        # instead of the ~58 the same camera/pipeline reaches headless
        # (core.recorder.record_clip, no GUI). The last live frame just
        # stays on screen for the recording's duration; the status label
        # already says "Nauhoitetaan...".
        if frame is not None and not self.session.is_recording:
            self._show_frame(frame)

        # Spec 091: the record button is disabled at the moment the 3-2-1
        # countdown starts (on_record_click) and the "Nauhoitetaan..."
        # status is set directly once capture actually begins (see
        # _run_countdown) - both used to be detected here indirectly via
        # button/status-text state, which broke once the countdown made
        # the button already disabled before is_recording turned True.
        if self.session.is_busy and not self.session.is_recording and self.status_var.get() == "Tallennus käynnistyi":
            # Capture just finished, encoding continues in the
            # background (session.close/other actions stay blocked via
            # is_busy) - the camera/preview are already back to normal
            # speed at this point. Just a placeholder for the brief gap
            # before the first on_progress callback (see
            # _on_recording_progress) replaces it with a per-stage
            # percentage.
            self.status_var.set("Käsitellään tallennetta…")

    def _show_frame(self, frame) -> None:
        # Always DISPLAY_SIZE[0] wide, with the height following the
        # frame's own aspect (spec 125): an annotated frame is taller
        # than a raw one (spectrogram + claps bands under the video),
        # and a fixed 640x360 box used to squash it.
        h, w = frame.shape[:2]
        target_w = DISPLAY_SIZE[0]
        target_h = round(target_w * h / w)
        if w != target_w or h != target_h:
            frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        self._photo_image = ImageTk.PhotoImage(image)
        self.preview_label.configure(image=self._photo_image)

    # --- recording -----------------------------------------------------

    def on_record_click(self) -> None:
        logger.info("User clicked Tallenna")
        # Spec 091: gates on is_recording, not is_busy - a previous
        # clip's background annotate/spectrogram/encode/sync is allowed
        # to still be running (see LiveSession.start_recording); only
        # actual capture (this clip's own, or a countdown about to
        # become one) blocks starting another.
        if self.session is None or self.session.is_recording or self._counting_down or self._mode == "playback":
            logger.info(
                "on_record_click: ignored (session=%s recording=%s counting_down=%s mode=%r)",
                self.session is not None, self.session.is_recording if self.session else None,
                self._counting_down, self._mode,
            )
            return
        self._exit_shots_mode()
        self._exit_image_mode()
        try:
            duration_s = float(self.duration_var.get())
        except ValueError:
            duration_s = DEFAULT_DURATION_S
        duration_s = max(1.0, duration_s)
        self.duration_var.set(str(duration_s if duration_s % 1 else int(duration_s)))

        self.record_button.config(state="disabled")
        self._counting_down = True
        self._run_countdown(duration_s, COUNTDOWN_SECONDS)

    def _run_countdown(self, duration_s: float, seconds_left: int) -> None:
        if seconds_left > 0:
            self.status_var.set(f"Tallennus alkaa: {seconds_left}..")
            self._play_beep()
            self.root.after(1000, lambda: self._run_countdown(duration_s, seconds_left - 1))
            return
        self._counting_down = False
        self._recording_deferred = self.defer_processing_var.get()
        self.status_var.set("Tallennus käynnistyi")
        self.session.start_recording(
            duration_s, RECORDINGS_DIR, self._on_recording_done,
            dispatch=lambda fn: self.root.after(0, fn),
            on_progress=self._on_recording_progress,
            on_raw_ready=self._on_raw_ready,
            defer_processing=self.defer_processing_var.get(),
            on_deferred_saved=self._on_deferred_saved,
            on_shots_ready=self._on_shots_ready,
            on_capture_ended=self._on_capture_ended,
            shot_distance_m=self._selected_shot_distance_m(),
        )

    def _update_record_button_text(self) -> None:
        try:
            seconds = max(1.0, float(self.duration_var.get()))
        except ValueError:
            self.record_button.config(text="Tallenna")
            return
        self.record_button.config(text=f"Tallenna {seconds:g} s")

    def _selected_shot_distance_m(self) -> float:
        label = self.shot_position_var.get()
        for position in SHOT_POSITIONS:
            if position.label == label:
                return position.distance_m
        return DEFAULT_SHOT_POSITION.distance_m

    @staticmethod
    def _play_beep() -> None:
        # winsound.Beep() blocks for its full duration - run it on its
        # own short-lived thread so the countdown's own 1s Tkinter
        # timing (and the live preview tick) never stalls waiting on it.
        threading.Thread(
            target=lambda: winsound.Beep(COUNTDOWN_BEEP_FREQ_HZ, COUNTDOWN_BEEP_MS),
            name="shot-improvement-beep", daemon=True,
        ).start()

    def _set_status_if_idle(self, text: str) -> None:
        # Spec 091: a previous clip's background work (its own progress,
        # sync status) can now finish while a NEWER recording's own
        # countdown or active capture is on screen - that newer, more
        # relevant status must never be clobbered by an older clip's
        # background-thread callback firing at an inconvenient moment.
        if self._counting_down or (self.session is not None and self.session.is_recording):
            return
        self.status_var.set(text)

    def _on_recording_progress(self, stage: str, fraction: float) -> None:
        self._set_status_if_idle(f"{stage}: {fraction * 100:.0f} %")

    def _on_raw_ready(self, raw_path: Path) -> None:
        # Spec 091: the raw clip is already a finished, playable file at
        # this point (annotate/spectrogram/encode(annotated)/cloud sync
        # for it continue in the background) - shown in the list and the
        # record button re-enabled immediately, rather than waiting for
        # the rest of that pipeline, so a new recording is never blocked
        # behind a previous one's background work.
        logger.info("_on_raw_ready: %s", raw_path.name)
        self.record_button.config(state="normal")
        self.refresh_recordings()

    def _on_recording_done(self, result: Optional[RecordingResult]) -> None:
        if result is None:
            logger.warning("_on_recording_done: recording failed")
            # _on_raw_ready never fired for this clip (zero frames means
            # no encode ever ran) - the button is still disabled from
            # on_record_click, and nothing newer could have started
            # since a real click can't reach a disabled button. Safe to
            # unconditionally re-enable, unlike the success path below.
            self.record_button.config(state="normal")
            self.status_var.set("Nauhoitus epäonnistui (ei kuvia kamerasta).")
            return
        logger.info("_on_recording_done: %s (%d frames, %.1f fps)", result.raw_path.name, result.frame_count, result.actual_fps)
        self._handle_processed_result(result)

    def _handle_processed_result(self, result: RecordingResult) -> None:
        # Shared by a normal recording's on_done (above) AND a processed
        # pending item's on_done (spec 093, _process_next_pending) -
        # refreshes the recordings list, uploads BOTH clips (spec 094:
        # the raw, native-resolution capture now syncs too, not just
        # the annotated pair), and reports status. Deliberately does
        # NOT touch the record button: a normal recording already
        # re-enabled it via _on_raw_ready well before this runs, and
        # pending-queue processing never disabled it in the first place
        # (it doesn't touch the camera).
        rec_id = result.raw_path.stem.rsplit("-", 1)[-1]
        saved_msg = f"Tallennettu ({result.frame_count} kuvaa, {result.actual_fps:.1f} fps)."
        self._set_status_if_idle(f"{saved_msg} Synkronoidaan verkkoon…")
        self.tmp_dir_var.set(f"Väliaikaiskansio: {result.tmp_dir_path}" if result.tmp_dir_path else "")
        self.refresh_recordings()

        def make_on_progress(label: str) -> Callable[[int, int], None]:
            def on_progress(bytes_sent: int, total_bytes: int) -> None:
                percent = (bytes_sent / total_bytes * 100) if total_bytes else 100.0
                self._set_status_if_idle(f"{saved_msg} Synkronoidaan verkkoon ({label}): {percent:.0f} %")
            return on_progress

        def make_on_done(label: str, path: Path) -> Callable[[Optional[Exception]], None]:
            def on_done(exc: Optional[Exception]) -> None:
                if exc is not None:
                    logger.warning("Cloud upload failed for %s: %s", path.name, exc)
                    self._set_status_if_idle(f"{saved_msg} Synkronointi epäonnistui ({label}): {exc}")
                else:
                    self._set_status_if_idle(f"{saved_msg} Synkronoitu verkkoon ({label}).")
                    # Spec 120: tell the website this clip is viewable.
                    self._status.update(rec_id, state="raw_ready" if label == "raaka" else "done")
            return on_done

        # SyncWorker's queue is a single, strictly-ordered FIFO (one
        # background thread - see its own docstring), so queuing both
        # here always uploads raw first, then annotated, with each
        # one's progress/done messages fully finishing before the
        # next's begin - no need to chain these through each other's
        # on_done to get that ordering.
        self._sync_worker.upload_recording(
            result.raw_path, on_done=make_on_done("raaka", result.raw_path), on_progress=make_on_progress("raaka"),
        )
        self._sync_worker.upload_recording(
            result.annotated_path, on_done=make_on_done("merkitty", result.annotated_path), on_progress=make_on_progress("merkitty"),
        )

    # --- shot browsing (spec 106) ----------------------------------------
    #
    # Fires once per recording (immediate or deferred alike - see
    # core.session.LiveSession.start_recording's on_shots_ready) well
    # before the rest of that recording's processing finishes: replaces
    # the live preview with the first detected shot's image and shows
    # the full list below it, so the result the user actually recorded
    # for is visible immediately instead of after a slow background
    # encode.

    def _on_capture_ended(self, rec_id: str) -> None:
        self._current_rec_id = rec_id
        self._status.begin(rec_id, deferred=self._recording_deferred)

    def _on_shots_ready(self, shots: list[ShotImage], source: ShotSource) -> None:
        speeds = [s.speed_kmh for s in shots if s.speed_kmh is not None]
        if self._current_rec_id:
            self._status.update(self._current_rec_id, fastest_kmh=round(max(speeds)) if speeds else None, shots_checked=True)
        self._shots = shots
        self._shot_source = source
        self._shot_frame_cache = {}
        self._raw_frame_pos = 0
        self.shots_tree.delete(*self.shots_tree.get_children())
        if not shots:
            self._set_status_if_idle("Ei tunnistettuja laukauksia.")
            return
        for shot in shots:
            speed_label = f"{round(shot.speed_kmh)} km/h" if shot.speed_kmh is not None else "—"
            self.shots_tree.insert("", "end", iid=str(shot.index - 1), text=f"Laukaus {shot.index}", values=(speed_label,))
        self._exit_image_mode()
        self._mode = "shots"
        # Left column, just above the recordings list.
        self.shots_frame.pack(fill="x", before=self.list_frame)
        self.raw_controls_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.shots_tree.selection_set("0")

    def on_shot_selected(self, _event=None) -> None:
        selection = self.shots_tree.selection()
        if not selection:
            return
        self._stop_raw_playback()
        self._raw_frame_pos = 0
        self._raw_window = None
        self._set_raw_scale(0, 1)
        self.raw_time_var.set("")
        self._show_shot(int(selection[0]))
        self._selected_raw_window()

    def _show_shot(self, index: int) -> None:
        shot = self._shots[index]
        frame = cv2.imread(str(shot.path))
        if frame is None:
            return
        self._show_frame(frame)
        speed_label = f"{round(shot.speed_kmh)} km/h" if shot.speed_kmh is not None else "ei laskettavissa"
        self.shot_speed_var.set(f"Nopeus: {speed_label}")

    def _exit_shots_mode(self) -> None:
        if self._mode != "shots":
            return
        self._stop_raw_playback()
        self.shots_frame.pack_forget()
        self.raw_controls_frame.pack_forget()
        self._mode = "live"
        self.status_var.set("Valmis.")

    # --- raw shot playback (spec 107/108) ---------------------------------
    #
    # "Play" next to a shot's speed steps through that shot's own raw
    # (unannotated) BMP frames directly in preview_label - no encoding,
    # no audio, just a quick look, from SHOT_PREROLL_S before the shot
    # through its paired hit. Stays inside "shots" mode the whole time
    # (no ClipPlayer, no mode switch) - see _stop_raw_playback for where
    # it gets interrupted.

    def _shot_frames(self, index: int) -> tuple[list[Path], list[float], int]:
        """(paths, times, shot_pos) for the selected shot's raw preview
        window: times shifted to start at 0, shot_pos the index of the
        frame closest to the shot itself (spec 126 - playback and
        stepping start from there, not from the window's start).
        Cached per shot index so a repeat Play reuses the same
        directory listing."""
        cached = self._shot_frame_cache.get(index)
        if cached is not None:
            return cached
        source = self._shot_source
        shot = self._shots[index]
        if source is None:
            return [], [], 0
        start_idx, end_idx = select_shot_frame_range(source.frame_times, shot.time_s, shot.hit_t)
        if start_idx < 0:
            return [], [], 0
        frame_paths = sorted(source.raw_dir.glob(f"*.{FRAME_FILE_EXTENSION}"))
        if len(frame_paths) != len(source.frame_times):
            logger.warning("_shot_frames: raw frame count mismatch for %s", source.raw_dir)
            return [], [], 0
        paths = frame_paths[start_idx : end_idx + 1]
        base_t = source.frame_times[start_idx]
        times = [t - base_t for t in source.frame_times[start_idx : end_idx + 1]]
        shot_pos = min(range(len(times)), key=lambda i: abs(times[i] - (shot.time_s - base_t)))
        result = (paths, times, shot_pos)
        self._shot_frame_cache[index] = result
        return result

    def _selected_raw_window(self) -> Optional[tuple[list[Path], list[float]]]:
        """The selected shot's raw frames (paths, times), loaded once
        and reused; None (with a status message) if unavailable."""
        if self._raw_window is not None:
            return self._raw_window
        selection = self.shots_tree.selection()
        if not selection:
            return None
        paths, times, shot_pos = self._shot_frames(int(selection[0]))
        if not paths:
            self._set_status_if_idle("Raakadataa ei ole enää saatavilla tälle laukaukselle.")
            return None
        self._raw_window = (paths, times)
        # Spec 126: start at the shot's own frame, so Play and +1/-1
        # ruutu continue from what the shot image shows.
        self._raw_frame_pos = shot_pos
        self._set_raw_scale(shot_pos, len(paths) - 1)
        self.raw_time_var.set(f"ruutu {shot_pos + 1}/{len(paths)}  {times[shot_pos]:.2f} s")
        return self._raw_window

    def _set_raw_scale(self, pos: int, last: int) -> None:
        self._raw_scale_setting = True
        try:
            self.raw_scale.config(to=max(last, 1))
            self.raw_scrub_var.set(pos)
        finally:
            self._raw_scale_setting = False

    def _raw_show_frame(self, pos: int) -> None:
        """Shows frame `pos` of the selected shot's window and syncs the
        slider and the frame/time label."""
        window = self._raw_window
        if window is None:
            return
        paths, times = window
        pos = max(0, min(len(paths) - 1, pos))
        self._raw_frame_pos = pos
        frame = cv2.imread(str(paths[pos]))
        if frame is not None:
            self._show_frame(frame)
        if not self._raw_scrubbing:
            self._raw_scale_setting = True
            try:
                self.raw_scrub_var.set(pos)
            finally:
                self._raw_scale_setting = False
        self.raw_time_var.set(f"ruutu {pos + 1}/{len(paths)}  {times[pos]:.2f} s")

    def _raw_step(self, delta: int) -> None:
        """One frame back/forward (pauses playback)."""
        self._stop_raw_playback()
        if self._selected_raw_window() is None:
            return
        self._raw_show_frame(self._raw_frame_pos + delta)

    def _on_raw_scale_moved(self, value: str) -> None:
        if self._raw_scale_setting or not self._raw_scrubbing:
            return
        if self._raw_playing:
            self._stop_raw_playback()
        if self._selected_raw_window() is None:
            return
        self._raw_show_frame(round(float(value)))

    def on_raw_shot_play_pause_click(self) -> None:
        if self._raw_playing:
            self._stop_raw_playback()
            return
        window = self._selected_raw_window()
        if window is None:
            return
        paths, times = window
        if self._raw_frame_pos >= len(paths) - 1:
            self._raw_frame_pos = 0
        self._raw_playing = True
        self.shot_play_button.config(text=RAW_PAUSE_TEXT)
        self._raw_playback_tick(paths, times)

    def _raw_playback_tick(self, paths: list[Path], times: list[float]) -> None:
        self._raw_show_frame(self._raw_frame_pos)
        if self._raw_frame_pos >= len(paths) - 1:
            self._raw_playing = False
            self.shot_play_button.config(text=RAW_PLAY_TEXT)
            return
        speed = _raw_shot_speed_from_label(self.raw_speed_var.get())
        gap_s = times[self._raw_frame_pos + 1] - times[self._raw_frame_pos]
        delay_ms = max(1, round(gap_s / speed * 1000))
        self._raw_frame_pos += 1
        self._raw_play_after_id = self.root.after(delay_ms, lambda: self._raw_playback_tick(paths, times))

    def _stop_raw_playback(self) -> None:
        if self._raw_play_after_id is not None:
            self.root.after_cancel(self._raw_play_after_id)
            self._raw_play_after_id = None
        self._raw_playing = False
        self.shot_play_button.config(text=RAW_PLAY_TEXT)

    def _on_sync_reconciled(self, exc: Optional[Exception]) -> None:
        if exc is not None:
            logger.warning("Cloud sync reconcile failed: %s", exc)

    # --- deferred processing ("Vain nauhoitus") -------------------------
    #
    # spec 093: when defer_processing was on for the recording that just
    # finished, this fires instead of _on_raw_ready/_on_recording_done -
    # no mp4 exists yet, just raw frames + audio saved to core.pending.
    # PENDING_DIR. Processing them into mp4s happens later, on request,
    # via on_process_pending_click below.

    def _on_deferred_saved(self, ok: bool) -> None:
        self.record_button.config(state="normal")
        self.refresh_pending()
        if ok:
            self._set_status_if_idle(f"Tallennettu käsittelyä varten ({len(self._pending_items)} odottaa).")
        else:
            self._set_status_if_idle("Nauhoitus epäonnistui (ei kuvia kamerasta).")

    def refresh_pending(self) -> None:
        self._pending_items = pending.list_pending()
        n = len(self._pending_items)
        self.pending_var.set(f"Odottaa käsittelyä: {n}" if n else "Ei odottavia tallenteita.")
        # Left alone while a batch is already running - on_process_
        # pending_click/​_process_next_pending own the button's state
        # for that whole stretch, re-enabling it only once the queue
        # they started is fully drained (not just whenever the count
        # happens to reach zero mid-batch, which it never does anyway
        # since finished items are removed from the list, not this one).
        if not self._processing_pending:
            self.process_pending_button.config(state="normal" if n else "disabled")

    def on_process_pending_click(self) -> None:
        if self._processing_pending or not self._pending_items:
            return
        logger.info("User clicked Käsittele odottavat (%d pending)", len(self._pending_items))
        self._processing_pending = True
        self.process_pending_button.config(state="disabled")
        self._process_next_pending(list(self._pending_items), 0)

    def _process_next_pending(self, queue: list[pending.PendingRecording], index: int) -> None:
        if index >= len(queue):
            self._processing_pending = False
            self.refresh_pending()
            self._set_status_if_idle("Käsittely valmis.")
            return
        item = queue[index]
        total = len(queue)
        self._set_status_if_idle(f"Käsitellään ({index + 1}/{total})…")

        def on_progress(stage: str, fraction: float) -> None:
            self._set_status_if_idle(f"Käsitellään ({index + 1}/{total}): {stage}: {fraction * 100:.0f} %")

        def on_done(result: Optional[RecordingResult]) -> None:
            if result is not None:
                logger.info("process_pending: done -> %s", result.raw_path.name)
                self._handle_processed_result(result)
            else:
                logger.warning("process_pending: failed for %s", item.dir_path)
                self._set_status_if_idle(f"Käsittely epäonnistui ({index + 1}/{total}) - tallenne säilyy odottavana.")
            # Refreshed after every item, not just once at the end of the
            # whole batch - the count should visibly drop as the queue
            # drains. Safe to call mid-batch: refresh_pending() leaves
            # the button alone while self._processing_pending is True.
            self.refresh_pending()
            self._process_next_pending(queue, index + 1)

        pending.process_pending_recording(
            item, RECORDINGS_DIR, on_done,
            dispatch=lambda fn: self.root.after(0, fn),
            on_progress=on_progress,
            on_raw_ready=self._on_raw_ready,
        )

    # --- recordings list -------------------------------------------------

    def refresh_recordings(self) -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        # Spec 125: the per-shot snapshot JPEGs are listed too, right
        # after their recording's videos (newest recording first).
        paths = list(RECORDINGS_DIR.glob("*.mp4")) + list(RECORDINGS_DIR.glob("*-shot-*.jpg"))
        paths.sort(key=lambda p: p.name)
        self._video_paths = sorted(paths, key=_recording_timestamp, reverse=True)
        self.tree.delete(*self.tree.get_children())
        for i, path in enumerate(self._video_paths):
            self.tree.insert("", "end", iid=str(i), text=path.name, values=(_created_label(path),))

    def _selected_path(self) -> Optional[Path]:
        selection = self.tree.selection()
        if not selection:
            return None
        return self._video_paths[int(selection[0])]

    def on_delete_selected(self) -> None:
        path = self._selected_path()
        if path is None:
            return
        if not messagebox.askyesno("Poista tallenne", f"Poistetaanko {path.name}?"):
            return
        logger.info("Deleting recording %s", path.name)
        path.unlink(missing_ok=True)
        self.refresh_recordings()

        # Spec 094: both the raw and annotated clip sync to the cloud
        # now (previously only the annotated one did, so only that one
        # got a matching cloud delete) - whichever member of the pair
        # was just deleted locally gets tombstoned the same way.
        def on_sync_done(exc: Optional[Exception]) -> None:
            if exc is not None:
                logger.warning("Cloud delete failed for %s: %s", path.name, exc)
                self.status_var.set(f"Poisto verkosta epäonnistui: {exc}")
            else:
                self.status_var.set("Poistettu myös verkosta.")

        if path.suffix.lower() == ".mp4":  # shot images aren't synced to the cloud
            self._sync_worker.delete_recording(path.name, on_done=on_sync_done)

    # --- native playback (same preview area, no external player) -------
    #
    # spec 084: a full transport control set, built on core.playback.
    # ClipPlayer, which owns the cv2.VideoCapture + pre-decoded audio
    # and all play/pause/seek/speed/volume/loop state; this class only
    # wires Tkinter widgets/events to it and repaints each frame it
    # returns.

    def on_play_selected(self, _event=None) -> None:
        path = self._selected_path()
        if path is None:
            return
        if path.suffix.lower() == ".jpg":
            self.show_still_image(path)
            return
        self.load_and_play(path)

    def show_still_image(self, path: Path) -> None:
        """Spec 125: a captured shot image picked from the recordings
        list is shown in the preview area (mode "image": static, like
        "shots"); "Takaisin livekuvaan" or a new recording leaves it."""
        if self.session is None or self.session.is_recording or self._counting_down or self._mode == "playback":
            return
        frame = cv2.imread(str(path))
        if frame is None:
            self.status_var.set(f"Kuvan avaaminen epäonnistui: {path.name}")
            return
        self._exit_shots_mode()
        self._mode = "image"
        self._show_frame(frame)
        self.image_back_button.pack(anchor="w", padx=8, pady=(0, 8))
        self.status_var.set(f"Kuva: {path.name}")

    def _exit_image_mode(self) -> None:
        if self._mode != "image":
            return
        self.image_back_button.pack_forget()
        self._mode = "live"
        self.status_var.set("Valmis.")

    def load_and_play(self, path: Path) -> None:
        logger.info("Loading %s for playback", path.name)
        if self.session is None or self.session.is_busy:
            logger.info(
                "load_and_play: ignored (session=%s busy=%s)",
                self.session is not None, self.session.is_busy if self.session else None,
            )
            return
        self._exit_shots_mode()
        self._exit_image_mode()
        self._close_player()
        self.status_var.set(f"Ladataan: {path.name}…")
        self.root.update_idletasks()

        try:
            output_index = self.session.output_index if self.session else None
            self._player = ClipPlayer(path, output_device=output_index)
        except Exception as exc:
            logger.exception("load_and_play: failed to open %s", path.name)
            self.status_var.set(f"Videon avaaminen epäonnistui: {exc}")
            return

        self._player.speed = _speed_from_label(self.speed_var.get())
        self._player.volume = self.volume_var.get()
        self._player.muted = self.mute_var.get()
        self._player.loop = self.loop_var.get()

        self.scrub_scale.config(to=max(self._player.duration_s, 0.01))
        self._mode = "playback"
        # pack()'s default placement follows call order, not creation
        # order - since playback_controls_frame is packed late (long
        # after the status label below it was already packed in
        # __init__), it would otherwise land at the very end of the
        # left column instead of where it visually belongs, right after
        # the record row. before= pins it there regardless of call
        # timing. timeline_frame has nothing packed after it in the
        # center column, so it needs no such pin.
        self.playback_controls_frame.pack(pady=(0, 4), before=self.status_label)
        self.timeline_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.record_button.config(state="disabled")
        self._player.play()
        self._next_frame_due = time.monotonic()
        self._update_play_pause_button()
        self.status_var.set(f"Toistetaan: {path.name}")

    def _update_play_pause_button(self) -> None:
        if self._player is not None and self._player.playing:
            self.play_pause_button.config(text="❚❚")
        else:
            self.play_pause_button.config(text="▶")

    def on_play_pause_click(self) -> None:
        if self._player is None:
            return
        if self._player.playing:
            self._player.pause()
        else:
            self._player.play()
            self._next_frame_due = time.monotonic()
        self._update_play_pause_button()
        self._show_current_player_frame()

    def on_stop_click(self) -> None:
        if self._player is None:
            return
        self._player.stop()
        self._update_play_pause_button()
        self._show_current_player_frame()

    def on_prev_frame_click(self) -> None:
        self._step_frame(-1)

    def on_next_frame_click(self) -> None:
        self._step_frame(1)

    def _step_frame(self, delta_frames: int) -> None:
        if self._player is None:
            return
        frame = self._player.step(delta_frames)
        self._update_play_pause_button()
        if frame is not None:
            self._show_frame(frame)
        self._update_scrub_and_time()

    def on_skip_back_click(self) -> None:
        self._skip(-SKIP_SECONDS)

    def on_skip_forward_click(self) -> None:
        self._skip(SKIP_SECONDS)

    def _skip(self, delta_seconds: float) -> None:
        if self._player is None:
            return
        was_playing = self._player.playing
        frame = self._player.seek_to_seconds(self._player.position_s + delta_seconds)
        if frame is not None:
            self._show_frame(frame)
        self._update_scrub_and_time()
        if was_playing:
            self._player.play()
            self._next_frame_due = time.monotonic()
        self._update_play_pause_button()

    def on_scrubber_press(self, _event=None) -> None:
        self._scrubbing = True

    def on_scrubber_release(self, _event=None) -> None:
        self._scrubbing = False
        if self._player is None:
            return
        was_playing = self._player.playing
        frame = self._player.seek_to_seconds(self.scrub_var.get())
        if frame is not None:
            self._show_frame(frame)
        self._update_scrub_and_time()
        if was_playing:
            self._player.play()
            self._next_frame_due = time.monotonic()
        self._update_play_pause_button()

    def on_speed_changed(self, _event=None) -> None:
        if self._player is None:
            return
        speed = _speed_from_label(self.speed_var.get())
        self._player.set_speed(speed)

    def on_volume_changed(self, _value=None) -> None:
        if self._player is None:
            return
        self._player.set_volume(self.volume_var.get())

    def on_mute_toggle(self) -> None:
        if self._player is None:
            return
        self._player.set_muted(self.mute_var.get())

    def on_loop_toggle(self) -> None:
        if self._player is None:
            return
        self._player.loop = self.loop_var.get()

    def on_back_to_live_click(self) -> None:
        self._close_player()
        self._mode = "live"
        self.playback_controls_frame.pack_forget()
        self.timeline_frame.pack_forget()
        if self.session is not None:
            self.record_button.config(state="normal")
        self.status_var.set("Valmis.")

    def on_reset_click(self) -> None:
        """Spec 133: resets every setting to its default and returns to the
        live camera preview - as if the app had just been restarted."""
        if self.session is None or self.session.is_recording or self._counting_down:
            self.status_var.set("Palautus ei onnistu kesken tallennuksen.")
            return
        logger.info("User clicked Palauta oletukset")
        # Leave whatever is on screen (saved-clip playback, shot list, a
        # still image) and go back to live view.
        self._stop_raw_playback()
        if self._mode == "playback":
            self.on_back_to_live_click()
        self._exit_shots_mode()
        self._exit_image_mode()
        self._shots = []
        self._shot_source = None
        self._shot_frame_cache = {}
        self._raw_window = None
        self._raw_frame_pos = 0
        self.shots_tree.delete(*self.shots_tree.get_children())
        self.shot_speed_var.set("")
        self.raw_time_var.set("")
        self.tree.selection_remove(*self.tree.selection())

        # Settings.
        self.duration_var.set(str(DEFAULT_DURATION_S))
        self.defer_processing_var.set(True)
        self.shot_position_var.set(DEFAULT_SHOT_POSITION.label)
        self.raw_speed_var.set(_speed_label(1.0))
        self.speed_var.set(_speed_label(1.0))
        self.volume_var.set(1.0)
        self.mute_var.set(False)
        self.loop_var.set(False)

        # Devices: back to what a fresh start picks (only touched if they differ).
        if self._default_device_labels is not None:
            camera, mic, speaker = self._default_device_labels
            if self.camera_var.get() != camera:
                self.camera_var.set(camera)
                self.on_camera_selected()
            if self.mic_var.get() != mic:
                self.mic_var.set(mic)
                self.on_mic_selected()
            if self.speaker_var.get() != speaker:
                self.speaker_var.set(speaker)
                self.on_speaker_selected()

        self.record_button.config(state="normal")
        self.status_var.set("Oletukset palautettu.")

    def on_remove_background_click(self) -> None:
        # Only the currently-displayed frame - pausing first so the
        # result isn't immediately overwritten by the next playing
        # frame. Isolates the person only; a held stick/racket is not
        # preserved - confirmed by testing against a real frame, not
        # assumed. See core.segmentation's module docstring.
        #
        # Model load + inference runs on a background thread, like spec
        # 083's ffmpeg encode fix - loading the model synchronously on
        # the GUI thread was observed to hang the whole app for over a
        # minute with the camera + a live PoseDetector already active
        # (root cause not pinned down; not worth risking a repeat).
        if self._player is None or self._segmenting:
            return
        logger.info("User clicked Häivytä tausta")
        if self._player.playing:
            self._player.pause()
            self._update_play_pause_button()
        frame = self._player.read_current_frame()
        if frame is None:
            return

        self._segmenting = True
        self.status_var.set("Häivytetään taustaa (voi kestää hetken)…")

        # A real, measured finding: even with _tick() skipping its own
        # work while segmenting (above), model creation still took
        # minutes instead of the ~30ms an isolated script needed.
        # Windows' camera capture backends (DirectShow/MSMF) can keep a
        # capture graph running its own background thread regardless of
        # whether the app calls read() on it - releasing the camera
        # entirely (not just skipping reads) is the next thing that
        # differs from the fast isolated repro, and it's safe here since
        # this button only exists in playback mode, where the live feed
        # isn't shown anyway.
        if self.session is not None:
            try:
                self.session.suspend_camera()
            except Exception:
                logger.exception("on_remove_background_click: suspend_camera failed")

        def worker() -> None:
            try:
                remover = self._background_remover
                if remover is None:
                    remover = BackgroundRemover()
                    self._background_remover = remover
                masked = remover.remove_background(frame)
            except Exception as exc:
                logger.exception("on_remove_background_click: failed")
                self.root.after(0, lambda: self._on_background_removed(None, exc))
                return
            self.root.after(0, lambda: self._on_background_removed(masked, None))

        threading.Thread(target=worker, name="shot-improvement-segmentation", daemon=True).start()

    def _on_background_removed(self, masked, exc: Optional[Exception]) -> None:
        self._segmenting = False
        if self.session is not None:
            try:
                self.session.resume_camera()
            except Exception:
                logger.exception("_on_background_removed: resume_camera failed")
        if exc is not None:
            self.status_var.set(f"Taustan häivytys epäonnistui: {exc}")
            return
        self._show_frame(masked)
        self.status_var.set("Tausta häivytetty (vain tämä kuva - liiku eteen/taakse palataksesi).")

    def _show_current_player_frame(self) -> None:
        if self._player is None:
            return
        frame = self._player.read_current_frame()
        if frame is not None:
            self._show_frame(frame)

    def _update_scrub_and_time(self) -> None:
        if self._player is None:
            return
        if not self._scrubbing:
            self.scrub_var.set(self._player.position_s)
        self.time_var.set(f"{format_time(self._player.position_s)} / {format_time(self._player.duration_s)}")

    def _playback_tick(self) -> None:
        player = self._player
        if player is None:
            return
        if player.playing and time.monotonic() >= self._next_frame_due:
            frame = player.advance()
            if frame is None:
                if player.loop:
                    player.frame_idx = 0
                    player.read_current_frame()
                    player.play()
                    self._next_frame_due = time.monotonic()
                else:
                    player.pause()
                    self._update_play_pause_button()
            else:
                self._show_frame(frame)
                self._next_frame_due += player.frame_interval_s()
        self._update_scrub_and_time()

    def _close_player(self) -> None:
        if self._player is not None:
            self._player.close()
            self._player = None

    # --- lifecycle -------------------------------------------------------

    def on_close(self) -> None:
        logger.info("Window closing")
        self._close_player()
        if self._background_remover is not None:
            self._background_remover.close()
        if self.session is not None:
            self.session.close()
        self.root.destroy()


def _report_callback_exception(exc, val, tb) -> None:
    # Tkinter's own default just prints to stderr and otherwise
    # silently swallows the exception, continuing as if nothing
    # happened - the app "tökkii" (stutters) instead of visibly
    # failing. Route it through the same log file as everything else.
    logger.error("Unhandled exception in a Tkinter callback", exc_info=(exc, val, tb))


def main() -> None:
    logger.info("=== shot-improvement GUI starting ===")
    root = tk.Tk()
    root.report_callback_exception = _report_callback_exception
    App(root)
    root.mainloop()
    logger.info("=== shot-improvement GUI exiting ===")


if __name__ == "__main__":
    main()
