from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from src.models import ImageAttachment


MAX_FRAME_BYTES = 350_000


class VideoFrameError(RuntimeError):
    """Raised when representative visual evidence cannot be extracted safely."""


class VideoFrameExtractor:
    def __init__(
        self,
        *,
        ffmpeg_exe: str | None = None,
        duration_reader: Callable[[str], tuple[int, float]] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._ffmpeg_exe = ffmpeg_exe
        self._duration_reader = duration_reader
        self._runner = runner

    def extract(
        self,
        video_path: Path,
        *,
        prefix: str,
        max_frames: int = 4,
    ) -> list[ImageAttachment]:
        path = video_path.resolve()
        if not path.is_file():
            raise VideoFrameError(f"Video file was not found: {path}")
        ffmpeg_exe, duration_reader = self._runtime()
        try:
            _frame_count, duration = duration_reader(str(path))
        except Exception as exc:
            raise VideoFrameError(f"Could not inspect video duration: {exc}") from exc
        if duration <= 0:
            raise VideoFrameError("Video duration was unavailable.")

        timestamps = _representative_timestamps(duration, max_frames=max_frames)
        attachments: list[ImageAttachment] = []
        for index, timestamp in enumerate(timestamps, start=1):
            output = path.parent / f"{prefix}-frame-{index:02d}.jpg"
            command = [
                ffmpeg_exe,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(512,iw)':-2",
                "-q:v",
                "6",
                "-y",
                str(output),
            ]
            try:
                completed = self._runner(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=45,
                    check=False,
                )
            except Exception as exc:
                raise VideoFrameError(f"FFmpeg frame extraction failed: {exc}") from exc
            if completed.returncode != 0 or not output.is_file():
                detail = " ".join(str(completed.stderr or "").split())
                raise VideoFrameError(
                    f"FFmpeg could not extract frame {index}: {detail or 'unknown error'}"
                )
            data = output.read_bytes()
            output.unlink(missing_ok=True)
            if not data or len(data) > MAX_FRAME_BYTES:
                raise VideoFrameError(
                    f"Extracted frame {index} had an invalid size ({len(data)} bytes)."
                )
            attachments.append(
                ImageAttachment(
                    name=f"{prefix}-frame-{index:02d}.jpg",
                    mime_type="image/jpeg",
                    data=data,
                )
            )
        if len(attachments) < 2:
            raise VideoFrameError("Fewer than two representative frames were extracted.")
        return attachments

    def _runtime(self) -> tuple[str, Callable[[str], tuple[int, float]]]:
        if self._ffmpeg_exe is not None and self._duration_reader is not None:
            return self._ffmpeg_exe, self._duration_reader
        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise VideoFrameError(
                "imageio-ffmpeg is not installed. Run pip install -e ."
            ) from exc
        return (
            self._ffmpeg_exe or imageio_ffmpeg.get_ffmpeg_exe(),
            self._duration_reader or imageio_ffmpeg.count_frames_and_secs,
        )


def _representative_timestamps(duration: float, *, max_frames: int = 4) -> list[float]:
    count = min(4, max(2, int(max_frames)))
    fractions = (0.08, 0.34, 0.66, 0.92)[:count]
    end_guard = max(0.0, duration - 0.05)
    values = [
        round(min(end_guard, max(0.0, duration * fraction)), 3)
        for fraction in fractions
    ]
    unique: list[float] = []
    for value in values:
        if not unique or abs(value - unique[-1]) >= 0.03:
            unique.append(value)
    return unique
