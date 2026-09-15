"""Integration test — verifies the full pipeline with mocked APIs."""
import json
import os
import sys
import shutil
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


def test_full_pipeline_local_file(tmp_path, monkeypatch):
    """Full pipeline with a local file, mocked Gemini, captions provided."""
    from avt import parse_avt

    # Setup: create a fake video and subtitle file
    video_path = tmp_path / "test-video.mp4"
    video_path.write_bytes(b'\x00' * 1000)

    subtitle_path = tmp_path / "test-video.en.vtt"
    shutil.copy(os.path.join(FIXTURES, 'sample.vtt'), subtitle_path)

    # Mock download to return our local files
    mock_dl_result = {
        'video_path': str(video_path),
        'subtitle_path': str(subtitle_path),
        'title': 'Test Video Integration',
        'uploader': 'Test Channel',
        'duration': 32,
        'slug': 'test-video-integration',
        'is_local': True,
    }

    # Mock Gemini response
    with open(os.path.join(FIXTURES, 'gemini_response.json'), 'r') as f:
        mock_gemini_segments = json.load(f)

    # Mock frame extraction (no actual ffmpeg)
    mock_frames = [
        {'path': 'frames/frame-001.jpg', 'timestamp': '00:00', 'seconds': 0.0, 'segment': 0, 'kind': 'start'},
        {'path': 'frames/frame-002.jpg', 'timestamp': '00:04', 'seconds': 4.0, 'segment': 1, 'kind': 'start'},
        {'path': 'frames/frame-003.jpg', 'timestamp': '00:15', 'seconds': 15.0, 'segment': 2, 'kind': 'start'},
    ]

    out_dir = tmp_path / "output"
    out_dir.mkdir()
    (out_dir / "frames").mkdir()

    # Patch sys.argv for parse_args
    monkeypatch.setattr(sys, 'argv', ['analyze.py', str(video_path), '--out-dir', str(out_dir)])

    with patch('analyze.download_video', return_value=mock_dl_result), \
         patch('analyze.understand_video', return_value=mock_gemini_segments), \
         patch('analyze.extract_frames', return_value=mock_frames), \
         patch('analyze._load_key', return_value='fake-key'), \
         patch('analyze.preflight_check', return_value={'ready': True, 'missing_binaries': [], 'missing_required_keys': []}):

        from analyze import main
        main()

    # Verify .avt file was created in nested structure
    avt_path = out_dir / "avt_outputs" / "test-video-integration" / "test-video-integration.avt"
    assert avt_path.exists()

    # Parse and validate
    result = parse_avt(str(avt_path))
    assert result['version'] == '1.0'
    assert result['metadata']['title'] == 'Test Video Integration'
    assert result['metadata']['transcript_source'] == 'captions'
    assert len(result['segments']) == 3
    assert result['segments'][0]['scene'] == 'intro'
    # Check that audio was aligned from VTT
    assert len(result['segments'][0]['audio']) > 0
