# Engineering audit progress

Base: PR #7, `a3de08dff3b8bc1cbe3af8be266c70805eae5204`; main `23f7893`.
Working branch: `audit/webcam-safety-20261030`. Never merge without owner approval.

## Evidence collected

- Read all source modules, PR changes, recent commits, CI configuration and existing tests.
- Original PR CI run 41008587943 succeeded; it installs dev dependencies without OpenCV.
- Original Ruff check and formatting passed locally. Full pytest baseline: 102 passed in 48.98 s.
- Reproduced invalid coordinate medians and precision loss relocating a hint (ValidationError).
- Reproduced false acceptance when camera disappears in the last two sampled frames.
- Actual render call chain does pass detected coordinates into RenderJob and FFmpeg.
- No real Twitch media or local Mac configuration is included in the repository. No real-video
  accuracy or M2 performance claim can be made from the synthetic FFmpeg fixtures.

## Next

1. Add regression tests and focused fixes for detection geometry, missing/ambiguous observations,
   detector/extraction failures, final crop containment, fallback visibility and diagnostic records.
2. Run baseline and revised pytest/Ruff including real FFmpeg execution on synthetic video.
3. Address confirmed unrelated high-impact pipeline/review problems on a separate branch/PR.
4. Publish audit report, exact rollout commands, known gaps and required private validation clips.
5. Push feature branches, open draft PRs, inspect their actual CI results. Do not publish media.

## Resume

Read this file and docs/ENGINEERING_AUDIT.md if present; inspect git status, branch heads and CI.
Continue the CS2 Clip Factory engineering audit from recorded evidence, preserve user changes,
finish tests and focused fixes, and do not merge or deploy.

## Completed webcam implementation

- 138 tests passed in 69.18 s; Ruff and format check passed.
- Added 4/5 + endpoints, ambiguity/size gates, actual final-crop face containment,
  valid-box medoid, precise relocation, detector failure fallback and bounded extraction.
- Added DB diagnostics, Telegram warnings, explicit CLI disable, full-frame fallback at zoom=1.
- CI matrix now covers dev and dev+vision. Awaiting new branch CI after push.
- `docs/ENGINEERING_AUDIT.md` contains evidence, limitations, local commands and fixture list.
- `docs/audit/fallback_comparison.png` is actual synthetic FFmpeg before/after output,
  not real Twitch validation. Linux detector median 1.294 s -> 1.022 s (3 runs each).
- Independent worktree `audit/pipeline-recovery-20261030` fixes six reproduced queue
  regressions and three reproduced Telegram/review failures; full suite running.

## GitHub checkpoints

- PR #8: https://github.com/alennchik/cs2-clip-factory/pull/8 (into PR #7's branch).
- Published webcam code commit: 86ab73fb3834ebec1c97a91370750adadea0f3f9.
  Remote tree exactly matches local tested tree fb9a4b2a1a95b8261a85fdde0051811b0e50bf53.
- CI 41062948784 completed successfully: dev 137 passed / 1 expected OpenCV skip;
  dev+vision also green. No real Twitch-media validation implied.
- Recovery branch final local result: 110 passed in 52.78 s; Ruff/format passed.
  Published commit dcac6e9e0e27037f008c8a3d806f6977aaaae18e; remote tree matches tested
  local tree 5d395790600866d7692d211970007b25e04c186c.
- Moving one webcam warning test away from the end of test_review.py avoids an otherwise
  trivial add/add conflict with the independent review regressions. No production change.
- Remaining: verify recovery CI and combined-branch test run, record results, final Russian report.
