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


def _frame(ts: float, *lines: str) -> tuple[Keyframe, ScreenAnalysis]:
    tstr = format_timestamp(ts)
    return (Keyframe(index=int(ts), timestamp=ts, timestamp_str=tstr, image_path=Path(f"f{int(ts)}.png")),
            ScreenAnalysis(timestamp=ts, timestamp_str=tstr, raw_text="\n".join(lines)))


_MENU = ("DLYA : : Bantotal - Trabajo", "Inicio Accesos", "Contabilidad", "Clientes", "Préstamos")


def _markdown(frames, transcript=()) -> str:
    keyframes, analyses = zip(*frames)
    timeline = VideoSynthesizer.align_timeline(list(keyframes), list(analyses), list(transcript),
                                               interface_min_frames=2)
    meta = VideoMetadata(source_video="v.mp4", title="T", module="M", duration_seconds=0, duration_str="00:00")
    return VideoSynthesizer.generate_markdown(meta, timeline)


@pytest.mark.unit
def test_screen_interface_is_written_once_per_video():
    # El menú de Bantotal sale en casi todos los frames: repetido en cada paso
    # ensucia la búsqueda léxica sin aportar nada.
    md = _markdown([_frame(0, *_MENU, "Mantenimiento de Personas"),
                    _frame(60, *_MENU, "Pizarra de Tasas por Cuenta")])

    assert md.count("Inicio Accesos") == 1
    assert "Pizarra de Tasas por Cuenta" in md


@pytest.mark.unit
def test_document_numbers_never_reach_the_markdown():
    md = _markdown([_frame(0, *_MENU, "Cedula", "17258608"),
                    _frame(60, *_MENU, "Titular 17258608 de la cuenta 999")])

    assert "17258608" not in md
    assert "[DOC]" in md


@pytest.mark.unit
def test_step_header_names_the_screen():
    md = _markdown([_frame(0, *_MENU, "Mantenimiento de Listas Negras",
                           "Mantenimiento de Listas Negras", "Filtrar por")])

    assert "### [00:00] Mantenimiento de Listas Negras" in md


@pytest.mark.unit
def test_a_step_without_its_own_title_inherits_the_previous_one():
    # Un diálogo de confirmación sobre la misma pantalla no trae el título
    # repetido: la pantalla sigue siendo la anterior.
    md = _markdown([_frame(0, *_MENU, "SubOrdinales del Ordinal", "SubOrdinales del Ordinal"),
                    _frame(30, *_MENU, "Imprimirt", "¿Está seguro que desea ejecutar la operación?")])

    assert "### [00:30] SubOrdinales del Ordinal" in md


@pytest.mark.unit
def test_function_keys_are_not_programs():
    # «F12» es la tecla con la que se confirma en Bantotal, no un programa.
    programs, _ = extract_bt_identifiers("Confirmamos con F12 y vamos a HW004")

    assert programs == ["HW004"]


@pytest.mark.unit
def test_screen_text_is_not_truncated():
    # El armado anterior cortaba en 10 líneas: perdía las filas de las grillas.
    rows = [f"Cuenta 11000{i:04d}" for i in range(15)]
    md = _markdown([_frame(0, *_MENU, *rows)])

    assert all(r in md for r in rows)


@pytest.mark.unit
def test_frame_without_a_useful_screen_does_not_open_a_step():
    # Recuadro de Teams con la cámara apagada: una línea, el nombre. Medido en el
    # piloto. Lo que se dijo mientras tanto sigue en el paso anterior.
    md = _markdown([_frame(0, *_MENU, "Mantenimiento de Personas"), _frame(30, "Juan Pérez")],
                   [TranscriptSegment(start=40, end=45, text="esto sigue siendo personas")])

    assert "[00:30]" not in md
    assert "esto sigue siendo personas" in md


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
            raw_text="\n".join([*_MENU, "Bantotal Core Banking - Menu Principal"]),
            programs_detected=[],
            tables_detected=[],
        ),
        ScreenAnalysis(
            timestamp=60.0,
            timestamp_str="01:00",
            raw_text="\n".join([*_MENU, "Transaccion B701 - Parametros Generales FSD011"]),
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
    # El encabezado lleva el nombre de la pantalla (test_step_header_names_the_screen).
    assert "### [00:00] " in markdown
    assert "- Transacción `B701`" in markdown
    assert "| Codigo | Descripcion |" in markdown
    assert "Explicación del Relator" in markdown
