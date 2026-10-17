"""sound_needs.py - which music moods and sound effects the stories already made need, and what the library lacks.

EMOTION_MUSIC_PLAN.md step 2 (17 Oct 2026). Reads only: the Experimental episodes (scene plans, scripts with their
feeling marks), the story ledger, the music and SFX folders, your ratings, the screening cache and the licence
registers. It writes one report (Markdown + JSON) in D:\\MukhtalifGPT\\Activity\\Reports and downloads nothing:
get the missing tracks and effects on the Sounds page ("Get more"), from the list in the report.

    python app\\sound_needs.py              (with the hub's Python on the PC)
    python app\\sound_needs.py --map "D:\\MukhtalifGPT=/mnt/x" ...   (away from the PC: rewrite path prefixes)
"""
import collections, glob, json, math, os, sys, time

ON_SERIES = ("bhay", "rahasya", "kisse", "romanch")     # the series whose music follows the emotion


def _load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _key(p):
    """a file's name, lower case, for matching caches written with Windows paths (also when read away from the PC)"""
    return str(p).replace("\\", "/").rsplit("/", 1)[-1].lower()


def music_target(n_series):
    """usable tracks a mood folder should hold: each story takes up to 3 tracks and the least-used one is picked, so
    every series that shares the folder needs a few of its own before tracks repeat"""
    return min(12, 4 + 2 * n_series)


