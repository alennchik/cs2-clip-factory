# Optional sponsor interlude — first WebM banner

An optional FFmpeg stage adds the **first** animated WebM banner (~6.0 seconds) to the already
rendered 1080×1920 CS2 clip, before the existing Telegram human-review step.
No auto-posting or additional paid service is required. **Off by default.**

## 1. Download the first banner

Use the first WebM asset (not the second H.265 MOV version) from the publisher's
Telegram post: https://t.me/musor_tiktok/125

Save it as `assets/sponsor/first_banner.webm` in your local checkout:

```bash
mkdir -p assets/sponsor
# Move the downloaded WebM file into assets/sponsor/first_banner.webm.
```

The original uploaded banner was verified as VP9 video (1902×1000, with alpha)
and stereo Opus audio, with a measured length of 6.008 seconds. The binary
is **not committed to GitHub**; `.gitignore` excludes local promotional media.
Copy the downloaded original to the asset path on your Mac. No additional file
conversion is necessary.

## 2. Enable the feature

Edit your existing `config/streamers.yaml`. Add this subsection under the
**existing** `render:` section (do not create a second `render:` key):

```yaml
render:
  # Keep your other render settings here.
  sponsor:
    enabled: true
    asset: assets/sponsor/first_banner.webm
    min_clip_seconds: 12
```

The example YAML file is also updated, but your already-created local config
is not automatically overwritten. Launch commands from your repository root.

## 3. Test it

```bash
clipfactory render-file ~/Downloads/clip.mp4 --webcam 0.72,0.70,0.28,0.30 --hook "Test"
python -m pytest -q tests/test_sponsor.py
ruff check . && ruff format --check .
```

- Clips shorter than the threshold are left as-is.
- On longer clips the module chooses a relatively quiet point around 35–60%
  of gameplay. This is an **audio heuristic**, not reliable AI scene detection.
- During the full ~6-second break a dark blurred freeze-frame of the same clip appears behind
  the original banner, with its full animation and sound (if present).
- The full original gameplay and sound survive before and after the break.
- An invalid or missing banner causes a rendering error rather than an
  unsponsored clip mistakenly being presented as sponsored.
- The timestamp and banner duration are logged as `sponsor_at` and
  `sponsor_duration` in the existing render event.
- The normal Telegram manual-approval workflow remains unchanged.

## Restrictions and review

Do not assume a banner meets the ad network's or platform's policies merely
because it renders successfully. CS2 skin case services may be treated as
gambling promotion and may be prohibited by YouTube or TikTok. Confirm the
advertiser's actual requirements, geographic eligibility, and relevant
advertising disclosures before publishing. The existing streamer permission
whitelist is unchanged.
