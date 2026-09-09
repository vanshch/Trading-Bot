# AGENTS.md

## Session Initialization (Mandatory First Step)
At the start of every session, read `README.md` (and `experience.md` for operational tasks) before running commands, diagnostics, or proposing edits. Treat `README.md` as the authoritative source of truth.

## Tech Stack & Runtime
- Python 3.12, FFmpeg, Streamlink (HLS live audio capture), Groq Whisper API (`whisper-large-v3`), Google Gemini API.
- Production: `trade-bot` systemd service on GCE VM `trade-bot-vm` (`us-central1-a`).
- Network: Local Cloudflare WARP daemon on VM (`socks5://127.0.0.1:40000`) for YouTube rate-limit (HTTP 429) avoidance.

## Commands
- Run test suite: `python -m unittest discover tests`
- Run single test: `python -m unittest tests.test_<name>`
- Run bot locally: `python main.py --config config.yaml --env .env`
- Bounded test: `python main.py --config config.yaml --env .env --test-minutes 5`
- Service check (VM): `systemctl status trade-bot.service`
- Service logs (VM): `journalctl -u trade-bot -n 100 --no-pager`

## Core Boundaries
- ALWAYS keep `README.md` updated in the same change if architecture, defaults, proxies, or dependencies change.
- NEVER commit secrets, API keys, `.env`, audio dumps, or runtime log artifacts.
- NEVER use personal YouTube account cookies (risks Google account suspension). Use WARP SOCKS5 proxy instead.
- On Windows executing remote commands via `gcloud compute ssh`: Base64-encode payloads to prevent quote/escape stripping.
- Do NOT poll or sleep in loops when commands run asynchronously; wait for harness task completion notifications.
