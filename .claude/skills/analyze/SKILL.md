---
name: analyze
description: Analyze any video (URL or local file). Downloads with yt-dlp, uploads to Gemini 2.5 Flash for native video understanding, extracts transcript (captions or Whisper fallback), pulls JPEG frames at AI-identified key moments, and produces a structured .avt (Agentic Video Transcript) file for agent consumption.
argument-hint: "<video-url-or-path>"
allowed-tools: Bash, Read, AskUserQuestion
user-invocable: true
---

# /analyze — Produce an Agentic Video Transcript

Takes any video URL (YouTube, Vimeo, X, TikTok, 400+ sites) or local file and produces a `.avt` file — a structured, plain-text document combining timestamped transcripts, AI-generated visual descriptions, scene tags, and frame file references. Designed for downstream agent consumption.

## Step 0 — Setup preflight (runs every `/analyze` invocation, silent on success)

Before every `/analyze` run, first ensure Python dependencies are installed, then verify system dependencies and API keys:

```bash
pip install -r "${CLAUDE_PROJECT_DIR}/requirements.txt" --quiet 2>/dev/null
python3 "${CLAUDE_PROJECT_DIR}/scripts/preflight.py" --check
```

The `pip install` is idempotent and silent when deps are already installed. On exit 0 from the preflight check, proceed to Step 1 without comment. **Do NOT announce "setup is complete" to the user.**

On non-zero exit from preflight, follow the table:

| Exit | Meaning | Action |
|------|---------|--------|
| `2` | Missing binaries (`ffmpeg` / `ffprobe` / `yt-dlp`) | Run installer |
| `3` | No `GOOGLE_API_KEY` | Run installer to scaffold `.env`, then ask user for key |
| `4` | Both missing | Run installer, then ask for key |

The installer is idempotent:

```bash
python3 "${CLAUDE_PROJECT_DIR}/scripts/preflight.py"
```

On macOS with Homebrew, it auto-installs `ffmpeg` and `yt-dlp`. It scaffolds `~/.config/video-analyzer/.env` with commented placeholders at `0600` permissions.

