"""Video ingestion pipeline for Bantotal training and demo videos.

Implements a high-semantic-density extraction pipeline:
1. Video scene-change keyframe extraction via ffmpeg.
2. Synchronized audio transcription with timestamps (Whisper, Gemini, or VTT/SRT).
3. Screen text and table extraction (PaddleOCR / PP-Structure with modular fallback).
4. Alignment into self-contained Markdown with YAML frontmatter, executive summary,
   and timestamped chronological steps optimized for LLM RAG retrieval.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Bantotal transaction & table patterns (e.g., B701, FSD011, FST900, RTE99, PA01)
_BT_TABLE_PATTERN = re.compile(r"\b(FS[A-Z0-9]{3,6})\b", re.IGNORECASE)
_BT_PROGRAM_PATTERN = re.compile(r"\b([A-Z]{1,4}\d{2,5}[A-Z0-9]?)\b")


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
    programs = sorted(p for p in raw_progs if p.upper() not in raw_tables and not p.upper().startswith("FS"))
    return programs, raw_tables


class FrameExtractor:
    """Extracts keyframes based on scene changes or periodic sampling using ffmpeg."""

    def __init__(self, ffmpeg_bin: str = "ffmpeg"):
        self.ffmpeg_bin = ffmpeg_bin

    def get_duration(self, video_path: Path) -> float:
        """Get video duration in seconds via ffprobe or ffmpeg."""
        ffprobe_bin = shutil.which("ffprobe") or "ffprobe"
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return float(res.stdout.strip())
        except Exception as e:
            log.warning(f"Could not get duration via ffprobe: {e}. Defaulting to 0.0")
            return 0.0

    def extract_keyframes(
        self,
        video_path: Path,
        output_dir: Path,
        scene_threshold: float = 0.3,
        fallback_interval: float = 10.0,
    ) -> list[Keyframe]:
        """Extract keyframes at scene changes into output_dir.

        Falls back to periodic sampling if scene detection yields fewer than 2 frames.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        img_pattern = output_dir / "frame_%04d.jpg"

        # Pass 1: Scene change detection filter with showinfo
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i", str(video_path),
            "-vf", f"select='gt(scene,{scene_threshold})',showinfo",
            "-vsync", "vfr",
            "-q:v", "2",
            str(img_pattern),
        ]

        keyframes: list[Keyframe] = []
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            stderr = proc.stderr

            # Parse showinfo output for timestamps: pts_time:12.34
            pts_matches = re.findall(r"pts_time:([\d\.]+)", stderr)
            extracted_files = sorted(output_dir.glob("frame_*.jpg"))

            for idx, img_file in enumerate(extracted_files):
                ts = float(pts_matches[idx]) if idx < len(pts_matches) else idx * fallback_interval
                keyframes.append(
                    Keyframe(
                        index=idx,
                        timestamp=ts,
                        timestamp_str=format_timestamp(ts),
                        image_path=img_file,
                        scene_score=scene_threshold,
                    )
                )
        except Exception as e:
            log.error(f"ffmpeg scene extraction failed: {e}")

        # Fallback: if no keyframes detected (e.g. static screen recording), sample every N seconds
        if len(keyframes) < 2:
            log.info(f"Few scene changes detected ({len(keyframes)}). Running interval fallback ({fallback_interval}s).")
            keyframes.clear()
            fallback_pattern = output_dir / "interval_%04d.jpg"
            cmd_fallback = [
                self.ffmpeg_bin,
                "-y",
                "-i", str(video_path),
                "-vf", f"fps=1/{fallback_interval}",
                "-q:v", "2",
                str(fallback_pattern),
            ]
            subprocess.run(cmd_fallback, capture_output=True, text=True)
            files = sorted(output_dir.glob("interval_*.jpg"))
            for idx, img_file in enumerate(files):
                ts = idx * fallback_interval
                keyframes.append(
                    Keyframe(
                        index=idx,
                        timestamp=ts,
                        timestamp_str=format_timestamp(ts),
                        image_path=img_file,
                        scene_score=0.0,
                    )
                )

        return keyframes


class AudioTranscriber:
    """Extracts audio and produces synchronized transcript segments."""

    def __init__(self, ffmpeg_bin: str = "ffmpeg"):
        self.ffmpeg_bin = ffmpeg_bin

    def extract_audio(self, video_path: Path, output_audio_path: Path) -> Path:
        """Extract 16kHz mono PCM WAV for transcription engines."""
        output_audio_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            str(output_audio_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True)
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

    def transcribe(
        self,
        audio_path: Path,
        whisper_model: str = "base",
        gemini_api_key: str | None = None,
    ) -> list[TranscriptSegment]:
        """Transcribe audio using local faster-whisper/whisper or Gemini if available."""
        # 1. Try Gemini if API key provided or present in environment
        key = gemini_api_key or os.environ.get("GEMINI_API_KEY")
        if key:
            try:
                return self._transcribe_gemini(audio_path, key)
            except Exception as e:
                log.warning(f"Gemini transcription failed: {e}. Falling back to Whisper.")

        # 2. Try faster-whisper or whisper
        try:
            import whisper  # type: ignore

            log.info(f"Transcribing with OpenAI Whisper ({whisper_model})...")
            model = whisper.load_model(whisper_model)
            result = model.transcribe(str(audio_path), language="es")
            segments = []
            for seg in result.get("segments", []):
                segments.append(
                    TranscriptSegment(
                        start=float(seg["start"]),
                        end=float(seg["end"]),
                        text=seg["text"].strip(),
                    )
                )
            return segments
        except ImportError:
            pass

        log.warning(
            "Neither whisper nor GEMINI_API_KEY available for automatic transcription. "
            "Pass an existing transcript file via --transcript."
        )
        return []

    def _transcribe_gemini(self, audio_path: Path, api_key: str) -> list[TranscriptSegment]:
        """Transcribe audio using Google Gemini multimodal API."""
        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore

            client = genai.Client(api_key=api_key)
            uploaded_file = client.files.upload(file=str(audio_path))

            prompt = (
                "Transcribe this banking training audio in Spanish with precise timestamps. "
                "Output ONLY a JSON array with objects containing 'start' (float seconds), "
                "'end' (float seconds), and 'text' (string). "
                "Example: [{\"start\": 0.0, \"end\": 4.2, \"text\": \"Buenas tardes...\"}]"
            )
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[uploaded_file, prompt],
            )
            text = response.text or ""
            # Strip markdown json codeblock if present
            cleaned = re.sub(r"^```json\s*", "", text.strip())
            cleaned = re.sub(r"```$", "", cleaned.strip())
            data = json.loads(cleaned)
            segments = [
                TranscriptSegment(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    text=item["text"].strip(),
                )
                for item in data
            ]
            return segments
        except Exception as e:
            raise RuntimeError(f"Gemini API transcription failed: {e}") from e


