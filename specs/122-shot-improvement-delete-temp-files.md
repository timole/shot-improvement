# 122 — Delete per-recording temp folders again

`core.session.KEEP_TEMP_DIR_FOR_INSPECTION` -> `False`. The kept
`%TEMP%\shot-improvement-*` folders (raw + annotated frames + audio)
had grown to 25 folders / 19.7 GB and filled the disk, breaking the
website image build (`No space left on device`). On request, temp files
are deleted after each recording again. `pending/` (deferred
recordings, incl. `pending/processed/`) and `recordings/` are unchanged.
