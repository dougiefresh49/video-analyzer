#!/usr/bin/env python3
"""Gemini video understanding — uploads video and gets structured visual analysis."""

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

VALID_SCENE_TAGS = [
    'intro', 'outro', 'hook', 'cta', 'sponsor',
    'talking-head', 'screen-recording', 'demo', 'tutorial',
    'slide', 'diagram', 'whiteboard', 'code',
    'b-roll', 'montage', 'transition',
    'interview', 'reaction', 'commentary',
    'other',
]

RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "start": {"type": "string"},
            "end": {"type": "string"},
            "visual": {"type": "string"},
            "audio": {"type": "string"},
            "scene": {"type": "string", "enum": VALID_SCENE_TAGS},
        },
        "required": ["start", "end", "visual", "audio", "scene"],
    },
}

PROMPTS_DIR = os.path.join(os.path.dirname(__file__), 'prompts')
POLL_INTERVAL = 5  # seconds
POLL_TIMEOUT = 300  # 5 minutes

DEFAULT_MODEL = "gemini-3.6-flash"

# Video is downsampled on ingest. Screen recordings whose small UI text matters
# (file/frame/layer names) need a higher setting than the default.
DEFAULT_MEDIA_RESOLUTION = "medium"
VALID_MEDIA_RESOLUTIONS = ("low", "medium", "high")

# The SDK has no default request timeout, so a stalled analysis call hangs
# forever with no output — POLL_TIMEOUT above only guards the upload poll.
# A 13-minute clip analyzes in ~2 min, so 15 min is generous but still bounded.
DEFAULT_REQUEST_TIMEOUT = 900  # seconds

# One generate_content call covers a whole file, so cost and latency scale with
# duration, and long videos time out or hang (a 38-minute video at high media
# resolution is ~670k input tokens in one request). Videos longer than this are
# split into keyframe-aligned chunks, analyzed separately, and re-stitched on
# the full-video timeline. 13 minutes analyzes in ~2 min.
DEFAULT_CHUNK_SECONDS = 780
MAX_PARALLEL_CHUNKS = 3

# Gemini returns the odd 503/429 mid-run. A chunk that hits one is retried
# (re-uploaded) rather than failing the whole video.
TRANSIENT_ATTEMPTS = 3
TRANSIENT_STATUS_CODES = (429, 500, 502, 503, 504)


def get_media_resolution() -> str:
    """Media resolution for video ingest. Override with GEMINI_MEDIA_RESOLUTION."""
    value = (_config_value('GEMINI_MEDIA_RESOLUTION') or DEFAULT_MEDIA_RESOLUTION).lower()
    if value not in VALID_MEDIA_RESOLUTIONS:
        raise ValueError(
            f"Invalid GEMINI_MEDIA_RESOLUTION: {value!r} "
            f"(expected one of {', '.join(VALID_MEDIA_RESOLUTIONS)})"
        )
    return value


