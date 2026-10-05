"""CLI for ingesting Bantotal training and demo videos into RAG documentation.

Usage:
    python -m scripts.ingest_video <video_path> [options]

Options:
    --output-dir DIR        Directory where .md and .meta.json will be saved (default: data/videos)
    --transcript FILE       Pre-existing transcript file (.vtt or .srt)
    --module NAME           Bantotal business module (e.g., Cuentas, Préstamos, Contabilidad)
    --title TITLE           Custom title for the document
    --scene-threshold N     Sensitivity for ffmpeg scene change detection (default: 0.3)
    --whisper-model SIZE    Whisper model size: tiny, base, small, medium (default: base)
    --index                 Automatically index into PostgreSQL vectorstore
    --keep-frames           Keep extracted keyframes in output directory for visual audit
    --dry-run               Parse and display preview without saving files or writing to DB
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.ingestion.video_pipeline import VideoIngestPipeline, format_timestamp


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
        "--scene-threshold",
        type=float,
        default=0.3,
        help="Scene change detection sensitivity (default: 0.3)",
    )
    parser.add_argument(
        "--whisper-model",
        type=str,
        default="base",
        help="Whisper model tier (tiny, base, small, medium)",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="Index the resulting document into PostgreSQL vector store",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default="gx_docs",
        help="Target collection for vector indexing (default: gx_docs)",
    )
    parser.add_argument(
        "--keep-frames",
        action="store_true",
        help="Retain extracted keyframe images",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate preview and stats without persisting to disk or database",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    video_path = Path(args.video).resolve()
    if not video_path.exists():
        print(f"Error: Video file not found: {video_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    pipeline = VideoIngestPipeline()

    print(f"==> Processing video: {video_path.name}")
    print(f"    Module: {args.module}")
    print(f"    Scene threshold: {args.scene_threshold}")
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
                scene_threshold=args.scene_threshold,
                whisper_model=args.whisper_model,
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
        scene_threshold=args.scene_threshold,
        whisper_model=args.whisper_model,
        keep_temp_frames=args.keep_frames,
    )

    print(f"\n[SUCCESS] Generated Markdown: {md_path}")
    print(f"          Metadata JSON:      {meta_path}")

    if args.index:
        print(f"\n==> Indexing into collection '{args.collection}' via pg_indexer...")
        try:
            from src.vectorstore.pg_indexer import index_article
            source_id = f"video/{video_path.stem}"
            chunk_ids = index_article(
                article_id=source_id,
                title=args.title or f"Capacitación Bantotal — {video_path.stem}",
                content=markdown,
                source=str(video_path.name),
                genre="video_guide",
                url=f"local://videos/{video_path.name}",
                content_hash="",
                collection=args.collection,
            )
            print(f"    Indexed {len(chunk_ids)} chunks into '{args.collection}'.")
        except Exception as e:
            print(f"Error during vector store indexing: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
