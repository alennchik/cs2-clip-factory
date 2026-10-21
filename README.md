# CS2 Clip Factory

Turns Twitch CS2 clips from streamers who allowed it into vertical 9:16 videos for TikTok:
game on top, streamer webcam below, a quoted hook line in the upper third, loudness
normalized. Every video goes through a Telegram review before you post it yourself.

```
Twitch Helix (metadata only)
  └─ discover ─▶ SQLite: ranked clips
       └─ select: listed streamers only, skip duplicates / same moment
            └─ fetch (yt-dlp) ─▶ transcribe (faster-whisper | mlx) ─▶ hooks ─▶ render (FFmpeg)
                 └─ Telegram: ✅ approve · ❌ reject · Хук 1/2/3 · ✏️ own hook
                      └─ out/ready/*.mp4 + caption .txt ─▶ you post in the TikTok app
```

Local-first and free to run: no paid APIs, no cloud.

## Quick start (macOS)

```bash
brew install python@3.12 ffmpeg
git clone <your private repo> cs2-clip-factory && cd cs2-clip-factory
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,asr]"        # Apple Silicon: also ".[mlx]" and set transcribe.backend: mlx
clipfactory init                    # creates .env, config/streamers.yaml, data/
clipfactory doctor                  # tells you what's still missing
```

Try a render right away — no keys needed, any clip file works:

```bash
clipfactory render-file ~/Downloads/clip.mp4 \
  --webcam 0.72,0.70,0.28,0.30 \
  --hook "Electronic: я бы замашнил" --credit "twitch.tv/streamer"
# -> out/test/clip_9x16.mp4
```

If `doctor` reports missing ffmpeg filters, install a fuller build (`brew install ffmpeg-full`)
and point `FFMPEG_BIN` / `FFPROBE_BIN` in `.env` at it.

### Twitch app (metadata)

1. <https://dev.twitch.tv/console/apps> → **Register Your Application**.
2. OAuth Redirect URL: `http://localhost` (not used by this app), Client Type: Confidential.
3. Copy the Client ID, create a Client Secret, put both into `.env`.

Only an app access token is used: it reads clip metadata (Get Users, Get Games, Get Clips).

### Telegram bot (review)

1. In Telegram, `@BotFather` → `/newbot` → put the token into `.env` as `TELEGRAM_BOT_TOKEN`.
2. Open your bot, press **Start**, then run `clipfactory review chat-id` and put the number into
   `TELEGRAM_CHAT_ID`. The bot ignores everyone else.

### Streamers

Edit `config/streamers.yaml`. For each streamer:

- `login` — only listed streamers are downloaded. Optional `permission_note` / `permission_proof`
  are for your records; `permission: denied` pauses a streamer without deleting the block.
- `webcam:` — the webcam box in fractions of the frame. After the first fetch run
  `clipfactory calibrate <login>`: it saves a frame with a 10% grid (and a suggested box with
  `pip install -e ".[vision]"`), then check it with `render-file ... --end 6`.

## Daily use

```bash
clipfactory run            # discover -> fetch -> transcribe -> render -> send drafts to Telegram
clipfactory review run     # keep running: handles the buttons in Telegram
```

In Telegram: **✅** exports the final file to `out/ready/` and sends it back as a document with a
ready caption (hook, `twitch.tv/<login>` credit, hashtags). Save it on the phone, post in TikTok,
press **📤 Опубликовал**. **Хук 1/2/3** or **✏️** re-render with another hook.

## Commands

| Command | What it does |
| --- | --- |
| `init` | Create `.env`, `config/streamers.yaml`, data folders, database |
| `doctor` | Check ffmpeg, encoders, yt-dlp, font, speech-to-text, secrets, streamers |
| `discover` | Fetch clip metadata for whitelisted streamers, rank, store |
| `fetch [--limit N] [--clip ID]` | Download top clips of listed streamers |
| `transcribe` / `render` | Run single steps; `render --clip ID --hook "..." --start --end` |
| `run [--limit N] [--no-discover] [--no-review]` | Whole pipeline |
| `review send` / `review run` / `review chat-id` | Telegram review queue |
| `status` | Counts per state and the best clips waiting |
| `calibrate LOGIN [--file F] [--box x,y,w,h]` | Find a streamer's webcam box |
| `render-file FILE [--webcam ...] [--hook ...]` | Render any local file, no database |
| `cleanup [--days 14] [--dry-run]` | Delete old source files and discarded renders |

## Layout and text

- 1080×1920, H.264 + AAC 48 kHz, −14 LUFS, ≤ 45 s (longer clips keep the ending, shifted to
  include the hook line), ≤ 7 Mbit/s so files fit Telegram's 50 MB bot limit.
- `split`: webcam 1080×770 over game 1080×1150 (center crop, `game_center_x`);
  `webcam_position: bottom` swaps them.
  `full` (no webcam box): blurred background with the whole frame in the middle.
- Hook: Inter Display Black, yellow `#FFE600`, black outline, max width 840, centered on the
  webcam/game boundary (`hook_position: middle`; `top` puts it at y = 300). Shown for
  `hook_seconds` (6 s); the `twitch.tv/<login>` credit takes its place afterwards.
  Optional word captions above the split line (`captions: true`).

## Data

`data/` (git-ignored): `clipfactory.sqlite3`, `raw/` downloads, `transcripts/`, `renders/`.
`out/ready/` holds approved videos with their captions. Every step writes an audit event
(`events` table).

## Scope

v0.1 covers discovery, download, transcription, hooks, rendering and human
review. Posting stays manual in the TikTok app. Not included: sponsor or ad insertion,
auto-posting, multi-account management, paid APIs. Next steps: [docs/ROADMAP.md](docs/ROADMAP.md).

## Development

```bash
pytest                                # unit + ffmpeg integration tests (synthetic clips)
ruff check . && ruff format --check .
```

CI runs both on every push and pull request. The bundled font is licensed under the SIL OFL
(`assets/fonts/OFL.txt`).
