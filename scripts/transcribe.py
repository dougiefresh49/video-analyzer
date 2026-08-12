#!/usr/bin/env python3
"""Transcript extraction — VTT parsing with HTML cleanup and deduplication."""

import os
import re
import sys


def format_timestamp(seconds: float) -> str:
    """Convert seconds to MM:SS or H:MM:SS format."""
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _parse_vtt_timestamp(ts: str) -> float:
    """Parse VTT timestamp (HH:MM:SS.mmm or MM:SS.mmm) to seconds."""
    parts = ts.strip().split(':')
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return 0.0


def _strip_html(text: str) -> str:
    """Remove HTML/VTT tags from text."""
    return re.sub(r'<[^>]+>', '', text)


def parse_vtt_content(content: str) -> list:
    """
    Parse VTT content string into segments with deduplication.

    Deduplication is per LINE, not per cue. YouTube's rolling auto-captions
    show each line three times — scrolling in as a cue's second line, alone in
    a filler cue, then scrolling out as the next cue's first line. The cues are
    all textually different, so dropping repeated cues leaves every line
    tripled; dropping lines already emitted by the previous cue does not.
    """
    lines = content.strip().split('\n')
    segments = []
    current_start = None
    current_end = None
    current_text = []
    last_line = None

    def flush():
        """Emit the pending cue, minus any lines already emitted."""
        nonlocal last_line
        fresh = []
        for raw in current_text:
            text = re.sub(r'\s+', ' ', _strip_html(raw)).strip()
            if text and text != last_line:
                fresh.append(text)
                last_line = text
        if not fresh:
            # Wholly duplicated cue — extend the previous segment instead, so
            # its time range still covers the span the words were on screen.
            if segments:
                segments[-1]['end'] = format_timestamp(current_end)
                segments[-1]['end_seconds'] = current_end
            return
        segments.append({
            'start': format_timestamp(current_start),
            'end': format_timestamp(current_end),
            'start_seconds': current_start,
            'end_seconds': current_end,
            'text': ' '.join(fresh),
        })

    timestamp_re = re.compile(r'(\d{1,2}:\d{2}(?::\d{2})?\.\d{3})\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?\.\d{3})')

    for line in lines:
        line = line.strip()

        if line.startswith('WEBVTT') or line.startswith('Kind:') or line.startswith('Language:'):
            continue

        if line.isdigit():
            continue

        match = timestamp_re.match(line)
        if match:
            if current_text:
                flush()

            current_start = _parse_vtt_timestamp(match.group(1))
            current_end = _parse_vtt_timestamp(match.group(2))
            current_text = []
        elif line and current_start is not None:
            current_text.append(line)

    # Save last segment
    if current_text:
        flush()

    return segments


def parse_vtt(vtt_path: str) -> list:
    """Parse a VTT file into segments."""
    with open(vtt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return parse_vtt_content(content)


def get_transcript(subtitle_path: str = None, video_path: str = None,
                   whisper_backend: str = 'auto', no_whisper: bool = False) -> dict:
    """
    Get transcript for a video.

    Returns:
        {'segments': [...], 'source': 'captions'|'whisper-groq'|'whisper-openai'|'none'}
    """
    if subtitle_path and os.path.exists(subtitle_path):
        segments = parse_vtt(subtitle_path)
        if segments:
            print(f"Using native captions ({len(segments)} segments)", file=sys.stderr)
            return {'segments': segments, 'source': 'captions'}

    if not no_whisper and video_path:
        try:
            from whisper import transcribe_with_whisper
            result = transcribe_with_whisper(video_path, backend=whisper_backend)
            if result and result['segments']:
                return result
        except ImportError:
            print("Whisper module not available", file=sys.stderr)
        except Exception as e:
            print(f"Whisper failed: {e}", file=sys.stderr)

    return {'segments': [], 'source': 'none'}
