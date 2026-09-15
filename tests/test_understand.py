import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def test_validate_scene_tags():
    from understand import VALID_SCENE_TAGS, validate_segments
    segments = [
        {"start": "00:00", "end": "00:10", "visual": "test", "scene": "intro"},
        {"start": "00:10", "end": "00:20", "visual": "test", "scene": "invalid-tag"},
    ]
    validated = validate_segments(segments)
    assert validated[0]['scene'] == 'intro'
    assert validated[1]['scene'] == 'other'


def test_parse_gemini_response():
    from understand import parse_gemini_response
    fixture_path = os.path.join(FIXTURES, 'gemini_response.json')
    with open(fixture_path, 'r') as f:
        raw = f.read()
    segments = parse_gemini_response(raw)
    assert len(segments) == 3
    assert segments[0]['scene'] == 'intro'
    assert segments[0]['start'] == '00:00'


def test_get_prompt():
    from understand import get_prompt
    prompt = get_prompt()
    assert 'visual' in prompt
    assert 'scene' in prompt
    assert 'JSON' in prompt


def test_valid_scene_tags_list():
    from understand import VALID_SCENE_TAGS
    assert 'intro' in VALID_SCENE_TAGS
    assert 'talking-head' in VALID_SCENE_TAGS
    assert 'other' in VALID_SCENE_TAGS
    assert len(VALID_SCENE_TAGS) == 20


def test_seconds_to_timestamp():
    from understand import seconds_to_timestamp
    assert seconds_to_timestamp(0) == "00:00"
    assert seconds_to_timestamp(783.6) == "13:04"
    assert seconds_to_timestamp(3600) == "1:00:00"
    assert seconds_to_timestamp(3725) == "1:02:05"


def test_timestamp_round_trip_matches_frames_parser():
    from understand import seconds_to_timestamp
    from frames import timestamp_to_seconds
    for secs in (0, 59, 60, 783, 2301, 3599, 3600, 5400):
        assert timestamp_to_seconds(seconds_to_timestamp(secs)) == secs


def test_offset_segments_shifts_onto_full_timeline():
    from understand import offset_segments
    segments = [
        {'start': '00:05', 'end': '00:10', 'visual': 'a', 'audio': '', 'scene': 'intro'},
        {'start': '12:58', 'end': '13:03', 'visual': 'b', 'audio': '', 'scene': 'code'},
    ]
    out = offset_segments(segments, 783.65)
    assert out[0]['start'] == '13:09' and out[0]['end'] == '13:14'
    assert out[1]['start'] == '26:02' and out[1]['end'] == '26:07'


def test_offset_segments_zero_is_identity():
    from understand import offset_segments
    out = offset_segments([{'start': '01:02', 'end': '01:30'}], 0)
    assert out == [{'start': '01:02', 'end': '01:30'}]


def test_merge_usage_sums_chunks():
    from understand import merge_usage
    usages = [
        {'available': True, 'prompt_tokens': 100, 'candidates_tokens': 10,
         'thoughts_tokens': 1, 'cached_tokens': 0, 'total_tokens': 111},
        {'available': True, 'prompt_tokens': 200, 'candidates_tokens': 20,
         'thoughts_tokens': 2, 'cached_tokens': 0, 'total_tokens': 222},
    ]
    merged = merge_usage(usages, 'm', 'high', video_seconds=30)
    assert merged['available'] is True
    assert merged['chunks'] == 2
    assert merged['prompt_tokens'] == 300
    assert merged['total_tokens'] == 333
    assert merged['tokens_per_video_second'] == 10.0


def test_merge_usage_all_unavailable():
    from understand import merge_usage
    merged = merge_usage([{'available': False}, None], 'm', 'high', video_seconds=30)
    assert merged['available'] is False
    assert merged['chunks'] == 2
    assert 'prompt_tokens' not in merged


