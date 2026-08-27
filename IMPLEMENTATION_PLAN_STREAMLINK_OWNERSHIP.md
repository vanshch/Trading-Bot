# Implementation Plan: Streamlink-Owned YouTube Audio Resolution

## Objective

Move production YouTube audio delivery onto the transport proven by the isolated
three-minute trial: yt-dlp validates liveness, the public YouTube watch URL is
passed to Streamlink's YouTube plugin, and FFmpeg only decodes Streamlink stdout.
Do not route YouTube audio through an already-resolved signed Googlevideo URL.

## Evidence and acceptance criteria

- Previous generic `hls://<signed-googlevideo-url>` trials ended at approximately
  55 seconds with four HTTP 403 segment failures and about 30.5% coverage.
- The Streamlink-plugin ownership trial delivered exactly 180 seconds, 100%
  coverage, eighteen complete WAV chunks, zero 403s, and zero 429s.
- Production must pass the original YouTube URL to Streamlink without an `hls://`
  prefix, use a three-segment live edge, and keep FFmpeg stdin decoding free of
  `-re`/`-readrate` throttling.
- A YouTube Streamlink startup/runtime failure must return to the outer supervised
  restart/resolution loop; it must not fall back to the signed direct media URL.
- Direct FFmpeg fallback remains available only for non-YouTube inputs where it is
  an intentional supported transport.
- Focused ownership/failure tests and the full offline suite must pass.
- README and `experience.md` must explain the ownership rule and reusable lesson.

## Protected paths and state

- Never read, print, edit, stage, or commit `.env`, credentials, tokens, or keys.
- Preserve all pre-existing untracked `data/`, `research/`, and scratch artifacts.
- Do not edit prior implementation plans except this new plan.
- Do not install dependencies globally or upgrade unrelated dependencies.
- Do not call live YouTube, Groq, Gemini, Telegram, or other provider APIs during
  delegated implementation or tests.
- Do not stage, commit, push, deploy, or modify the server during delegation.

## 1. Make the provider plugin own YouTube playback resolution

1. Add robust URL classification for public YouTube watch/live URLs using parsed
   hostnames rather than substring matching.
2. Keep yt-dlp/`StreamManager` responsible for liveness and broadcast identity,
   while passing the canonical public YouTube URL—not the signed resolved media
   URL—to `AudioStreamManager` and Streamlink.
3. Configure the Streamlink HLS transport to the proven three-segment live edge.
   Preserve bounded retries and buffering without introducing an FFmpeg `-re` or
   `-readrate` on Streamlink stdin.
4. For YouTube inputs, fail back to the outer supervised restart loop when the
   Streamlink plugin fails at startup, exits, stalls, or reaches EOF. Never invoke
   direct FFmpeg against the signed Googlevideo URL for this provider.
5. Preserve the existing one-shot direct FFmpeg fallback for non-YouTube inputs.
6. Keep failure classification, sanitized diagnostics, deterministic cleanup,
   restart backoff, and the existing HTTP 429 cooldown behavior intact.
7. Add focused regression tests proving the positive ownership path and negative
   no-direct-fallback guarantee at startup and runtime, while retaining coverage
   for non-YouTube fallback.
8. Update README architecture notes and append a dated, generalizable section to
   `experience.md` covering URL ownership, ephemeral signed URLs, failure-boundary
   live probes, provider-aware fallbacks, and FFmpeg live-input throttling.
