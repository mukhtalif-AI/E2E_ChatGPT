"""feedback.py - your ratings on everything Experimental mode makes, and what they are used for.

Ratings: 5 levels (1 very bad, 2 bad, 3 avg, 4 good, 5 very good) with an optional note, on
  videos and reels (overall + script, voice, BGM, SFX, SFX timing, picture theme, picture quality, subtitles),
  narrations, scripts, pictures, music tracks, sound effect files, and single SFX cues (on time / early / late /
  wrong sound / not needed).
Everything is kept in ONE SQLite file: <experimental_root>\\Activity\\ratings.sqlite. Each row stores the item, the
series and episode, the settings and prompt versions the run used, and who rated it ("user" or "ai" for the
self-review). Nothing is overwritten: the newest row per item/aspect/source counts, and your rating always wins
over the AI's.

Uses:
  * Experimental music and SFX selection skips files you rated (very) bad and prefers the ones you liked
    (install() wraps tools._horror_track and tools._sfx_names; Working mode is not touched).
  * insights(): weak spots per series, prompt and model versions compared by score, how far the AI's
    self-review agrees with you, best and worst sounds, suggestions that point at the Tinker page.
  * suggest_prompt(): asks the AI for a revised prompt or writing rules from your notes (you review and save it
    in Tinker; nothing is applied automatically).
  * export(): JSONL files for fine-tuning (scripts with scores, chosen/rejected pairs, picture prompts, SFX cues).
"""
import copy
import glob
import hashlib
import json
import os
import random
import re
import sqlite3
import threading
import time

LEVELS = {1: "very bad", 2: "bad", 3: "avg", 4: "good", 5: "very good"}
ASPECTS = {
    "video": ["overall", "script", "voice", "clarity", "bgm", "sfx", "sfx_timing", "picture_theme", "picture_quality",
              "subtitles"],
    "reel": ["overall"],
    "narration": ["overall", "voice", "clarity", "bgm", "sfx", "sfx_timing"],
    "script": ["overall"],
    "picture": ["overall", "quality", "theme"],
    "music": ["overall"],
    "sfx": ["overall"],
    "cue": ["timing"],
}
ASPECT_LABELS = {"overall": "Overall", "script": "Script", "voice": "Voice", "clarity": "Speech clarity",
                 "bgm": "Background music",
                 "sfx": "Sound effects", "sfx_timing": "SFX timing", "picture_theme": "Picture theme",
                 "picture_quality": "Picture quality", "subtitles": "Subtitles", "quality": "Quality",
                 "theme": "Theme", "timing": "Timing"}
VERDICTS = {"ok": "on time", "early": "too early", "late": "too late", "wrong_sound": "wrong sound",
            "not_needed": "not needed"}
SETTING_KEYS = ("genre", "series", "speech_min", "total_min", "level", "writer", "voice_style", "scenes",
                "best_pictures", "reels", "music_track", "topic", "emotions", "dramatic", "acoustic_context",
                "word_timed_sfx", "self_review")

_LOCK = threading.Lock()
_CACHE = {}


# ------------------------------------------------------------------ paths and the database
def _root(cfg):
    import experimental_workspace
    return experimental_workspace.root(cfg)


def _activity(cfg):
    return os.path.join(_root(cfg), (cfg.get("layout") or {}).get("experimental_activity", "Activity"))


def db_path(cfg):
    return os.path.join(_activity(cfg), "ratings.sqlite")


def norm(p):
    return os.path.normcase(os.path.abspath(str(p))) if p else ""