**If `GOOGLE_API_KEY` is still missing after install:** use `AskUserQuestion` to ask the user for their Gemini API key (get one at https://aistudio.google.com/apikey). Write it into `~/.config/video-analyzer/.env`.

**Optional Whisper keys:** If the user wants transcript fallback for videos without native captions, ask whether they have a Groq API key (preferred — faster, cheaper) or OpenAI key. Write it to the same `.env` file. If they don't want Whisper, proceed with `--no-whisper` and inform them that videos without native captions will produce empty transcript segments.

Within a single session, skip Step 0 on follow-up `/analyze` calls once `--check` has returned 0.

## When to use

- User pastes a video URL and wants it analyzed or broken down.
- User points at a local video file and wants structured analysis.
- User types `/analyze <url-or-path>`.
- Any time an agent needs structured video understanding for downstream processing.

## How to invoke

**Step 1 — Parse user input.** Separate the video source (URL or file path) from any question or instruction the user included. Example: `/analyze https://youtu.be/abc what are the best learning moments?` → source = `https://youtu.be/abc`, question = `what are the best learning moments?`. If the user included flags like `--start 2:30`, pass them to the script.

**Step 2 — Run the analyzer.**

Run with a 10-minute timeout (the maximum allowed):

```bash
python3 "${CLAUDE_PROJECT_DIR}/scripts/analyze.py" "<source>" --out-dir .
```

Use `timeout: 600000` on the Bash tool call.

**If the command times out:** The script saves checkpoints automatically. Simply re-run the exact same command with `run_in_background: true`. It will resume from where it left off (skipping download and transcript if already completed). You will be notified when it finishes.

**If it times out again in background mode:** This should not happen (background has no timeout), but if the process fails, re-run — checkpoints ensure no work is repeated.

Optional flags:
- `--max-frames N` — Cap on extracted frames (default: 80)
- `--frame-interval N` — One extra frame per N seconds of a long segment, evenly spaced inside it (default: 45; 0 = a frame at each segment start only)
- `--start T` — Focus start time (SS, MM:SS, or HH:MM:SS). Zooms into a section.
- `--end T` — Focus end time (SS, MM:SS, or HH:MM:SS). Use with --start for a range.
- `--low-res` — Use 256px frame width instead of 512px (smaller output)
- `--no-whisper` — Disable Whisper fallback (frames + visuals only if no captions)
- `--whisper groq|openai` — Force a specific Whisper backend
- `--force-long` — Allow videos over 90 minutes (default: rejected with warning)
- `--out-dir DIR` — Output directory (default: current directory)

**Step 3 — Report results.** The script prints the `.avt` file path to stdout and progress to stderr. Once complete:

1. Read the `.avt` file to get the structured breakdown.
2. Report to the user:
   - Video title and duration
   - Number of segments identified
   - Number of frames extracted
   - Transcript source (captions, whisper-groq, whisper-openai, or none)
   - A brief summary of the video's content structure (key scenes, transitions, topics covered)

If the user asked a specific question about the video, answer it using the segment data (VISUAL descriptions, AUDIO transcript, scene tags).

**Step 4 — Optionally read frames.** If the user needs visual detail about a specific moment, use `Read` on the relevant frame file from the `frames/` directory. Frame paths are listed in the `.avt` file's FRAME lines.

## The .avt output format

```
AGENTIC-VT 1.0

[metadata]
title: Video Title Here
channel: Channel Name
duration: 08:36
source: https://youtube.com/watch?v=abc123
analyzed: 2026-05-16 14:30:00
model: gemini-2.5-flash
frames_extracted: 24
transcript_source: captions

---

[00:00 - 00:04] [scene:intro]
VISUAL: Man sitting at desk, ring light behind. Dark room, low-budget setup.
AUDIO: 'What\'s up guys, today I want to show you something.'
FRAME: frames/frame-001.jpg

[00:04 - 00:15] [scene:screen-recording]
VISUAL: VS Code with Claude Code terminal in bottom panel. Dark theme.
AUDIO: 'The first thing you need to do is install this package.'
FRAME: frames/frame-002.jpg
```

Each segment contains:
- **Timestamp range** — when this segment occurs
- **Scene tag** — content type (intro, talking-head, screen-recording, demo, tutorial, slide, b-roll, etc.)
- **VISUAL** — AI-generated description of what's on screen
- **AUDIO** — Transcript text for this time range (single quotes, escaped internal quotes)
- **FRAME** — Relative path to extracted JPEG (optional — not every segment has a frame). A segment's first `FRAME:` line is taken at its start; long segments may have further `FRAME: <path> @MM:SS` lines for frames taken inside the segment — check those when the start frame doesn't show what the AUDIO is describing.

## Failure modes and handling

- **Preflight failed** → Run the installer, ask for missing keys via `AskUserQuestion`.
- **Download fails** → yt-dlp error goes to stderr. If it's login-required, age-restricted, or region-locked, tell the user plainly. Do not retry auth failures.
- **Video too large (>2 GB)** → Script rejects before upload. Tell the user the file exceeds Gemini's limit.
- **Video too long (>90 min)** → Script rejects with warning. Offer `--force-long` if they want to proceed.
- **Gemini processing timeout (>5 min)** → Tell the user the video took too long to process. Suggest trying a shorter video or a lower-quality download.
- **No transcript available** → Captions missing AND Whisper unavailable. The `.avt` file will have empty AUDIO lines. Visual descriptions and scene tags still work. Inform the user.
- **Whisper fails** → Error printed to stderr. Proceed without transcript. Offer to retry with the other backend (`--whisper openai` if Groq failed, or vice versa).

## Cost

- **Gemini 2.5 Flash:** ~$0.05 per 10-minute video (native video understanding)
- **Whisper (if needed):** ~$0.01/minute of audio
- Most YouTube videos have native captions, so Whisper is rarely triggered.

## Security and permissions

**What this skill does:**
- Runs `yt-dlp` locally to download the video (public data, direct request to video host)
- Runs `ffmpeg`/`ffprobe` locally to extract frames as JPEGs and audio for Whisper
- Uploads the video file to Google's Gemini Files API for visual analysis, then deletes it immediately after
- Sends extracted audio to Groq (`api.groq.com`) or OpenAI (`api.openai.com`) for Whisper transcription when native captions are unavailable
- Writes output (`.avt` file + `frames/` directory) to the specified output directory
- Reads/creates `~/.config/video-analyzer/.env` (mode `0600`) to store API keys

**What this skill does NOT do:**
- Does not access any platform account (no login, no cookies, no posting)
- Does not share API keys between providers
- Does not log or print API keys to stdout, stderr, or output files
- Does not persist downloaded videos — temp files are deleted after analysis
- Does not retain files on Gemini — uploaded video is deleted in a `finally` block regardless of success or failure

**Bundled scripts:** `scripts/analyze.py` (orchestrator), `scripts/preflight.py` (dependency checker), `scripts/download.py` (yt-dlp wrapper), `scripts/transcribe.py` (caption extraction), `scripts/whisper.py` (Groq/OpenAI Whisper clients), `scripts/understand.py` (Gemini video analysis), `scripts/frames.py` (ffmpeg frame extraction), `scripts/avt.py` (format writer/parser)
