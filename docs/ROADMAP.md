# Roadmap

## Where we are (v0.1)

Discovery (Helix metadata), ranking, permission-gated download, speech-to-text, hook
suggestions, 9:16 render (split / full), Telegram review, export with caption, CI.

## Phase 0 — rights and access (owner)

- [ ] Screenshot each streamer's "ok" into `permissions/<login>.png`; fill `config/streamers.yaml`
- [ ] Register the Twitch app; create the Telegram bot; `clipfactory doctor` all green

## Acceptance for v0.1 on the Mac

- [ ] 10 videos rendered from real clips
- [ ] Hook readable on a phone screen (check in Telegram before approving)
- [ ] Nothing downloaded outside the whitelist (`events` table: every `fetch` has a proof)
- [ ] A second `clipfactory run` creates no duplicate downloads or renders
- [ ] Under 1 minute render per clip

## Next issues (one PR each)

1. **First real renders** — run on 10 real clips, tune defaults (hook size and position,
   `game_center_x`, `hook_seconds`). Done when the acceptance list above is checked.
2. **Webcam calibration for every streamer** — `calibrate` + `render-file --end 6` checks,
   boxes saved in config.
3. **Speech-to-text choice** — compare faster-whisper `small` / `large-v3-turbo` and mlx `turbo`
   on 10 Russian clips: time per clip and how many hook lines are recognized correctly.
4. **Hook evaluation set** — 30 clips with the hook you would pick by hand
   (`tests/data/hooks_eval.yaml`); metric: top-3 hit rate; tune weights in `hooks.py`.
5. **Review bot extras** — trim buttons (−2 s start / +2 s end), `/queue`, re-send button.
6. ~~**Schedule**~~ — done: `clipfactory schedule install` (launchd). Log rotation still to do.
7. **Analytics v0** — record TikTok results per posted video (views, average watch time,
   followers gained) by hand or from a TikTok Studio export; weekly report by streamer and
   hook type.
8. **Official download path** — Helix *Get Clips Download* for channels where you are an
   editor (user OAuth with `editor:manage:clips`), replacing yt-dlp for those streamers.
9. **Retention** — run `cleanup --days 14` weekly.

## Phase 3 — validate the audience before scaling

Post 1–3 videos a day for 2–3 weeks. Watch average watch time, share of viewers who stay past
the first seconds, and followers per 1,000 views. Keep what works (streamer, hook style,
length), drop the rest. Scale volume or add Reels/Shorts only if the numbers justify it.
