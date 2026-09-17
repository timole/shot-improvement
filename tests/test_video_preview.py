from core.video_preview import preview_name


def test_preview_name_strips_mp4_extension_and_adds_jpg() -> None:
    assert preview_name("shot-improvement-20260915120000.mp4") == "shot-improvement-20260915120000.jpg"


def test_preview_name_handles_annotated_suffix() -> None:
    assert preview_name("shot-improvement-20260915120000-annotated.mp4") == "shot-improvement-20260915120000-annotated.jpg"
