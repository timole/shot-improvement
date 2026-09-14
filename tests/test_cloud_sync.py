from core.cloud_sync import plan_sync


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
