import os
import sys
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def test_parse_vtt_basic():
    from transcribe import parse_vtt
    vtt_path = os.path.join(FIXTURES, 'sample.vtt')
    segments = parse_vtt(vtt_path)
    assert len(segments) == 3
    assert segments[0]['start'] == '00:00'
    assert segments[0]['text'] == "What's up guys, today I want to show you something"


def test_parse_vtt_strips_html_tags():
    from transcribe import parse_vtt_content
    content = """WEBVTT

00:00:00.000 --> 00:00:03.000
<c>Hello</c> <b>world</b>
"""
    segments = parse_vtt_content(content)
    assert segments[0]['text'] == "Hello world"


def test_parse_vtt_deduplicates_rolling_captions():
    from transcribe import parse_vtt_content
    content = """WEBVTT

00:00:00.000 --> 00:00:02.000
Hello world

00:00:02.000 --> 00:00:04.000
Hello world

00:00:04.000 --> 00:00:06.000
Something new
"""
    segments = parse_vtt_content(content)
    assert len(segments) == 2
    assert segments[0]['text'] == "Hello world"
    assert segments[1]['text'] == "Something new"


def test_parse_vtt_deduplicates_two_line_rolling_captions():
    """
    YouTube's real auto-caption shape: each line scrolls in as a cue's second
    line, appears alone in a filler cue, then scrolls out as the next cue's
    first line. No two cues are textually identical, so per-cue dedup leaves
    every line tripled.
    """
    from transcribe import parse_vtt_content
    content = """WEBVTT

00:00:00.000 --> 00:00:01.000
the first line

00:00:01.000 --> 00:00:01.010
the first line

00:00:01.010 --> 00:00:02.000
the first line
the second line

00:00:02.000 --> 00:00:02.010
the second line

00:00:02.010 --> 00:00:03.000
the second line
the third line
"""
    segments = parse_vtt_content(content)
    text = ' '.join(s['text'] for s in segments)
    assert text == "the first line the second line the third line"
    for line in ("the first line", "the second line", "the third line"):
        assert text.count(line) == 1


def test_format_timestamp_seconds():
    from transcribe import format_timestamp
    assert format_timestamp(0) == "00:00"
    assert format_timestamp(65) == "01:05"
    assert format_timestamp(3661) == "1:01:01"


def test_get_transcript_from_vtt(tmp_path):
    from transcribe import get_transcript
    vtt_src = os.path.join(FIXTURES, 'sample.vtt')
    vtt_dst = tmp_path / "subs.vtt"
    shutil.copy(vtt_src, vtt_dst)
    result = get_transcript(subtitle_path=str(vtt_dst))
    assert result['source'] == 'captions'
    assert len(result['segments']) > 0
