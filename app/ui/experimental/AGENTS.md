# Experimental UI mode rules

- `/experimental` loads this isolated full-site copy. The stable site is `app/ui/index.html`.
- When the user asks to change the website while working in Experimental mode, implement presentation or interaction changes in this directory first. Preserve the stable UI copy unless the user explicitly requests promotion.
- Keep generation order, model selection, media settings, output paths, and job semantics unchanged for visual-only requests. Do not alter `app/tools.py`, voice engines, or the stable page to achieve a redesign.
- If a requested feature truly needs server behavior, isolate it under an experimental module and keep any shared server change to a narrow route dispatch. Record that boundary in `EXPERIMENT_NOTES.md`.
- Before recommending promotion, show the Experimental result for review and keep the stable UI intact until the user asks to promote it.

## Notes to read and keep up to date (16 Oct 2026)

- Before changing code, read `PROJECT_MAP.md`, `CHANGELOG.md`, `KNOWN_PROBLEMS.md` (its Decisions table) and `CLEANUP_PLAN.md` in the hub folder, and `EXPERIMENT_NOTES.md` here. Re-read them right before editing: other sessions change them too.
- After a change, add one row to `CHANGELOG.md` under today's date with the files, an honest status (verified on PC / ran live / compiled only / untested; say when something was only tested on a copy or with a fake AI) and the backup suffix. Note shared-file boundaries in `EXPERIMENT_NOTES.md`.
- Back up each file next to itself as `<file>_old_<YYYYMMDD_HHMMSS>_<what>` before editing. Never delete without the user's OK.
- Experimental data lives under `config.json` `paths.experimental_root` (`D:\MukhtalifGPT`); `D:\HubGPT` is read only.
- Picture prompts and repaint prompts must say what to paint, never "no X": Z-Image Turbo ignores negatives.
- Real Experimental jobs carry `cfg["experimental_mode"]`; test scripts must not set flags that real runs lack.
