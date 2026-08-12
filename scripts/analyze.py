#!/usr/bin/env python3
"""
video-analyzer — Orchestrator
Takes any video URL or local file and produces an .avt file with frames.

Supports checkpointing: saves intermediate results to the output directory
so that if the process is interrupted (e.g. timeout), re-running the same
command resumes from the last completed step.
"""

import argparse
import json
import os
import shutil
import sys
import uuid
from datetime import datetime

from preflight import preflight_check, check_api_keys, ENV_FILE
from download import download_video
from transcribe import get_transcript
from understand import understand_video, get_model
from frames import extract_frames, timestamp_to_seconds
from avt import write_avt, align_transcript_to_segments

CACHE_BASE = os.path.expanduser("~/.cache/video-analyzer")


def parse_args(argv=None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Analyze a video and produce an .avt (Agentic Video Transcript) file.",
    )
    parser.add_argument('source', help="Video URL or local file path")
    parser.add_argument('--out-dir', default='.', help="Output directory (default: current dir)")
    parser.add_argument('--max-frames', type=int, default=80, help="Max frames to extract (default: 80)")
    parser.add_argument('--transcript', choices=['auto', 'captions', 'gemini', 'whisper'],
                        default='auto',
                        help="Transcript source (default: auto — native captions when the "
                             "source has them, otherwise Gemini's in-pass transcript)")
    parser.add_argument('--no-whisper', action='store_true', help="Disable Whisper fallback")
    parser.add_argument('--whisper', choices=['groq', 'openai', 'auto'], default='auto',
                        help="Force Whisper backend")
    parser.add_argument('--frame-width', type=int, default=1280,
                        help="Frame width in px (default: 1280; use 512+ to keep UI text legible)")
    parser.add_argument('--low-res', action='store_true', help="Use 256px frame width")
    parser.add_argument('--force-long', action='store_true', help="Allow videos over 90 minutes")
    parser.add_argument('--start', type=str, default=None,
                        help="Start time to focus on (SS, MM:SS, or HH:MM:SS)")
    parser.add_argument('--end', type=str, default=None,
                        help="End time to focus on (SS, MM:SS, or HH:MM:SS)")

    return parser.parse_args(argv)


def get_cache_dir() -> str:
    """Create and return a unique cache directory for this run."""
    run_id = str(uuid.uuid4())[:8]
    cache_dir = os.path.join(CACHE_BASE, run_id)
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _save_checkpoint(video_out_dir: str, name: str, data: dict):
    """Save intermediate result as a checkpoint JSON file."""
    checkpoint_path = os.path.join(video_out_dir, f".checkpoint_{name}.json")
    with open(checkpoint_path, 'w') as f:
        json.dump(data, f)


def _load_checkpoint(video_out_dir: str, name: str):
    """Load a checkpoint if it exists. Returns None if not found."""
    checkpoint_path = os.path.join(video_out_dir, f".checkpoint_{name}.json")
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path, 'r') as f:
            return json.load(f)
    return None


def _clear_checkpoints(video_out_dir: str):
    """Remove checkpoint files after successful completion."""
    for f in os.listdir(video_out_dir):
        if f.startswith('.checkpoint_'):
            os.remove(os.path.join(video_out_dir, f))


