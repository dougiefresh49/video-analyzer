#!/usr/bin/env python3
"""Frame extraction — extract JPEG frames at Gemini-identified timestamps."""

import os
import subprocess
import sys

# One extra frame per this many seconds of segment length, evenly spaced
# inside the segment. A frame at the segment *start* only shows the first
# screen of a long screen recording; a five-minute scroll through a document
# would otherwise be represented by its top.
DEFAULT_FRAME_INTERVAL = 45


def timestamp_to_seconds(ts: str) -> float:
    """Convert MM:SS or H:MM:SS timestamp to seconds."""
    parts = ts.split(':')
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return 0.0


def get_frame_filename(index: int) -> str:
    """Generate frame filename from index (0-based)."""
    return f"frame-{index + 1:03d}.jpg"


def select_timestamps(timestamps: list, max_frames: int = 80) -> list:
    """Select evenly-spaced subset if over max_frames limit."""
    if len(timestamps) <= max_frames:
        return timestamps
    if max_frames <= 0:
        return []
    if max_frames == 1:
        return [timestamps[0]]

    step = (len(timestamps) - 1) / (max_frames - 1)
    indices = [round(i * step) for i in range(max_frames)]
    return [timestamps[i] for i in indices]


def plan_frames(segments: list, max_frames: int = 80,
                frame_interval: int = DEFAULT_FRAME_INTERVAL) -> list:
    """
    Decide which timestamps to extract.

    Every segment gets a frame at its start. Segments longer than
    frame_interval also get floor(length / frame_interval) extra frames,
    evenly spaced inside the segment (a 46 s segment gets one at its midpoint;
    a 280 s segment gets six, every 40 s). Start frames are capped to
    max_frames first; extras fill whatever budget is left.

    Args:
        segments: List of dicts with 'start' (and optionally 'end') timestamps
        max_frames: Hard cap on total frames
        frame_interval: Seconds of segment length per extra frame; 0 disables

    Returns:
        List of {'seconds': float, 'segment': int, 'kind': 'start'|'extra'},
        sorted by time.
    """
    starts = []
    for i, seg in enumerate(segments):
        starts.append({
            'seconds': timestamp_to_seconds(seg['start']),
            'segment': i,
            'kind': 'start',
        })
    starts = select_timestamps(starts, max_frames)

    extras = []
    if frame_interval and frame_interval > 0:
        for i, seg in enumerate(segments):
            if not seg.get('end'):
                continue
            start = timestamp_to_seconds(seg['start'])
            length = timestamp_to_seconds(seg['end']) - start
            n = int(length // frame_interval)
            for k in range(1, n + 1):
                extras.append({
                    'seconds': float(int(start + length * k / (n + 1))),
                    'segment': i,
                    'kind': 'extra',
                })

    budget = max_frames - len(starts)
    extras = select_timestamps(extras, max(budget, 0))

    # Sort by time; a start frame wins a tie so filenames stay chronological
    # and the primary frame for a segment is numbered before its extras.
    return sorted(starts + extras, key=lambda p: (p['seconds'], p['kind'] != 'start'))


def _build_frame_cmd(video_path: str, seconds: float, output_path: str, width: int) -> list:
    """Build ffmpeg command to extract a single frame."""
    return [
        'ffmpeg', '-y',
        '-ss', str(seconds),
        '-i', video_path,
        '-vframes', '1',
        '-vf', f'scale={width}:-1',
        '-q:v', '2',
        output_path,
    ]


def extract_frames(video_path: str, segments: list, output_dir: str,
                   max_frames: int = 80, width: int = 512,
                   frame_interval: int = DEFAULT_FRAME_INTERVAL) -> list:
    """
    Extract JPEG frames at segment start timestamps, plus evenly spaced extra
    frames inside long segments (see plan_frames).

    Args:
        video_path: Path to video file
        segments: List of dicts with 'start' and 'end' keys (from understand.py)
        output_dir: Directory to write frames/ into
        max_frames: Maximum number of frames to extract
        width: Frame width in pixels
        frame_interval: Seconds of segment length per extra frame; 0 disables

    Returns:
        List of {'path': str, 'timestamp': str, 'seconds': float,
                 'segment': int, 'kind': 'start'|'extra'}
    """
    frames_dir = os.path.join(output_dir, 'frames')
    os.makedirs(frames_dir, exist_ok=True)

    planned = plan_frames(segments, max_frames=max_frames, frame_interval=frame_interval)

    results = []
    total = len(planned)
    for i, plan in enumerate(planned):
        seconds = plan['seconds']
        filename = get_frame_filename(i)
        output_path = os.path.join(frames_dir, filename)

        cmd = _build_frame_cmd(video_path, seconds, output_path, width)
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"Warning: frame extraction failed at {seconds}s: {result.stderr}", file=sys.stderr)
            continue

        relative_path = os.path.join('frames', filename)
        results.append({
            'path': relative_path,
            'timestamp': _seconds_to_timestamp(seconds),
            'seconds': seconds,
            'segment': plan['segment'],
            'kind': plan['kind'],
        })

        if (i + 1) % 10 == 0 or i == total - 1:
            print(f"Extracted {i + 1}/{total} frames", file=sys.stderr)

    return results


def attach_frames(segments: list, frame_results: list) -> list:
    """
    Assign extracted frames to their segments in place.

    Sets seg['frame'] to the start frame path (or None) and
    seg['extra_frames'] to a list of {'path', 'timestamp'} for frames taken
    inside the segment.
    """
    for seg in segments:
        seg['frame'] = None
        seg['extra_frames'] = []

    for fr in frame_results:
        seg = segments[fr['segment']]
        if fr['kind'] == 'start':
            seg['frame'] = fr['path']
        else:
            seg['extra_frames'].append({'path': fr['path'], 'timestamp': fr['timestamp']})

    return segments


def _seconds_to_timestamp(seconds: float) -> str:
    """Convert seconds back to MM:SS or H:MM:SS."""
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"
