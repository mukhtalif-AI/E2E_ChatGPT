# Known problems and decisions

Last updated: 16 Oct 2026, ~23:55. Short on purpose. When something here is fixed, move it to "What is right" with the date.
Quality problems per series (what to fix, what to hide) are in `reviews/fix_or_mask_plan.md` (project folder).
What changed and how far each change was tested: `CHANGELOG.md`.

## What is wrong or unknown right now

### Not yet proven
1. **Very little has been checked by you.** One full Experimental genre job ran on the new code (Kisse-1016-2155,
   21:55 to 23:29). It ran before the emotion-marks fix and the 10 s ending. The four jobs queued at 23:48 (Bhay,
   Kisse, Rahasya, Gyaan) are the first real runs of the late changes: title names, emotion batches, thinking off for
   picture checks, the subscribe and bell ending. Nothing from 16 Oct is tagged "verified on PC" yet. Fix: watch one
   of those videos, rate it on the Review page, and change the tags in `CHANGELOG.md`.
2. **"Plan the scenes and paint the pictures again" has never been used in a real job** (tested on a copy only).
3. **The corner narrator has not run on the PC.** No series has it switched on.

### Picture check (new, Experimental)
4. **Good pictures can fail for "too many people".** The judge (gemma3:12b) counts a face behind glass and a
   close-up of eyes as people, even though the ask tells it not to (test pictures 013 and m03 fail in every run).
   Kisse-1016-2155 flagged 004 and 009 for people after 2 repaints. A false fail costs 2 repaints and a flag.
   Ideas not yet tried: count only "whole people standing in the room", or let the people count only lower the
   score unless the checklist said 0.
5. **The checklist changes from run to run.** The same scene sometimes gets `max_people: 0` and sometimes no limit,
   so the same picture can pass in one run and fail in the next (test picture 012). With thinking off the checklists
   say 0 more often, which is a little stricter.
6. **Repaint prompts still use negative words sometimes** ("no people visible", "None are visible", "zero people"),
   although the ask says not to. Z-Image Turbo ignores negatives, so these can paint people. A rewrite can also add
   people the scene does not have (Kisse 010: "two men").
7. **Wanted writing fails a picture.** Kisse m01 (a diary page with Hindi handwriting, which the scene asked for)
   failed for "text or letters" after 2 repaints.
8. **The picture judge cannot be changed on the Tinker page.** It is `picture_check_model` in the hub config, else
   the code value gemma3:12b.

### Scenes, sound and script
9. **Kisse-1016-2155 has no ambience or sound effects.** The new director planned 14 scenes and 1 close-up but no
   sound cues (log: "no sound effect cues in this plan", then "0 ambience / sound effects placed"). The old planner
   wrote cues, so the director rework may have dropped them. Not looked into yet; check whether the new Bhay job has cues.
10. **Sukoon scripts can come out without full stops (।).** Reported 16 Oct, not looked into yet. Genre scenes are cut
    where a line ends in । ? ! (`tools._SENT_END`); without them a cut falls after ~45 s wherever the line is. How the
    voice pauses and subtitles behave on such a script has not been checked.
11. **Gemma 4 is slow where thinking stays on** (your choice, for quality). The 26B wrote a Bhay script in 160 s; as
    director it took 875 s in a test (the director is the 12B now). Emotion marks now go in 15-line batches with
    thinking on, so that step makes several requests; its time on the PC is not measured yet.
12. **Z-Image Turbo has no working negative prompt** (the graph zeroes it). All styles now say what to paint instead;
    a real negative prompt is held until tested.
13. **Old episodes keep their old ending (1 min music) and old prompts** until their sound, scenes or video steps run
    again.

