# 120 — Website: live latest recording with countdowns

## What
`https://shot.timolehtonen.tech/` now updates itself while open:
- Polls `/api/videos` and `/api/status` every 3 s; new recordings appear
  in the existing "Liikeratatallenteet" list without a reload (older
  recordings stay listed).
- A "latest recording" panel on top follows the laptop's stage:
  - **processing** (from the moment capture ends): "Uusi tallenne
    käsittelyssä", the fastest shot's km/h, and countdowns "Video
    katsottavissa noin N s" (estimate 34 s after capture end) and
    "Merkitty video valmis noin M s" (estimate 51 s).
  - **raw_ready**: the raw clip plays automatically; annotated countdown
    continues.
  - **done**: the annotated clip replaces it and plays automatically.
  Autoplay only for a recording that appears/changes while the page is
  open, not for whatever was there on load (muted, as browsers require).
- Countdowns are computed against the server's clock (`server_now` in
  `/api/status`), so a wrong browser clock does not skew them.

## How
- Laptop: `core/status_publish.py` `StatusPublisher` writes
  `status/latest.json` to the Azure container (background thread,
  latest-state-wins, never blocks or fails a recording). Hooks: capture
  end (`LiveSession.start_recording(on_capture_ended=...)`, immediate
  mode only), shots ready (fastest km/h), raw upload done, annotated
  upload done (`gui.py`). ETAs are the measured 34 s / 51 s.
- Server: `GET /api/status`; raw clip names are now servable via
  `/api/videos/{name}` (still not listed).
- `web/app.js`: polling, `LatestPanel`.

## Notes
- "Vain nauhoitus" (deferred) recordings do not publish status.
- Needs a server redeploy (README) to go live.

## Tests
`server/tests` 47 passed (4 new), `tests` 112 passed; `web/app.js`
compiles as JSX.
