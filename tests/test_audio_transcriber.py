"""Transcripción local por bloques (faster-whisper) y extracción de audio sin ffmpeg."""

import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

av = pytest.importorskip("av")

from src.ingestion.video_pipeline import AudioTranscriber

pytestmark = pytest.mark.unit

RATE = 16000


def _wav(path: Path, seconds: float, rate: int = RATE, channels: int = 1) -> Path:
    samples = (np.sin(np.arange(int(seconds * rate)) / 20) * 8000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.repeat(samples, channels).tobytes())
    return path


class FakeWhisper:
    """Devuelve un segmento por bloque, con tiempos RELATIVOS al bloque, como faster-whisper."""

    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, **kwargs):
        self.calls += 1
        return iter([SimpleNamespace(start=0.25, end=0.5, text=f" bloque {self.calls} ")]), None


def test_block_timestamps_are_absolute(tmp_path):
    # faster-whisper hace segfault con el audio entero de 2 h 38 min (medido en el
    # piloto), así que se transcribe por bloques. Cada bloque devuelve tiempos desde
    # su propio cero: sin el desplazamiento, todo el video caería en los primeros minutos.
    model = FakeWhisper()
    transcriber = AudioTranscriber(model=model, block_seconds=1)

    segments = transcriber.transcribe(_wav(tmp_path / "a.wav", 2.5), work_dir=tmp_path)

    assert [s.start for s in segments] == [0.25, 1.25, 2.25]
    assert segments[1].text == "bloque 2"


def test_whisper_is_primed_with_the_domain_vocabulary(tmp_path):
    # Sin contexto, Whisper escribe «man total» por «Bantotal» (medido en el
    # video de Definición de transacciones).
    class Recording(FakeWhisper):
        def transcribe(self, audio, **kwargs):
            self.kwargs = kwargs
            return super().transcribe(audio, **kwargs)

    model = Recording()
    AudioTranscriber(model=model).transcribe(_wav(tmp_path / "a.wav", 0.5), work_dir=tmp_path)

    assert "Bantotal" in model.kwargs["initial_prompt"]


def test_finished_blocks_are_not_transcribed_again(tmp_path):
    # 2 h 38 min de audio son horas de CPU: si el proceso se corta, se reanuda
    # desde el último bloque terminado.
    blocks = tmp_path / "bloques"
    blocks.mkdir()
    (blocks / "b000.json").write_text(json.dumps([{"start": 0.1, "end": 0.2, "text": "ya estaba"}]),
                                      encoding="utf-8")
    model = FakeWhisper()

    segments = AudioTranscriber(model=model, block_seconds=1).transcribe(
        _wav(tmp_path / "a.wav", 2.5), work_dir=tmp_path)

    assert model.calls == 2
    assert segments[0].text == "ya estaba"


def test_missing_engine_is_an_error_not_an_empty_transcript(tmp_path, monkeypatch):
    # Antes devolvía [] con un warning: el video se ingería sin audio y nadie se enteraba.
    monkeypatch.setitem(sys.modules, "faster_whisper", None)

    with pytest.raises(RuntimeError, match="faster-whisper"):
        AudioTranscriber().transcribe(_wav(tmp_path / "a.wav", 0.5), work_dir=tmp_path)


class CrashingWhisper(FakeWhisper):
    def transcribe(self, audio, **kwargs):
        if self.calls == 1:
            raise MemoryError("se cayó en el bloque 2")
        return super().transcribe(audio, **kwargs)


class NoFrames:
    def get_duration(self, video_path):
        return 2.5

    def extract_keyframes(self, video_path, output_dir, **kwargs):
        return []


def test_a_crash_keeps_the_finished_blocks_for_the_next_run(tmp_path):
    # El directorio de trabajo se borraba en un `finally`: justo cuando el proceso
    # fallaba se perdían los bloques que la reanudación necesitaba.
    from src.ingestion.video_pipeline import VideoIngestPipeline

    video = _wav(tmp_path / "clase.wav", 2.5)
    pipeline = VideoIngestPipeline(frame_extractor=NoFrames(), ocr=object(),
                                   transcriber=AudioTranscriber(model=CrashingWhisper(), block_seconds=1))

    with pytest.raises(MemoryError):
        pipeline.process(video_path=video, output_dir=tmp_path / "out")

    assert (tmp_path / "out" / "_temp_clase" / "bloques" / "b000.json").exists()


def test_audio_is_extracted_as_16khz_mono_without_ffmpeg(tmp_path):
    source = _wav(tmp_path / "estereo.wav", 1.0, rate=44100, channels=2)

    out = AudioTranscriber().extract_audio(source, tmp_path / "salida.wav")

    with wave.open(str(out)) as w:
        assert (w.getframerate(), w.getnchannels()) == (RATE, 1)
        assert abs(w.getnframes() / RATE - 1.0) < 0.05