def _db(cfg):
    path = db_path(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS ratings(
        id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, item_type TEXT, item_id TEXT, aspect TEXT,
        score INTEGER, verdict TEXT, offset_s REAL, note TEXT, source TEXT, series TEXT, episode TEXT,
        path TEXT, context TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS r_item ON ratings(item_type, item_id)")
    con.execute("CREATE INDEX IF NOT EXISTS r_episode ON ratings(episode)")
    return con


def _rows(cfg, where="", args=()):
    """the newest row per item / aspect / source"""
    with _LOCK:
        con = _db(cfg)
        try:
            q = ("SELECT * FROM ratings WHERE id IN (SELECT MAX(id) FROM ratings " + (("WHERE " + where) if where else "")
                 + " GROUP BY item_type, item_id, aspect, source)")
            return [dict(r) for r in con.execute(q, args).fetchall()]
        finally:
            con.close()


def _load(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return copy.deepcopy(default)


# ------------------------------------------------------------------ where an item belongs
def episode_of(cfg, path):
    """(series, episode name, episode dir) for a path inside <root>\\Series\\<series>\\Episodes\\<episode>"""
    L = cfg.get("layout") or {}
    series_root = norm(os.path.join(_root(cfg), L.get("experimental_series", "Series")))
    p = norm(path)
    if not p.startswith(series_root + os.sep):
        return "", "", ""
    rest = p[len(series_root) + 1:].split(os.sep)
    if len(rest) >= 3 and rest[1] == os.path.normcase(L.get("experimental_episode_collection", "Episodes")):
        # keep the original letter case of the folder names
        parts = os.path.abspath(path).split(os.sep)
        n = len(series_root.split(os.sep))
        return parts[n], parts[n + 2], os.sep.join(parts[:n + 3])
    return rest[0], "", ""


def _settings_of(request):
    out = {k: request.get(k) for k in SETTING_KEYS if k in request}
    if isinstance(out.get("topic"), str):
        out["topic"] = out["topic"][:200]
    tk = request.get("_tinker") or {}
    if tk:
        out["tinker"] = {"models": tk.get("models") or {}, "prompts_edited": sorted((tk.get("prompts") or {}).keys()),
                         "tables_edited": sorted((tk.get("tables") or {}).keys())}
    return out


def context_for(cfg, path):
    """what a rated thing was made with: settings, models and prompt versions of its run"""
    series, episode, ep_dir = episode_of(cfg, path)
    ctx = {"series": series, "episode": episode}
    if not ep_dir:
        return ctx
    L = cfg.get("layout") or {}
    src = _load(os.path.join(ep_dir, L.get("episode_output", "Output"), "review_sources.json"), {}) or {}
    if src:
        ctx.update({k: src.get(k) for k in ("job_id", "settings", "prompt_versions", "name") if src.get(k)})
        if src.get("series"):
            ctx["series"] = src["series"]
        return ctx
    inp = _load(os.path.join(ep_dir, L.get("experimental_input", "00_Input"), "input.json"), {}) or {}
    req = inp.get("request") or {}
    if inp:
        ctx.update({"job_id": inp.get("job_id"), "settings": _settings_of(req),
                    "prompt_versions": req.get("_prompt_versions") or {}})
        if inp.get("series"):
            ctx["series"] = inp["series"]
    return ctx


# ------------------------------------------------------------------ prompt versions
def prompt_versions(cfg, snap=None, series=""):
    """{"prompts": {key: hash}, "models": {...}} of what an Experimental run uses now; texts saved once per hash"""
    import tinker
    if not tinker._ORIG:
        tinker.install(cfg)
    vdir = os.path.join(_activity(cfg), "PromptVersions")
    snap = snap or {}
    out = {}

    def keep(key, text):
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
        f = os.path.join(vdir, re.sub(r"[^\w.-]+", "_", key), h + ".txt")
        if not os.path.isfile(f):
            os.makedirs(os.path.dirname(f), exist_ok=True)
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(text)
        out[key] = h

    ws = tinker.working_settings()
    for key in tinker.prompt_keys():
        text = (snap.get("prompts") or {}).get(key)
        if not isinstance(text, str):
            text = tinker._working_value("prompts", key, ws)
        keep(key, text)
    for key in tinker.TABLES:
        if key in tinker._ORIG:
            val = tinker.merge(tinker._working_value("tables", key, ws), (snap.get("tables") or {}).get(key) or {})
            keep(key, json.dumps(val, ensure_ascii=False, sort_keys=True))
    if series:
        try:
            import story
            exp_cfg = dict(cfg, media_dir=tinker._exp_media(cfg))
            rules = story.genre_rules(exp_cfg, series)
            if rules:
                keep("genre_rules." + series, json.dumps(rules, ensure_ascii=False, sort_keys=True))
        except Exception:
            pass
    models = {}
    for key, _label, _src, _hint in tinker.MODELS:
        try:
            models[key] = (snap.get("models") or {}).get(key) or tinker._model_working(cfg, key)
        except Exception:
            pass
    return {"prompts": out, "models": models, "taken": time.strftime("%Y-%m-%d %H:%M:%S")}


def version_text(cfg, key, h):
    f = os.path.join(_activity(cfg), "PromptVersions", re.sub(r"[^\w.-]+", "_", key), str(h) + ".txt")
    try:
        with open(f, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


# ------------------------------------------------------------------ saving and reading
def _item_id(item_type, item_id):
    if item_type == "cue":
        a, _, b = str(item_id).partition("#")
        return norm(a) + "#" + b
    return norm(item_id)


def save(cfg, body, source="user"):
    item_type = str(body.get("item_type") or "")
    if item_type not in ASPECTS:
        raise ValueError("unknown item type " + item_type)
    aspect = str(body.get("aspect") or "overall")
    if aspect not in ASPECTS[item_type]:
        raise ValueError(f"'{aspect}' is not rated on a {item_type}")
    raw = body.get("item_id") or ""
    if not raw:
        raise ValueError("which item?")
    path = str(body.get("path") or raw.split("#")[0])
    score = body.get("score")
    if score in ("", None, 0, "0"):
        score = None
    else:
        score = int(score)
        if score not in LEVELS:
            raise ValueError("score must be 1 (very bad) to 5 (very good)")
    verdict = str(body.get("verdict") or "") or None
    if verdict and verdict not in VERDICTS:
        raise ValueError("unknown verdict " + verdict)
    note = str(body.get("note") or "").strip()[:2000]
    try:
        offset = float(body["offset_s"]) if body.get("offset_s") not in (None, "") else None
    except (TypeError, ValueError):
        offset = None
    ctx = context_for(cfg, path)
    extra = body.get("extra")
    if isinstance(extra, dict):
        ctx["extra"] = extra
    series = str(body.get("series") or ctx.get("series") or "")
    row = (time.strftime("%Y-%m-%d %H:%M:%S"), item_type, _item_id(item_type, raw), aspect, score, verdict, offset,
           note, source, series, str(ctx.get("episode") or ""), path, json.dumps(ctx, ensure_ascii=False))
    with _LOCK:
        con = _db(cfg)
        try:
            cur = con.execute("INSERT INTO ratings(time,item_type,item_id,aspect,score,verdict,offset_s,note,source,"
                              "series,episode,path,context) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            con.commit()
            rid = cur.lastrowid
        finally:
            con.close()
    _CACHE.clear()
    return {"ok": True, "id": rid, "item_id": row[2], "aspect": aspect, "score": score, "verdict": verdict,
            "source": source}


def save_ai(cfg, item_type, item_id, aspect, score, note="", verdict=None, offset_s=None, path="", series="", extra=None):
    return save(cfg, {"item_type": item_type, "item_id": item_id, "aspect": aspect, "score": score, "note": note,
                      "verdict": verdict, "offset_s": offset_s, "path": path or str(item_id).split("#")[0],
                      "series": series, "extra": extra}, source="ai")


def current(cfg, ids):
    """{raw id: {aspect: {"user": row, "ai": row}}} for the given ids (paths, or cue ids)"""
    want = {}
    for raw in ids or []:
        t = "cue" if "#" in str(raw) else ""
        want[_item_id(t, raw)] = raw
    if not want:
        return {}
    out = {}
    keys = list(want)
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        for r in _rows(cfg, "item_id IN (" + ",".join("?" * len(chunk)) + ")", tuple(chunk)):
            slot = out.setdefault(want[r["item_id"]], {}).setdefault(r["aspect"], {})
            slot[r["source"]] = {k: r[k] for k in ("time", "score", "verdict", "offset_s", "note", "item_type")}
    return out


def effective(slot):
    """your rating if you gave one, else the AI's (None when nobody rated)"""
    u, a = (slot or {}).get("user") or {}, (slot or {}).get("ai") or {}
    if u.get("score") or u.get("verdict"):
        return u.get("score"), "user"
    if a.get("score") or a.get("verdict"):
        return a.get("score"), "ai"
    return None, ""


def _effective_rows(cfg, where="", args=()):
    """one row per item+aspect: yours, else the AI's"""
    best = {}
    for r in _rows(cfg, where, args):
        if r["score"] is None and not r["verdict"]:
            continue
        k = (r["item_type"], r["item_id"], r["aspect"])
        if k not in best or (r["source"] == "user" and best[k]["source"] != "user"):
            best[k] = r
    return list(best.values())


# ------------------------------------------------------------------ what an episode is made of
def _newest(paths):
    paths = [p for p in paths if os.path.exists(p)]
    return max(paths, key=os.path.getmtime) if paths else ""


def episode_parts(cfg, video="", ep_dir=""):
    """{video, narration, pictures, script, track, series, name, lead} for a video (or an episode folder)"""
    L = cfg.get("layout") or {}
    if video and not ep_dir:
        ep_dir = os.path.dirname(os.path.dirname(video))
    out_dir = os.path.join(ep_dir, L.get("episode_output", "Output"))
    if not video:
        video = _newest([p for p in glob.glob(os.path.join(out_dir, "*.mp4")) if "_preview" not in p and not p.endswith(".part.mp4")])
    series, episode, _ = episode_of(cfg, video or ep_dir)
    src = _load(os.path.join(out_dir, "review_sources.json"), {}) or {}
    parts = {"_cfg": cfg, "video": video, "ep_dir": ep_dir, "series": src.get("series") or series, "name": src.get("name") or "",
             "narration": src.get("narration") or "", "pictures": src.get("pictures") or "", "script": "",
             "track": "", "lead": 0.0}
    if not parts["pictures"] or not os.path.isdir(parts["pictures"]):
        # older runs: the scenes folder whose name matches the video file (pictures may sit in another episode folder)
        stem = re.sub(r"[\W_]+", "", os.path.splitext(os.path.basename(video or ""))[0]).lower()
        cands = glob.glob(os.path.join(_root(cfg), L.get("experimental_series", "Series"), parts["series"] or "*",
                                       L.get("experimental_episode_collection", "Episodes"), "*",
                                       L.get("experimental_artwork", "03_Artwork"), "*", "shots.json"))
        hit = [c for c in cands if re.sub(r"[\W_]+", "", os.path.basename(os.path.dirname(c))[:-len("_scenes")]).lower() == stem]
        parts["pictures"] = os.path.dirname(_newest(hit)) if hit else ""
    if (not parts["narration"] or not os.path.isdir(parts["narration"])) and parts["pictures"]:
        plan = _load(os.path.join(parts["pictures"], "video_plan.json"), {}) or {}
        if plan.get("audio"):
            parts["narration"] = os.path.dirname(plan["audio"])
        if not parts["narration"]:
            shots = _load(os.path.join(parts["pictures"], "shots.json"), {}) or {}
            parts["narration"] = shots.get("source") or ""
    nar = parts["narration"]
    if nar and os.path.isdir(nar):
        hs = _load(os.path.join(nar, "hub_series.json"), {}) or {}
        parts["played_track"] = hs.get("track") or (_load(os.path.join(nar, "music_shape.json"), {}) or {}).get("track", "")
        parts["track"] = _original(cfg, parts["played_track"])
        ms = _load(os.path.join(nar, "music_shape.json"), {}) or {}      # emotion music: up to 3 tracks
        parts["tracks"] = [_original(cfg, x.get("file")) for x in ms.get("tracks") or [] if x.get("file")] or \
            ([parts["track"]] if parts["track"] else [])
        parts["series"] = parts["series"] or hs.get("genre", "")
        parts["lead"] = float((_load(os.path.join(nar, "music_shape.json"), {}) or {}).get("lead_s", 0) or 0)
        parts["name"] = parts["name"] or os.path.basename(nar)
    for s in (os.path.join(ep_dir, "script.txt"), os.path.join(ep_dir, L.get("experimental_script", "01_Script"), "script.txt"),
              os.path.join(nar, "script.txt") if nar else ""):
        if s and os.path.isfile(s):
            parts["script"] = s
            break
    return parts


def cues_of(parts):
    """every SFX cue of an episode, with where it was placed (narration time) and its id"""
    import self_review
    pics, nar = parts.get("pictures"), parts.get("narration")
    sj = os.path.join(pics or "", "shots.json")
    meta = _load(sj, {}) or {}
    tim = _load(os.path.join(nar or "", "timing.json"), []) or []
    out = []
    for si, s in enumerate(meta.get("shots") or []):
        if not isinstance(s, dict):
            continue
        for ci, c in enumerate(s.get("cues") or []):
            if not isinstance(c, dict):
                continue
            at = c.get("at")
            method = c.get("at_method") or ("word count estimate" if at is None else "")
            if at is None:
                at = self_review.estimate_old(tim, s, c.get("words", ""))
            orig = _original(parts.get("_cfg"), c.get("file", ""))
            out.append({"id": f"{sj}#{si}.{ci}", "scene": si + 1, "words": c.get("words", ""), "file": orig,
                        "played": c.get("file", ""), "treated": orig != c.get("file", ""),
                        "sound": os.path.basename(orig), "at": at, "method": method,
                        "video_at": (at + parts.get("lead", 0)) if at is not None else None,
                        "line": next((x.get("text", "") for x in tim if at is not None
                                      and float(x["start"]) - 0.05 <= at <= float(x["end"]) + 0.05), "")})
    return out


def _original(cfg, path):
    if not cfg or not path:
        return path
    try:
        import audio_screen
        return audio_screen.original_of(cfg, path)
    except Exception:
        return path


def sounds_used(parts):
    meta = _load(os.path.join(parts.get("pictures") or "", "shots.json"), {}) or {}
    used = set()
    for s in meta.get("shots") or []:
        if isinstance(s, dict):
            used |= {x for x in (s.get("ambience"), s.get("hit")) if x}
            used |= {c.get("file") for c in s.get("cues") or [] if isinstance(c, dict) and c.get("file")}
    return sorted({_original(parts.get("_cfg"), f) for f in used})


# ------------------------------------------------------------------ ratings -> selection
def file_scores(cfg, kind):
    """{norm(path): {"avg", "weight", "n"}} for music ("music") or sound effect files ("sfx"):
    your direct ratings (weight 1), your episode ratings of the BGM / SFX the episode used (0.5 / 0.25),
    and SFX cues marked 'wrong sound' (yours 1, the AI's 0.4)."""
    key = ("scores", kind, db_path(cfg))
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    acc = {}

    def add(path, score, w):
        if not path or not score:
            return
        a = acc.setdefault(norm(path), [0.0, 0.0, 0])
        a[0] += score * w
        a[1] += w
        a[2] += 1

    try:
        rows = _effective_rows(cfg)
    except sqlite3.Error:
        rows = []
    episodes = {}
    for r in rows:
        if r["item_type"] == kind:
            add(r["path"], r["score"], 1.0 if r["source"] == "user" else 0.5)
        elif r["item_type"] in ("video", "narration") and r["aspect"] in (("bgm", "clarity") if kind == "music" else ("sfx",)) \
                and r["source"] == "user":                 # the AI's own bgm/sfx estimate comes from these scores
            episodes.setdefault(r["path"], []).append(r)
        elif kind == "sfx" and r["item_type"] == "cue" and r["verdict"] == "wrong_sound":
            ctx = _load_json_str(r["context"])
            add((ctx.get("extra") or {}).get("file"), 1, 1.0 if r["source"] == "user" else 0.4)
    for video, rs in episodes.items():
        try:
            parts = episode_parts(cfg, video)
        except Exception:
            continue
        files = (parts.get("tracks") or [parts["track"]]) if kind == "music" else sounds_used(parts)
        for r in rs:
            for f in files:
                add(f, r["score"], 0.5 if kind == "music" else 0.25)
    out = {k: {"avg": round(v[0] / v[1], 2), "weight": round(v[1], 2), "n": v[2]} for k, v in acc.items() if v[1]}
    _CACHE[key] = (time.time(), out)
    return out


def _load_json_str(s):
    try:
        return json.loads(s or "{}") or {}
    except ValueError:
        return {}


def rejected(sc):
    """a file you do not want picked again: rated very bad, or bad on balance"""
    if not sc:
        return False
    return (sc["avg"] <= 1.5 and sc["weight"] >= 1) or (sc["avg"] < 2.5 and sc["weight"] >= 1.5)


def pick_track(cfg, folders, orig):
    import tools
    import sound_library
    md = cfg.get("music_dir") or ""
    cands = [f for sub in folders for f in glob.glob(os.path.join(md, sub, "*.mp3")) if "crate" not in f.lower()]
    if not cands:
        return orig(cfg, folders)
    scores = file_scores(cfg, "music")
    blocked = sound_library.blocked(cfg, "music")
    ok = [f for f in cands if norm(f) not in blocked and not rejected(scores.get(norm(f)))] or cands
    import audio_screen
    rows = {f: audio_screen.screen(cfg, f, "music") for f in ok}
    # 18 Oct 2026: when every track in these folders is rejected (a song, no voice band, does not sound like the mood)
    # the answer is '' and the caller tries a neighbouring mood - it used to take a rejected track anyway
    ok = [f for f in ok if audio_screen.effective(rows[f]) != "reject"]
    if not ok:
        cfg.setdefault("_music_misfit", []).append(
            f"{'/'.join(folders)}: " + "; ".join(f"{os.path.basename(f)} - {(rows[f].get('why') or ['rejected'])[-1]}"
                                                 for f in rows))
        return ""
    skip = float(cfg.get("music_harsh_skip", 19))
    ok = [f for f in ok if tools.harshness(cfg, f) < skip] or ok
    up = os.path.join(cfg["media_dir"], "bhay_music_used.json")
    used = _load(up, {}) or {}

    def bonus(f):
        sc = scores.get(norm(f))
        return 0.0 if not sc else (sc["avg"] - 3) * min(sc["weight"], 3) / 3      # -2 .. +2 "uses"

    # Experimental (17 Oct 2026): a use count per series, and a track from the last 3 episodes of the series is skipped
    # while another one fits. Gyaan, Kareeb, Sukoon and Working leave "_music_series" unset: the shared count as before.
    series = cfg.get("_music_series") if (cfg.get("experimental_mode") or cfg.get("_experimental_workspace")) else ""
    sp = os.path.join(cfg["media_dir"], "music_used.json")
    per = (_load(sp, {}) or {}) if series else {}
    mine = per.get(series) or {}
    if series:
        ep, recent, last = cfg.get("_music_episode") or "", mine.get("_recent") or [], []
        for e, _f in reversed(recent):
            if e != ep and e not in last:
                last.append(e)
        avoid = {f for e, f in recent if e in last[:3]}
        ok = [f for f in ok if f not in avoid] or ok
        count = lambda f: (mine.get(f) or {}).get("count", 0)
    else:
        count = lambda f: used.get(f, 0)
    t = min(ok, key=lambda f: (count(f) - bonus(f), random.random()))
    used[t] = used.get(t, 0) + 1
    with open(up, "w", encoding="utf-8") as fh:
        json.dump(used, fh, indent=1)
    if series:
        mine[t] = {"count": count(t) + 1, "last": time.strftime("%Y-%m-%d %H:%M")}
        if [ep, t] not in mine.get("_recent", []):
            mine["_recent"] = (mine.get("_recent") or [])[-29:] + [[ep, t]]
        per[series] = mine
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump(per, fh, ensure_ascii=False, indent=1)
    if audio_screen.effective(rows.get(t) or {}) == "treat":
        return audio_screen.treated(cfg, t, "music", rows.get(t))      # voice-band dip etc.; original untouched
    return t


def filter_sfx(cfg, lib):
    import sound_library
    scores = file_scores(cfg, "sfx")
    blocked = sound_library.blocked(cfg, "sfx")
    import audio_screen
    out = {}
    for k, f in lib.items():
        if norm(f) in blocked or rejected(scores.get(norm(f))):
            continue
        row = audio_screen.screen(cfg, f, "sfx")
        eff = audio_screen.effective(row)
        if eff == "reject":
            continue
        out[k] = audio_screen.treated(cfg, f, "sfx", row) if eff == "treat" else f
    return out or lib


def install(cfg):
    """at hub start: Experimental jobs pick music and effects with your ratings (Working mode unchanged)"""
    import tools
    if not getattr(tools._horror_track, "_feedback", False):
        orig_track = tools._horror_track

        def _horror_track(cfg, folders=("Dark", "Tension", "Eerie")):
            if not cfg.get("experimental_mode"):
                return orig_track(cfg, folders)
            try:
                return pick_track(cfg, folders, orig_track)
            except Exception:
                return orig_track(cfg, folders)
        _horror_track._feedback = True
        tools._horror_track = _horror_track
    if not getattr(tools._sfx_names, "_feedback", False):
        orig_sfx = tools._sfx_names

        def _sfx_names(cfg):
            lib = orig_sfx(cfg)
            if not cfg.get("experimental_mode"):
                return lib
            try:
                return filter_sfx(cfg, lib)
            except Exception:
                return lib
        _sfx_names._feedback = True
        tools._sfx_names = _sfx_names
    if not getattr(tools.shape_mix, "_feedback", False):
        orig_mix = tools.shape_mix

        def shape_mix(ctx, name, track="", tail="1min", music_db=None):
            cfg = ctx["cfg"]
            if cfg.get("experimental_mode") and music_db in (None, "") and track:
                try:
                    import audio_screen
                    src = audio_screen.original_of(cfg, track)
                    row = audio_screen.cached(cfg, src)
                    if src != track and row.get("verdict") in ("risky", "reject") and any("speech band" in w for w in row.get("why") or []):
                        base = float((tools.load_audio_index(cfg).get("shape") or {}).get("story_db", -18.0))
                        duck = float(audio_screen.LIMITS["music"].get("duck_db", 4))
                        music_db = abs(base) + duck
                        ctx["say"](f"   this music competes with the voice: treated copy, and {duck:.0f} dB lower under the story")
                except Exception:
                    pass
            return orig_mix(ctx, name, track, tail, music_db)
        shape_mix._feedback = True
        tools.shape_mix = shape_mix
    import sfx_lib
    if not getattr(sfx_lib.mark_used, "_feedback", False):
        orig_mark = sfx_lib.mark_used

        def mark_used(cfg, files, video):
            try:
                import audio_screen
                files = [audio_screen.original_of(cfg, f) for f in files]
            except Exception:
                pass
            return orig_mark(cfg, files, video)
        mark_used._feedback = True
        sfx_lib.mark_used = mark_used


# ------------------------------------------------------------------ insights
SUGGEST = {
    "script": [("Edit this series' writing rules (story shape, topics, tellings)", "rules", ""),
               ("Ask the AI to revise the rules from your notes", "ai", "genre_rules")],
    "voice": [("Change expressiveness (ex), pause and temperature for this series", "tables", "tools.GENRES"),
              ("Change where the narration pauses and breathes", "tables", "tools.DIRECTION")],
    "bgm": [("Rate tracks on the Sounds page: tracks rated bad are no longer picked", "sounds", "music"),
            ("Change the mood folders this series takes music from", "tables", "tools.GENRES"),
            ("Get more tracks for this mood (Sounds → Get more)", "sounds", "get")],
    "sfx": [("Fewer or different effects per minute for this series", "tables", "sfx_lib.SERIES"),
            ("Rate effect files on the Sounds page: bad ones are skipped", "sounds", "sfx"),
            ("Ask the AI to revise the scene planner from your notes", "ai", "tools.SCENES_ASK")],
    "sfx_timing": [("Word-timed SFX is on for Experimental runs: effects are placed at the spoken word (Whisper)", "", ""),
                   ("Mark cues early / late in the video review; the planner's cue rules can be tightened", "ai", "tools.SCENES_ASK")],
    "picture_theme": [("Change the image style of this series", "tables", "tools.STYLES"),
                      ("Ask the AI to revise the scene planner from your notes", "ai", "tools.SCENES_ASK")],
    "picture_quality": [("Try another image engine or model", "models", "image_engine"),
                        ("Make the picture judge stricter", "ai", "tools.SCENE_ASK"),
                        ("Turn on 'best pictures' (2 versions per scene, the judge keeps the better one)", "", "")],
    "clarity": [("Music or effects that mask speech are now treated or skipped: check flagged files on the Sounds page", "sounds", "music"),
                ("Lower the music for this series, or pick calmer mood folders", "tables", "tools.GENRES"),
                ("Make the audio screening stricter (Tinker → Series settings → audio_screen.LIMITS)", "tables", "audio_screen.LIMITS")],
    "subtitles": [("Re-time the subtitles on the Repair page", "", ""),
                  ("Change where lines break and pause", "tables", "tools.DIRECTION")],
}


def _avg(xs):
    xs = [x for x in xs if x]
    return round(sum(xs) / len(xs), 2) if xs else None


def insights(cfg):
    rows = _effective_rows(cfg)
    videos = [r for r in rows if r["item_type"] in ("video", "narration") and r["score"]]
    by_series = {}
    for r in videos:
        s = by_series.setdefault(r["series"] or "other", {})
        s.setdefault(r["aspect"], []).append(r)
    series = []
    for sid, asp in sorted(by_series.items()):
        aspects = {a: {"avg": _avg([r["score"] for r in rs]), "n": len(rs),
                       "user": sum(r["source"] == "user" for r in rs)} for a, rs in asp.items()}
        weak = sorted([a for a in aspects if a != "overall" and aspects[a]["avg"] is not None and aspects[a]["avg"] < 3.3],
                      key=lambda a: aspects[a]["avg"])[:3]
        tips = []
        for a in weak:
            notes = [r["note"] for r in sorted(asp[a], key=lambda r: r["time"], reverse=True) if r["note"] and r["score"] <= 2 and r["source"] == "user"][:3]
            tips.append({"aspect": a, "label": ASPECT_LABELS.get(a, a), "avg": aspects[a]["avg"], "notes": notes,
                         "suggest": [{"text": t, "where": w, "key": (k if k != "genre_rules" else "genre_rules." + sid)}
                                     for t, w, k in SUGGEST.get(a, [])]})
        series.append({"series": sid, "aspects": aspects, "weak": tips,
                       "videos": len({r["item_id"] for r in asp.get("overall", [])})})
    # prompt / model versions compared by the overall score of the videos made with them
    overall = [r for r in videos if r["aspect"] == "overall"]
    versions = {}
    for r in overall:
        ctx = _load_json_str(r["context"])
        pv = ctx.get("prompt_versions") or {}
        for key, h in (pv.get("prompts") or {}).items():
            versions.setdefault(("prompt", key), {}).setdefault(h, []).append(r["score"])
        for key, v in (pv.get("models") or {}).items():
            versions.setdefault(("model", key), {}).setdefault(v, []).append(r["score"])
    compare = []
    for (kind, key), vs in versions.items():
        if len(vs) >= 2:
            compare.append({"kind": kind, "key": key, "versions": sorted(
                [{"version": v, "avg": _avg(s), "n": len(s)} for v, s in vs.items()], key=lambda x: -(x["avg"] or 0))})
    # how close the AI self-review is to you
    pairs = {}
    for r in _rows(cfg):
        if r["score"] is None:
            continue
        pairs.setdefault((r["item_type"], r["item_id"], r["aspect"]), {})[r["source"]] = r["score"]
    agree = {}
    for (t, _i, a), d in pairs.items():
        if "user" in d and "ai" in d:
            agree.setdefault(a, []).append(abs(d["user"] - d["ai"]))
    agreement = [{"aspect": a, "label": ASPECT_LABELS.get(a, a), "pairs": len(v), "mean_gap": round(sum(v) / len(v), 2)}
                 for a, v in sorted(agree.items())]

    def ranked(kind):
        sc = file_scores(cfg, kind)
        items = sorted(sc.items(), key=lambda kv: -kv[1]["avg"])
        top = [{"file": k, "name": os.path.basename(k), **v} for k, v in items[:6]]
        low = [{"file": k, "name": os.path.basename(k), **v} for k, v in items[::-1][:6] if v["avg"] < 3]
        return {"best": top, "worst": low, "skipped": sum(rejected(v) for v in sc.values())}

    cues = [r for r in rows if r["item_type"] == "cue" and r["verdict"]]
    verdicts = {}
    for r in cues:
        verdicts.setdefault(r["source"], {}).setdefault(r["verdict"], 0)
        verdicts[r["source"]][r["verdict"]] += 1
    counts = {}
    for r in _rows(cfg):
        counts.setdefault(r["source"], 0)
        counts[r["source"]] += 1
    return {"series": series, "compare": compare, "agreement": agreement, "music": ranked("music"),
            "sfx": ranked("sfx"), "cues": verdicts, "counts": counts, "db": db_path(cfg),
            "levels": LEVELS, "aspect_labels": ASPECT_LABELS}


# ------------------------------------------------------------------ AI revision of a prompt from your notes
def suggest_prompt(cfg, key, series="", aspect=""):
    import story
    import tinker
    if not tinker._ORIG:
        tinker.install(cfg)
    rows = [r for r in _effective_rows(cfg) if r["note"] and (not series or r["series"] == series)
            and (not aspect or r["aspect"] in (aspect, "overall"))]
    bad = [f"- [{ASPECT_LABELS.get(r['aspect'], r['aspect'])}, {LEVELS.get(r['score'], r['verdict'] or '')}] {r['note']}"
           for r in sorted(rows, key=lambda r: r["time"], reverse=True) if (r["score"] or 3) <= 2 or r["verdict"] in ("early", "late", "wrong_sound", "not_needed")][:25]
    good = [f"- [{ASPECT_LABELS.get(r['aspect'], r['aspect'])}] {r['note']}"
            for r in sorted(rows, key=lambda r: r["time"], reverse=True) if (r["score"] or 0) >= 4][:10]
    if not bad:
        raise ValueError("no low ratings with a note yet for this - add a short note when you rate something bad")
    if key.startswith("genre_rules."):
        gid = key.split(".", 1)[1]
        exp_cfg = dict(cfg, media_dir=tinker._exp_media(cfg))
        current = json.dumps(story.genre_rules(exp_cfg, gid), ensure_ascii=False, indent=1)
        kind = "the writing rules (a JSON object) of the series '" + gid + "'"
        shape = 'Answer ONLY JSON: {"rules": <the full revised JSON object, same keys>, "changes": ["..."]}'
    else:
        es = tinker.exp_settings(cfg)
        current = es["prompts"].get(key) or tinker._working_value("prompts", key)
        ph = sorted(set(tinker._PH.findall(tinker._ORIG.get(key, ""))))
        kind = "a prompt used by an automatic video pipeline (" + tinker.PROMPT_LABELS.get(key, key) + ")"
        shape = ("Keep every placeholder exactly as written: " + (", ".join("{" + p + "}" for p in ph) or "none") +
                 "; write any other literal brace as {{ or }}. Keep the same answer format the prompt asks for. "
                 'Answer ONLY JSON: {"prompt": "<the full revised prompt>", "changes": ["..."]}')
    ask = (f"You improve {kind}. The person who watches the results rated them and wrote these notes.\n"
           f"PROBLEMS:\n" + "\n".join(bad) + ("\nWHAT WORKED:\n" + "\n".join(good) if good else "") +
           f"\n\nRevise it so the problems stop, change only what the notes call for, keep what worked.\n{shape}\n\n"
           f"CURRENT:\n{current}")
    text, model = story.ask(cfg, ask, "auto", 0.3, True)
    d = json.loads(text)
    out = {"key": key, "model": model, "changes": [str(x) for x in (d.get("changes") or [])][:12], "notes_used": len(bad)}
    if key.startswith("genre_rules."):
        if not isinstance(d.get("rules"), dict):
            raise ValueError("the AI did not return rules")
        out["value"] = d["rules"]
        out["section"] = "genre_rules"
        out["save_key"] = key.split(".", 1)[1]
    else:
        out["value"] = str(d.get("prompt") or "")
        if not out["value"].strip():
            raise ValueError("the AI did not return a prompt")
        tinker._check_prompt(key, out["value"])
        out["section"] = "prompts"
        out["save_key"] = key
    out["current"] = current
    return out


# ------------------------------------------------------------------ export for fine-tuning
def _read(p, n=200000):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read(n)
    except OSError:
        return ""


def export(cfg):
    rows = _effective_rows(cfg)
    n_user = sum(1 for r in rows if r["source"] == "user")
    if not rows:
        return {"ok": True, "dir": "", "counts": {}, "total": 0, "user": 0, "ai": 0, "db": db_path(cfg),
                "message": "0 ratings saved yet, so there is nothing to export. Rate some videos (Library > Review), "
                           "pictures or sounds first, or run the AI self-review on a video."}
    stamp = time.strftime("%Y%m%d_%H%M%S")
    L = cfg.get("layout") or {}
    out_dir = os.path.join(_activity(cfg), L.get("experimental_reports", "Reports"), "finetune_" + stamp)
    os.makedirs(out_dir, exist_ok=True)
    counts = {}

    def write(name, items):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        counts[name] = len(items)

    # scripts: the script rating, else the video's script aspect
    scripts = {}
    for r in rows:
        if not r["score"]:
            continue
        if r["item_type"] == "script" or (r["item_type"] == "video" and r["aspect"] == "script"):
            path = r["path"]
            if r["item_type"] == "video":
                try:
                    path = episode_parts(cfg, r["path"]).get("script") or ""
                except Exception:
                    path = ""
            text = _read(path)
            if not text.strip():
                continue
            ctx = _load_json_str(r["context"])
            k = norm(path)
            if k not in scripts or r["source"] == "user":
                scripts[k] = {"series": r["series"], "score": r["score"], "label": LEVELS[r["score"]], "note": r["note"],
                              "rated_by": r["source"], "topic": (ctx.get("settings") or {}).get("topic", ""),
                              "prompt_versions": (ctx.get("prompt_versions") or {}).get("prompts", {}),
                              "file": path, "script": text}
    write("scripts.jsonl", list(scripts.values()))
    pairs = []
    by = {}
    for s in scripts.values():
        by.setdefault(s["series"], []).append(s)
    for sid, ss in by.items():
        good = [s for s in ss if s["score"] >= 4]
        bad = [s for s in ss if s["score"] <= 2]
        for g in good:
            for b in bad:
                pairs.append({"series": sid, "prompt": f"Write a {sid} story" + (f" about: {g['topic']}" if g["topic"] else ""),
                              "chosen": g["script"], "rejected": b["script"], "chosen_score": g["score"],
                              "rejected_score": b["score"]})
    write("script_pairs.jsonl", pairs[:2000])
    pics = []
    for r in rows:
        if r["item_type"] != "picture" or not r["score"]:
            continue
        prompt = _read(os.path.splitext(r["path"])[0] + ".prompt.txt", 4000).split("\n\n")[0]
        pics.append({"image": r["path"], "aspect": r["aspect"], "score": r["score"], "label": LEVELS[r["score"]],
                     "note": r["note"], "rated_by": r["source"], "series": r["series"], "prompt": prompt})
    write("pictures.jsonl", pics)
    cues = []
    for r in rows:
        if r["item_type"] != "cue" or not r["verdict"]:
            continue
        ex = _load_json_str(r["context"]).get("extra") or {}
        cues.append({"series": r["series"], "verdict": r["verdict"], "offset_s": r["offset_s"], "rated_by": r["source"],
                     "words": ex.get("words", ""), "sound": os.path.basename(ex.get("file", "")),
                     "line": ex.get("line", ""), "note": r["note"]})
    write("sfx_cues.jsonl", cues)
    write("all_ratings.jsonl", [{k: (v if k != "context" else _load_json_str(v)) for k, v in r.items()} for r in rows])
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as f:
        f.write("HubGPT ratings export " + stamp + "\n\n"
                "scripts.jsonl       one rated script per line (score 1-5, your note, the prompt versions used)\n"
                "script_pairs.jsonl  chosen (good/very good) vs rejected (bad/very bad) scripts of the same series,\n"
                "                    the usual input for preference tuning (DPO / ORPO)\n"
                "pictures.jsonl      image prompt + picture rating: teaches the prompt writer / picture judge\n"
                "sfx_cues.jsonl      sound effect placements you judged (on time, early, late, wrong sound)\n"
                "all_ratings.jsonl   every current rating (yours win over the AI's)\n\n"
                "Ratings by the AI self-review have rated_by = ai; keep only rated_by = user for training if you prefer.\n")
    return {"ok": True, "dir": out_dir, "counts": counts, "total": len(rows), "user": n_user, "ai": len(rows) - n_user,
            "db": db_path(cfg)}


# ------------------------------------------------------------------ episode review data for the page
def episode_review(cfg, video):
    parts = episode_parts(cfg, video)
    cues = cues_of(parts)
    meta = _load(os.path.join(parts.get("pictures") or "", "shots.json"), {}) or {}
    pictures = []
    for s in (meta.get("shots") or []) + (meta.get("moments") or []):
        if isinstance(s, dict) and s.get("file"):
            f = os.path.join(parts["pictures"], s["file"])
            if os.path.isfile(f):
                pictures.append({"file": f, "judge": s.get("score"), "text": str(s.get("text") or s.get("words") or "")[:160]})
    L = cfg.get("layout") or {}
    out_dir = os.path.join(parts["ep_dir"], L.get("episode_output", "Output"))
    review = _load(os.path.join(out_dir, "self_review.json"), {}) or {}
    ids = [video] + [c["id"] for c in cues] + [p["file"] for p in pictures] + [c["file"] for c in cues if c["file"]]
    if parts.get("script"):
        ids.append(parts["script"])
    if parts.get("track"):
        ids.append(parts["track"])
    ratings = current(cfg, ids)
    parts = {k: v for k, v in parts.items() if k != "_cfg"}
    return {"parts": parts, "cues": cues, "pictures": pictures, "review": review, "ratings": ratings,
            "aspects": ASPECTS["video"], "aspect_labels": ASPECT_LABELS, "levels": LEVELS, "verdicts": VERDICTS,
            "credits": os.path.join(out_dir, "credits.txt") if os.path.isfile(os.path.join(out_dir, "credits.txt")) else ""}


# ------------------------------------------------------------------ the routes (server.py dispatches /api/rate*)
def handle_get(cfg, p, q):
    if p == "/api/rate/insights":
        return 200, insights(cfg)
    if p == "/api/rate/episode":
        v = q.get("video", "")
        if not v or not os.path.isfile(v):
            return 400, {"error": "choose a video"}
        return 200, episode_review(cfg, v)
    if p == "/api/rate/version":
        return 200, {"text": version_text(cfg, q.get("key", ""), q.get("v", ""))}
    return 404, {"error": "unknown"}


def handle_post(cfg, p, body, jobs=None):
    if p == "/api/rate/save":
        return 200, save(cfg, body)
    if p == "/api/rate/get":
        return 200, {"ratings": current(cfg, body.get("ids") or []), "levels": LEVELS}
    if p == "/api/rate/export":
        return 200, export(cfg)
    if p == "/api/rate/suggest_prompt":
        return 200, suggest_prompt(cfg, str(body.get("key") or ""), str(body.get("series") or ""), str(body.get("aspect") or ""))
    if p == "/api/rate/review":
        return 200, queue_review(cfg, body, jobs)
    return 404, {"error": "unknown"}


def queue_review(cfg, body, jobs):
    """an Experimental job that runs the AI self-review of one video"""
    import experimental_workspace
    video = str(body.get("video") or "")
    if not video or not os.path.isfile(video):
        raise ValueError("choose a video")
    parts = episode_parts(cfg, video)
    if not parts.get("narration"):
        raise ValueError("could not find the narration of this video (only Experimental genre videos can be reviewed)")
    base = experimental_workspace.initialize(cfg)
    L = cfg.get("layout") or {}
    lib = os.path.join(base, L.get("experimental_library", "Library"))
    meta = {"root": base, "series": parts["series"], "episode_dir": "", "media_dir": lib,
            "sukoonvoice_media": os.path.dirname(os.path.dirname(parts["narration"])), "layout": L}
    checks = [c for c in (body.get("checks") or []) if c in CHECKS] or list(CHECKS)
    steps = [{"fn": "exp_self_review", "label": "AI self-review: " + ", ".join(CHECKS[c] for c in checks),
              "args": {"video": video, "narration": parts["narration"], "pictures": parts["pictures"],
                       "genre": parts["series"], "name": parts["name"], "checks": checks}}]
    j = jobs.add("exp_review", "AI review: " + os.path.basename(video), steps,
                 [os.path.dirname(video)], {"_experimental_workspace": meta})
    return {"ok": True, "id": j["id"]}


CHECKS = {"sfx_timing": "SFX timing", "voice": "voice and subtitles", "clarity": "speech clarity", "pictures": "pictures",
          "script": "script", "sounds": "music and effects"}
