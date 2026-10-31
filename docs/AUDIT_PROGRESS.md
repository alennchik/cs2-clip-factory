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

## Remaining acceptance work

Code changes, regression tests, draft PRs and CI verification are complete. Automatic webcam
accuracy remains unvalidated on real Twitch clips and on the owner's M2. Keep auto disabled
by default; use the exact fixture list and local-only commands in ENGINEERING_AUDIT.md.
Neither green CI nor the word "confident" proves that an estimated crop is the webcam panel.

## Resume

Read this file and docs/ENGINEERING_AUDIT.md if present; inspect git status, branch heads and CI.
Continue the CS2 Clip Factory engineering audit from recorded evidence, preserve user changes,
finish tests and focused fixes, and do not merge or deploy.

## Completed webcam implementation

- 138 tests passed in 69.18 s; Ruff and format check passed.
- Added 4/5 + endpoints, ambiguity/size gates, actual final-crop face containment,
  valid-box medoid, precise relocation, detector failure fallback and bounded extraction.
- Added DB diagnostics, Telegram warnings, explicit CLI disable, full-frame fallback at zoom=1.
- CI matrix now covers dev and dev+vision; both jobs passed after publication.
- `docs/ENGINEERING_AUDIT.md` contains evidence, limitations, local commands and fixture list.
- `docs/audit/fallback_comparison.png` is actual synthetic FFmpeg before/after output,
  not real Twitch validation. Linux detector median 1.294 s -> 1.022 s (3 runs each).
- Independent worktree `audit/pipeline-recovery-20261030` fixes six reproduced queue
  regressions and three reproduced Telegram/review failures; final full suite passed.

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

## Final verification

- PR #8 follow-up commit a875b394056b97e2d169a9c205ea87c09bc768db:
  [CI 41063818003](https://github.com/alennchik/cs2-clip-factory/actions/runs/41063818003)
  succeeded. Job logs confirm dev: 137 passed, 1 expected OpenCV skip in 63.66 s;
  dev+vision: 138 passed in 52.98 s. Ruff and format checks passed in both jobs.
- PR #9: https://github.com/alennchik/cs2-clip-factory/pull/9 (into main).
  [CI 41063774218](https://github.com/alennchik/cs2-clip-factory/actions/runs/41063774218)
  on dcac6e9e0e27037f008c8a3d806f6977aaaae18e succeeded; job logs confirm
  110 passed in 45.88 s, plus Ruff and format checks.
- A temporary local combination of both tested branches merges without conflicts:
  **155 passed in 59.91 s**, Ruff and format checks passed (51 Python files).
  This was an integration check in a detached worktree, not a GitHub/main merge.
- GitHub metadata verified: PRs #7, #8 and #9 are open drafts and unmerged;
  main is still 23f78936e413a0552788a5cd67466aa3fb27a66d, and PR #7 still has
  head a3de08dff3b8bc1cbe3af8be266c70805eae5204.
- This final checkpoint changes documentation only; the final PR head's CI is linked
  in the PR description after verification. No scheduled continuation has been created.

## Final recovery follow-up

- During final review, replacing a missing source with changed bytes incorrectly reused
  old speech. Reproduced before fixing; regression checks changed and identical restores.
- PR #9 follow-up 28808e33865720704fd3f39949b9e62eeffeb672 invalidates the transcript
  reference before publishing changed media. No schema change or deletion of transcript
  files. Local recovery suite: **112 passed in 55.75 s**; Ruff/format passed.
- Combined suite with the follow-up: **157 passed in 63.65 s**, Ruff/format passed.
  These supersede the earlier 110/155-test counts above.
- PR #8 checkpoint 678b11fe899e51dfefc3682cbea640aebfc4187f passed
  [CI 41219428140](https://github.com/alennchik/cs2-clip-factory/actions/runs/41219428140).
  The next checkpoint updates these documents and clarifies CLI help text only.
  Final head CI evidence for both PRs is maintained in their descriptions.

One-message resume instruction:
"Continue the CS2 Clip Factory audit from docs/AUDIT_PROGRESS.md on
audit/webcam-safety-20261030 and draft PRs #8/#9. Verify current heads and CI, then
evaluate the supplied private clips against labelled webcam panel boundaries and
measure M2 runtime/memory. Preserve manual mode; do not merge, deploy or publish."
