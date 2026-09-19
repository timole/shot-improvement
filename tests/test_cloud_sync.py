from pathlib import Path
from typing import Callable, Optional

import pytest

from core.cloud_sync import Backend, SyncWorker, _local_video_names, delete, plan_sync, upload


def test_plan_sync_uploads_a_local_clip_missing_remotely() -> None:
    to_upload, to_delete = plan_sync({"a.mp4"}, set(), set())
    assert to_upload == {"a.mp4"}
    assert to_delete == set()


def test_plan_sync_deletes_only_tombstoned_names() -> None:
    to_upload, to_delete = plan_sync({"a.mp4"}, {"a.mp4", "b.mp4"}, {"b.mp4"})
    assert to_upload == set()
    assert to_delete == {"b.mp4"}


def test_plan_sync_does_not_delete_a_clip_missing_locally_but_not_tombstoned() -> None:
    # This is the race a naive "absent locally => delete remotely" rule
    # would get wrong: a reconcile that snapshots the local folder
    # before a recording finishes and the bucket after its upload lands
    # would otherwise see the brand new clip as an orphan and delete it.
    to_upload, to_delete = plan_sync(set(), {"just-uploaded.mp4"}, set())
    assert to_upload == set()
    assert to_delete == set()


def test_plan_sync_does_not_reupload_a_tombstoned_name_missing_remotely() -> None:
    # Already deleted remotely (or never uploaded before being deleted
    # locally) - a pending tombstone must not be treated as "needs
    # uploading" just because it's also absent from remote_names.
    to_upload, to_delete = plan_sync({"a.mp4"}, set(), {"a.mp4"})
    assert to_upload == set()
    assert to_delete == set()


def test_plan_sync_handles_the_steady_state_with_nothing_to_do() -> None:
    to_upload, to_delete = plan_sync({"a.mp4", "b.mp4"}, {"a.mp4", "b.mp4"}, set())
    assert to_upload == set()
    assert to_delete == set()


def test_local_video_names_includes_both_raw_and_annotated(tmp_path: Path) -> None:
    (tmp_path / "shot-improvement-20260915120000.mp4").write_bytes(b"")
    (tmp_path / "shot-improvement-20260915120000-annotated.mp4").write_bytes(b"")
    (tmp_path / "not-a-recording.mp4").write_bytes(b"")  # must not match - wrong prefix
    (tmp_path / "cloud_sync_tombstones.json").write_text("[]", encoding="utf-8")

    names = _local_video_names(tmp_path)

    assert names == {
        "shot-improvement-20260915120000.mp4",
        "shot-improvement-20260915120000-annotated.mp4",
    }


# --- spec 095: multi-backend upload()/delete()/SyncWorker ------------------


class FakeBackend:
    """Test double implementing core.cloud_sync.Backend, standing in for
    core.azure_sync.AzureBlobBackend."""

    def __init__(self, name: str, fail_upload: bool = False, fail_delete: bool = False) -> None:
        self.name = name
        self.fail_upload = fail_upload
        self.fail_delete = fail_delete
        self.uploaded_videos: list[str] = []
        self.uploaded_previews: list[str] = []
        self.deleted: list[str] = []
        self._remote: set[str] = set()

    def list_remote_names(self) -> set[str]:
        return set(self._remote)

    def upload_video(self, path: Path, on_progress: Optional[Callable[[int, int], None]]) -> None:
        if self.fail_upload:
            raise RuntimeError(f"{self.name} upload failed")
        self.uploaded_videos.append(path.name)
        self._remote.add(path.name)
        if on_progress is not None:
            size = path.stat().st_size
            on_progress(0, size)
            on_progress(size, size)

    def upload_preview(self, jpg_path: Path, blob_name: str) -> None:
        self.uploaded_previews.append(blob_name)

    def delete(self, name: str) -> None:
        if self.fail_delete:
            raise RuntimeError(f"{self.name} delete failed")
        self.deleted.append(name)
        self._remote.discard(name)


def _make_video_file(tmp_path: Path, name: str = "shot-improvement-20260101000000.mp4") -> Path:
    path = tmp_path / name
    path.write_bytes(b"fake video bytes")
    return path


