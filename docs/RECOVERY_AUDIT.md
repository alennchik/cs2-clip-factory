# Operational recovery and review hardening

Branch `audit/pipeline-recovery-20261030`, based on main `23f7893`.
Kept separate from PR #7's webcam feature.

## Reproduced failures and changes

- Only discovered clips were eligible. Interrupted fetched/transcribed jobs now resume,
  reusing media after size + SHA-256 validation and successful cached ASR. Corrupt ASR
  cache is treated as absent. Explicit `transcribe --clip ID` still recomputes ASR.
- Restoring a missing source may produce different media bytes. Successful ASR is reused
  only while the recorded source hash remains the same; replacing it invalidates the
  transcript reference before saving new media. Identical restored media keeps the ASR.
- 55 higher-ranked disallowed clips hid the eligible clip behind a pre-filter SQL LIMIT.
  The allowlist is now applied inside the SQL query before LIMIT.
- Fixed 30-second start-distance was not interval overlap. The selector now compares
  half-open [vod_offset, vod_offset + duration) ranges in the same VOD. Unprocessed
  alternatives stay eligible if the preferred clip fails. Only rendered clips suppress
  others permanently. This does not identify semantically identical re-encoded moments.
- Metadata refresh now records delayed VOD IDs/offsets without resetting pipeline state.
  Twitch documents Get Clips vod_offset as clip **start** and notes its delayed arrival:
  https://dev.twitch.tv/docs/api/clips/#offset-to-the-clip-in-the-video
- A render/probe timeout no longer aborts the remaining candidate batch.
- Telegram transport errors can contain token-bearing URLs. Sanitized errors suppress
  their original exception chain; echoed tokens in API error descriptions are redacted.
- A pending custom-hook edit cannot resurrect an already-approved render. Callback
  actions require both the owner user and owner private chat.
- `permission: explicit` remains a local operator allowlist assertion. Doctor/docs no
  longer imply that this field independently verifies redistribution/monetization rights.

## Validation

Six new queue regressions and three review/transport regressions failed on the original
implementation. Final local run: **112 passed in 55.75 s**, including real FFmpeg with
synthetic media; Ruff and formatting passed. Additional tests cover corrupted source
hashes, transcript recovery/cache reuse, delayed metadata, timeout isolation and safe
state transitions. A further regression reproduced stale ASR after a changed-source
restore; tests now cover both identical and changed restored content. No live
Twitch/Telegram calls or actual credentials used. Published CI links are in PR #9.

## Limits

The existing `run` flock protects scheduled/button-triggered runs. It does not provide
transactional claims for every standalone command or Telegram send. Queue sends can
still race with a manual `/pending`; approved-file delivery needs an outbox to recover
reliably from a send failure. Status and event statements remain separate autocommits.
Failed jobs require an explicit retry; no unlimited automatic retry was introduced.
Rank refresh and bounded candidate overfetch are unchanged. Original permission defaults
are preserved for compatibility. No schema migration, deployment, publishing or merge.
