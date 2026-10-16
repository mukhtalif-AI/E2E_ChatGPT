# Known problems and decisions

Last updated: 16 Oct 2026. Short on purpose. When something here is fixed, move it to "What is right" with the date.
Quality problems per series (what to fix, what to hide) are in `reviews/fix_or_mask_plan.md` (project folder).

## What is wrong or unknown right now

1. **Almost nothing from 15-16 Oct has been tried on the PC.** Code compiles, but no real run has confirmed
   genre naming, cadence, artwork anchors, Tinker, ratings, self-review, word-timed SFX, Sounds, screening, Git
   page or playback speed. Biggest risk to your sanity: you don't know what works. Fix: the smoke test in
   `CLEANUP_PLAN.md`, then update `CHANGELOG.md`.
2. **Hub needs a restart** for the `steps` crash fix (16 Oct). Any Experimental job cut off by the crash shows as
   "stopped" afterwards and must be started again.
3. **16 Oct work is not committed.** 23 files are staged, 2 new narrator files untracked. One bad edit could lose a
   day. Fix: Save / Git page or the in-app Restart (it commits a snapshot).
4. **Genres are Hindi only.** English exists only for Sukoon.
5. **Kareeb (ASMR) has no trained series voice**; it uses the plain voice clone.
6. **Adding or changing a genre touches about 7 places in 4 files** (no single genre registry).
7. **Music comes only from your library** (plus CC0 downloads in Experimental). Thin mood folders repeat tracks.
8. **`tools.py` is 3700 lines / 210 KB.** Hard to read, and every AI session has to re-read it.
9. **Backups pile up next to live files** (55 `_old_` / `.bak` files, about 13 MB, plus `_old\` 6 MB and
   `_tmp\` 18 MB). They are already ignored by git, so this is clutter, not danger.
10. **Two copies of the site** (`ui/index.html` 140 KB and `ui/experimental/index.html` 218 KB) are drifting
    apart. Promoting Experimental features to Working will get harder over time.
11. **Experimental values can leak into Working for a moment**: Tinker swaps module values while an Experimental
    job runs, so a Working-page preview at that moment sees them. Jobs never overlap, so finished outputs are safe.
12. **`Code\ALL_CODE.txt` lags behind the live code** unless you run `update_all_code.bat`.
13. **Machine-bound**: fixed D:/E: paths, one GPU shared by ComfyUI, voice and video, Ollama, Gemini key.
14. **No tests run automatically.** The only test files are `engines\voice\tests\test_hindi_narration.py`,
    `engines\video\engine\text_selftest.py` and `smoketest_real_episode.py` (that one still points at the old
    `engines\studio` path).

## What is right (keep it)

- Both pipelines run end to end; Bhay was verified on the PC (13 Oct), Bhay reel fix verified (15 Oct).
- Experimental mode is isolated: own folder `D:\HubGPT`, own job list, own page. Working mode is untouched unless
  you install or promote.
- Every change keeps a backup; nothing is deleted.
- Hub restarts resume jobs from the step they were on.
- One GPU job at a time; models are unloaded before ComfyUI and video rendering.
- All machine paths live in `config.json`.
- Licences: downloads only CC0 / no-credit (CC BY when allowed, credit recorded); NC/ND never.

## Decisions (and why)

| Decision | Why | Date |
|---|---|---|
| Media on D:, code on E:, never C: | your rule | 13 Oct |
| Never delete; move to `_old` | your rule; easy undo | 13 Oct |
| Ask before installing anything | your rule | 13 Oct |
| New features go to Experimental first, Working only when you say | keep a working site while experimenting | 14 Oct |
| Ollama only for local AI: Gemma writes stories/Hindi, Qwen3 for image prompts, qwen2.5vl judges pictures | Gemma writes better Hindi; Qwen reasons better | 15 Oct |
| Gemini first for scripts, local model as fallback | quality, with no dead end when Gemini is busy | 13 Oct |
| Don't do broad edits to voice `textsplit.py` | video subtitles share it | 15 Oct |
| Don't raise voice emotion globally without listening to a Bhay/Kisse sample | changes are subtle and easy to overdo | 15 Oct |
| Effects are placed by Whisper word times (Experimental) | the old word-count guess put effects in the wrong place | 16 Oct |
| YouTube Audio Library: download by hand, then Import | no API; its terms forbid automated download | 16 Oct |
| Ratings live in one SQLite file; your rating beats the AI's | one place to learn from | 16 Oct |
| Git page uses fixed commands only and never offers secrets/media/backups | safety | 16 Oct |
| Documentation first, refactor only after you agree | avoid breaking a working pipeline | 16 Oct |