class ScreenOCR:
    """OCR and Document AI adapter for video frames (PaddleOCR / PP-Structure)."""

    def __init__(self, lang: str = "es"):
        self.lang = lang
        self._paddle = None
        self._paddle_available = False
        self._init_paddle()

    def _init_paddle(self) -> None:
        try:
            from paddleocr import PaddleOCR  # type: ignore

            self._paddle = PaddleOCR(use_angle_cls=True, lang=self.lang, show_log=False)
            self._paddle_available = True
            log.info("PaddleOCR successfully initialized.")
        except ImportError:
            log.info("PaddleOCR not installed. Using heuristic text extraction fallback.")
            self._paddle_available = False

    def analyze_frame(self, keyframe: Keyframe) -> ScreenAnalysis:
        """Run OCR and structure detection on a single keyframe."""
        if not keyframe.image_path.exists():
            return ScreenAnalysis(timestamp=keyframe.timestamp, timestamp_str=keyframe.timestamp_str)

        raw_text = ""
        tables_md: list[str] = []

        if self._paddle_available and self._paddle:
            try:
                result = self._paddle.ocr(str(keyframe.image_path), cls=True)
                lines = []
                if result and result[0]:
                    for line_info in result[0]:
                        text = line_info[1][0]
                        lines.append(text)
                raw_text = "\n".join(lines)
            except Exception as e:
                log.warning(f"PaddleOCR failed on {keyframe.image_path}: {e}")

        programs, tables = extract_bt_identifiers(raw_text)

        return ScreenAnalysis(
            timestamp=keyframe.timestamp,
            timestamp_str=keyframe.timestamp_str,
            raw_text=raw_text,
            programs_detected=programs,
            tables_detected=tables,
            tables_markdown=tables_md,
        )


class VideoSynthesizer:
    """Combines transcription timeline and OCR screen analysis into dense RAG Markdown."""

    @staticmethod
    def align_timeline(
        keyframes: list[Keyframe],
        analyses: list[ScreenAnalysis],
        transcript: list[TranscriptSegment],
    ) -> list[dict[str, Any]]:
        """Align narration segments with the corresponding active keyframe."""
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

        for i, kf in enumerate(sorted_frames):
            next_ts = sorted_frames[i + 1].timestamp if i + 1 < len(sorted_frames) else float("inf")
            # All transcript segments that occur during this frame's window
            segs = [s for s in transcript if s.start >= kf.timestamp and s.start < next_ts]
            audio_text = " ".join(s.text for s in segs)

            analysis = analyses_by_ts.get(kf.timestamp)
            screen_text = analysis.raw_text if analysis else ""
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
            step_header = f"### [{ts_str}] Paso Operativo{progs_str}"
            md_parts.append(step_header)
            md_parts.append("")

            audio = step.get("audio_text", "").strip()
            if audio:
                md_parts.append("**Explicación del Relator:**")
                md_parts.append(f"> {audio}")
                md_parts.append("")

            screen = step.get("screen_text", "").strip()
            if screen:
                md_parts.append("**Datos y Estado en Pantalla:**")
                # Format clean screen lines
                lines = [l.strip() for l in screen.splitlines() if l.strip()]
                for l in lines[:10]:  # Cap lines to preserve high semantic density
                    md_parts.append(f"- `{l}`")
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
        scene_threshold: float = 0.3,
        whisper_model: str = "base",
        gemini_api_key: str | None = None,
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
            log.info(f"Extracting keyframes for {video_path.name} (scene_threshold={scene_threshold})...")
            keyframes = self.extractor.extract_keyframes(
                video_path=video_path,
                output_dir=frames_dir,
                scene_threshold=scene_threshold,
            )

            # 2. Transcription
            transcript_segments: list[TranscriptSegment] = []
            if transcript_file and Path(transcript_file).exists():
                log.info(f"Loading external transcript from {transcript_file}...")
                transcript_segments = self.transcriber.parse_transcript_file(Path(transcript_file))
            else:
                log.info("Extracting audio and generating transcript...")
                self.transcriber.extract_audio(video_path, audio_path)
                transcript_segments = self.transcriber.transcribe(
                    audio_path=audio_path,
                    whisper_model=whisper_model,
                    gemini_api_key=gemini_api_key,
                )

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
            return md_path, meta_path, markdown

        finally:
            if not keep_temp_frames and work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)
