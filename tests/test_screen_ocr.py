"""OCR de frames: máscara de la webcam, umbral de confianza y error explícito sin motor."""

import sys
from pathlib import Path

import numpy as np
import pytest

PIL = pytest.importorskip("PIL.Image")

from src.ingestion.video_pipeline import Keyframe, ScreenOCR  # noqa: E402

pytestmark = pytest.mark.unit


class FakeEngine:
    """Imita PaddleOCR 3.x: predict(img) -> [{'rec_texts': [...], 'rec_scores': [...]}]."""

    def __init__(self, texts, scores):
        self.texts, self.scores, self.seen = texts, scores, None

    def predict(self, img):
        self.seen = img
        return [{"rec_texts": self.texts, "rec_scores": self.scores}]


def _keyframe(tmp_path: Path, width=960, height=540) -> Keyframe:
    path = tmp_path / "f.png"
    PIL.fromarray(np.zeros((height, width, 3), np.uint8)).save(path)
    return Keyframe(index=0, timestamp=0, timestamp_str="00:00", image_path=path)


def test_webcam_corner_is_blanked_before_the_engine_sees_it(tmp_path):
    # El recuadro de la webcam trae el nombre y la cara de quien expone: no debe
    # llegar al RAG. Medido sobre 1920x1080 en el piloto; aquí en 960x540 para
    # comprobar que la máscara escala con la resolución.
    engine = FakeEngine([], [])

    ScreenOCR(engine=engine).analyze_frame(_keyframe(tmp_path))

    seen = engine.seen
    assert (seen[:80, 820:] == 255).all()       # esquina superior derecha, tapada
    assert (seen[100:, :800] == 0).all()        # el resto, intacto


def test_low_confidence_lines_are_dropped(tmp_path):
    engine = FakeEngine(["Mantenimiento de Personas", "~#l1"], [0.97, 0.41])

    analysis = ScreenOCR(engine=engine).analyze_frame(_keyframe(tmp_path))

    assert analysis.raw_text == "Mantenimiento de Personas"


def test_missing_engine_is_an_error_not_an_empty_screen(monkeypatch):
    # Antes "caía a una heurística" que devolvía texto vacío: el video se ingería
    # sin pantalla y nadie se enteraba.
    monkeypatch.setitem(sys.modules, "paddleocr", None)

    with pytest.raises(RuntimeError, match="paddleocr"):
        ScreenOCR()