def _config_value(name: str) -> str | None:
    """
    Read a setting from the shell environment or the config file (shell wins),
    so it can be set the same way API keys are.
    """
    from_env = os.environ.get(name)
    if from_env and from_env.strip():
        return from_env.strip()

    try:
        from preflight import ENV_FILE
        with open(ENV_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith(f'{name}='):
                    value = line.split('=', 1)[1].strip()
                    if value:
                        return value
    except (OSError, ImportError):
        pass

    return None


def get_model() -> str:
    """Model id for video understanding. Override with GEMINI_MODEL."""
    return _config_value('GEMINI_MODEL') or DEFAULT_MODEL


def _positive_int_setting(name: str, default: int) -> int:
    value = _config_value(name)
    if not value:
        return default
    try:
        seconds = int(value)
    except ValueError:
        raise ValueError(f"Invalid {name}: {value!r} (expected seconds)")
    if seconds <= 0:
        raise ValueError(f"Invalid {name}: {seconds} (must be positive)")
    return seconds


def get_request_timeout() -> int:
    """Request timeout in seconds. Override with GEMINI_TIMEOUT."""
    return _positive_int_setting('GEMINI_TIMEOUT', DEFAULT_REQUEST_TIMEOUT)


def get_chunk_seconds() -> int:
    """Longest video (seconds) sent in one request. Override with GEMINI_CHUNK_SECONDS."""
    return _positive_int_setting('GEMINI_CHUNK_SECONDS', DEFAULT_CHUNK_SECONDS)


def _video_seconds(video_path: str) -> float | None:
    """Duration in seconds, or None if ffprobe can't read it."""
    try:
        from download import _get_duration
        return _get_duration(video_path) or None
    except Exception:
        return None


def timestamp_to_seconds(ts: str) -> float:
    """Parse SS, MM:SS, or H:MM:SS. Unparseable input counts as 0."""
    parts = str(ts).strip().split(':')
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return 0.0


def seconds_to_timestamp(seconds: float) -> str:
    """MM:SS under an hour, H:MM:SS from an hour on — the forms frames.py parses."""
    total = max(0, int(round(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def offset_segments(segments: list, offset_seconds: float) -> list:
    """Shift chunk-relative start/end timestamps onto the full-video timeline."""
    for seg in segments:
        seg['start'] = seconds_to_timestamp(timestamp_to_seconds(seg['start']) + offset_seconds)
        seg['end'] = seconds_to_timestamp(timestamp_to_seconds(seg['end']) + offset_seconds)
    return segments


def split_video(video_path: str, chunk_seconds: int, work_dir: str) -> list:
    """
    Split a video into chunks of roughly chunk_seconds each.

    Stream copy, cut at the first keyframe after each boundary, so chunks run a
    little long and the last one is short. Returns, in order:
        [{'path': str, 'offset': float, 'duration': float}, ...]
    where offset is the chunk's start on the full-video timeline.
    """
    ext = os.path.splitext(video_path)[1] or '.mp4'
    list_path = os.path.join(work_dir, 'chunks.csv')
    cmd = [
        'ffmpeg', '-v', 'error', '-y',
        '-i', video_path,
        '-map', '0', '-c', 'copy',
        '-f', 'segment',
        '-segment_time', str(chunk_seconds),
        '-reset_timestamps', '1',
        '-segment_list', list_path,
        '-segment_list_type', 'csv',
        os.path.join(work_dir, f'chunk-%03d{ext}'),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to split video: {result.stderr.strip()}")

    chunks = []
    with open(list_path, newline='') as f:
        for row in csv.reader(f):
            if len(row) < 3:
                continue
            name, start, end = row[0], float(row[1]), float(row[2])
            chunks.append({
                'path': os.path.join(work_dir, name),
                'offset': start,
                'duration': end - start,
            })
    if not chunks:
        raise RuntimeError("ffmpeg produced no chunks")
    return chunks


def _chunk_overlaps(chunk: dict, start_sec: float | None, end_sec: float | None) -> bool:
    chunk_start = chunk['offset']
    chunk_end = chunk['offset'] + chunk['duration']
    if start_sec is not None and chunk_end < start_sec:
        return False
    if end_sec is not None and chunk_start > end_sec:
        return False
    return True


def _is_transient(error: Exception) -> bool:
    """True for rate limits and server-side failures worth retrying."""
    code = getattr(error, 'code', None)
    if code is None:
        code = getattr(error, 'status_code', None)
    if isinstance(code, int):
        return code in TRANSIENT_STATUS_CODES
    message = str(error).lower()
    return any(marker in message for marker in (
        'unavailable', 'overloaded', 'resource exhausted', 'deadline exceeded',
        'connection reset', 'temporarily', '503', '502', '504', '429',
    ))


def _with_transient_retry(fn, tag: str = '', attempts: int = TRANSIENT_ATTEMPTS):
    """Call fn(); on a transient error wait and try again, up to `attempts` total."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            if attempt >= attempts or not _is_transient(e):
                raise
            delay = 5 * attempt
            print(f"{tag}Transient Gemini error ({type(e).__name__}: {str(e)[:100]}); "
                  f"retrying in {delay}s ({attempt}/{attempts - 1})...", file=sys.stderr)
            time.sleep(delay)


def get_prompt() -> str:
    """Load the Gemini prompt template."""
    prompt_path = os.path.join(PROMPTS_DIR, 'understand.txt')
    with open(prompt_path, 'r') as f:
        return f.read().strip()


def validate_segments(segments: list) -> list:
    """Validate and fix scene tags in segments."""
    for seg in segments:
        if seg.get('scene') not in VALID_SCENE_TAGS:
            seg['scene'] = 'other'
        seg['audio'] = (seg.get('audio') or '').strip()
    return segments


def parse_gemini_response(raw_text: str) -> list:
    """Parse Gemini's JSON response into validated segments."""
    text = raw_text.strip()
    if text.startswith('```'):
        lines = text.split('\n')
        lines = [l for l in lines if not l.strip().startswith('```')]
        text = '\n'.join(lines)

    segments = json.loads(text)
    return validate_segments(segments)


def extract_usage(response, model: str, resolution: str, video_seconds: float = None) -> dict:
    """
    Pull token counts off a Gemini response.

    Video dominates the input token count, so tokens_per_video_second is the
    figure worth keeping: it lets you predict cost for a video of any length
    at this model and media resolution.
    """
    meta = getattr(response, 'usage_metadata', None)
    if meta is None:
        return {'model': model, 'media_resolution': resolution, 'available': False}

    def _n(attr):
        return getattr(meta, attr, None) or 0

    prompt = _n('prompt_token_count')
    usage = {
        'model': model,
        'media_resolution': resolution,
        'available': True,
        'prompt_tokens': prompt,
        'candidates_tokens': _n('candidates_token_count'),
        'thoughts_tokens': _n('thoughts_token_count'),
        'cached_tokens': _n('cached_content_token_count'),
        'total_tokens': _n('total_token_count'),
    }
    if video_seconds:
        usage['video_seconds'] = round(video_seconds, 2)
        usage['tokens_per_video_second'] = round(prompt / video_seconds, 1)
    return usage


_USAGE_COUNTERS = ('prompt_tokens', 'candidates_tokens', 'thoughts_tokens',
                   'cached_tokens', 'total_tokens')


def merge_usage(usages: list, model: str, resolution: str, video_seconds: float = None) -> dict:
    """Sum per-chunk usage dicts into one figure for the whole video."""
    available = [u for u in usages if u and u.get('available')]
    merged = {
        'model': model,
        'media_resolution': resolution,
        'available': bool(available),
        'chunks': len(usages),
    }
    if not available:
        return merged
    for key in _USAGE_COUNTERS:
        merged[key] = sum(u.get(key, 0) for u in available)
    if video_seconds:
        merged['video_seconds'] = round(video_seconds, 2)
        merged['tokens_per_video_second'] = round(merged['prompt_tokens'] / video_seconds, 1)
    return merged


def _analyze_file(client, video_path: str, prompt: str, model: str, resolution: str,
                  timeout: int, label: str = '') -> tuple:
    """
    Upload one video file to Gemini, analyze it, and delete the upload.

    Returns (segments, usage). Timestamps in segments are relative to the file.
    """
    from google.genai import types

    tag = f"[{label}] " if label else ''

    file_size = os.path.getsize(video_path)
    print(f"{tag}Uploading video ({file_size / 1e6:.1f} MB) to Gemini...", file=sys.stderr)

    uploaded_file = client.files.upload(file=video_path)
    print(f"{tag}Upload complete. Processing...", file=sys.stderr)

    try:
        # Poll until ACTIVE
        elapsed = 0
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            if elapsed >= POLL_TIMEOUT:
                raise TimeoutError(f"{tag}Gemini file processing timed out after {POLL_TIMEOUT}s")
            uploaded_file = _with_transient_retry(
                lambda: client.files.get(name=uploaded_file.name), tag)

        if uploaded_file.state.name != "ACTIVE":
            raise RuntimeError(f"{tag}Gemini file in unexpected state: {uploaded_file.state.name}")

        print(f"{tag}Video ready. Analyzing with {model} (media resolution: {resolution}, "
              f"timeout: {timeout}s)...", file=sys.stderr)
        try:
            response = client.models.generate_content(
                model=model,
                contents=[uploaded_file, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    media_resolution=f"MEDIA_RESOLUTION_{resolution.upper()}",
                    http_options=types.HttpOptions(timeout=timeout * 1000),
                ),
            )
        except Exception as e:
            # Cost and latency scale with the file's duration (~290 tokens/second
            # at high media resolution).
            if 'timeout' in str(e).lower() or 'timed out' in str(e).lower():
                raise TimeoutError(
                    f"{tag}Gemini did not respond within {timeout}s. Either raise "
                    f"GEMINI_TIMEOUT, lower GEMINI_MEDIA_RESOLUTION, or lower "
                    f"GEMINI_CHUNK_SECONDS to send shorter pieces."
                ) from e
            raise

        usage = extract_usage(response, model, resolution, _video_seconds(video_path))
        if usage.get('available'):
            per_sec = usage.get('tokens_per_video_second')
            print(
                f"{tag}Tokens: {usage['prompt_tokens']:,} in / {usage['candidates_tokens']:,} out"
                + (f" ({per_sec}/video-second)" if per_sec else ""),
                file=sys.stderr,
            )
        else:
            print(f"{tag}Token usage unavailable on this response", file=sys.stderr)

        segments = parse_gemini_response(response.text)
        print(f"{tag}Gemini identified {len(segments)} visual segments", file=sys.stderr)
        return segments, usage

    finally:
        # Always delete uploaded file
        try:
            client.files.delete(name=uploaded_file.name)
            print(f"{tag}Cleaned up Gemini uploaded file", file=sys.stderr)
        except Exception as e:
            print(f"{tag}Warning: failed to delete Gemini file: {e}", file=sys.stderr)


def understand_video(video_path: str, api_key: str,
                     start_time: str = None, end_time: str = None,
                     usage_out: dict = None) -> list:
    """
    Upload video to Gemini and get structured visual analysis.

    Videos longer than GEMINI_CHUNK_SECONDS are split into chunks that are
    analyzed separately (up to MAX_PARALLEL_CHUNKS at a time); the returned
    timestamps are always on the full-video timeline.

    Args:
        video_path: Path to video file
        api_key: Gemini API key
        start_time: Optional start time to focus on (MM:SS or HH:MM:SS)
        end_time: Optional end time to focus on (MM:SS or HH:MM:SS)
        usage_out: Optional dict, populated in place with token usage

    Returns: list of segment dicts [{start, end, visual, audio, scene}, ...]
    """
    from google import genai

    client = genai.Client(api_key=api_key)
    model = get_model()
    resolution = get_media_resolution()
    timeout = get_request_timeout()
    chunk_seconds = get_chunk_seconds()
    prompt = get_prompt()
    duration = _video_seconds(video_path) or 0

    if duration <= chunk_seconds:
        if start_time or end_time:
            prompt += (f" Focus ONLY on the section from {start_time or '0:00'} to "
                       f"{end_time or 'the end'}. Ignore content outside this range.")
        segments, usage = _with_transient_retry(
            lambda: _analyze_file(client, video_path, prompt, model, resolution, timeout))
        if usage_out is not None:
            usage_out.update(usage)
        return segments

    work_dir = tempfile.mkdtemp(prefix='video-analyzer-chunks-')
    try:
        chunks = split_video(video_path, chunk_seconds, work_dir)
        start_sec = timestamp_to_seconds(start_time) if start_time else None
        end_sec = timestamp_to_seconds(end_time) if end_time else None
        selected = [c for c in chunks if _chunk_overlaps(c, start_sec, end_sec)]
        skipped = len(chunks) - len(selected)
        print(
            f"Video is {seconds_to_timestamp(duration)} — analyzing as {len(selected)} "
            f"chunk(s) of ~{chunk_seconds // 60} min"
            + (f" ({skipped} outside the requested range skipped)" if skipped else ""),
            file=sys.stderr,
        )

        results = [None] * len(selected)
        usages = [None] * len(selected)
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_CHUNKS, len(selected))) as pool:
            def _run(chunk, label):
                return _with_transient_retry(
                    lambda: _analyze_file(client, chunk['path'], prompt, model, resolution,
                                          timeout, label),
                    f"[{label}] ")

            futures = {
                pool.submit(_run, chunk, f"chunk {i + 1}/{len(selected)}"): i
                for i, chunk in enumerate(selected)
            }
            try:
                for future in as_completed(futures):
                    i = futures[future]
                    segments, usage = future.result()
                    results[i] = offset_segments(segments, selected[i]['offset'])
                    usages[i] = usage
            except BaseException:
                pool.shutdown(wait=False, cancel_futures=True)
                raise

        all_segments = [seg for segs in results for seg in segs]
        print(f"Gemini identified {len(all_segments)} visual segments across "
              f"{len(selected)} chunks", file=sys.stderr)
        if usage_out is not None:
            usage_out.update(merge_usage(usages, model, resolution, duration))
        return all_segments
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
