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
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Optional

import cv2
from PIL import Image, ImageTk

from core import cloud_sync, devices
from core.log_setup import get_logger, setup_logging
from core.playback import SKIP_SECONDS, SPEED_OPTIONS, ClipPlayer, format_time
from core.recorder import RECORDINGS_DIR
from core.segmentation import BackgroundRemover
from core.session import LiveSession, RecordingResult

setup_logging()
logger = get_logger("gui")

PREVIEW_WIDTH = 480
DEFAULT_DURATION_S = 10
PREVIEW_POLL_MS = 10  # self-paced anyway - actual cadence follows the work each tick does
# _tick() runs the whole GUI's live preview/playback loop; one call
# taking much longer than that is worth a log line (see LiveSession's
# own, tighter, SLOW_FRAME_WARN_THRESHOLD_S for the read_frame()-level
# version of this same idea).
SLOW_TICK_WARN_THRESHOLD_S = 1.0

_FILENAME_TIMESTAMP_RE = re.compile(r"shot-improvement-(\d{14})(?:-annotated)?\.mp4$")


def _device_choices(raw_devices: list[dict]) -> list[str]:
    return [f"{i}: {info['name']}" for i, info in enumerate(raw_devices)]


def _speed_label(speed: float) -> str:
    return f"{speed:g}x"


_SPEED_LABELS = [_speed_label(s) for s in SPEED_OPTIONS]


