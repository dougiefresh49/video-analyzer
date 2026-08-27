import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))


def test_timestamp_to_seconds():
    from frames import timestamp_to_seconds
    assert timestamp_to_seconds("00:00") == 0
    assert timestamp_to_seconds("01:30") == 90
    assert timestamp_to_seconds("1:00:00") == 3600
    assert timestamp_to_seconds("00:05") == 5


def test_build_ffmpeg_command():
    from frames import _build_frame_cmd
    cmd = _build_frame_cmd("/video.mp4", 30.0, "/out/frame-001.jpg", 512)
    assert 'ffmpeg' in cmd[0]
    assert '-ss' in cmd
    assert '30.0' in cmd
    assert '/out/frame-001.jpg' in cmd


def test_select_timestamps_under_max():
    from frames import select_timestamps
    timestamps = [0, 10, 20, 30, 40]
    result = select_timestamps(timestamps, max_frames=80)
    assert result == timestamps


def test_select_timestamps_over_max():
    from frames import select_timestamps
    timestamps = list(range(100))
    result = select_timestamps(timestamps, max_frames=10)
    assert len(result) == 10
    assert result[0] == 0
    assert result[-1] == 99


def test_get_frame_filename():
    from frames import get_frame_filename
    assert get_frame_filename(0) == "frame-001.jpg"
    assert get_frame_filename(9) == "frame-010.jpg"
    assert get_frame_filename(99) == "frame-100.jpg"


def test_select_timestamps_tiny_max():
    from frames import select_timestamps
    assert select_timestamps([0, 10, 20], max_frames=1) == [0]
    assert select_timestamps([0, 10, 20], max_frames=0) == []


def test_plan_frames_start_only_for_short_segments():
    from frames import plan_frames
    segments = [
        {'start': '00:00', 'end': '00:10'},
        {'start': '00:10', 'end': '00:40'},
    ]
    plan = plan_frames(segments, max_frames=80, frame_interval=45)
    assert [(p['seconds'], p['segment'], p['kind']) for p in plan] == [
        (0.0, 0, 'start'),
        (10.0, 1, 'start'),
    ]


def test_plan_frames_adds_evenly_spaced_extras_to_long_segments():
    from frames import plan_frames
    segments = [
        {'start': '00:00', 'end': '00:46'},   # 46s -> one extra at the midpoint
        {'start': '00:46', 'end': '05:27'},   # 281s -> six extras, every ~40s
        {'start': '05:27', 'end': '05:30'},
    ]
    plan = plan_frames(segments, max_frames=80, frame_interval=45)

    extras = [p for p in plan if p['kind'] == 'extra']
    assert [p['seconds'] for p in extras if p['segment'] == 0] == [23.0]
    seg1 = [p['seconds'] for p in extras if p['segment'] == 1]
    assert len(seg1) == 6
    assert all(46 < s < 327 for s in seg1)
    # evenly spaced: 281 / 7 ≈ 40.1, truncated to whole seconds
    assert all(39 <= b - a <= 42 for a, b in zip(seg1, seg1[1:]))

    # sorted by time, start frame first on the segment boundary
    seconds = [p['seconds'] for p in plan]
    assert seconds == sorted(seconds)
    assert plan[0]['kind'] == 'start'
    assert plan[-1] == {'seconds': 327.0, 'segment': 2, 'kind': 'start'}


def test_plan_frames_interval_zero_disables_extras():
    from frames import plan_frames
    segments = [{'start': '00:00', 'end': '10:00'}]
    plan = plan_frames(segments, max_frames=80, frame_interval=0)
    assert plan == [{'seconds': 0.0, 'segment': 0, 'kind': 'start'}]


def test_plan_frames_tolerates_missing_end():
    from frames import plan_frames
    plan = plan_frames([{'start': '01:00'}], max_frames=80, frame_interval=45)
    assert plan == [{'seconds': 60.0, 'segment': 0, 'kind': 'start'}]


def test_plan_frames_extras_yield_to_max_frames():
    from frames import plan_frames
    # 3 starts + 6 extras would be 9; cap at 5 keeps all starts, 2 extras
    segments = [
        {'start': '00:00', 'end': '00:10'},
        {'start': '00:10', 'end': '04:51'},
        {'start': '04:51', 'end': '05:00'},
    ]
    plan = plan_frames(segments, max_frames=5, frame_interval=45)
    assert len(plan) == 5
    assert sum(p['kind'] == 'start' for p in plan) == 3
    assert sum(p['kind'] == 'extra' for p in plan) == 2

    # cap below the number of starts: no extras at all
    plan = plan_frames(segments, max_frames=2, frame_interval=45)
    assert len(plan) == 2
    assert all(p['kind'] == 'start' for p in plan)


def test_attach_frames():
    from frames import attach_frames
    segments = [{'start': '00:00', 'end': '01:40'}, {'start': '01:40', 'end': '01:50'}]
    results = [
        {'path': 'frames/frame-001.jpg', 'timestamp': '00:00', 'seconds': 0.0, 'segment': 0, 'kind': 'start'},
        {'path': 'frames/frame-002.jpg', 'timestamp': '00:33', 'seconds': 33.0, 'segment': 0, 'kind': 'extra'},
        {'path': 'frames/frame-003.jpg', 'timestamp': '01:06', 'seconds': 66.0, 'segment': 0, 'kind': 'extra'},
        {'path': 'frames/frame-004.jpg', 'timestamp': '01:40', 'seconds': 100.0, 'segment': 1, 'kind': 'start'},
    ]
    attach_frames(segments, results)
    assert segments[0]['frame'] == 'frames/frame-001.jpg'
    assert segments[0]['extra_frames'] == [
        {'path': 'frames/frame-002.jpg', 'timestamp': '00:33'},
        {'path': 'frames/frame-003.jpg', 'timestamp': '01:06'},
    ]
    assert segments[1]['frame'] == 'frames/frame-004.jpg'
    assert segments[1]['extra_frames'] == []


def test_attach_frames_segment_without_start_frame():
    from frames import attach_frames
    segments = [{'start': '00:00', 'end': '01:40'}]
    results = [
        {'path': 'frames/frame-001.jpg', 'timestamp': '00:50', 'seconds': 50.0, 'segment': 0, 'kind': 'extra'},
    ]
    attach_frames(segments, results)
    assert segments[0]['frame'] is None
    assert segments[0]['extra_frames'] == [{'path': 'frames/frame-001.jpg', 'timestamp': '00:50'}]
