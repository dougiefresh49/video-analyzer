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

### Prompt

Rewritten to request near-verbatim audio and to read on-screen UI chrome —
open file names, active tabs, canvas element labels, selected layers — quoted
exactly, so those names can be looked up in the source tool afterward.
