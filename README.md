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

## Automatic webcam detection (opt-in)

Streamers sometimes move their webcam between broadcasts. When enabled, the render pipeline
checks five frames from each **selected clip window** and only uses a split layout when
OpenCV consistently finds a peripheral face in at least three frames. If the location is
unstable, no face is found, or OpenCV is missing, it uses the **full** layout instead
of blindly cropping the old webcam position.

Install the optional face detector:

```bash
pip install -e ".[vision]"
```

Add the flag under the `render:` section of the local, git-ignored
`config/streamers.yaml`:

```yaml
render:
  auto_webcam: true
```

Keep the **previously validated** `webcam: {x, y, w, h}` coordinates where available.
The detector reuses those dimensions when the camera moves. Streamers without a saved
webcam can also be detected (with less reliable crop sizing). The YAML is never
modified, and an explicitly requested `layout: full` remains full.

Try a private local test before enabling it for the scheduler:

```bash
clipfactory render-file data/raw/keliencs_private_test.mp4 \
  --webcam 0,0,0.24,0.235 --auto-webcam --end 6 \
  --out out/test/keliencs_auto_webcam.mp4
```

Check the result visually. This is an **experimental face-based crop estimate**,
not a trained webcam-boundary detector: it can fail when the streamer turns away,
a game scene contains a face, a border moves, or a camera size changes. For these cases
the safe fallback is full video, not a guessed crop. It also cannot follow a webcam
that moves halfway through one clip.

## Optional sponsor integration

You can enable a ~5.8-second MP4 sponsor interlude in the final vertical clip before
Telegram review. This is disabled by default. See
[the sponsor insertion guide](docs/SPONSOR_INSERTION.md) for setup and limitations.

## Autostart (macOS)

```bash
clipfactory schedule install                  # bot always on + runs at 12:00 and 20:00, 3 clips each
clipfactory schedule install --at 10:00 --limit 5   # change times / volume (re-run to update)
clipfactory schedule status | now | uninstall
```

The bot then runs in the background (restarted at login and if it stops), and the pipeline runs
on schedule; a run missed while the Mac was asleep starts when it wakes. Logs:
`data/logs/bot.log`, `data/logs/run.log`. Don't start `review run` by hand while it's installed.

### Create one clip from Telegram (without disabling the schedule)

After installing the scheduler, send **/start** to your Telegram bot to show its
persistent **🎬 Создать ролик** button. Tap it (or send **/new**) to ask your
**own Mac** to discover, download, transcribe, render and send up to **one new clip**
for your normal Telegram approval flow. The command acknowledges immediately,
while rendering continues in the background. **📋 Очередь** sends already
rendered drafts, and **📊 Статус** shows the queue counts.

Scheduled and button-triggered runs use the same cross-process file lock, so
pressing the button repeatedly during a run reports that the pipeline is busy
instead of starting duplicate jobs. Manual run output/errors are written to
`data/logs/manual.log`. If no new eligible Twitch clips are available, no new
video is delivered. The Mac must be awake, online and running the Telegram bot;
no cloud worker or paid service is added. To verify setup:

```bash
clipfactory schedule status
# If the two agents are not installed:
clipfactory schedule install --at 12:00 --at 20:00 --limit 3
# Then send /start in Telegram to show the buttons.
```

The local **`clipfactory run --limit 1`** command still works independently;
it shares the same lock with scheduled and Telegram-requested jobs.

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
