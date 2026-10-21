# CLAUDE.md

CS2 Clip Factory: permitted Twitch CS2 clips → reviewed 9:16 TikTok videos. Read README.md first.
Talk to the owner (Alen) in Russian; code, identifiers, commits and docs in English.

## Commands

- Install: `pip install -e ".[dev,asr]"` (Apple Silicon: add `mlx`)
- Tests: `pytest` (ffmpeg integration tests use synthetic clips from `tests/conftest.make_clip`)
- Lint: `ruff check . && ruff format --check .`
- Manual check of a layout: `clipfactory render-file <clip.mp4> --webcam x,y,w,h --hook "..."`

## Map of src/clipfactory

| Module | Role |
| --- | --- |
| `cli.py` | click commands; thin, no business logic |
| `settings.py` | secrets and paths from `.env` / environment |
| `config.py` | `config/streamers.yaml` models (whitelist, permission, webcam box, knobs) |
| `db.py` | SQLite schema and helpers; `models.py` has the state enums |
| `twitch.py`, `discover.py`, `ranking.py` | Helix metadata, ranking heuristic |
| `fetch.py` | download of listed streamers (yt-dlp), checksum, duplicate check |
| `transcribe.py`, `hooks.py` | speech-to-text backends, hook heuristics |
| `layout.py`, `textcard.py`, `render.py` | pure geometry, Pillow text layers, FFmpeg command |
| `review.py`, `telegram.py` | Telegram review bot (owner-only) |
| `pipeline.py` | glue: select → fetch → transcribe → hooks → render → export |
| `calibrate.py`, `doctor.py` | webcam calibration, environment checks |
| `schedule.py` | macOS launchd autostart (bot keep-alive, daily runs) |

## Rules

- Media is downloaded only through `fetch.fetch_clip`, and only for streamers listed in
  `config/streamers.yaml` (not marked `unknown`/`denied`). Keep that whitelist check.
- Secrets live only in `.env`. Never log or print tokens; keep the `httpx` logger at WARNING
  (Telegram API URLs contain the bot token). Never commit `.env` or `permissions/`.
- Steps stay idempotent: status changes go through `db` helpers, every step logs `db.log_event`.
- A human approves every video in Telegram; posting stays manual.
- Pure logic gets unit tests; anything that shells out to ffmpeg gets an integration test.
- One feature per branch, small PRs, CI green before merge.

## Out of scope for v0.1

Sponsor or ad insertion, auto-posting, multi-account management, paid APIs.

## Next work

See `docs/ROADMAP.md`. Ask the owner before spending money, contacting streamers, publishing,
or changing any account settings.
