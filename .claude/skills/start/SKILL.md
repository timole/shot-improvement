---
description: Start the shot-improvement GUI app
disable-model-invocation: true
---

Start the shot-improvement GUI app:

1. Check for any lingering `python.exe` processes from a previous session (`ps -W | grep -i python`) - if one is idle/stale (started a while ago, low CPU), it's likely safe to leave alone unless it's actively causing problems.
2. Launch `venv\Scripts\python gui.py` from the repo root, in the background.
3. Wait a few seconds and check the task's output for the startup log lines (camera/mic picked, camera opened, session started) - report which camera/mic got picked, and flag anything that looks like an error (e.g. `RuntimeError`, camera failing to open).
4. Report status back in one or two sentences - don't wait for the window to close before responding.
