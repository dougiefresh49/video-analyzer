# Fork changes

This is a modified fork of **[docusphere/video-analyzer](https://github.com/docusphere/video-analyzer)**
by **Frank Nillard**, used under its Source-Available Non-Commercial license and
redistributed under that same license (clause 5, share-alike).

All original copyright, license, trademark, and attribution notices are intact.
The `.avt` format, the "Agentic Video Transcript" name, and the `AGENTIC-VT`
identifier remain Frank Nillard's trademarks — this fork implements the format,
it does not claim authorship of it.

This file exists to satisfy clause 1 ("indicate if changes were made"). Below is
what changed and why.

## Changes

### Newer Gemini model, configurable

`gemini-2.5-flash` was hardcoded. The model is now `gemini-3.6-flash` by
default and overridable with `GEMINI_MODEL`, read from the shell environment or
`~/.config/video-analyzer/.env` the same way API keys are.

### Media resolution control (`GEMINI_MEDIA_RESOLUTION`)

Video is downsampled on ingest. At the default setting, small UI text in a
screen recording is illegible — in testing, Gemini described a Figma mockup as
if it were a shipped web app. `high` reads it correctly. Costs roughly 290
input tokens per second of video at `high`.

### Request timeout (`GEMINI_TIMEOUT`)

**Bug fix.** `generate_content` had no timeout. The existing `POLL_TIMEOUT`
only guards the upload poll loop, so a stalled analysis call hung indefinitely
with no output and no error — observed once for over two hours on a 51-minute
video. Now defaults to 900s and raises an actionable `TimeoutError` suggesting
a longer timeout, a lower media resolution, or splitting the video.

### Long videos are analyzed in chunks (`GEMINI_CHUNK_SECONDS`)

A single `generate_content` call covered the whole file, so a 38-minute video
at `high` media resolution was one ~670k-token request that hit the timeout.
Videos longer than `GEMINI_CHUNK_SECONDS` (default 780, i.e. 13 minutes) are
now split with ffmpeg into keyframe-aligned chunks (stream copy, no re-encode),
analyzed up to three at a time, and stitched back together with every
timestamp shifted onto the full-video timeline. The output is still one `.avt`
file with global timestamps; captions, frames, and the usage sidecar are
unaffected apart from a `chunks` count in the latter. `--start/--end` skip
chunks entirely outside the range.

### YouTube 403 fallbacks

YouTube gates its adaptive (DASH) formats behind a PO token and refuses them
with HTTP 403 to a stock `yt-dlp`. A format selector with `/` alternatives only
falls through on *selection* failure, not on a download 403, so the pipeline
died at step 1. The download now retries in order: 720p DASH, progressive
(muxed) formats, then the Android innertube client (not PO-token gated as of
yt-dlp 2026.07, 720p, same caption tracks). Partial `.part`/`.ytdl` files left
by refused attempts are never mistaken for the video.

### Per-line VTT deduplication

**Bug fix.** YouTube's rolling auto-captions show each line three times: it
scrolls in as a cue's second line, appears alone in a filler cue, then scrolls
out as the next cue's first line. The cues are all textually different, so
deduplicating identical *cues* left every line tripled — a real 51-minute
transcript parsed to 33,477 words instead of 11,162. Deduplication is now
per-line. Covered by a regression test.

### Native captions preferred over Gemini's transcript

`--transcript` gained `auto` (now the default) and `captions`. When the source
has a caption track, it is used: verbatim, complete, already downloaded, and
free. Gemini's in-pass transcript is the fallback.

This matters because Gemini elides. On one 13-minute clip its transcript kept
391 words where the captions had ~2,980, truncating every segment with an
ellipsis despite the prompt forbidding paraphrase. Gemini's real value here is
the visual analysis, which is unaffected.

Note that auto-captions mistranscribe technical terms (Codex → "codecs", Grok →
"gro", GUI → "guey"). Verify identifiers against the extracted frames.

### Transcript source, and a silent failure

Gemini's understanding pass now runs before transcription, and an empty
transcript logs a warning instead of passing silently. Previously a local file
with no Whisper key produced a complete-looking `.avt` containing no audio at
all.

### Token usage logging

`response.usage_metadata` is captured and written to a `<slug>.usage.json`
sidecar next to the `.avt`, including a `tokens_per_video_second` figure that
makes cost predictable for any video length. A sidecar rather than `.avt`
metadata, because the `.avt` spec has a fixed field set and this is run
bookkeeping, not transcript data.

### Higher-resolution frames

`--frame-width` (default 1280, was 256) so frames stay legible enough to read
file names and UI labels. `--low-res` restores the old 256px behavior.

### Extra frames inside long segments (`--frame-interval`)

One frame was taken per segment, at its start. Gemini often emits a single
segment for a multi-minute screen recording (a five-minute scroll through an
`AGENTS.md`, a chat reply that renders 30 seconds in), so the only frame showed
the first screen and the numbers read aloud later were never on any frame.
Segments now also get `floor(length / interval)` evenly spaced extra frames
(default interval 45 s; a 46 s segment gets one at its midpoint, a 281 s
segment gets six). Start frames are capped to `--max-frames` first; extras
fill the remaining budget. In the `.avt`, extras follow the segment's start
frame as additional `FRAME: <path> @MM:SS` lines; the first `FRAME:` line is
unchanged, so readers that only look at one frame per segment see what they
saw before.

### Prompt

Rewritten to request near-verbatim audio and to read on-screen UI chrome —
open file names, active tabs, canvas element labels, selected layers — quoted
exactly, so those names can be looked up in the source tool afterward.
