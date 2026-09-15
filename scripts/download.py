#!/usr/bin/env python3
"""Video download module — wraps yt-dlp for URL sources, passes through local files."""

import json
import os
import re
import subprocess
import sys

BEST_FORMAT = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
FALLBACK_FORMAT = (
    'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/'
    'bestvideo[height<=720]+bestaudio/'
    'best[height<=720]/best'
)
PROGRESSIVE_FORMAT = 'best[height<=720][ext=mp4]/best[ext=mp4]/18/best'
# Last resort: the Android innertube client is not PO-token gated (as of
# yt-dlp 2026.07) and serves muxed streams that the web client refuses.
ANDROID_FORMAT = 'best[height<=720][ext=mp4]/best[height<=720]/best'
ANDROID_EXTRACTOR_ARGS = 'youtube:player_client=android'


def is_url(source: str) -> bool:
    """Check if source is a URL (vs local file path)."""
    return source.startswith('http://') or source.startswith('https://')


def get_slug(title: str) -> str:
    """Convert a title to a URL-safe slug."""
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'[\s]+', '-', slug.strip())
    slug = re.sub(r'-+', '-', slug)
    return slug


def download_video(source: str, cache_dir: str) -> dict:
    """
    Download video from URL or validate local file.

    Returns:
        {
            'video_path': str,
            'subtitle_path': str|None,
            'title': str,
            'uploader': str,
            'duration': int,          # seconds
            'slug': str,
            'is_local': bool,
        }
    """
    if not is_url(source):
        if not os.path.exists(source):
            raise FileNotFoundError(f"Video file not found: {source}")

        filename = os.path.basename(source)
        title = os.path.splitext(filename)[0]
        return {
            'video_path': source,
            'subtitle_path': None,
            'title': title,
            'uploader': 'local',
            'duration': _get_duration(source),
            'slug': get_slug(title),
            'is_local': True,
        }

    # URL — download via yt-dlp
    os.makedirs(cache_dir, exist_ok=True)

    # First get metadata
    meta_cmd = ['yt-dlp', '--dump-json', '--no-download', source]
    print(f"Fetching metadata...", file=sys.stderr)
    meta_result = subprocess.run(meta_cmd, capture_output=True, text=True)

    if meta_result.returncode != 0:
        error = meta_result.stderr.strip()
        if 'private' in error.lower() or 'sign in' in error.lower():
            raise PermissionError(f"Video requires authentication: {error}")
        if 'age' in error.lower():
            raise PermissionError(f"Age-restricted video: {error}")
        raise RuntimeError(f"yt-dlp metadata failed: {error}")

    meta = json.loads(meta_result.stdout)
    title = meta.get('title', 'untitled')
    uploader = meta.get('uploader', 'unknown')
    duration = meta.get('duration', 0)
    slug = get_slug(title)

    # Check file size estimate (reject > 2GB)
    filesize = meta.get('filesize') or meta.get('filesize_approx') or 0
    if filesize > 2 * 1024 * 1024 * 1024:
        raise ValueError(f"Video too large ({filesize / 1e9:.1f} GB). Max 2 GB for Gemini upload.")

    # Download video + subtitles
    output_template = os.path.join(cache_dir, f"{slug}.%(ext)s")

    def _dl_cmd(fmt: str, extractor_args: str | None = None) -> list:
        cmd = [
            'yt-dlp',
            '-f', fmt,
            '--write-subs', '--write-auto-subs',
            '--sub-langs', 'en.*,en',
            '--sub-format', 'vtt',
            '--no-playlist',
            '-o', output_template,
        ]
        if extractor_args:
            cmd += ['--extractor-args', extractor_args]
        cmd.append(source)
        return cmd

    print(f"Downloading: {title}...", file=sys.stderr)
    dl_result = subprocess.run(_dl_cmd(BEST_FORMAT), capture_output=True, text=True)

    # YouTube gates adaptive (DASH) formats behind a PO token; without one they
    # 403. Format selectors with `/` only fall through on selection failure, not
    # download failure — so a selected DASH pair that 403s never reaches the
    # progressive alternative. Retry first with a 720p DASH cap, then with a
    # progressive-only selector (format 18 / best muxed) that still serves.
    if dl_result.returncode != 0 and '403' in dl_result.stderr:
        print("Best format was refused (HTTP 403). Retrying at 720p...", file=sys.stderr)
        dl_result = subprocess.run(_dl_cmd(FALLBACK_FORMAT), capture_output=True, text=True)

    if dl_result.returncode != 0 and '403' in dl_result.stderr:
        print("Adaptive 720p also refused (HTTP 403). Retrying progressive...", file=sys.stderr)
        dl_result = subprocess.run(_dl_cmd(PROGRESSIVE_FORMAT), capture_output=True, text=True)

    if dl_result.returncode != 0 and '403' in dl_result.stderr:
        print("Progressive also refused (HTTP 403). Retrying with Android client...", file=sys.stderr)
        dl_result = subprocess.run(
            _dl_cmd(ANDROID_FORMAT, ANDROID_EXTRACTOR_ARGS), capture_output=True, text=True
        )

    if dl_result.returncode != 0:
        raise RuntimeError(f"yt-dlp download failed: {dl_result.stderr.strip()}")

    # Find downloaded files
    video_path = None
    subtitle_path = None
    # Refused attempts above can leave partial downloads (*.part, *.ytdl)
    # behind; never treat those as the video.
    for f in os.listdir(cache_dir):
        full = os.path.join(cache_dir, f)
        if f.endswith(('.part', '.ytdl')):
            continue
        if f.endswith('.vtt') and subtitle_path is None:
            subtitle_path = full
        elif not f.endswith('.vtt') and not f.endswith('.json'):
            video_path = full

    if not video_path:
        raise RuntimeError("Download completed but video file not found in cache dir")

    return {
        'video_path': video_path,
        'subtitle_path': subtitle_path,
        'title': title,
        'uploader': uploader,
        'duration': duration,
        'slug': slug,
        'is_local': False,
    }


def _get_duration(video_path: str) -> int:
    """Get video duration in seconds via ffprobe."""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_format',
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 0
    try:
        data = json.loads(result.stdout)
        return int(float(data['format']['duration']))
    except (KeyError, ValueError, json.JSONDecodeError):
        return 0
