import json
from types import SimpleNamespace

from azure.core.exceptions import ResourceModifiedError

from server import shot_index


def test_invalidated_analysis_falls_back_to_original_pipeline(monkeypatch):
    monkeypatch.setattr(shot_index, "find", lambda stem: {"analysis_version": None})
    assert shot_index.artifact("shot-20260927125048-5", "mp4") is None


def test_gallery_reads_manifest_once_for_repeated_polls(monkeypatch):
    calls = []
    monkeypatch.setattr(shot_index, "_cached", None)
    monkeypatch.setattr(shot_index, "_read", lambda: (calls.append(1) or [{"stem": "b"}], "etag"))
    assert shot_index.list_shots() == [{"stem": "b"}]
    assert shot_index.list_shots() == [{"stem": "b"}]
    assert len(calls) == 1


def test_concurrent_manifest_update_retries_without_losing_other_shot(monkeypatch):
    existing = [{"stem": "a", "speed_kmh": 90}]
    writes = []
    def read():
        return existing.copy(), str(len(existing))
    def upload(data, **kwargs):
        if not writes:
            writes.append(None)
            existing.append({"stem": "b", "speed_kmh": 100})
            raise ResourceModifiedError("concurrent writer")
        writes.append(json.loads(data))
    blob = SimpleNamespace(upload_blob=upload)
    monkeypatch.setattr(shot_index, "_read", read)
    monkeypatch.setattr(shot_index.android_blobs, "_container", lambda: SimpleNamespace(get_blob_client=lambda _: blob))
    shot_index.update([{"stem": "a", "top_hand_speed_kmh": 20}])
    assert writes[-1] == [{"stem": "b", "speed_kmh": 100}, {"stem": "a", "speed_kmh": 90, "top_hand_speed_kmh": 20}]