def test_get_chunk_seconds(monkeypatch):
    import pytest
    from understand import get_chunk_seconds, DEFAULT_CHUNK_SECONDS
    monkeypatch.delenv('GEMINI_CHUNK_SECONDS', raising=False)
    assert get_chunk_seconds() == DEFAULT_CHUNK_SECONDS
    monkeypatch.setenv('GEMINI_CHUNK_SECONDS', '600')
    assert get_chunk_seconds() == 600
    monkeypatch.setenv('GEMINI_CHUNK_SECONDS', 'soon')
    with pytest.raises(ValueError):
        get_chunk_seconds()
    monkeypatch.setenv('GEMINI_CHUNK_SECONDS', '0')
    with pytest.raises(ValueError):
        get_chunk_seconds()


def test_chunk_overlaps():
    from understand import _chunk_overlaps
    chunk = {'offset': 100.0, 'duration': 50.0}
    assert _chunk_overlaps(chunk, None, None)
    assert _chunk_overlaps(chunk, 120, 130)
    assert _chunk_overlaps(chunk, 0, 100)
    assert _chunk_overlaps(chunk, 150, None)
    assert not _chunk_overlaps(chunk, 151, None)
    assert not _chunk_overlaps(chunk, None, 99)


def test_split_video_covers_whole_file(tmp_path):
    import shutil
    import subprocess
    import pytest
    if not shutil.which('ffmpeg'):
        pytest.skip("ffmpeg not installed")
    from understand import split_video

    src = tmp_path / 'src.mp4'
    gen = subprocess.run([
        'ffmpeg', '-v', 'error', '-y',
        '-f', 'lavfi', '-i', 'testsrc=duration=20:size=160x120:rate=30',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=20',
        '-c:v', 'libx264', '-g', '30', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-shortest', str(src),
    ], capture_output=True, text=True)
    if gen.returncode != 0:
        pytest.skip(f"ffmpeg can't generate fixture: {gen.stderr.strip()}")

    work = tmp_path / 'chunks'
    work.mkdir()
    chunks = split_video(str(src), 8, str(work))

    assert len(chunks) >= 2
    assert chunks[0]['offset'] == 0.0
    for prev, cur in zip(chunks, chunks[1:]):
        assert cur['offset'] == pytest.approx(prev['offset'] + prev['duration'], abs=0.05)
        assert cur['offset'] > prev['offset']
    assert sum(c['duration'] for c in chunks) == pytest.approx(20, abs=0.5)
    for c in chunks:
        assert os.path.exists(c['path'])


def test_is_transient_by_status_code():
    from understand import _is_transient

    class Err(Exception):
        def __init__(self, code):
            super().__init__("x")
            self.code = code

    assert _is_transient(Err(503))
    assert _is_transient(Err(429))
    assert not _is_transient(Err(400))
    assert not _is_transient(Err(404))
    assert _is_transient(RuntimeError("503 UNAVAILABLE. The service is currently unavailable."))
    assert not _is_transient(ValueError("bad json"))


def test_with_transient_retry_recovers(monkeypatch):
    import understand
    monkeypatch.setattr(understand.time, 'sleep', lambda s: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("503 UNAVAILABLE")
        return "ok"

    assert understand._with_transient_retry(flaky, attempts=3) == "ok"
    assert len(calls) == 3


def test_with_transient_retry_gives_up(monkeypatch):
    import pytest
    import understand
    monkeypatch.setattr(understand.time, 'sleep', lambda s: None)

    def always():
        raise RuntimeError("503 UNAVAILABLE")

    with pytest.raises(RuntimeError):
        understand._with_transient_retry(always, attempts=3)


def test_with_transient_retry_does_not_retry_permanent(monkeypatch):
    import pytest
    import understand
    monkeypatch.setattr(understand.time, 'sleep', lambda s: None)
    calls = []

    def permanent():
        calls.append(1)
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        understand._with_transient_retry(permanent, attempts=3)
    assert len(calls) == 1
