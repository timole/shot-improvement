# 094 — Shot-improvement: sync raw clips too, add cloud preview images

## What

Two changes to `core/cloud_sync.py` (spec 087):

1. **Raw clips now sync to the cloud, not just annotated ones.**
   Previously only `*-annotated.mp4` was ever uploaded (README's own
   "Web gallery" section said as much: "raw clips never leave the
   laptop") - now every `shot-improvement-<timestamp>.mp4` file syncs,
   raw and annotated alike, via a single `ALL_VIDEOS_GLOB` covering
   both instead of `ANNOTATED_GLOB` alone.
2. **A JPEG preview image is generated and uploaded for every video.**
   `upload()` now extracts the video's first frame via ffmpeg and
   uploads it alongside the video, as `<video-stem>.jpg` in the same
   bucket - e.g. `shot-improvement-20260915112625-annotated.mp4` gets
   `shot-improvement-20260915112625-annotated.jpg`. `delete()`
   symmetrically deletes a video's preview when the video itself is
   deleted. Both are best-effort: a preview failure never fails the
   video's own upload/delete.

## Why

The user asked for both directly. Raw sync closes a real gap - a
"private, laptop-only" default was reasonable when set (spec 087), but
now that the whole bucket exists and both clip types matter to the
user, there's no reason to leave half of every recording off the
cloud. Previews exist because a gallery listing dozens of clips (this
session alone pushed the bucket from 0 to 39 videos) needs something
to show before the reader picks one to actually play.

## Design

- **First frame, not a seek into the middle.** This machine's camera
  has a documented, currently-active intermittent driver stall (specs
  090/092/093) that can produce clips only a handful of frames long.
  `imageio_ffmpeg` bundles only `ffmpeg`, not `ffprobe`, so there's no
  cheap way to know a clip's real duration before deciding where to
  seek - and even with duration in hand, `-ss` into a clip whose
  recording stalled mid-capture risks landing past its actual content.
  Frame 0 always exists; grabbing it (`-frames:v 1`, no `-ss`) is the
  one approach that can't fail on a short/degraded clip.
- **Previews are a side effect of `upload()`/`delete()`, not an
  independently-tracked name.** `plan_sync()`'s `to_upload`/`to_delete`
  sets, and the tombstone file, all still key on video names only.
  `list_remote_names()` now filters to `.mp4` so a `.jpg` preview is
  never mistaken for a video reconcile itself needs to act on. This
  keeps the whole reconcile/tombstone design (spec 087's actual hard
  part) completely unchanged - previews just ride along.
- **Both clips upload on every new recording now**
  (`gui.py::_handle_processed_result`, shared by a normal recording and
  a processed pending item alike - spec 093): raw first, then
  annotated, both status-reported separately ("Synkronoidaan verkkoon
  (raaka)/(merkitty)") - `SyncWorker`'s queue is strictly serial
  (one background thread), so queuing both immediately, without
  chaining one's completion to the other, already uploads them in
  that order with clean, non-interleaved progress messages.
- **Deleting either clip now cloud-deletes it too**
  (`gui.py::on_delete_selected`) - previously only an annotated
  filename got tombstoned (the "raw clips never leave the laptop"
  assumption baked into the old comment); now whichever clip was just
  deleted locally is tombstoned, matching the new symmetric sync.
- **Existing videos backfilled once**, since normal sync only
  generates a preview as a side effect of a video's own
  upload/reconcile - a video already in the bucket before this spec
  would otherwise never get one. `tools/backfill_previews.py` (new,
  re-runnable, only touches videos still missing a preview): lists the
  bucket, finds videos with no matching `.jpg`, downloads, generates,
  uploads. Run once this session for the 19 clips already in the
  bucket (all succeeded), followed by the reconcile that picked up the
  20 not-yet-synced raw clips (each uploaded with its own preview as
  the new `upload()` path's normal side effect). Bucket now holds 39
  videos / 39 previews / 0 missing, confirmed directly against GCS.

## Out of scope

- No change to the (currently unserved - see README's "Web gallery"
  section) `web/` gallery page itself to actually display the new
  preview images - that page has no server behind it right now, so
  there's nothing to wire the previews into yet.
- No preview regeneration for a video whose content changes after
  upload (not a real scenario in this app - a given filename's
  timestamp never gets re-encoded once uploaded).
- No thumbnail size/resolution tuning beyond ffmpeg's default frame
  size (matches the source video's own resolution) - a real gallery
  page choosing to downscale can do so client-side.

## Test plan

- `tests/test_cloud_sync.py`: 3 new tests - `_preview_name()` (plain
  and `-annotated` suffix cases) and `_local_video_names()` (includes
  both raw and annotated, ignores non-matching files). All 5
  pre-existing `plan_sync` tests pass unchanged (its contract didn't
  change, only what's fed into it).
- Full suite: `venv\Scripts\pytest tests\` - **55 passed** (52
  pre-existing + 3 new), no regressions.
- Live, against the real bucket this session: backfilled previews for
  all 19 pre-existing videos (19/19 succeeded); ran a real reconcile
  that uploaded the 20 not-yet-synced raw clips, each with its preview
  generated automatically; verified directly against GCS afterward -
  **39 videos, 39 previews, 0 videos missing a preview**.

## Implemented

- `core/cloud_sync.py`: `ALL_VIDEOS_GLOB`, `PREVIEW_SUFFIX`,
  `_local_video_names()`, `_preview_name()`, `_generate_preview()`,
  `_upload_preview()`, `_delete_preview()`; `list_remote_names()`
  filtered to `.mp4`; `upload()`/`delete()` now also handle the
  paired preview; `_reconcile()` uses `_local_video_names()` instead
  of `ANNOTATED_GLOB` alone.
- `gui.py`: `_handle_processed_result()` uploads both `raw_path` and
  `annotated_path`; `on_delete_selected()` tombstones whichever file
  was deleted, not just an `-annotated.mp4` one.
- `tools/backfill_previews.py` (new).
- `tests/test_cloud_sync.py`: 3 new tests.
