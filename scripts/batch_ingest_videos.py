"""Batch ingest script for Bantotal training videos directory.

Scans a video directory, matches each .mp4 with its corresponding .md transcript in
the `transcripciones/` subfolder, and processes them into high-density Markdown.

Usage:
    python -m scripts.batch_ingest_videos <video_dir> [--output-dir DIR] [--index]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import unicodedata
from pathlib import Path

from src.ingestion.video_pipeline import VideoIngestPipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("batch_ingest")


def normalize_name(name: str) -> str:
    """Normalize accented characters and non-alphanumeric chars for robust matching."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]", "", s)


def match_videos_with_transcripts(video_dir: Path) -> list[tuple[Path, Path | None]]:
    trans_dir = video_dir / "transcripciones"
    transcripts = {}
    if trans_dir.exists():
        for t in trans_dir.glob("*.md"):
            transcripts[normalize_name(t.stem)] = t

    pairs = []
    for video in sorted(video_dir.glob("*.mp4")):
        v_norm = normalize_name(video.stem)
        match = transcripts.get(v_norm)
        if not match:
            for t_norm, t_path in transcripts.items():
                if t_norm in v_norm or v_norm in t_norm:
                    match = t_path
                    break
        pairs.append((video, match))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch process Bantotal videos and transcripts.")
    parser.add_argument("video_dir", type=str, help="Directory containing .mp4 videos")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/videos_processed",
        help="Target output directory (default: data/videos_processed)",
    )
    parser.add_argument(
        "--module",
        type=str,
        default="Buenas Prácticas Bantotal",
        help="Module name for frontmatter metadata",
    )
    parser.add_argument(
        "--index",
        action="store_true",
        help="Index directly into PostgreSQL vectorstore (collection bt_docs)",
    )
    parser.add_argument(
        "--collection",
        type=str,
        default="bt_docs",
        help="Target vector collection name (default: bt_docs)",
    )
    args = parser.parse_args()

    video_dir = Path(args.video_dir).resolve()
    if not video_dir.exists():
        print(f"Error: Directory not found: {video_dir}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pairs = match_videos_with_transcripts(video_dir)
    print(f"Found {len(pairs)} videos in {video_dir.name}:")
    for v, t in pairs:
        t_status = f"-> {t.name}" if t else "[NO TRANSCRIPT]"
        print(f"  • {v.name} {t_status}")

    pipeline = VideoIngestPipeline()
    succeeded = 0
    failed = 0

    print(f"\n==> Starting batch processing ({len(pairs)} items)...")
    for idx, (video, transcript) in enumerate(pairs, 1):
        print(f"\n[{idx}/{len(pairs)}] Processing: {video.name}")
        try:
            md_path, meta_path, markdown = pipeline.process(
                video_path=video,
                output_dir=output_dir,
                module=args.module,
                transcript_file=transcript,
            )
            print(f"    [OK] Generated: {md_path.name} ({len(markdown.split())} words)")
            succeeded += 1

            if args.index:
                try:
                    from src.vectorstore.pg_indexer import index_article

                    source_id = f"video/{video.stem}"
                    chunk_ids = index_article(
                        article_id=source_id,
                        title=f"Capacitación Bantotal — {video.stem}",
                        content=markdown,
                        source=str(video.name),
                        genre="video_guide",
                        url=f"local://videos/{video.name}",
                        content_hash="",
                        collection=args.collection,
                    )
                    print(f"    [OK] Indexed {len(chunk_ids)} chunks into '{args.collection}'.")
                except Exception as e:
                    print(f"    [WARN] Indexing error: {e}", file=sys.stderr)

        except Exception as e:
            print(f"    [ERROR] Failed processing {video.name}: {e}", file=sys.stderr)
            failed += 1

    print(f"\n==========================================")
    print(f"Batch completed: {succeeded} succeeded, {failed} failed.")
    print(f"Output files located at: {output_dir.resolve()}")
    print(f"==========================================")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
