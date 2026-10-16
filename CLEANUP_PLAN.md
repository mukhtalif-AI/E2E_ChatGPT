# Cleanup plan (proposed, nothing done yet)

Status: **proposal only**. No files have been moved, deleted or split. Each step is small, can be undone, and
should be followed by the smoke test. Say which steps you want and in what order.

## Step 0. Commit what you have (5 minutes, do first)

16 Oct work is staged but not committed. Open Experimental > Save / Git, tick everything offered, commit
with a message like "16 Oct: Tinker, ratings, sounds, git page, steps fix". Now any later step can be undone
with git.

## Step 1. Smoke test: one 1-minute episode per series (do second)

A small script that queues a 1-minute run of every series in Experimental mode and prints pass/fail per series.
It only calls the hub's own `/api/jobs`, so it uses the same code you click in the page. Default is a dry run
that prints what it would send; `--run` actually queues the jobs (they run one at a time on the GPU, about
7 jobs in all). Proposed location: `tests\smoke_test.py`, run with `python tests\smoke_test.py --run` while the
hub is running and idle.

```python
"""smoke_test.py - queue a 1-minute Experimental run per series and report pass/fail.
Dry run by default; --run queues the jobs. Needs the hub running at 127.0.0.1:8781."""
import json, sys, time, urllib.request

HUB = "http://127.0.0.1:8781"
SERIES = ["bhay", "rahasya", "kisse", "romanch", "kareeb", "gyaan"]
HDR = {"Content-Type": "application/json", "X-Hub-Experimental": "1"}


def call(path, body=None):
    req = urllib.request.Request(HUB + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=HDR, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def bodies():
    stamp = time.strftime("%m%d_%H%M")
    for g in SERIES:
        yield g, {"kind": "genre", "genre": g, "name": f"Smoke {g} {stamp}", "speech_min": 1, "total_min": 1,
                  "reels": 0, "stages": ["script", "voice", "scenes", "sound", "pictures", "video"]}
    yield "sukoon", {"kind": "episode", "name": f"Smoke sukoon {stamp}", "auto_topic": True,
                     "speech_min": 1, "total_min": 1, "reels": 0}


def main():
    run = "--run" in sys.argv
    ids = {}
    for label, b in bodies():
        if not run:
            print("would queue", label, json.dumps(b, ensure_ascii=False))
            continue
        ids[call("/api/jobs", b)["id"]] = label
        print("queued", label)
    if not run:
        print("dry run only; add --run to queue the jobs")
        return
    while True:
        rows = {j["id"]: j for j in call("/api/jobs")}
        left = [i for i in ids if rows.get(i, {}).get("status") in ("queued", "running")]
        for i, label in ids.items():
            j = rows.get(i, {})
            print(f"{label:8} {j.get('status', '?'):8} {j.get('step_label', '')[:60]}")
        if not left:
            break
        print("-" * 40)
        time.sleep(60)
    bad = [ids[i] for i in ids if rows.get(i, {}).get("status") != "done"]
    print("ALL PASSED" if not bad else "FAILED: " + ", ".join(bad) + " (open Activity for the log)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
```

The form fields (`speech_min`, `total_min`, `stages`, `auto_topic`) come from `tools.plan()`. Before the first
real run I'd check the dry-run output against what the page sends, because the Experimental page may add
fields the planner expects. After each passing run, mark the matching rows "verified on PC" in `CHANGELOG.md`.

## Step 2. Backups into one place

Today: 55 `*_old_*` / `.bak` files (about 13 MB) sit next to live files in `app\`, `app\ui\`,
`app\ui\experimental\`, the root, `engines\voice\` and `engines\video\`; plus `_old\`, `app\_old\`,
`app\ui\_old\`, two `_backup_*_20261015\` folders.

Proposal: one folder `_backups\<YYYY-MM-DD>_<what>\` that keeps the original sub-path, for example
`_backups\2026-10-16_ratings\app\server.py`. A small script would:
1. list every `*_old_<date>_<tag>` / `.bak_*` / `.before_*` file and where it would go (dry run first),
2. move them only after you say yes (moves, never deletes),
3. write `_backups\INDEX.md` with date, tag, original path.

Also change the habit for future edits: new backups go straight into `_backups\` (one line to change in each
AI helper's instructions and in `AGENTS.md`).

Not to move: `engines\voice\config - 14102026_2304.json` and `config.json.before_*.json` until we check
the voice engine doesn't read them; `secrets.json_old_*` stays put (it holds keys).

## Step 3. .gitignore

Your `.gitignore` already ignores backups (`*_old_20*`, `*.bak_*`, `_old/`, `_backup_*/`), `_tmp/`, logs,
`Code/ALL_CODE.txt*`, `secrets.json`, model folders and bundled libraries. Nothing risky is tracked.
Proposed additions only:

```gitignore
# one backups folder (Step 2)
/_backups/

# leftover backup spellings not covered above
*.bak
**/*.before_*

# per-run render output and caches
engines/video/output/
**/.pytest_cache/
```

## Step 4. Split tools.py, in small steps

**Important constraint:** other files change `tools` at run time. `server.py` runs every step as
`getattr(tools, fn)`; `feedback.py` replaces `tools._horror_track`, `tools._sfx_names` and `tools.shape_mix`;
`tinker.py` replaces prompts and tables (`GENRES`, `STYLES`, `DIRECTION`) with `setattr(tools, ...)`. If code
moves to a new file and calls its neighbours directly, those replacements stop working silently (Tinker settings
and rating-based picking would just be ignored). So the split must keep `tools` as the single front door:

- New modules live in `app\steps\` (for example `steps\genre.py`).
- `tools.py` re-exports every moved name (`from steps.genre import *`), so `getattr(tools, fn)` still works.
- Moved code reads tables and swappable helpers through the module (`tools.GENRES`, `tools._horror_track(...)`),
  never by `from tools import GENRES` (that copies the value once and misses Tinker's later change).
- One section per step, smoke test after each, commit after each. Any step can be undone by reverting one commit.

Proposed order (least connected first):

| Step | Move to | Lines today | Risk |
|---|---|---|---|
| 4a | `steps/library.py`: `status`, `suggestions`, `library`, `episodes`, `remove_item`, `open_path`, `system_action` | 3406-3717 | low (no job steps) |
| 4b | `steps/thumbs.py`: style index, `look_text` | 295-403 | low |
| 4c | `steps/voice_train.py`: series voice + training | 2313-2553 | low |
| 4d | `steps/sukoon_video.py`: `studio_prepare`, `studio_video`, `slideshow`, `make_ass` | 3147-3405 | medium |
| 4e | `steps/pictures.py`: `art_prompt`, `pick_art`, `comfy_images`, `pick_scenes`, `repaint` | 2717-3146 | medium (Tinker prompts) |
| 4f | `steps/genre.py`: `horror_*`, `sfx_fetch`, `genre_reels` | 1876-2312, 2599-2648 | higher (feedback wraps these) |
| 4g | `steps/voice.py`: `sv_speak`, `en_speak`, `free_gpu` | 1700-1875 | medium |
| 4h | `steps/planner.py`: `plan()` | 618-1120 | highest, last |

After 4h, `tools.py` would be the tables (GENRES, STYLES, VOICES...) plus re-exports, a few hundred lines.
Each step also updates `PROJECT_MAP.md`.

## Later (not part of this cleanup)

- One `genres.json` for all per-genre settings (would fix the "7 places" problem); best done after Step 4.
- Decide whether Working should simply become Experimental (one page instead of two drifting copies).
