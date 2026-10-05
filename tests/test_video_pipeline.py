"""Unit tests for the Bantotal video ingestion pipeline."""

from pathlib import Path
import pytest

from src.ingestion.video_pipeline import (
    AudioTranscriber,
    Keyframe,
    ScreenAnalysis,
    TranscriptSegment,
    VideoMetadata,
    VideoSynthesizer,
    extract_bt_identifiers,
    format_timestamp,
    parse_timestamp_str,
)


@pytest.mark.unit
def test_timestamp_formatting_and_parsing():
    assert format_timestamp(45.0) == "00:45"
    assert format_timestamp(125.0) == "02:05"
    assert format_timestamp(3665.0) == "01:01:05"

    assert parse_timestamp_str("00:45") == 45.0
    assert parse_timestamp_str("02:05") == 125.0
    assert parse_timestamp_str("01:01:05") == 3665.0
    assert parse_timestamp_str("01:23.450") == 83.45


@pytest.mark.unit
def test_extract_bt_identifiers():
    text = "En la transacción B701 se consulta la tabla FSD011 y luego se ejecuta el proceso RTE99 sobre FST900."
    programs, tables = extract_bt_identifiers(text)
    assert "B701" in programs
    assert "RTE99" in programs
    assert "FSD011" in tables
    assert "FST900" in tables


@pytest.mark.unit
def test_parse_vtt_subtitles(tmp_path: Path):
    vtt_content = """WEBVTT

00:00:01.000 --> 00:00:04.500
Bienvenidos a la capacitación del módulo Cuentas Corrientes Bantotal.

00:00:05.000 --> 00:00:09.200
Ingresamos a la transacción B702 para dar de alta una cuenta.
"""
    vtt_file = tmp_path / "test.vtt"
    vtt_file.write_text(vtt_content, encoding="utf-8")

    segments = AudioTranscriber.parse_vtt_or_srt(vtt_file)
    assert len(segments) == 2
    assert segments[0].start == 1.0
    assert segments[0].end == 4.5
    assert "Cuentas Corrientes" in segments[0].text
    assert segments[1].start == 5.0
    assert "B702" in segments[1].text


@pytest.mark.unit
def test_parse_srt_subtitles(tmp_path: Path):
    srt_content = """1
00:01:10,000 --> 00:01:15,500
Revisamos los parámetros en la tabla FST011.

2
00:01:16,000 --> 00:01:20,000
Confirmamos la operación con F12.
"""
    srt_file = tmp_path / "test.srt"
    srt_file.write_text(srt_content, encoding="utf-8")

    segments = AudioTranscriber.parse_vtt_or_srt(srt_file)
    assert len(segments) == 2
    assert segments[0].start == 70.0
    assert segments[0].end == 75.5
    assert "FST011" in segments[0].text


@pytest.mark.unit
def test_video_synthesizer_alignment_and_markdown():
    keyframes = [
        Keyframe(index=0, timestamp=0.0, timestamp_str="00:00", image_path=Path("f0.jpg")),
        Keyframe(index=1, timestamp=60.0, timestamp_str="01:00", image_path=Path("f1.jpg")),
    ]
    analyses = [
        ScreenAnalysis(
            timestamp=0.0,
            timestamp_str="00:00",
            raw_text="Bantotal Core Banking - Menu Principal",
            programs_detected=[],
            tables_detected=[],
        ),
        ScreenAnalysis(
            timestamp=60.0,
            timestamp_str="01:00",
            raw_text="Transaccion B701 - Parametros Generales FSD011",
            programs_detected=["B701"],
            tables_detected=["FSD011"],
            tables_markdown=["| Codigo | Descripcion |\n|---|---|\n| 001 | Moneda Nacional |"],
        ),
    ]
    transcript = [
        TranscriptSegment(start=5.0, end=15.0, text="Arrancamos abriendo el sistema."),
        TranscriptSegment(start=62.0, end=75.0, text="Ahora entramos al programa B701 para verificar."),
    ]

    timeline = VideoSynthesizer.align_timeline(keyframes, analyses, transcript)
    assert len(timeline) == 2

    # Step 0
    assert timeline[0]["timestamp"] == 0.0
    assert "Arrancamos abriendo" in timeline[0]["audio_text"]

    # Step 1
    assert timeline[1]["timestamp"] == 60.0
    assert "B701" in timeline[1]["programs"]
    assert "FSD011" in timeline[1]["tables"]
    assert len(timeline[1]["tables_markdown"]) == 1

    meta = VideoMetadata(
        source_video="demo_b701.mp4",
        title="Capacitación Transacción B701",
        module="Cuentas",
        duration_seconds=120.0,
        duration_str="02:00",
        programs=["B701"],
        tables=["FSD011"],
    )

    markdown = VideoSynthesizer.generate_markdown(meta, timeline)

    # Assert Markdown structure
    assert markdown.startswith("---")
    assert 'source_type: "video_training"' in markdown
    assert 'title: "Capacitación Transacción B701"' in markdown
    assert 'module: "Cuentas"' in markdown
    assert "## Resumen Ejecutivo de Negocio" in markdown
    assert "### [00:00] Paso Operativo" in markdown
    assert "### [01:00] Paso Operativo - Transacción `B701`" in markdown
    assert "| Codigo | Descripcion |" in markdown
    assert "Explicación del Relator" in markdown
