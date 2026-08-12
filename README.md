# video-analyzer

> **This is a modified fork** of [docusphere/video-analyzer](https://github.com/docusphere/video-analyzer)
> by Frank Nillard, used and redistributed under its original
> Source-Available Non-Commercial license. See [FORK-CHANGES.md](FORK-CHANGES.md)
> for what was changed. For the original, unmodified project, go upstream.

Analyze any video and produce an `.avt` (Agentic Video Transcript) file — a structured, plain-text format designed for AI agent consumption.

Takes any video URL (YouTube, Vimeo, X, TikTok, 400+ sites) or local file and produces a document combining timestamped transcripts, AI-generated visual descriptions, scene tags, and extracted frame references.

```
Input:  Any video URL or local file
Output: video-slug.avt + frames/ directory
```

## Install

Three ways to install, depending on how you want to use it.

### Option A: Global Install (works from any project)

Clone to your global Claude Code skills directory. After this, `/analyze` is available in every project you open:

```bash
git clone https://github.com/docusphere/video-analyzer.git ~/.claude/skills/analyze
```

### Option B: Project-Local Install (works in one project only)

Clone into a specific project's `.claude/skills/` directory. The skill only works when you're inside that project:

```bash
cd /path/to/your-project
git clone https://github.com/docusphere/video-analyzer.git .claude/skills/analyze
```

### Option C: Plugin Marketplace

If available, install via the Claude Code plugin system:

```
/plugin marketplace add docusphere/video-analyzer
/plugin install analyze@video-analyzer
```

### First run

After installing, type `/analyze` from any project (global) or your project (local):

```
/analyze https://youtube.com/watch?v=VIDEO_ID
/analyze https://vimeo.com/123456789
/analyze https://x.com/user/status/123456789
/analyze /path/to/local-video.mp4
```

On first run, Claude will automatically:
- Install Python dependencies
- Install ffmpeg and yt-dlp (macOS with Homebrew)
- Ask for your Gemini API key and save it securely
- Run the analysis and present results

You'll need a free Gemini API key. Get one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

## What you get

The `.avt` format gives AI agents everything they need to understand a video without watching it:

- **VISUAL** — AI-generated description of what's on screen
- **AUDIO** — Timestamped transcript of what's being said
- **FRAME** — Extracted JPEG frame for deep visual analysis
- **Scene tags** — Content type classification (intro, demo, talking-head, screen-recording, etc.)

### Example output

```
AGENTIC-VT 1.0

[metadata]
title: How I Built X With Claude Code
channel: Some Creator
duration: 08:36
source: https://youtube.com/watch?v=abc123
analyzed: 2026-05-16 14:30:00
model: gemini-2.5-flash
frames_extracted: 24
transcript_source: captions

---

[00:00 - 00:04] [scene:intro]
VISUAL: Man sitting at desk, ring light behind. Dark room setup.
AUDIO: 'What\'s up guys, today I want to show you something.'
FRAME: frames/frame-001.jpg

[00:04 - 00:15] [scene:screen-recording]
VISUAL: VS Code with Claude Code terminal in bottom panel.
AUDIO: 'The first thing you need to do is install this package.'
FRAME: frames/frame-002.jpg
```

Full format spec: [docs/avt-spec.md](docs/avt-spec.md)

## Use Cases

You can append any question or instruction after the URL. Claude analyzes the video first, then answers using the full context (visual descriptions, transcript, and frame images).

### Content research
```
/analyze https://youtube.com/watch?v=abc123 break down the hook — what did they do in the first 10 seconds?
/analyze https://youtube.com/watch?v=abc123 what CTAs did they use and where?
/analyze https://youtube.com/watch?v=abc123 how did they structure this video? what sections did they use?
```

### Learn from videos
```
/analyze https://youtube.com/watch?v=abc123 share me the best moments for learning with the relevant screenshots
/analyze https://youtube.com/watch?v=abc123 summarize every topic covered
/analyze https://youtube.com/watch?v=abc123 explain the key concepts from this tutorial
```

### Production analysis
```
/analyze https://youtube.com/watch?v=abc123 what's their production setup? show me
/analyze https://youtube.com/watch?v=abc123 find every time they show a diagram and show me the screenshots
/analyze https://youtube.com/watch?v=abc123 what's on screen at 3:45?
```

### Zoom into a section
```
/analyze https://youtube.com/watch?v=abc123 --start 0:00 --end 0:30 break down the intro visually
/analyze https://youtube.com/watch?v=abc123 --start 10:00 --end 12:00 what do they demo here?
```

### Debug screen recordings
```
/analyze /path/to/bug-recording.mp4 what happens right before the crash?
/analyze recording.mov --start 0:15 --end 0:25 what state change causes the error?
```

### Compare multiple videos
Analyze several videos, then ask Claude to find patterns across the `.avt` files:
```
/analyze https://youtube.com/watch?v=video1
/analyze https://youtube.com/watch?v=video2
/analyze https://youtube.com/watch?v=video3
```
Then: "Compare these 3 videos — what patterns do you see in their hooks, structure, and CTAs?"

### Meeting and loom notes
```
/analyze https://www.loom.com/share/abc123 give me structured notes from this meeting
/analyze recording.mp4 what action items were discussed?
```

## How it works

1. **Download** — yt-dlp fetches the video (supports 400+ sites via yt-dlp)
2. **Transcribe** — Native captions extracted first, Whisper API fallback if none available
3. **Understand** — Video uploaded to Gemini 2.5 Flash for native video understanding (not frame-by-frame — the model sees the actual video)
4. **Extract** — JPEG frames pulled at AI-identified key moments via ffmpeg
5. **Assemble** — Everything combined into the `.avt` format

## Limits

| Constraint | Value | Override |
|------------|-------|---------|
| Max duration | 90 minutes | `--force-long` |
| Max file size | 2 GB | None (Gemini hard limit) |
| Max frames | 80 | `--max-frames N` |
| Frame resolution | 512px wide | `--low-res` (256px) |
| Gemini processing timeout | 5 minutes | None |
| Supported sites | 1400+ | Via yt-dlp |

**Unsupported inputs:** Age-restricted videos, private/unlisted videos, members-only content, DRM-protected streams (Netflix, Disney+), and live streams over 90 minutes are all rejected with a clear error message.

## Cost

- **Gemini 2.5 Flash:** ~$0.05 per 10-minute video
- **Whisper (only if no captions):** ~$0.01/minute of audio
- Most YouTube videos have native captions, so Whisper is rarely needed

| Video length | Gemini cost |
|---|---|
| 5 min | ~$0.03 |
| 10 min | ~$0.05 |
| 20 min | ~$0.10 |
| 45 min | ~$0.22 |

## Standalone CLI Usage

You can also run video-analyzer directly without Claude Code.

### Prerequisites

- Python 3.11+
- ffmpeg and yt-dlp (`brew install ffmpeg yt-dlp` on macOS)
- Gemini API key

### Setup

```bash
git clone https://github.com/docusphere/video-analyzer.git
cd video-analyzer
pip install -r requirements.txt
python3 scripts/preflight.py  # checks deps + scaffolds config
```

Add your Gemini API key to `~/.config/video-analyzer/.env`:

```
GOOGLE_API_KEY=your-key-here
```

Optional keys for Whisper transcription fallback:
```
GROQ_API_KEY=your-key-here
OPENAI_API_KEY=your-key-here
```

### Run

```bash
python3 scripts/analyze.py https://youtube.com/watch?v=VIDEO_ID
```

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `<source>` | required | Video URL or local file path |
| `--out-dir DIR` | `.` | Output directory |
| `--max-frames N` | 80 | Maximum frames to extract |
| `--start T` | none | Focus start time (SS, MM:SS, or HH:MM:SS) |
| `--end T` | none | Focus end time (SS, MM:SS, or HH:MM:SS) |
| `--no-whisper` | false | Disable Whisper fallback |
| `--whisper groq\|openai` | auto | Force Whisper backend |
| `--low-res` | false | 256px frames (vs 512px default) |
| `--force-long` | false | Allow videos over 90 minutes |

## License

Copyright (c) 2026 Frank Nillard. Source-Available (Non-Commercial).

Free for personal and educational use. Commercial use requires written permission. See [LICENSE](LICENSE) for full terms.

The .avt format, "Agentic Video Transcript" name, and "AGENTIC-VT" identifier are trademarks™ of Frank Nillard.