def main():
    args = parse_args()

    # Preflight check
    status = preflight_check()
    if not status['ready']:
        print("Preflight check failed.", file=sys.stderr)
        if status['missing_binaries']:
            print(f"  Missing binaries: {', '.join(status['missing_binaries'])}", file=sys.stderr)
        if status['missing_required_keys']:
            print(f"  Missing API keys: {', '.join(status['missing_required_keys'])}", file=sys.stderr)
        print("Run: python3 scripts/preflight.py", file=sys.stderr)
        sys.exit(1)

    cache_dir = get_cache_dir()

    try:
        # Parse time range
        start_sec = _parse_time(args.start) if args.start else None
        end_sec = _parse_time(args.end) if args.end else None

        # Determine output directory early (needed for checkpoints)
        base_out_dir = os.path.abspath(args.out_dir)

        # Step 1: Download
        # Check for existing checkpoint from a previous interrupted run
        # We need the slug first to find the right checkpoint dir,
        # but slug comes from download. Use source URL hash as temp identifier.
        temp_id = str(hash(args.source))[-8:]
        temp_checkpoint_dir = os.path.join(base_out_dir, 'avt_outputs', f".pending_{temp_id}")

        dl_checkpoint = _load_checkpoint(temp_checkpoint_dir, 'download') if os.path.isdir(temp_checkpoint_dir) else None

        if dl_checkpoint:
            print("Step 1/5: Resuming — download checkpoint found", file=sys.stderr)
            dl = dl_checkpoint
            # Verify video file still exists in cache
            if not os.path.exists(dl['video_path']):
                print("Step 1/5: Video file missing, re-downloading...", file=sys.stderr)
                dl_checkpoint = None

        if not dl_checkpoint:
            print("Step 1/5: Downloading video...", file=sys.stderr)
            dl = download_video(args.source, cache_dir)

        # Now we have the slug — set up the real output directory
        video_out_dir = os.path.join(base_out_dir, 'avt_outputs', dl['slug'])
        os.makedirs(video_out_dir, exist_ok=True)

        # If we were using a temp checkpoint dir, migrate to the real one
        if os.path.isdir(temp_checkpoint_dir) and temp_checkpoint_dir != video_out_dir:
            for f in os.listdir(temp_checkpoint_dir):
                if f.startswith('.checkpoint_'):
                    src = os.path.join(temp_checkpoint_dir, f)
                    dst = os.path.join(video_out_dir, f)
                    if not os.path.exists(dst):
                        shutil.move(src, dst)
            shutil.rmtree(temp_checkpoint_dir, ignore_errors=True)

        # Save download checkpoint
        _save_checkpoint(video_out_dir, 'download', dl)

        # Duration guard
        if dl['duration'] > 5400 and not args.force_long:  # 90 minutes
            print(f"Video is {dl['duration'] // 60} minutes. Use --force-long to proceed.",
                  file=sys.stderr)
            sys.exit(1)

        # File size guard
        video_size = os.path.getsize(dl['video_path'])
        if video_size > 2 * 1024 * 1024 * 1024:
            print(f"Video file too large ({video_size / 1e9:.1f} GB). Max 2 GB.", file=sys.stderr)
            sys.exit(1)

        # Step 2: Gemini video understanding (visual + transcript in one pass)
        visual_segments = _load_checkpoint(video_out_dir, 'visual')
        if visual_segments:
            print("Step 2/5: Resuming — Gemini checkpoint found", file=sys.stderr)
        else:
            print("Step 2/5: Analyzing video with Gemini...", file=sys.stderr)
            api_key = _load_key('GOOGLE_API_KEY', ENV_FILE)
            usage = {}
            visual_segments = understand_video(
                dl['video_path'], api_key,
                start_time=args.start, end_time=args.end,
                usage_out=usage,
            )
            _save_checkpoint(video_out_dir, 'visual', visual_segments)

            # Sidecar rather than .avt metadata — the .avt format spec has a
            # fixed field set and this is run bookkeeping, not transcript data.
            if usage:
                usage_path = os.path.join(video_out_dir, f"{dl['slug']}.usage.json")
                with open(usage_path, 'w') as f:
                    json.dump(usage, f, indent=2)

        # Filter visual segments to range
        if start_sec is not None or end_sec is not None:
            visual_segments = _filter_visual_to_range(
                visual_segments, start_sec, end_sec
            )

        # Step 3: Transcript.
        # Gemini transcribes in the same pass, so its text is already aligned to
        # each visual segment. Only fall back to captions/Whisper if that yielded
        # nothing (silent video, or --transcript whisper).
        gemini_spoke = any(seg.get('audio') for seg in visual_segments)

        # Native captions beat Gemini's transcript when they exist: they are
        # verbatim and complete, cost nothing, and were already downloaded,
        # whereas Gemini elides long stretches of speech. Gemini's value is the
        # visual analysis, which it keeps either way.
        has_captions = bool(dl['subtitle_path'] and os.path.exists(dl['subtitle_path']))
        source = args.transcript
        if source == 'auto':
            source = 'captions' if has_captions else 'gemini'

        if source == 'gemini' and gemini_spoke:
            print("Step 3/5: Using Gemini transcript (aligned in-pass)", file=sys.stderr)
            transcript = {'segments': [], 'source': 'gemini'}
        else:
            transcript = _load_checkpoint(video_out_dir, 'transcript')
            if transcript:
                print("Step 3/5: Resuming — transcript checkpoint found", file=sys.stderr)
            else:
                if source == 'gemini':
                    print("Step 3/5: Gemini found no speech — falling back to captions/Whisper",
                          file=sys.stderr)
                elif source == 'captions':
                    print("Step 3/5: Using native captions (verbatim, no extra API cost)",
                          file=sys.stderr)
                else:
                    print("Step 3/5: Extracting transcript...", file=sys.stderr)
                transcript = get_transcript(
                    subtitle_path=dl['subtitle_path'],
                    video_path=dl['video_path'],
                    whisper_backend=args.whisper,
                    no_whisper=args.no_whisper,
                )
                _save_checkpoint(video_out_dir, 'transcript', transcript)

            if start_sec is not None or end_sec is not None:
                transcript['segments'] = _filter_segments_to_range(
                    transcript['segments'], start_sec, end_sec
                )

            if not transcript['segments']:
                if gemini_spoke:
                    print("No captions or Whisper transcript — using Gemini's instead.",
                          file=sys.stderr)
                    transcript = {'segments': [], 'source': 'gemini'}
                else:
                    print("Warning: no transcript from any source — .avt will have no AUDIO content.",
                          file=sys.stderr)

        # Step 4: Extract frames
        print("Step 4/5: Extracting frames...", file=sys.stderr)
        width = 256 if args.low_res else args.frame_width
        frame_results = extract_frames(
            dl['video_path'], visual_segments, video_out_dir,
            max_frames=args.max_frames, width=width,
        )

        # Step 5: Assemble .avt file
        print("Step 5/5: Assembling .avt file...", file=sys.stderr)

        # Align transcript to visual segments. Gemini's transcript is already
        # per-segment, so it needs no fuzzy time-overlap matching.
        if transcript['source'] == 'gemini':
            aligned = visual_segments
        else:
            aligned = align_transcript_to_segments(transcript['segments'], visual_segments)

        # Assign frame paths to segments
        frame_map = {}
        for fr in frame_results:
            frame_map[fr['seconds']] = fr['path']

        for seg in aligned:
            seg_seconds = timestamp_to_seconds(seg['start'])
            seg['frame'] = frame_map.get(seg_seconds)

        # Build metadata
        duration_s = dl['duration']
        h = duration_s // 3600
        m = (duration_s % 3600) // 60
        s = duration_s % 60
        duration_fmt = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

        metadata = {
            'title': dl['title'],
            'channel': dl['uploader'],
            'duration': duration_fmt,
            'source': args.source,
            'analyzed': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'model': get_model(),
            'frames_extracted': len(frame_results),
            'transcript_source': transcript['source'],
        }

        avt_path = os.path.join(video_out_dir, f"{dl['slug']}.avt")
        write_avt(metadata, aligned, avt_path)

        # Clean up checkpoints on success
        _clear_checkpoints(video_out_dir)

        # Output final path to stdout
        print(avt_path)
        print(f"\nDone! {len(aligned)} segments, {len(frame_results)} frames.", file=sys.stderr)
        print(f"Output: {video_out_dir}", file=sys.stderr)

    finally:
        # Cleanup temp files (but NOT checkpoints — those enable resume)
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir, ignore_errors=True)