def collect(cfg, say=print):
    import tools
    import sfx_lib
    import emotion_music
    root = cfg.get("experimental_root") or ""
    series_dir = os.path.join(root, (cfg.get("layout") or {}).get("experimental_series", "Series"))
    music_dir = cfg.get("music_dir") or ""
    out = {"made": time.strftime("%Y-%m-%d %H:%M:%S"), "series": {}, "music": {}, "sfx": {}, "notes": []}

    # 1. the stories already made: minutes of each mood (emotion map from the marks, no AI) + effects asked for
    asked = collections.Counter()
    asked_missing = collections.Counter()
    for genre in sorted(os.listdir(series_dir)) if os.path.isdir(series_dir) else []:
        row = {"episodes": 0, "with_scenes": 0, "mood_min": collections.Counter(), "sections": [], "turn_lines": 0}
        for ep in sorted(glob.glob(os.path.join(series_dir, genre, "Episodes", "*"))):
            row["episodes"] += 1
            shots_p = sorted(glob.glob(os.path.join(ep, "03_Artwork", "*", "shots.json")))
            nar = sorted(glob.glob(os.path.join(ep, "02_Narration", "narrations", "*", "timing.json")))
            if not shots_p:
                continue
            row["with_scenes"] += 1
            shots = (_load(shots_p[0], {}) or {}).get("shots") or []
            for s in shots:
                for c in s.get("cues") or []:
                    name = str(c.get("sfx") or "").strip()
                    if name:
                        asked[(genre, name)] += 1
                        if not c.get("file") or not os.path.exists(str(c.get("file"))):
                            asked_missing[(genre, name)] += 1
            if genre in ON_SERIES and nar:
                try:
                    m = emotion_music.build(cfg, genre, os.path.dirname(nar[0]), os.path.dirname(shots_p[0]),
                                            say=lambda *a: None, use_ai=False,
                                            script_path=os.path.join(ep, "01_Script", "script.txt"))
                except Exception as e:
                    out["notes"].append(f"{os.path.basename(ep)}: no emotion map ({e})")
                    continue
                for x in m["scenes"]:
                    row["mood_min"][x["mood"]] += (x["end"] - x["start"]) / 60
                row["sections"].append(len(m["sections"]))
        row["mood_min"] = {k: round(v, 1) for k, v in row["mood_min"].most_common()}
        out["series"][genre] = row
    ledger = _load(os.path.join(cfg.get("media_dir") or "", "story_ledger.json")) or \
        _load(os.path.join(root, "story_ledger.json"), []) or []
    if isinstance(ledger, dict):
        ledger = ledger.get("stories") or ledger.get("entries") or []
    waiting = collections.Counter(str(e.get("genre") or "").replace("horror", "bhay") for e in ledger
                                  if isinstance(e, dict) and "no voice" in str(e.get("status") or ""))
    out["written_not_voiced"] = dict(waiting)

    # 2. music: per mood folder, how many tracks the pipeline can really use
    try:
        import feedback
        scores = {_key(k): v for k, v in feedback.file_scores(cfg, "music").items()}
        rejected = feedback.rejected
    except Exception as e:
        scores, rejected = {}, (lambda sc: False)
        out["notes"].append(f"ratings not read: {e}")
    try:
        import sound_library
        blocked = {_key(p) for p in sound_library.blocked(cfg, "music")}
    except Exception:
        blocked = set()
    screen = _load(os.path.join(root, (cfg.get("layout") or {}).get("experimental_activity", "Activity"),
                                "audio_screen.json"), {}) or {}
    srows = {_key(k): v for k, v in (screen.get("files") or {}).items()}
    sover = {_key(k): v for k, v in (screen.get("overrides") or {}).items()}
    vb_min = 0.03
    try:
        import audio_screen
        vb_min = float(audio_screen.LIMITS["music"].get("voice_band_min", 0.03))
    except Exception:
        pass
    harsh = {}
    for d in (root, cfg.get("media_dir") or ""):                     # the hub's media folder and the Experimental Library
        harsh.update(_load(os.path.join(d, "music_harsh.json"), {}) or {})
    hrows = {_key(k.split("|")[0]): v for k, v in harsh.items()}
    skip = float(cfg.get("music_harsh_skip", 19))
    users = collections.defaultdict(list)
    for g in ON_SERIES:
        for m in emotion_music.series_moods(cfg, g):
            users[m].append(g)
    folders = sorted(d for d in (os.listdir(music_dir) if os.path.isdir(music_dir) else [])
                     if os.path.isdir(os.path.join(music_dir, d)) and not d.startswith(("_", ".")))
    for mood in sorted(set(folders) | set(users)):
        files = [f for f in glob.glob(os.path.join(music_dir, mood, "*.mp3")) if "crate" not in f.lower()]
        why = collections.Counter()
        usable = []
        for f in files:
            k = _key(f)
            r = srows.get(k) or {}
            o = sover.get(k, "")
            vb = (r.get("m") or {}).get("voice_band")
            if k in blocked:
                why["licence"] += 1
            elif rejected(scores.get(k)):
                why["rated bad"] += 1
            elif o == "reject" or (o not in ("ok", "treat") and r.get("verdict") == "reject"):
                why["screening: covers the voice"] += 1
            elif vb is not None and vb < vb_min and o != "ok":
                why["only deep rumble"] += 1
            elif float(hrows.get(k, 0) or 0) >= skip:
                why["too piercing"] += 1
            else:
                usable.append(f)
        unscreened = sum(1 for f in usable if _key(f) not in srows)
        need = music_target(len(users.get(mood, []))) if users.get(mood) else 0
        out["music"][mood] = {"files": len(files), "usable": len(usable), "not_usable": dict(why),
                              "not_screened_yet": unscreened, "series": users.get(mood, []), "target": need,
                              "short_by": max(0, need - len(usable))}

    # 3. sound effects: categories each series uses with fewer than sfx_lib.KEEP files, and cue names the director
    # asked for that had no file
    lib = {}
    try:
        lib = sfx_lib.library(cfg)
    except Exception as e:
        out["notes"].append(f"SFX library not read: {e}")
    for sid, (cats, per_min) in sfx_lib.SERIES.items():
        try:
            miss = sfx_lib.missing(cfg, sid)
        except Exception:
            miss = []
        names = sorted({n for (g, n) in asked_missing if g == sid})
        out["sfx"][sid] = {"categories": len(cats), "thin_categories": miss,
                           "asked_without_file": {n: asked_missing[(sid, n)] for n in names}}
    out["sfx_files"] = len(lib)
    return out


