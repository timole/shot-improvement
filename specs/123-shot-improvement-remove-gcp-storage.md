# 123 — Remove Google Cloud Storage; Azure is the only backend

## What
- `core/cloud_sync.py`: `GcsBackend` and the `BUCKET` constant removed
  (the `Backend` protocol, `SyncWorker`, tombstones, `plan_sync` stay).
- `gui.py`: `SyncWorker(backends=[AzureBlobBackend()])`.
- `requirements.txt`: `google-cloud-storage` dropped.
- `tools/backfill_previews.py` deleted (GCS-only one-off; it already
  imported helpers that no longer existed).
- Docstrings/README updated ("Two galleries" -> "Web gallery").
- Specs 078-097 are left as historical record.

## Not done
The GCS bucket `wide-exchanger-463707-c6-shot-improvement` and the old
`ai.timolehtonen.tech/shot-improvement` gallery (another repo) are
untouched. mediapipe model downloads still come from
storage.googleapis.com (public model URLs, not project storage).

## Tests
`venv\Scripts\pytest tests\` passes.
