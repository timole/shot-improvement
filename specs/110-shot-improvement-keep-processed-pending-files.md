# 110 — Shot-improvement: keep a processed pending item's raw files

## What

`core.pending.process_pending_recording()` no longer deletes a "Vain
nauhoitus" recording's raw frames/audio once it's been turned into the
usual raw + annotated mp4s. Instead it moves the whole
`pending/<timestamp>/` directory into `pending/processed/<timestamp>/`
(`archive_processed()`) - every file survives, just out of
`list_pending()`'s view so it doesn't linger in the "Odottaa
käsittelyä" queue or get reprocessed.

## Why

The user asked where the raw files for a specific processed recording
were, and on learning `process_pending_recording()` deletes the source
directory on success, said plainly: keep all files, don't delete them,
going forward - not a one-off request, an explicit standing rule.

## Design

Unlike spec 105's `KEEP_TEMP_DIR_FOR_INSPECTION` (a flag documented as
temporary, meant to be flipped back on request), this is a durable
behavior change - `delete_pending()` is no longer called on the
success path at all.

Simply skipping the delete wasn't enough on its own: `list_pending()`
scans `PENDING_DIR`'s direct children for a valid `meta.json`, so a
processed item left in place would reappear as still-pending on the
next refresh/restart and get reprocessed forever - close to the
"Odottaa käsittelyä resets" symptom investigated (and not reproduced)
earlier this session, and exactly what removing the delete outright
would have caused. `archive_processed()` moves the directory instead,
and `list_pending()` explicitly skips an entry literally named
`processed` (by name, not by full path, so both functions stay
testable against an arbitrary directory the same way `list_pending()`
already was via its own `pending_dir` parameter).

`delete_pending()` itself is untouched and still used for the one
unrelated case that already existed: a deferred recording that
captured zero frames (nothing to keep).

## Files

- `core/pending.py` - `PROCESSED_DIR`, `archive_processed()`,
  `list_pending()` skips it, `process_pending_recording()` archives
  instead of deleting on success.
- `tests/test_pending.py` - `archive_processed` moves files intact and
  out of the source dir; `list_pending` skips an archived entry.

## Test plan

- `venv\Scripts\pytest tests\` - 107 passed.
- Process a real pending "Vain nauhoitus" item via "Käsittele
  odottavat" - confirm both mp4s appear as before, the pending count
  drops, and `pending/processed/<ts>/` now holds the raw frames +
  audio.wav + meta.json instead of nothing.