def _parse_time(time_str: str) -> float:
    """Parse SS, MM:SS, or HH:MM:SS to seconds."""
    parts = time_str.split(':')
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def _filter_segments_to_range(segments: list, start_sec: float = None, end_sec: float = None) -> list:
    """Filter transcript segments to a time range."""
    filtered = []
    for seg in segments:
        seg_start = seg.get('start_seconds', 0)
        seg_end = seg.get('end_seconds', seg_start)
        if start_sec is not None and seg_end < start_sec:
            continue
        if end_sec is not None and seg_start > end_sec:
            continue
        filtered.append(seg)
    return filtered


def _filter_visual_to_range(segments: list, start_sec: float = None, end_sec: float = None) -> list:
    """Filter visual segments to a time range."""
    from frames import timestamp_to_seconds
    filtered = []
    for seg in segments:
        seg_start = timestamp_to_seconds(seg['start'])
        seg_end = timestamp_to_seconds(seg['end'])
        if start_sec is not None and seg_end < start_sec:
            continue
        if end_sec is not None and seg_start > end_sec:
            continue
        filtered.append(seg)
    return filtered


def _load_key(key_name: str, env_path: str) -> str:
    """Load API key from env file."""
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith(f'{key_name}='):
                return line.split('=', 1)[1].strip()
    raise RuntimeError(f"{key_name} not found in {env_path}")


if __name__ == '__main__':
    main()