def _speed_from_label(label: str) -> float:
    return SPEED_OPTIONS[_SPEED_LABELS.index(label)]


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
        self._mode = "live"  # "live" | "playback" - playback while a clip is loaded, whether playing or paused
        self._player: Optional[ClipPlayer] = None
        self._scrubbing = False  # user is dragging the timeline slider - suppress auto position updates
        self._next_frame_due = 0.0  # time.monotonic() value; paces _playback_tick() by player.frame_interval_s()
        self._background_remover: Optional[BackgroundRemover] = None  # lazy - model load is slow, reused after first use
        self._segmenting = False  # a background-removal worker thread is currently running
        self._photo_image = None  # keep a reference alive - Tkinter/PhotoImage gotcha

        self._build_device_row(root)

        self.preview_label = tk.Label(root, background="#000")
        self.preview_label.pack(padx=8, pady=8)

        self._build_record_row(root)
        self._build_playback_controls(root)

        self.status_var = tk.StringVar(value="Käynnistetään kameraa…")
        self.status_label = ttk.Label(root, textvariable=self.status_var)
        self.status_label.pack(pady=(0, 8))

        self._build_list(root)

        self._video_paths: list[Path] = []
        self.refresh_recordings()

        # Spec 087: uploads new annotated clips to the web gallery and
        # deletes ones removed locally. Runs on its own background
        # thread (see cloud_sync.SyncWorker) - starting it never blocks
        # camera startup below.
        self._sync_worker = cloud_sync.SyncWorker(dispatch=lambda fn: self.root.after(0, fn))
        self._sync_worker.start_reconcile(on_done=self._on_sync_reconciled)

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

    def _build_playback_controls(self, root: tk.Tk) -> None:
        # Hidden (not packed) while mode == "live"; shown for as long as
        # a clip is loaded, whether it's actually playing or paused -
        # see spec 084. Built once, up front, so on_play_selected() etc.
        # only ever need to pack/unpack this one frame.
        self.playback_frame = ttk.Frame(root)

        transport = ttk.Frame(self.playback_frame)
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

        scrub_row = ttk.Frame(self.playback_frame)
        scrub_row.pack(fill="x", padx=8, pady=(4, 0))
        self.scrub_var = tk.DoubleVar(value=0.0)
        self.scrub_scale = ttk.Scale(scrub_row, from_=0.0, to=1.0, variable=self.scrub_var, orient="horizontal")
        self.scrub_scale.pack(side="left", fill="x", expand=True)
        self.scrub_scale.bind("<ButtonPress-1>", self.on_scrubber_press)
        self.scrub_scale.bind("<ButtonRelease-1>", self.on_scrubber_release)
        self.time_var = tk.StringVar(value="0:00.0 / 0:00.0")
        ttk.Label(scrub_row, textvariable=self.time_var, width=14).pack(side="left", padx=(8, 0))

        options_row = ttk.Frame(self.playback_frame)
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

        frame_tools_row = ttk.Frame(self.playback_frame)
        frame_tools_row.pack(pady=(0, 4))
        ttk.Button(frame_tools_row, text="Häivytä tausta", command=self.on_remove_background_click).pack(side="left")
        ttk.Label(
            frame_tools_row,
            text="(vain nykyinen kuva - tunnistaa vain ihmisen, ei mailaa)",
            foreground="#555",
        ).pack(side="left", padx=(6, 0))

    def _build_list(self, root: tk.Tk) -> None:
        list_frame = ttk.Frame(root)
        list_frame.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        ttk.Label(list_frame, text="Tallenteet (kaksoisnapsauta toistaaksesi):").pack(anchor="w")
        inner = ttk.Frame(list_frame)
        inner.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(inner, columns=("created",), show="tree headings", height=8, selectmode="browse")
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

        button_shows_busy = self.record_button["state"] == "disabled"
        if self.session.is_recording and not button_shows_busy:
            self.record_button.config(state="disabled")
            self.status_var.set(f"Nauhoitetaan {self.duration_var.get()} sekuntia…")
        elif self.session.is_busy and not self.session.is_recording and self.status_var.get().startswith("Nauhoitetaan"):
            # Capture just finished, encoding continues in the
            # background (session.close/other actions stay blocked via
            # is_busy) - the camera/preview are already back to normal
            # speed at this point, only the button stays disabled.
            self.status_var.set("Tallennetaan levylle…")

    def _show_frame(self, frame) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        scale = PREVIEW_WIDTH / image.width
        image = image.resize((PREVIEW_WIDTH, int(image.height * scale)))
        self._photo_image = ImageTk.PhotoImage(image)
        self.preview_label.configure(image=self._photo_image)

    # --- recording -----------------------------------------------------

    def on_record_click(self) -> None:
        logger.info("User clicked Tallenna")
        if self.session is None or self.session.is_busy or self._mode == "playback":
            logger.info("on_record_click: ignored (session=%s busy=%s mode=%r)", self.session is not None, self.session.is_busy if self.session else None, self._mode)
            return
        try:
            duration_s = float(self.duration_var.get())
        except ValueError:
            duration_s = DEFAULT_DURATION_S
        duration_s = max(1.0, duration_s)
        self.duration_var.set(str(duration_s if duration_s % 1 else int(duration_s)))

        self.record_button.config(state="disabled")
        self.status_var.set(f"Nauhoitetaan {duration_s:g} sekuntia…")
        self.session.start_recording(
            duration_s, RECORDINGS_DIR, self._on_recording_done, dispatch=lambda fn: self.root.after(0, fn)
        )

    def _on_recording_done(self, result: Optional[RecordingResult]) -> None:
        self.record_button.config(state="normal")
        if result is None:
            logger.warning("_on_recording_done: recording failed")
            self.status_var.set("Nauhoitus epäonnistui (ei kuvia kamerasta).")
            return
        logger.info("_on_recording_done: %s (%d frames, %.1f fps)", result.raw_path.name, result.frame_count, result.actual_fps)
        saved_msg = f"Tallennettu ({result.frame_count} kuvaa, {result.actual_fps:.1f} fps)."
        self.status_var.set(f"{saved_msg} Synkronoidaan verkkoon…")
        self.refresh_recordings()

        def on_sync_done(exc: Optional[Exception]) -> None:
            if exc is not None:
                logger.warning("Cloud upload failed for %s: %s", result.annotated_path.name, exc)
                self.status_var.set(f"{saved_msg} Synkronointi epäonnistui: {exc}")
            else:
                self.status_var.set(f"{saved_msg} Synkronoitu verkkoon.")

        self._sync_worker.upload_recording(result.annotated_path, on_done=on_sync_done)

    def _on_sync_reconciled(self, exc: Optional[Exception]) -> None:
        if exc is not None:
            logger.warning("Cloud sync reconcile failed: %s", exc)

    # --- recordings list -------------------------------------------------

    def refresh_recordings(self) -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        self._video_paths = sorted(RECORDINGS_DIR.glob("*.mp4"), reverse=True)
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

        if path.name.endswith("-annotated.mp4"):
            # The raw member of a pair has no cloud counterpart (only
            # annotated clips ever sync), so deleting it is local-only.
            def on_sync_done(exc: Optional[Exception]) -> None:
                if exc is not None:
                    logger.warning("Cloud delete failed for %s: %s", path.name, exc)
                    self.status_var.set(f"Poisto verkosta epäonnistui: {exc}")
                else:
                    self.status_var.set("Poistettu myös verkosta.")

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
        self.load_and_play(path)

    def load_and_play(self, path: Path) -> None:
        logger.info("Loading %s for playback", path.name)
        if self.session is None or self.session.is_busy:
            logger.info(
                "load_and_play: ignored (session=%s busy=%s)",
                self.session is not None, self.session.is_busy if self.session else None,
            )
            return
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
        # order - since this is packed late (long after the status
        # label/recordings list below it were already packed in
        # __init__), it would otherwise land at the very end of the
        # window instead of where it visually belongs, right after the
        # record row. before= pins it there regardless of call timing.
        self.playback_frame.pack(pady=(0, 4), before=self.status_label)
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
        self.playback_frame.pack_forget()
        if self.session is not None:
            self.record_button.config(state="normal")
        self.status_var.set("Valmis.")

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
