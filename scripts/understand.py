#!/usr/bin/env python3
"""Gemini video understanding — uploads video and gets structured visual analysis."""

import json
import os
import sys
import time

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


def get_request_timeout() -> int:
    """Request timeout in seconds. Override with GEMINI_TIMEOUT."""
    value = _config_value('GEMINI_TIMEOUT')
    if not value:
        return DEFAULT_REQUEST_TIMEOUT
    try:
        seconds = int(value)
    except ValueError:
        raise ValueError(f"Invalid GEMINI_TIMEOUT: {value!r} (expected seconds)")
    if seconds <= 0:
        raise ValueError(f"Invalid GEMINI_TIMEOUT: {seconds} (must be positive)")
    return seconds


def _video_seconds(video_path: str) -> float | None:
    """Duration in seconds, or None if ffprobe can't read it."""
    try:
        from download import _get_duration
        return _get_duration(video_path) or None
    except Exception:
        return None


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


def understand_video(video_path: str, api_key: str,
                     start_time: str = None, end_time: str = None,
                     usage_out: dict = None) -> list:
    """
    Upload video to Gemini and get structured visual analysis.

    Args:
        video_path: Path to video file
        api_key: Gemini API key
        start_time: Optional start time to focus on (MM:SS or HH:MM:SS)
        end_time: Optional end time to focus on (MM:SS or HH:MM:SS)
        usage_out: Optional dict, populated in place with token usage

    Returns: list of segment dicts [{start, end, visual, audio, scene}, ...]
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    # Upload video file
    file_size = os.path.getsize(video_path)
    print(f"Uploading video ({file_size / 1e6:.1f} MB) to Gemini...", file=sys.stderr)

    uploaded_file = client.files.upload(file=video_path)
    print(f"Upload complete. Processing...", file=sys.stderr)

    try:
        # Poll until ACTIVE
        elapsed = 0
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            if elapsed >= POLL_TIMEOUT:
                raise TimeoutError(f"Gemini file processing timed out after {POLL_TIMEOUT}s")
            uploaded_file = client.files.get(name=uploaded_file.name)

        if uploaded_file.state.name != "ACTIVE":
            raise RuntimeError(f"Gemini file in unexpected state: {uploaded_file.state.name}")

        print("Video ready. Analyzing...", file=sys.stderr)

        # Generate content with structured output
        prompt = get_prompt()
        if start_time or end_time:
            range_str = f" Focus ONLY on the section from {start_time or '0:00'} to {end_time or 'the end'}. Ignore content outside this range."
            prompt += range_str

        model = get_model()
        resolution = get_media_resolution()
        timeout = get_request_timeout()
        print(f"Analyzing with {model} (media resolution: {resolution}, "
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
            # Long videos are the usual cause: one call covers the whole file,
            # so cost and latency scale with duration (~290 tokens/second at
            # high media resolution).
            if 'timeout' in str(e).lower() or 'timed out' in str(e).lower():
                raise TimeoutError(
                    f"Gemini did not respond within {timeout}s. Either raise "
                    f"GEMINI_TIMEOUT, lower GEMINI_MEDIA_RESOLUTION, or split "
                    f"the video into shorter parts."
                ) from e
            raise

        usage = extract_usage(response, model, resolution, _video_seconds(video_path))
        if usage.get('available'):
            per_sec = usage.get('tokens_per_video_second')
            print(
                f"Tokens: {usage['prompt_tokens']:,} in / {usage['candidates_tokens']:,} out"
                + (f" ({per_sec}/video-second)" if per_sec else ""),
                file=sys.stderr,
            )
        else:
            print("Token usage unavailable on this response", file=sys.stderr)
        if usage_out is not None:
            usage_out.update(usage)

        segments = parse_gemini_response(response.text)
        print(f"Gemini identified {len(segments)} visual segments", file=sys.stderr)
        return segments

    finally:
        # Always delete uploaded file
        try:
            client.files.delete(name=uploaded_file.name)
            print("Cleaned up Gemini uploaded file", file=sys.stderr)
        except Exception as e:
            print(f"Warning: failed to delete Gemini file: {e}", file=sys.stderr)