def test_upload_writes_video_and_preview_to_every_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.cloud_sync.generate_preview",
        lambda video_path, out_path: out_path.write_bytes(b"fake jpeg") or True,
    )
    video = _make_video_file(tmp_path)
    b1, b2 = FakeBackend("b1"), FakeBackend("b2")

    upload(video, [b1, b2])

    assert b1.uploaded_videos == [video.name]
    assert b2.uploaded_videos == [video.name]
    assert b1.uploaded_previews == ["shot-improvement-20260101000000.jpg"]
    assert b2.uploaded_previews == ["shot-improvement-20260101000000.jpg"]


def test_upload_skips_preview_on_every_backend_when_generation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.cloud_sync.generate_preview", lambda v, o: False)
    video = _make_video_file(tmp_path)
    b1, b2 = FakeBackend("b1"), FakeBackend("b2")

    upload(video, [b1, b2])  # must not raise - a missing preview is never a sync failure

    assert b1.uploaded_videos == [video.name]
    assert b1.uploaded_previews == []
    assert b2.uploaded_previews == []


def test_upload_scales_progress_across_backends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.cloud_sync.generate_preview", lambda v, o: False)
    video = _make_video_file(tmp_path)
    calls: list[tuple[int, int]] = []
    b1, b2 = FakeBackend("b1"), FakeBackend("b2")

    upload(video, [b1, b2], on_progress=lambda done, total: calls.append((done, total)))

    size = video.stat().st_size
    assert calls[0] == (0, 2 * size)
    assert calls[-1] == (2 * size, 2 * size)


def test_upload_continues_to_other_backends_after_one_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("core.cloud_sync.generate_preview", lambda v, o: False)
    video = _make_video_file(tmp_path)
    failing = FakeBackend("failing", fail_upload=True)
    working = FakeBackend("working")

    with pytest.raises(RuntimeError):
        upload(video, [failing, working])

    assert working.uploaded_videos == [video.name]  # not skipped just because failing failed first


def test_delete_removes_from_every_backend() -> None:
    b1, b2 = FakeBackend("b1"), FakeBackend("b2")

    delete("a.mp4", [b1, b2])

    assert b1.deleted == ["a.mp4"]
    assert b2.deleted == ["a.mp4"]


def test_delete_continues_to_other_backends_after_one_fails() -> None:
    failing = FakeBackend("failing", fail_delete=True)
    working = FakeBackend("working")

    with pytest.raises(RuntimeError):
        delete("a.mp4", [failing, working])

    assert working.deleted == ["a.mp4"]  # not skipped just because failing failed first


def test_reconcile_retires_a_tombstone_only_once_gone_from_every_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The bug the validation pass specifically flagged: discarding a
    # tombstone as soon as ONE backend confirms deletion would orphan
    # the name forever on any backend that hadn't caught up yet.
    empty_recordings = tmp_path / "recordings"
    empty_recordings.mkdir()
    monkeypatch.setattr("core.cloud_sync.RECORDINGS_DIR", empty_recordings)
    monkeypatch.setattr("core.cloud_sync.TOMBSTONES_PATH", tmp_path / "tombstones.json")

    has_it = FakeBackend("has_it")
    has_it._remote = {"gone.mp4"}
    never_had_it = FakeBackend("never_had_it")

    worker = SyncWorker(backends=[has_it, never_had_it])
    worker._tombstones = {"gone.mp4"}

    worker._reconcile()

    assert has_it.deleted == ["gone.mp4"]
    assert never_had_it.deleted == []  # nothing to delete there - it never had it
    assert worker._tombstones == set()  # retired: confirmed gone everywhere


def test_reconcile_uploads_only_to_the_backend_missing_a_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Mid-migration to a newly added backend: a name already on one
    # backend must still be uploaded to the other, not skipped because
    # SOME backend already has it (a union-based reconcile would get
    # this wrong - see core.cloud_sync.plan_sync's docstring).
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    video = _make_video_file(recordings)
    monkeypatch.setattr("core.cloud_sync.RECORDINGS_DIR", recordings)
    monkeypatch.setattr("core.cloud_sync.TOMBSTONES_PATH", tmp_path / "tombstones.json")
    monkeypatch.setattr("core.cloud_sync.generate_preview", lambda v, o: False)

    has_it = FakeBackend("has_it")
    has_it._remote = {video.name}
    missing_it = FakeBackend("missing_it")

    worker = SyncWorker(backends=[has_it, missing_it])
    worker._reconcile()

    assert has_it.uploaded_videos == []
    assert missing_it.uploaded_videos == [video.name]
