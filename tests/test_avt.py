import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def test_parse_avt_header():
    from avt import parse_avt
    fixture_path = os.path.join(FIXTURES, 'sample.avt')
    result = parse_avt(fixture_path)
    assert result['version'] == '1.0'
    assert result['metadata']['title'] == 'Test Video'
    assert result['metadata']['channel'] == 'Test Channel'
    assert result['metadata']['frames_extracted'] == 3


def test_parse_avt_segments():
    from avt import parse_avt
    fixture_path = os.path.join(FIXTURES, 'sample.avt')
    result = parse_avt(fixture_path)
    assert len(result['segments']) == 3
    seg = result['segments'][0]
    assert seg['start'] == '00:00'
    assert seg['end'] == '00:04'
    assert seg['scene'] == 'intro'
    assert 'ring light' in seg['visual']
    assert seg['frame'] == 'frames/frame-001.jpg'


def test_parse_avt_audio_with_escaped_quotes():
    from avt import parse_avt
    fixture_path = os.path.join(FIXTURES, 'sample.avt')
    result = parse_avt(fixture_path)
    seg = result['segments'][0]
    assert "What's up" in seg['audio']


def test_parse_avt_segment_without_frame():
    from avt import parse_avt_content
    content = """AGENTIC-VT 1.0

[metadata]
title: Test
channel: Test
duration: 00:10
source: test
analyzed: 2026-01-01 00:00:00
model: test
frames_extracted: 0
transcript_source: none

---

[00:00 - 00:10] [scene:talking-head]
VISUAL: Person talking to camera.
AUDIO: 'Hello world'
"""
    result = parse_avt_content(content)
    assert result['segments'][0]['frame'] is None


def test_write_avt(tmp_path):
    from avt import write_avt, parse_avt

    metadata = {
        'title': 'Write Test',
        'channel': 'Test Channel',
        'duration': '00:30',
        'source': 'https://example.com/video',
        'analyzed': '2026-05-16 10:00:00',
        'model': 'gemini-2.5-flash',
        'frames_extracted': 2,
        'transcript_source': 'captions',
    }
    segments = [
        {
            'start': '00:00',
            'end': '00:15',
            'scene': 'intro',
            'visual': 'Person at desk with microphone.',
            'audio': "Hello, let's get started.",
            'frame': 'frames/frame-001.jpg',
        },
        {
            'start': '00:15',
            'end': '00:30',
            'scene': 'demo',
            'visual': 'Screen recording of terminal.',
            'audio': "Now I'll show you the demo.",
            'frame': 'frames/frame-002.jpg',
        },
    ]

    output_path = str(tmp_path / "test.avt")
    write_avt(metadata, segments, output_path)

    result = parse_avt(output_path)
    assert result['version'] == '1.0'
    assert result['metadata']['title'] == 'Write Test'
    assert len(result['segments']) == 2
    assert result['segments'][0]['audio'] == "Hello, let's get started."


def test_write_avt_escapes_quotes(tmp_path):
    from avt import write_avt, parse_avt

    metadata = {
        'title': 'Quote Test',
        'channel': 'Test',
        'duration': '00:10',
        'source': 'test',
        'analyzed': '2026-01-01 00:00:00',
        'model': 'test',
        'frames_extracted': 0,
        'transcript_source': 'none',
    }
    segments = [
        {
            'start': '00:00',
            'end': '00:10',
            'scene': 'talking-head',
            'visual': 'Person talking.',
            'audio': "It's a test with single 'quotes' inside.",
            'frame': None,
        },
    ]

    output_path = str(tmp_path / "quotes.avt")
    write_avt(metadata, segments, output_path)

    result = parse_avt(output_path)
    assert result['segments'][0]['audio'] == "It's a test with single 'quotes' inside."


def test_extra_frames_round_trip(tmp_path):
    from avt import write_avt, parse_avt

    metadata = {
        'title': 'Extras', 'channel': 'c', 'duration': '05:00', 'source': 'x',
        'analyzed': '2026-08-27 00:00:00', 'model': 'm', 'frames_extracted': 4,
        'transcript_source': 'captions',
    }
    segments = [
        {
            'start': '00:00', 'end': '04:41', 'scene': 'code',
            'visual': 'Scrolling through a long file.', 'audio': 'talk',
            'frame': 'frames/frame-001.jpg',
            'extra_frames': [
                {'path': 'frames/frame-002.jpg', 'timestamp': '01:33'},
                {'path': 'frames/frame-003.jpg', 'timestamp': '03:07'},
            ],
        },
        {
            'start': '04:41', 'end': '05:00', 'scene': 'outro',
            'visual': 'Wave.', 'audio': 'bye',
            'frame': 'frames/frame-004.jpg',
        },
    ]
    output_path = str(tmp_path / 'extras.avt')
    write_avt(metadata, segments, output_path)

    with open(output_path) as f:
        text = f.read()
    assert 'FRAME: frames/frame-001.jpg\nFRAME: frames/frame-002.jpg @01:33\nFRAME: frames/frame-003.jpg @03:07\n' in text

    result = parse_avt(output_path)
    assert result['segments'][0]['frame'] == 'frames/frame-001.jpg'
    assert result['segments'][0]['extra_frames'] == [
        {'path': 'frames/frame-002.jpg', 'timestamp': '01:33'},
        {'path': 'frames/frame-003.jpg', 'timestamp': '03:07'},
    ]
    assert result['segments'][1]['frame'] == 'frames/frame-004.jpg'
    assert result['segments'][1]['extra_frames'] == []


def test_parse_extra_frame_without_start_frame():
    from avt import parse_avt_content
    content = (
        "AGENTIC-VT 1.0\n\n[metadata]\ntitle: t\n\n---\n\n"
        "[00:00 - 01:40] [scene:code]\nVISUAL: v\nAUDIO: 'a'\n"
        "FRAME: frames/frame-001.jpg @00:50\n"
    )
    result = parse_avt_content(content)
    seg = result['segments'][0]
    assert seg['frame'] is None
    assert seg['extra_frames'] == [{'path': 'frames/frame-001.jpg', 'timestamp': '00:50'}]
