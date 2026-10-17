"""CLI for ingesting Bantotal training and demo videos into RAG documentation.

Usage:
    python -m scripts.ingest_video <video_path> [options]

Options:
    --output-dir DIR        Directory where .md and .meta.json will be saved (default: data/videos)
    --transcript FILE       Pre-existing transcript file (.vtt or .srt)
    --module NAME           Bantotal business module (e.g., Cuentas, Préstamos, Contabilidad)
    --title TITLE           Custom title for the document
    --diff-threshold N      Mean thumbnail difference (0-255) that counts as a new screen (default: 6.0)
    --whisper-model SIZE    faster-whisper model size: tiny, base, small, medium (default: small)
    --index                 Automatically index into PostgreSQL vectorstore
    --keep-frames           Keep extracted keyframes in output directory for visual audit
    --dry-run               Parse and display preview without saving files or writing to DB
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts._console import use_utf8_stdout
from src.collection_policy import add_collection_argument
from src.ingestion.video_pipeline import (
    AudioTranscriber,
    FrameExtractor,
    VideoIngestPipeline,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest Bantotal training videos into high-density RAG Markdown."
    )
    parser.add_argument("video", type=str, help="Path to video file (.mp4, .mkv, .avi, etc.)")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/videos",
        help="Directory to save generated .md and metadata (default: data/videos)",
    )
    parser.add_argument(
        "--transcript",
        type=str,
        default=None,
        help="Path to pre-existing transcript file (.vtt, .srt)",
    )
    parser.add_argument(
        "--module",
        type=str,
        default="Bantotal General",
        help="Bantotal business module name (e.g. Cuentas, Préstamos, Contabilidad)",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Custom title for the generated document",
    )
    parser.add_argument(
        "--diff-threshold",
        type=float,
        default=6.0,
        help="Mean grayscale thumbnail difference (0-255) for a new screen; 6.0 measured in the pilot",
    )
    parser.add_argument(
        "--whisper-model",
        type=str,
        default="small",
        help="faster-whisper model tier (tiny, base, small, medium); small is what the pilot measured",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="DEPRECATED: La indexación directa fue retirada. Usa scripts.ingest_local --manifest.",
    )
    add_collection_argument(
        parser,
        default="bt_docs",
        help="Target collection (unused: use ingest_local --manifest to index)",
    )
    parser.add_argument(
        "--keep-frames",
        action="store_true",
        help="Retain extracted keyframe images",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate preview and stats without persisting to disk",
    )
    return parser


def main() -> int:
    use_utf8_stdout()
    parser = build_parser()
    args = parser.parse_args()

    if args.index:
        print(
            "Error: La opción '--index' fue retirada de ingest_video para evitar identidades duplicadas (#10, #11).\n"
            "ingest_video solo genera Markdown estructurado (.md y .meta.json).\n"
            "Para indexar en la base de datos, declara el documento en un manifiesto y corre:\n"
            "    python -m scripts.ingest_local --manifest <manifest_file>",
            file=sys.stderr,
        )
        return 1

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        print(f"Error: Video file not found: {video_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    pipeline = VideoIngestPipeline(
        frame_extractor=FrameExtractor(diff_threshold=args.diff_threshold),
        transcriber=AudioTranscriber(model_size=args.whisper_model),
    )

    print(f"==> Processing video: {video_path.name}")
    print(f"    Module: {args.module}")
    print(f"    Diff threshold: {args.diff_threshold}")
    if args.transcript:
        print(f"    Transcript source: {args.transcript}")

    if args.dry_run:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            md_path, meta_path, markdown = pipeline.process(
                video_path=video_path,
                output_dir=tmp_path,
                title=args.title,
                module=args.module,
                transcript_file=Path(args.transcript) if args.transcript else None,
                keep_temp_frames=False,
            )
            print("\n[DRY-RUN PREVIEW]")
            print(f"Generated Markdown length: {len(markdown)} characters ({len(markdown.split())} words)")
            lines = markdown.splitlines()
            print("\nFirst 40 lines of generated Markdown:")
            print("-" * 60)
            for line in lines[:40]:
                print(line)
            print("-" * 60)
            return 0

    md_path, meta_path, markdown = pipeline.process(
        video_path=video_path,
        output_dir=output_dir,
        title=args.title,
        module=args.module,
        transcript_file=Path(args.transcript) if args.transcript else None,
        keep_temp_frames=args.keep_frames,
    )

    print(f"\n[SUCCESS] Generated Markdown: {md_path}")
    print(f"          Metadata JSON:      {meta_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
