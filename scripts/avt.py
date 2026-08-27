#!/usr/bin/env python3
"""AVT format writer and parser — Agentic Video Transcript."""

import os
import re
import sys

FORMAT_VERSION = "1.0"
FORMAT_HEADER = f"AGENTIC-VT {FORMAT_VERSION}"

# Regex patterns for parsing
RE_HEADER = re.compile(r'^AGENTIC-VT (\d+\.\d+)$')
RE_METADATA = re.compile(r'^(\w+):\s*(.+)$')
RE_SEPARATOR = re.compile(r'^---$')
RE_SEGMENT = re.compile(
    r'^\[(\d{1,2}:\d{2}(?::\d{2})?) - (\d{1,2}:\d{2}(?::\d{2})?)\] \[scene:([a-z0-9-]+)\]$'
)
RE_VISUAL = re.compile(r'^VISUAL:\s*(.+)$')
RE_AUDIO = re.compile(r"^AUDIO:\s*'(.*)'$")
# A segment's first FRAME line is the frame at its start. Further FRAME lines
# carry an "@MM:SS" suffix: frames taken inside a long segment.
RE_FRAME = re.compile(r'^FRAME:\s*(.+?)(?:\s+@(\d{1,2}:\d{2}(?::\d{2})?))?$')

INT_METADATA_FIELDS = {'frames_extracted'}


def write_avt(metadata: dict, segments: list, output_path: str):
    """
    Write an .avt file.

    Args:
        metadata: dict with title, channel, duration, source, analyzed, model,
                  frames_extracted, transcript_source
        segments: list of dicts with start, end, scene, visual, audio, frame (or None)
        output_path: path to write the .avt file
    """
    lines = [FORMAT_HEADER, '', '[metadata]']

    field_order = ['title', 'channel', 'duration', 'source', 'analyzed',
                   'model', 'frames_extracted', 'transcript_source']
    for field in field_order:
        value = metadata.get(field, '')
        lines.append(f"{field}: {value}")

    lines.append('')
    lines.append('---')

    for seg in segments:
        lines.append('')
        lines.append(f"[{seg['start']} - {seg['end']}] [scene:{seg['scene']}]")
        lines.append(f"VISUAL: {seg['visual']}")

        audio = seg.get('audio', '')
        escaped_audio = audio.replace("'", "\\'")
        lines.append(f"AUDIO: '{escaped_audio}'")

        if seg.get('frame'):
            lines.append(f"FRAME: {seg['frame']}")
        for extra in seg.get('extra_frames') or []:
            lines.append(f"FRAME: {extra['path']} @{extra['timestamp']}")

    lines.append('')

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def parse_avt(file_path: str) -> dict:
    """Parse an .avt file into structured dict."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return parse_avt_content(content)


def parse_avt_content(content: str) -> dict:
    """Parse .avt content string into structured dict."""
    lines = content.split('\n')

    if not lines or not RE_HEADER.match(lines[0].strip()):
        raise ValueError(f"Invalid .avt file: missing AGENTIC-VT header")

    version_match = RE_HEADER.match(lines[0].strip())
    version = version_match.group(1)

    if version != FORMAT_VERSION:
        raise ValueError(f"Unsupported .avt version: {version} (expected {FORMAT_VERSION})")

    # Parse metadata
    metadata = {}
    in_metadata = False
    separator_idx = None

    for i, line in enumerate(lines[1:], 1):
        line = line.strip()
        if line == '[metadata]':
            in_metadata = True
            continue
        if RE_SEPARATOR.match(line):
            separator_idx = i
            break
        if in_metadata:
            m = RE_METADATA.match(line)
            if m:
                key, value = m.group(1), m.group(2)
                if key in INT_METADATA_FIELDS:
                    try:
                        value = int(value)
                    except ValueError:
                        pass
                metadata[key] = value

    if separator_idx is None:
        raise ValueError("Invalid .avt file: missing --- separator")

    # Parse segments
    segments = []
    current_segment = None

    for line in lines[separator_idx + 1:]:
        line_stripped = line.strip()

        if not line_stripped:
            if current_segment:
                segments.append(current_segment)
                current_segment = None
            continue

        seg_match = RE_SEGMENT.match(line_stripped)
        if seg_match:
            if current_segment:
                segments.append(current_segment)
            current_segment = {
                'start': seg_match.group(1),
                'end': seg_match.group(2),
                'scene': seg_match.group(3),
                'visual': '',
                'audio': '',
                'frame': None,
                'extra_frames': [],
            }
            continue

        if current_segment:
            vis_match = RE_VISUAL.match(line_stripped)
            if vis_match:
                current_segment['visual'] = vis_match.group(1)
                continue

            aud_match = RE_AUDIO.match(line_stripped)
            if aud_match:
                current_segment['audio'] = aud_match.group(1).replace("\\'", "'")
                continue

            frame_match = RE_FRAME.match(line_stripped)
            if frame_match:
                path, at = frame_match.group(1), frame_match.group(2)
                if at:
                    current_segment['extra_frames'].append({'path': path, 'timestamp': at})
                else:
                    current_segment['frame'] = path
                continue

    # Don't forget last segment
    if current_segment:
        segments.append(current_segment)

    return {
        'version': version,
        'metadata': metadata,
        'segments': segments,
    }


def align_transcript_to_segments(transcript_segments: list, visual_segments: list) -> list:
    """
    Align transcript text to visual segment time ranges.

    Takes transcript segments (from VTT/Whisper) and visual segments (from Gemini),
    assigns the appropriate audio text to each visual segment.

    Returns: updated visual segments with 'audio' field populated.
    """
    from frames import timestamp_to_seconds

    for vis_seg in visual_segments:
        vis_start = timestamp_to_seconds(vis_seg['start'])
        vis_end = timestamp_to_seconds(vis_seg['end'])

        texts = []
        for t_seg in transcript_segments:
            t_start = t_seg.get('start_seconds', timestamp_to_seconds(t_seg['start']))
            t_end = t_seg.get('end_seconds', timestamp_to_seconds(t_seg['end']))

            if t_start < vis_end and t_end > vis_start:
                texts.append(t_seg['text'])

        vis_seg['audio'] = ' '.join(texts) if texts else ''

    return visual_segments
