from pathlib import Path
from types import SimpleNamespace

from src.video_frame_service import VideoFrameExtractor, _representative_timestamps


def test_representative_timestamps_cover_the_video() -> None:
    assert _representative_timestamps(10.0) == [0.8, 3.4, 6.6, 9.2]


def test_frame_extractor_returns_named_jpeg_attachments(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake video")

    def runner(command, **kwargs):
        assert kwargs["timeout"] == 45
        Path(command[-1]).write_bytes(b"j" * 400)
        return SimpleNamespace(returncode=0, stderr="")

    extractor = VideoFrameExtractor(
        ffmpeg_exe="ffmpeg",
        duration_reader=lambda _path: (300, 10.0),
        runner=runner,
    )

    frames = extractor.extract(video, prefix="candidate-1", max_frames=3)

    assert [frame.name for frame in frames] == [
        "candidate-1-frame-01.jpg",
        "candidate-1-frame-02.jpg",
        "candidate-1-frame-03.jpg",
    ]
    assert all(frame.mime_type == "image/jpeg" for frame in frames)
    assert all(frame.data == b"j" * 400 for frame in frames)
    assert not list(tmp_path.glob("*-frame-*.jpg"))