def write(cfg, data, say=print):
    rep = os.path.join(cfg.get("experimental_root") or "", (cfg.get("layout") or {}).get("experimental_activity", "Activity"),
                       "Reports")
    os.makedirs(rep, exist_ok=True)
    stem = os.path.join(rep, time.strftime("sound_needs_%Y%m%d_%H%M"))
    with open(stem + ".json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    L = [f"# Sound needs ({data['made']})", "",
         "What the stories already made ask for, and what the music and SFX library can give. Nothing was downloaded: "
         "use the Sounds page (Get more) for the moods and effects below.", "",
         "## Music moods", "", "| Mood | Series | Files | Usable | Not usable (why) | Target | Short by |",
         "|---|---|---|---|---|---|---|"]
    for mood, r in sorted(data["music"].items(), key=lambda kv: -kv[1]["short_by"]):
        why = ", ".join(f"{k} {v}" for k, v in r["not_usable"].items()) or "-"
        L.append(f"| {mood} | {', '.join(r['series']) or '-'} | {r['files']} | {r['usable']}"
                 f"{' (' + str(r['not_screened_yet']) + ' not screened yet)' if r['not_screened_yet'] else ''} | {why} | "
                 f"{r['target'] or '-'} | {('**' + str(r['short_by']) + '**') if r['short_by'] else '0'} |")
    L += ["", "## What the stories need (emotion map from the feeling marks, no AI)", "",
          "| Series | Episodes | With scenes | Minutes per mood | Music sections per story | Written, no voice yet |",
          "|---|---|---|---|---|---|"]
    for g, r in data["series"].items():
        mm = ", ".join(f"{k} {v}" for k, v in r["mood_min"].items()) or "-"
        secs = ", ".join(str(x) for x in r["sections"]) or "-"
        L.append(f"| {g} | {r['episodes']} | {r['with_scenes']} | {mm} | {secs} | {data['written_not_voiced'].get(g, 0)} |")
    L += ["", "## Sound effects", "", "| Series | Thin categories (fewer than 3 files) | Asked for by the director, no file |",
          "|---|---|---|"]
    for g, r in data["sfx"].items():
        asked = ", ".join(f"{k} ({v})" for k, v in r["asked_without_file"].items()) or "-"
        L.append(f"| {g} | {', '.join(r['thin_categories']) or '-'} | {asked} |")
    short = [m for m, r in data["music"].items() if r["short_by"]]
    L += ["", "## Get these first", ""]
    L += [f"- Music **{m}**: {data['music'][m]['short_by']} more usable tracks" for m in
          sorted(short, key=lambda m: -data["music"][m]["short_by"])] or ["- Music: every mood has enough usable tracks"]
    thin = sorted({c for r in data["sfx"].values() for c in r["thin_categories"]})
    if thin:
        L.append("- Effects: " + ", ".join(thin))
    if data["notes"]:
        L += ["", "## Notes", ""] + [f"- {n}" for n in data["notes"]]
    with open(stem + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    say("   report: " + stem + ".md")
    return stem + ".md"


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--map", action="append", default=[], help="FROM=TO path prefix (away from the PC)")
    a = ap.parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import path_config
    cfg = path_config.load_config()
    cfg["experimental_mode"] = True
    cfg.setdefault("experimental_root", r"D:\MukhtalifGPT")
    for fr, to in (m.split("=", 1) for m in a.map if "=" in m):
        for k, v in list(cfg.items()):              # path_config makes them absolute, so the prefix can sit inside
            i = v.lower().find(fr.lower()) if isinstance(v, str) else -1
            if i >= 0:
                cfg[k] = to + v[i + len(fr):].replace("\\", "/")
    L = cfg.get("layout") or {}
    cfg["media_dir"] = os.path.join(cfg["experimental_root"], L.get("experimental_library", "Library"))
    print(write(cfg, collect(cfg)))


if __name__ == "__main__":
    main(sys.argv[1:])
