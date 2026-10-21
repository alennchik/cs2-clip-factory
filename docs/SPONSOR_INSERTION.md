# Optional sponsor integration — MP4 banner

The existing FFmpeg pipeline can insert a supplied local sponsor animation
after rendering and **before Telegram manual review**. No auto-posting or paid
API is required. The feature is disabled by default.

## Preferred asset

The currently preferred banner was provided by the owner as `musordrop.mp4`
and checked using FFprobe:

- H.264 MP4, **1280×672**, 30 FPS, opaque video
- Stereo AAC audio
- **5.800 seconds** total length

The original wide animation, including its opaque background, is shown in
full in the middle of a vertical 1080×1920 clip. A dark, blurred freeze-frame
of the clip fills the space above and below it. The banner is not stretched,
cropped or shortened. The original video and audio resume afterwards.

## Install the preferred MP4 asset

Download the owner's ready-to-extract banner package from this chat and extract
it into the **repository root**. It creates:

```text
assets/sponsor/banner.mp4
```

Alternatively, manually copy the owner-supplied file:

```bash
mkdir -p assets/sponsor
cp ~/Downloads/musordrop.mp4 assets/sponsor/banner.mp4
```

This binary is **not committed**: `.gitignore` intentionally excludes locally
downloaded sponsor media. Older WebM ads remain supported if you explicitly
configure their file path instead of the default.

## Enable in your existing local configuration

Edit `config/streamers.yaml` and add under its existing `render:` section:

```yaml
render:
  # Keep the existing render options.
  sponsor:
    enabled: true
    asset: assets/sponsor/banner.mp4
    min_clip_seconds: 12
```

Do not create a second `render:` key. Merely merging the PR does not enable
advertising or overwrite existing local configuration.

## Test

From the repository root:

```bash
clipfactory render-file ~/Downloads/clip.mp4 --webcam 0.72,0.70,0.28,0.30 --hook "Test"
python -m pytest -q tests/test_sponsor.py
ruff check . && ruff format --check .
```

- Shorts under `min_clip_seconds` are left untouched.
- Longer clips get **exactly one** sponsor interlude around 35–60% into the
  gameplay, where audio is relatively quiet. This is a heuristic and cannot
  reliably detect an important kill or clutch. Verify placement in Telegram.
- Missing or invalid configured media causes an error, not a misleading
  "sponsored" export without the asset.
- Inserted position and actual asset duration are included in the render
  log event as `sponsor_at` and `sponsor_duration`.
- The streamer permission whitelist and manual review before posting remain.

## Rules and disclosure

Do not assume technical rendering implies approval by the advertiser or
platform. CS2 skin-related case or upgrade promotions may be treated as
gambling promotion under TikTok/YouTube rules. Confirm advertiser-specific
formats, audience geography, approvals, and any required commercial
disclosures before publishing. Only use clips with the applicable rights.
