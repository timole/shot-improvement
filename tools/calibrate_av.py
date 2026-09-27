"""Local primary-screen flash / speaker impulse fixture for a phone at 240 fps.

Run while Snapshot records in DEV mode, at 22.5 m. Six pairs, with a
0.877 s gap (100 km/h including sound return), start after five seconds.
The window closes automatically. Escape also stops it. Output uses the
speaker DAC clock, not PlaySound launch time. Display refresh adds up to
one refresh interval, so this is a bench check, not sub-frame metrology.
"""
import argparse
import json
import tkinter as tk
from pathlib import Path

import numpy as np
import sounddevice as sd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-device", type=int, default=None)
    parser.add_argument("--pairs", type=int, default=6)
    parser.add_argument("--gain", type=float, default=1.0)
    parser.add_argument("--log", type=Path, default=Path("recordings/calibration.json"))
    args = parser.parse_args()
    rate = 48000
    gap = 22.5 / (100 / 3.6) + 22.5 / 337.4
    events = [5 + pair * 6 + offset for pair in range(args.pairs) for offset in (0, gap)]
    samples = np.zeros((int((events[-1] + 5) * rate), 2), dtype=np.float32)
    rng = np.random.default_rng(160)
    burst = rng.uniform(-0.6, 0.6, int(rate * 0.015)) * np.exp(-np.linspace(0, 5, int(rate * 0.015)))
    for t in events:
        i = round(t * rate)
        samples[i:i + len(burst)] = np.clip(burst[:, None] * args.gain, -1, 1)
    cursor = 0
    dac_origin = None
    log = []

    def callback(out, frames, timing, status):
        nonlocal cursor, dac_origin
        if dac_origin is None:
            dac_origin = timing.outputBufferDacTime
        out.fill(0)
        n = min(frames, len(samples) - cursor)
        if n > 0:
            out[:n] = samples[cursor:cursor+n]
        cursor += n

    root = tk.Tk()
    root.title("Snapshot 240 fps calibration")
    root.geometry(f"{root.winfo_screenwidth()}x{root.winfo_screenheight()}+0+0")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    root.bind("<Escape>", lambda _: root.destroy())
    stream = sd.OutputStream(samplerate=rate, channels=2, dtype="float32", device=args.output_device,
                             latency="low", callback=callback)
    stream.start()
    white = False
    seen = set()

    def tick():
        nonlocal white
        elapsed = stream.time - dac_origin if dac_origin is not None else -1
        active = next((i for i, t in enumerate(events) if t <= elapsed < t + .12), None)
        new_white = active is not None
        if new_white != white:
            root.configure(bg="white" if new_white else "black")
            root.update_idletasks()
            white = new_white
            if active is not None and active not in seen:
                seen.add(active)
                log.append({"pulse": active, "scheduled_s": events[active], "paint_s": elapsed})
        if elapsed > events[-1] + 5:
            root.destroy()
        else:
            root.after(1, tick)

    root.after(1, tick)
    try:
        root.mainloop()
    finally:
        stream.stop()
        stream.close()
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text(json.dumps(log, indent=2), encoding="utf-8")
        print(f"Presented {len(log)} pulses; timing log: {args.log}")


if __name__ == "__main__":
    main()
