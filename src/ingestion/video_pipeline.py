"""Video ingestion pipeline for Bantotal training and demo videos.

Todo local y en CPU; nada sale de la máquina (sin motores en la nube, a propósito):
1. Frames por cambio de pantalla con PyAV (`FrameExtractor`); ffmpeg no está instalado.
2. Transcripción con faster-whisper por bloques reanudables (`AudioTranscriber`), o un
   transcript existente (VTT/SRT/JSON/Markdown).
3. OCR de pantalla con PaddleOCR 3.x, con la webcam tapada (`ScreenOCR`).
4. Armado en Markdown por pantalla, con el texto limpio por `screen_text`: la interfaz
   una sola vez, los documentos de identidad enmascarados, el título de la pantalla.

Dependencias pesadas en requirements-video.txt; los parámetros por defecto son los que
midió el piloto «Curso Contabilidad — Clientes y contabilidad» (2026-10-05).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from src.ingestion.screen_text import (
    clean_screen_lines,
    document_numbers,
    interface_keys,
    is_useful_screen,
    mask_document_numbers,
    screen_title,
)

log = logging.getLogger(__name__)

# Bantotal transaction & table patterns (e.g., B701, FSD011, FST900, RTE99, PA01)
_BT_TABLE_PATTERN = re.compile(r"\b(FS[A-Z0-9]{3,6})\b", re.IGNORECASE)
_BT_PROGRAM_PATTERN = re.compile(r"\b([A-Z]{1,4}\d{2,5}[A-Z0-9]?)\b")
# Teclas de función (F12 confirma en Bantotal): tienen forma de programa y no lo son.
_FUNCTION_KEY = re.compile(r"^F\d{1,2}$")


@dataclass
class Keyframe:
    """A visual keyframe extracted from a scene change."""

    index: int
    timestamp: float
    timestamp_str: str
    image_path: Path
    scene_score: float = 0.0


@dataclass
class TranscriptSegment:
    """A timestamped segment of audio narration."""

    start: float
    end: float
    text: str

    @property
    def start_str(self) -> str:
        return format_timestamp(self.start)

    @property
    def end_str(self) -> str:
        return format_timestamp(self.end)


@dataclass
class ScreenAnalysis:
    """Extracted visual and structured information from a keyframe screen."""

    timestamp: float
    timestamp_str: str
    raw_text: str = ""
    programs_detected: list[str] = field(default_factory=list)
    tables_detected: list[str] = field(default_factory=list)
    tables_markdown: list[str] = field(default_factory=list)
    ui_fields: list[dict[str, str]] = field(default_factory=list)


@dataclass
class VideoMetadata:
    """Metadata for the processed video document."""

    source_video: str
    title: str
    module: str
    duration_seconds: float
    duration_str: str
    programs: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    keyframe_count: int = 0
    transcript_segments_count: int = 0
    tags: list[str] = field(default_factory=list)


def format_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    secs = int(seconds)
    hours = secs // 3600
    minutes = (secs % 3600) // 60
    rem_secs = secs % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{rem_secs:02d}"
    return f"{minutes:02d}:{rem_secs:02d}"


def parse_timestamp_str(time_str: str) -> float:
    """Parse MM:SS or HH:MM:SS or HH:MM:SS,mmm to float seconds."""
    parts = time_str.replace(",", ".").strip().split(":")
    if len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def extract_bt_identifiers(text: str) -> tuple[list[str], list[str]]:
    """Extract Bantotal programs and tables from text."""
    raw_tables = sorted(set(t.upper() for t in _BT_TABLE_PATTERN.findall(text)))
    raw_progs = set(_BT_PROGRAM_PATTERN.findall(text))
    # Exclude identifiers that are already identified as tables
    programs = sorted(p for p in raw_progs if p.upper() not in raw_tables and not p.upper().startswith("FS")
                      and not _FUNCTION_KEY.match(p))
    return programs, raw_tables


class FrameExtractor:
    """Extrae un frame cada vez que cambia la pantalla, con PyAV (sin ffmpeg instalado).

    Compara una miniatura en grises contra la del último frame guardado. En una
    grabación de pantalla eso equivale a "cambió la pantalla". Se decodifica todo
    el video y se mira una muestra por segundo: solo con los I-frames del códec se
    pierden pantallas (el GOP del piloto era de ~34 s).
    """

    THUMBNAIL = (64, 36)  # ancho, alto de la huella que se compara

    def __init__(self, diff_threshold: float = 6.0, min_gap_seconds: float = 3.0,
                 sample_seconds: float = 1.0):
        # diff_threshold: diferencia media absoluta (0-255) para "pantalla nueva".
        # min_gap_seconds: una animación o un parpadeo no es una pantalla nueva.
        self.diff_threshold = diff_threshold
        self.min_gap_seconds = min_gap_seconds
        self.sample_seconds = sample_seconds

    def get_duration(self, video_path: Path) -> float:
        import av  # type: ignore

        with av.open(str(video_path)) as container:
            if container.duration is None:
                raise ValueError(f"El contenedor no declara duración: {video_path}")
            return container.duration / av.time_base

    def extract_keyframes(self, video_path: Path, output_dir: Path) -> list[Keyframe]:
        import av  # type: ignore

        output_dir.mkdir(parents=True, exist_ok=True)
        keyframes: list[Keyframe] = []
        last_print = last_kept_at = last_sample = None
        with av.open(str(video_path)) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            for frame in container.decode(stream):
                t = float(frame.time)
                if last_sample is not None and t - last_sample < self.sample_seconds:
                    continue
                last_sample = t
                print_ = frame.to_ndarray(format="gray", width=self.THUMBNAIL[0],
                                          height=self.THUMBNAIL[1]).astype(np.int16)
                diff = float(np.abs(print_ - last_print).mean()) if last_print is not None else 0.0
                if last_print is not None and (t - last_kept_at < self.min_gap_seconds
                                               or diff < self.diff_threshold):
                    continue
                image_path = output_dir / f"f_{len(keyframes):05d}.png"
                frame.to_image().save(image_path)
                keyframes.append(Keyframe(index=len(keyframes), timestamp=t, timestamp_str=format_timestamp(t),
                                          image_path=image_path, scene_score=diff))
                last_print, last_kept_at = print_, t
        return keyframes


class AudioTranscriber:
    """Extracts audio and produces synchronized transcript segments.

    Todo local: faster-whisper en CPU. No hay motor en la nube a propósito: el
    audio de una capacitación del banco no sale de la máquina.
    """

    SAMPLE_RATE = 16000  # lo que espera Whisper

    # Contexto para Whisper: sin él escribe «man total» por «Bantotal» (medido en el
    # video de Definición de transacciones). Solo términos que el audio usa de verdad.
    DOMAIN_PROMPT = ("Capacitación de Bantotal, core bancario. Módulo, transacción, ordinal, "
                     "subordinal, rubro, concepto, asiento contable, cuenta vista, Banco Contactar.")

    def __init__(self, model_size: str = "small", model: Any = None, block_seconds: int = 600,
                 initial_prompt: str = DOMAIN_PROMPT):
        self.model_size = model_size
        self._model = model
        self.block_seconds = block_seconds
        self.initial_prompt = initial_prompt

    def extract_audio(self, video_path: Path, output_audio_path: Path) -> Path:
        """Extract 16kHz mono PCM WAV with PyAV (ffmpeg no está instalado en esta máquina)."""
        import av  # type: ignore

        output_audio_path.parent.mkdir(parents=True, exist_ok=True)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=self.SAMPLE_RATE)
        with av.open(str(video_path)) as container, wave.open(str(output_audio_path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(self.SAMPLE_RATE)
            for frame in container.decode(audio=0):
                for chunk in resampler.resample(frame):
                    out.writeframes(chunk.to_ndarray().tobytes())
            for chunk in resampler.resample(None):  # vacía lo que quedó en el resampler
                out.writeframes(chunk.to_ndarray().tobytes())
        return output_audio_path

    @staticmethod
    def parse_transcript_file(file_path: Path) -> list[TranscriptSegment]:
        """Parse subtitle, json, or markdown timestamped files into TranscriptSegments."""
        content = file_path.read_text(encoding="utf-8-sig")
        suffix = file_path.suffix.lower()

        if suffix == ".json":
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    return [
                        TranscriptSegment(
                            start=float(d.get("start", 0)),
                            end=float(d.get("end", d.get("start", 0) + 2.0)),
                            text=str(d.get("text", "")).strip(),
                        )
                        for d in data
                        if d.get("text")
                    ]
            except Exception as e:
                log.warning(f"Failed to parse JSON transcript: {e}")

        # Check for Markdown timestamps: **[00:05]** Text or [00:05] Text
        md_time_pattern = re.compile(r"^\*{0,2}\[(\d{1,2}:\d{2}(?::\d{2})?)\]\*{0,2}\s*(.+)$")
        lines = content.splitlines()
        md_segments: list[tuple[float, str]] = []
        for line in lines:
            line_str = line.strip()
            match = md_time_pattern.match(line_str)
            if match:
                sec = parse_timestamp_str(match.group(1))
                txt = match.group(2).strip()
                if txt:
                    md_segments.append((sec, txt))

        if md_segments:
            segments: list[TranscriptSegment] = []
            for i, (sec, txt) in enumerate(md_segments):
                next_sec = md_segments[i + 1][0] if i + 1 < len(md_segments) else sec + 3.0
                segments.append(TranscriptSegment(start=sec, end=next_sec, text=txt))
            return segments

        # Otherwise parse as standard VTT or SRT
        return AudioTranscriber.parse_vtt_or_srt(file_path)

    @staticmethod
    def parse_vtt_or_srt(file_path: Path) -> list[TranscriptSegment]:
        """Parse standard VTT or SRT subtitle files into TranscriptSegments."""
        content = file_path.read_text(encoding="utf-8-sig")
        segments: list[TranscriptSegment] = []

        # Matches: 00:01:23.456 --> 00:01:28.000 or 01:23,456 --> 01:28,000
        time_block_regex = re.compile(
            r"((?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*((?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})"
        )

        blocks = re.split(r"\n\s*\n", content.strip())
        for block in blocks:
            lines = [l.strip() for l in block.splitlines() if l.strip()]
            if not lines:
                continue
            time_match = None
            text_lines = []
            for line in lines:
                match = time_block_regex.search(line)
                if match:
                    time_match = match
                elif not line.isdigit() and line != "WEBVTT":
                    text_lines.append(line)

            if time_match and text_lines:
                start_sec = parse_timestamp_str(time_match.group(1))
                end_sec = parse_timestamp_str(time_match.group(2))
                raw_text = " ".join(text_lines)
                # Clean html tags if present
                clean_text = re.sub(r"<[^>]+>", "", raw_text).strip()
                if clean_text:
                    segments.append(
                        TranscriptSegment(start=start_sec, end=end_sec, text=clean_text)
                    )

        return segments

    def transcribe(self, audio_path: Path, work_dir: Path) -> list[TranscriptSegment]:
        """Transcribe por bloques de `block_seconds`, reanudable.

        faster-whisper hace segfault con el audio entero de 2 h 38 min (con 60 s
        funciona, con VAD y sin VAD; medido en el piloto). Por bloques no pasa, y
        cada bloque terminado queda en `work_dir/bloques/` para no repetirlo si el
        proceso se corta.
        """
        model = self._load_model()
        blocks_dir = work_dir / "bloques"
        blocks_dir.mkdir(parents=True, exist_ok=True)
        segments: list[TranscriptSegment] = []
        with wave.open(str(audio_path)) as wav:
            rate = wav.getframerate()
            n_blocks = math.ceil(wav.getnframes() / (rate * self.block_seconds))
            for i in range(n_blocks):
                block_file = blocks_dir / f"b{i:03d}.json"
                if not block_file.exists():
                    offset = i * self.block_seconds
                    wav.setpos(offset * rate)
                    pcm = wav.readframes(self.block_seconds * rate)
                    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
                    found, _ = model.transcribe(audio, language="es", vad_filter=True,
                                                vad_parameters={"min_silence_duration_ms": 500},
                                                initial_prompt=self.initial_prompt)
                    block = [{"start": offset + s.start, "end": offset + s.end, "text": s.text.strip()}
                             for s in found]
                    block_file.write_text(json.dumps(block, ensure_ascii=False), encoding="utf-8")
                    log.info(f"Bloque {i + 1}/{n_blocks} transcrito ({len(block)} segmentos)")
                segments += [TranscriptSegment(**d)
                             for d in json.loads(block_file.read_text(encoding="utf-8"))]
        return segments

    def _load_model(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel  # type: ignore
            except ImportError as e:
                raise RuntimeError(
                    "faster-whisper no está instalado (pip install -r requirements-video.txt). "
                    "Para ingerir sin transcribir, pasa un transcript existente con --transcript."
                ) from e
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        return self._model


class ScreenOCR:
    """OCR de los frames con PaddleOCR 3.x (PP-OCRv6 medium en la 3.7), en CPU.

    Tapa la webcam antes del OCR: el recuadro trae el nombre y la cara de quien
    expone, y ese texto no debe llegar al RAG.
    """

    # Recuadro de la webcam como fracción de la imagen (x0, y0, x1, y1). Medido en
    # el piloto sobre 1920x1080: (1600, 0, 1920, 185). None desactiva la máscara.
    WEBCAM_BOX = (1600 / 1920, 0.0, 1.0, 185 / 1080)
    MIN_CONFIDENCE = 0.80

    def __init__(self, lang: str = "es", engine: Any = None,
                 webcam_box: tuple[float, float, float, float] | None = WEBCAM_BOX):
        self.webcam_box = webcam_box
        self._engine = engine or self._load_engine(lang)

    @staticmethod
    def _load_engine(lang: str) -> Any:
        try:
            import truststore  # type: ignore

            truststore.inject_into_ssl()  # la red del banco intercepta TLS al bajar los modelos
            os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "huggingface")
            from paddleocr import PaddleOCR  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "paddleocr no está instalado (pip install -r requirements-video.txt). "
                "Sin OCR el video se ingeriría sin el texto de pantalla."
            ) from e
        return PaddleOCR(lang=lang, use_doc_orientation_classify=False, use_doc_unwarping=False,
                         use_textline_orientation=False,
                         # paddle 3.3.1 + oneDNN en CPU: NotImplementedError
                         # ConvertPirAttribute2RuntimeAttribute (medido en el piloto).
                         enable_mkldnn=False)

    def _masked(self, image_path: Path) -> np.ndarray:
        from PIL import Image  # type: ignore

        img = np.array(Image.open(image_path).convert("RGB"))
        if self.webcam_box:
            h, w = img.shape[:2]
            x0, y0, x1, y1 = self.webcam_box
            img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = 255
        return img

    def analyze_frame(self, keyframe: Keyframe) -> ScreenAnalysis:
        """Run OCR on a single keyframe; keeps the lines above MIN_CONFIDENCE."""
        result = self._engine.predict(self._masked(keyframe.image_path))[0]
        lines = [t for t, score in zip(result["rec_texts"], result["rec_scores"])
                 if score >= self.MIN_CONFIDENCE]
        raw_text = "\n".join(lines)
        programs, tables = extract_bt_identifiers(raw_text)
        return ScreenAnalysis(timestamp=keyframe.timestamp, timestamp_str=keyframe.timestamp_str,
                              raw_text=raw_text, programs_detected=programs, tables_detected=tables)


class VideoSynthesizer:
    """Combines transcription timeline and OCR screen analysis into dense RAG Markdown."""

    @staticmethod
    def align_timeline(
        keyframes: list[Keyframe],
        analyses: list[ScreenAnalysis],
        transcript: list[TranscriptSegment],
        interface_min_frames: int = 5,
    ) -> list[dict[str, Any]]:
        """Align narration segments with the corresponding active keyframe.

        El texto de pantalla sale limpio (`screen_text`): la interfaz una sola vez
        por video, los documentos enmascarados y el título de la pantalla aparte.
        """
        if not keyframes and not transcript:
            return []

        # If no keyframes, group transcript by 30-second windows
        if not keyframes:
            windows: list[dict[str, Any]] = []
            if not transcript:
                return windows
            window_size = 30.0
            max_time = max(s.end for s in transcript)
            cur_time = 0.0
            while cur_time <= max_time:
                end_time = cur_time + window_size
                segs = [s for s in transcript if s.start >= cur_time and s.start < end_time]
                if segs:
                    combined_text = " ".join(s.text for s in segs)
                    progs, tbls = extract_bt_identifiers(combined_text)
                    windows.append({
                        "timestamp": cur_time,
                        "timestamp_str": format_timestamp(cur_time),
                        "audio_text": combined_text,
                        "screen_text": "",
                        "programs": progs,
                        "tables": tbls,
                        "tables_markdown": [],
                    })
                cur_time = end_time
            return windows

        analyses_by_ts = {a.timestamp: a for a in analyses}
        timeline_steps: list[dict[str, Any]] = []

        sorted_frames = sorted(keyframes, key=lambda k: k.timestamp)
        lines_by_ts = {
            kf.timestamp: [l for l in analyses_by_ts[kf.timestamp].raw_text.splitlines() if l.strip()]
            if kf.timestamp in analyses_by_ts else []
            for kf in sorted_frames
        }
        useful_ts = {ts for ts, lines in lines_by_ts.items() if is_useful_screen(lines)}

        # Solo una pantalla útil abre un paso (el primer frame siempre, para no
        # perder la narración inicial); lo dicho frente al recuadro de Teams queda
        # en el paso anterior.
        steps = [kf for i, kf in enumerate(sorted_frames) if i == 0 or kf.timestamp in useful_ts]
        interface = interface_keys([lines_by_ts[ts] for ts in sorted(useful_ts)],
                                   min_frames=interface_min_frames)
        ids = document_numbers(list(lines_by_ts.values()))
        shown: set[str] = set()
        title = ""

        for i, kf in enumerate(steps):
            next_ts = steps[i + 1].timestamp if i + 1 < len(steps) else float("inf")
            # All transcript segments that occur during this frame's window
            segs = [s for s in transcript if s.start >= kf.timestamp and s.start < next_ts]
            audio_text = " ".join(s.text for s in segs)

            analysis = analyses_by_ts.get(kf.timestamp)
            lines = lines_by_ts[kf.timestamp]
            is_screen = kf.timestamp in useful_ts
            # El título sale del texto crudo: sin esto, una cédula en la línea del
            # título llegaría al encabezado aunque el cuerpo esté enmascarado.
            own_title = mask_document_numbers(screen_title(lines), ids) if is_screen else ""
            # Sin título propio (un diálogo de confirmación sobre la misma
            # pantalla), el paso sigue en la pantalla anterior.
            title = own_title or title
            screen_text = "\n".join(clean_screen_lines(lines, interface, shown, ids)) if is_screen else ""
            tbls_md = analysis.tables_markdown if analysis else []

            # Gather all detected codes from audio and screen
            audio_progs, audio_tbls = extract_bt_identifiers(audio_text)
            screen_progs = analysis.programs_detected if analysis else []
            screen_tbls = analysis.tables_detected if analysis else []

            all_progs = sorted(set(audio_progs + screen_progs))
            all_tbls = sorted(set(audio_tbls + screen_tbls))

            timeline_steps.append({
                "timestamp": kf.timestamp,
                "timestamp_str": kf.timestamp_str,
                "title": title,
                "audio_text": audio_text,
                "screen_text": screen_text,
                "programs": all_progs,
                "tables": all_tbls,
                "tables_markdown": tbls_md,
            })

        return timeline_steps

    @classmethod
    def generate_markdown(
        cls,
        meta: VideoMetadata,
        timeline_steps: list[dict[str, Any]],
        summary_override: str | None = None,
    ) -> str:
        """Construct self-contained, high-density Markdown ready for RAG indexing."""
        # Collect distinct programs and tables
        all_programs = sorted(
            set(meta.programs + [p for step in timeline_steps for p in step.get("programs", [])])
        )
        all_tables = sorted(
            set(meta.tables + [t for step in timeline_steps for t in step.get("tables", [])])
        )

        frontmatter_dict = {
            "source_type": "video_training",
            "source_video": meta.source_video,
            "title": meta.title,
            "module": meta.module,
            "duration": meta.duration_str,
            "programs_involved": all_programs,
            "tables_touched": all_tables,
            "tags": list(set(["bantotal", "training", meta.module.lower()] + meta.tags)),
        }

        # Build YAML frontmatter manually to ensure pristine formatting
        yaml_lines = ["---"]
        for key, val in frontmatter_dict.items():
            if isinstance(val, list):
                if not val:
                    yaml_lines.append(f"{key}: []")
                else:
                    yaml_lines.append(f"{key}:")
                    for item in val:
                        yaml_lines.append(f"  - \"{item}\"")
            else:
                yaml_lines.append(f"{key}: \"{val}\"")
        yaml_lines.append("---")
        yaml_header = "\n".join(yaml_lines)

        # Executive summary
        summary = summary_override or (
            f"Capacitación operativa y técnica de Bantotal para el módulo de **{meta.module}**. "
            f"Presenta la ejecución y parametrización de las transacciones principales: "
            f"{', '.join(f'`{p}`' for p in all_programs) if all_programs else 'operaciones estándar'}."
        )

        md_parts = [
            yaml_header,
            "",
            f"# {meta.title}",
            "",
            "## Resumen Ejecutivo de Negocio",
            "",
            f"**Módulo:** {meta.module}  ",
            f"**Duración:** {meta.duration_str}  ",
            f"**Programas / Transacciones:** {', '.join(f'`{p}`' for p in all_programs) if all_programs else 'N/A'}  ",
            f"**Tablas:** {', '.join(f'`{t}`' for t in all_tables) if all_tables else 'N/A'}",
            "",
            summary,
            "",
            "---",
            "",
            "## Flujo Operativo y Detalle Temporal",
            "",
        ]

        for step in timeline_steps:
            ts_str = step["timestamp_str"]
            progs = step.get("programs", [])
            progs_str = f" - Transacción {', '.join(f'`{p}`' for p in progs)}" if progs else ""
            step_header = f"### [{ts_str}] {step.get('title') or 'Paso Operativo'}{progs_str}"
            md_parts.append(step_header)
            md_parts.append("")

            audio = step.get("audio_text", "").strip()
            if audio:
                md_parts.append("**Explicación del Relator:**")
                md_parts.append(f"> {audio}")
                md_parts.append("")

            screen = step.get("screen_text", "").strip()
            if screen:
                # Ya viene limpia: sin tope de líneas (el tope de 10 perdía las filas
                # de las grillas) y en una sola línea, que rinde más en un chunk de 800.
                lines = [l.strip() for l in screen.splitlines() if l.strip()]
                md_parts.append("**Datos y Estado en Pantalla:** " + " · ".join(lines))
                md_parts.append("")

            tbls = step.get("tables_markdown", [])
            for tbl in tbls:
                md_parts.append(tbl)
                md_parts.append("")

            md_parts.append("")

        return "\n".join(md_parts).strip() + "\n"


class VideoIngestPipeline:
    """End-to-end coordinator for processing video into RAG Markdown."""

    def __init__(
        self,
        frame_extractor: FrameExtractor | None = None,
        transcriber: AudioTranscriber | None = None,
        ocr: ScreenOCR | None = None,
    ):
        self.extractor = frame_extractor or FrameExtractor()
        self.transcriber = transcriber or AudioTranscriber()
        self.ocr = ocr or ScreenOCR()

    def process(
        self,
        video_path: Path,
        output_dir: Path,
        title: str | None = None,
        module: str = "Bantotal General",
        transcript_file: Path | None = None,
        keep_temp_frames: bool = False,
    ) -> tuple[Path, Path, str]:
        """Run full extraction and write .md + .meta.json into output_dir.

        Returns (md_path, meta_path, markdown_content).
        """
        video_path = Path(video_path).resolve()
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        stem = video_path.stem
        output_dir.mkdir(parents=True, exist_ok=True)
        work_dir = output_dir / f"_temp_{stem}"
        work_dir.mkdir(parents=True, exist_ok=True)

        frames_dir = work_dir / "frames"
        audio_path = work_dir / f"{stem}.wav"

        doc_title = title or f"Capacitación Bantotal — {stem.replace('_', ' ').replace('-', ' ').title()}"

        try:
            # 1. Video Duration & Keyframes
            duration = self.extractor.get_duration(video_path)
            log.info(f"Extracting keyframes for {video_path.name}...")
            keyframes = self.extractor.extract_keyframes(
                video_path=video_path,
                output_dir=frames_dir,
            )

            # 2. Transcription
            transcript_segments: list[TranscriptSegment] = []
            if transcript_file and Path(transcript_file).exists():
                log.info(f"Loading external transcript from {transcript_file}...")
                transcript_segments = self.transcriber.parse_transcript_file(Path(transcript_file))
            else:
                log.info("Extracting audio and generating transcript...")
                self.transcriber.extract_audio(video_path, audio_path)
                transcript_segments = self.transcriber.transcribe(audio_path=audio_path, work_dir=work_dir)

            # 3. Screen OCR Analysis
            log.info(f"Analyzing {len(keyframes)} keyframes with OCR...")
            screen_analyses: list[ScreenAnalysis] = []
            for kf in keyframes:
                analysis = self.ocr.analyze_frame(kf)
                screen_analyses.append(analysis)

            # 4. Alignment & Markdown Synthesis
            timeline = VideoSynthesizer.align_timeline(keyframes, screen_analyses, transcript_segments)

            meta = VideoMetadata(
                source_video=video_path.name,
                title=doc_title,
                module=module,
                duration_seconds=duration,
                duration_str=format_timestamp(duration),
                keyframe_count=len(keyframes),
                transcript_segments_count=len(transcript_segments),
            )

            markdown = VideoSynthesizer.generate_markdown(meta, timeline)

            # 5. Persist artifacts
            md_path = output_dir / f"{stem}.md"
            meta_path = output_dir / f"{stem}.meta.json"

            md_path.write_text(markdown, encoding="utf-8")

            meta_dict = {
                "source_video": video_path.name,
                "title": doc_title,
                "module": module,
                "duration_seconds": duration,
                "duration_str": format_timestamp(duration),
                "keyframe_count": len(keyframes),
                "transcript_segments_count": len(transcript_segments),
                "md_file": md_path.name,
            }
            meta_path.write_text(json.dumps(meta_dict, indent=2, ensure_ascii=False), encoding="utf-8")

            log.info(f"Video ingested successfully: {md_path} ({len(markdown.split())} words)")
        except BaseException:
            # Sin borrar: los bloques de audio ya transcritos son lo que la próxima
            # corrida reanuda (horas de CPU en un video largo).
            log.error(f"Ingesta interrumpida; el trabajo parcial queda en {work_dir}")
            raise

        if not keep_temp_frames:
            shutil.rmtree(work_dir, ignore_errors=True)
        return md_path, meta_path, markdown
