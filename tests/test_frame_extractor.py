"""Extracción de frames por cambio de pantalla, con PyAV (sin ffmpeg instalado)."""

from pathlib import Path

import numpy as np
import pytest

av = pytest.importorskip("av")

from src.ingestion.video_pipeline import FrameExtractor  # noqa: E402

pytestmark = pytest.mark.unit

FPS = 5


def _video(path: Path, screens: list[tuple[int, int]]) -> Path:
    """Video de pantallas planas: [(gris 0-255, segundos), ...]."""
    with av.open(str(path), "w") as out:
        stream = out.add_stream("mpeg4", rate=FPS)
        stream.width, stream.height, stream.pix_fmt = 320, 180, "yuv420p"
        for gray, seconds in screens:
            img = np.full((180, 320, 3), gray, np.uint8)
            for _ in range(seconds * FPS):
                out.mux(stream.encode(av.VideoFrame.from_ndarray(img, format="rgb24")))
        out.mux(stream.encode(None))
    return path


def test_a_static_screen_yields_one_frame_and_a_change_yields_another(tmp_path):
    video = _video(tmp_path / "v.mp4", [(20, 6), (230, 6)])

    frames = FrameExtractor().extract_keyframes(video, tmp_path / "frames")

    assert len(frames) == 2
    assert frames[1].timestamp == pytest.approx(6, abs=1)
    assert all(f.image_path.exists() for f in frames)


def test_kept_frames_are_never_closer_than_the_minimum_gap(tmp_path):
    # Una pantalla que cambia cada segundo (animación, scroll) no produce un frame
    # por segundo. Ojo: el destello SÍ puede quedar guardado; lo que se garantiza
    # es el espaciado, no que se ignore.
    video = _video(tmp_path / "v.mp4", [(20, 1), (230, 1)] * 5)

    frames = FrameExtractor(min_gap_seconds=3).extract_keyframes(video, tmp_path / "frames")

    gaps = [b.timestamp - a.timestamp for a, b in zip(frames, frames[1:])]
    assert gaps and min(gaps) >= 3


def test_duration_is_read_from_the_container(tmp_path):
    video = _video(tmp_path / "v.mp4", [(20, 7)])

    assert FrameExtractor().get_duration(video) == pytest.approx(7, abs=0.5)