### Long-standing
14. **Genres are Hindi only.** English exists only for Sukoon.
15. **Kareeb (ASMR) has no trained series voice**; it uses the plain voice clone.
16. **Adding or changing a genre touches about 7 places in 4 files** (no single genre registry).
17. **Music comes only from your library** plus CC0 downloads in Experimental. Thin mood folders repeat tracks.
18. **`tools.py` was 4350 lines / 238 KB.** Split on 17 Oct into `tools.py` (~625 lines: tables, prompts, paths) and 12 files in `app\steps\` (see `PROJECT_MAP.md`). Checked with `tests\smoke_tools.py` on a copy; not yet run in a real job after a hub restart.
19. **Two copies of the site** (`ui/index.html` 138 KB and `ui/experimental/index.html` 270 KB) drift further apart.
20. **Experimental values can leak into Working for a moment**: Tinker swaps module values while an Experimental job
    runs, so a Working-page preview at that moment sees them. Jobs never overlap, so finished outputs are safe.
21. **`Code\ALL_CODE.txt` lags behind the live code** unless you run `update_all_code.bat`.
22. **Machine-bound**: fixed D:/E: paths, one 12 GB GPU (RTX 3060) shared by ComfyUI, voice, video and Ollama.
23. **No tests run automatically.** The only test files are `engines\voice\tests\test_hindi_narration.py`,
    `engines\video\engine\text_selftest.py` and `smoketest_real_episode.py` (it still points at the old
    `engines\studio` path and at `D:\HubGPT`).
24. **`D:\HubGPT` still exists** as the old Experimental data folder. The hub only reads it now (old jobs and files).
    It must not be deleted without your OK.
25. **The doc updates from 16 Oct ~23:55 are not committed yet** (all code is, up to `0d0501b`).

## What is right (keep it)

- Both pipelines run end to end; Bhay was verified on the PC (13 Oct), Bhay reel fix verified (15 Oct).
- Kisse-1016-2155 (16 Oct) ran all 14 steps on the new Experimental code without a crash: 26B script, sentence-end
  scenes with 'what happens' lines, the new picture check with repaints and flags, AI self-review, 2 reels.
- The picture check now has teeth: on the 16 test pictures gemma3:12b caught 7 of 7 wrong ones (the old judge gave
  164 of 165 pictures 7/10). A failing scene no longer stops the job; it is flagged and the video is still made.
- Thinking off for checklists and repaint prompts made them about 2.7x and 4x faster with no loss in the test.
- All output goes to one data folder, `D:\MukhtalifGPT` (Working and Experimental), since ~16:40 on 16 Oct.
- Experimental mode is isolated: own page, own job list, own Tinker settings. Working mode is untouched unless you
  install or promote.
- Every change keeps a backup next to the file (`_old_<date>_<what>`). Old clutter was cleaned once on 16 Oct, with
  your OK; git keeps the committed versions.
- Hub restarts resume jobs from the step they were on.
- One GPU job at a time; every `free_gpu` step unloads ComfyUI and every Ollama model the job used (script writer,
  director, picture prompts, picture judge), so the 26B is out before the voice and ComfyUI.
- All machine paths live in `config.json`.
- Licences: downloads only CC0 / no-credit (CC BY when allowed, credit recorded); NC/ND never. The subscribe and bell
  ending is drawn and synthesised by the hub's own code, so it has no licence to track.

## Decisions (and why)

| Decision | Why | Date |
|---|---|---|
| Media on D:, code on E:, never C: | your rule | 13 Oct |
| Never delete without asking; back up next to the file | your rule; easy undo (the 16 Oct cleanup deleted old backups only after your OK) | 13 Oct |
| Ask before installing anything | your rule | 13 Oct |
| New features go to Experimental first, Working only when you say | keep a working site while experimenting | 14 Oct |
| Working mode: Gemini first for scripts, local model as fallback; Gemma writes Hindi, qwen2.5vl judges pictures | quality, with no dead end when Gemini is busy | 13-15 Oct |
| Experimental: local models only (story engine local) | your choice | 16 Oct |
| Experimental: gemma4:26b-a4b-it-qat writes the script; gemma4:12b-it-qat directs and writes picture prompts | the 26B wrote the best story in the test; the 12B is a good, faster director | 16 Oct |
| gemma3:12b judges pictures (Experimental) | your pick; 7/7 wrong caught at 7 s per picture (gemma4:12b 9/10 but 31 s) | 16 Oct |
| Thinking stays on for script, director and emotion marks; off for picture checklists and repaint prompts | quality over speed where it matters; the check lost nothing with it off | 16 Oct |
| The code, not the model, decides if a picture passes; a failing scene is flagged, the job goes on | models copy example scores; one bad picture should not cost a whole video | 16 Oct |
| Picture prompts never say "no X" | Z-Image Turbo ignores negatives and paints X | 16 Oct |
| Genre videos with 'Whole video' 0 end 10 s after the voice, with the subscribe and bell animation; Sukoon keeps 30 min and no bell | your rule (first 7 s, you changed it to 10 s) | 16 Oct |
| Experimental genre episodes are named `<Series>-<English title words>` | short folder names you can read | 16 Oct |
| Effects are placed by Whisper word times (Experimental) | the old word-count guess put effects in the wrong place | 16 Oct |
| YouTube Audio Library: download by hand, then Import | no API; its terms forbid automated download | 16 Oct |
| Ratings live in one SQLite file; your rating beats the AI's | one place to learn from | 16 Oct |
| Git page uses fixed commands only and never offers secrets/media/backups | safety | 16 Oct |
| Don't do broad edits to voice `textsplit.py` | video subtitles share it | 15 Oct |
| Don't raise voice emotion globally without listening to a Bhay/Kisse sample | changes are subtle and easy to overdo | 15 Oct |
| Documentation first, refactor only after you agree | avoid breaking a working pipeline | 16 Oct |
| Before writing code, read these notes; after a change, add a CHANGELOG row | you kept forgetting what was done and what works | 16 Oct |
| Emotion music: never more than 3 tracks in a story, a track change only for a mood that lasts 90 s, never back to an earlier track | your rule: no jumping between tracks | 17 Oct |
| Emotion music: the AI decides within code limits, nothing is edited during a run | you fix by hand after the episode if needed | 17 Oct |
| Emotion music only for Bhay, Rahasya, Kisse, Romanch; Gyaan and Kareeb keep one track like Sukoon | your choice: one good track is enough there | 17 Oct |
| A heavy (weight 3) line: music, ambience and effects stop 2-3 s, then one soft low note | agreed 17 Oct | 17 Oct |
