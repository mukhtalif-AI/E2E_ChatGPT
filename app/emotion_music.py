"""emotion_music.py - music and effects that follow how heavy the story's emotion is (Experimental genre runs only).

Plan: EMOTION_MUSIC_PLAN.md in the hub folder. Step 1 (17 Oct 2026): the emotion map only - nothing here changes the
sound yet. After the scene plan (and the Whisper word times) the step exp_emotion_map writes emotion_map.json next to
shots.json:
  scenes    each director scene with a mood (one of the series' music folders) and a weight 0-3
  sections  neighbouring scenes merged into at most 3 music sections, as few track changes as possible
  turns     the few heaviest lines (twist, death, reveal...) with the time their first words are spoken
The AI (director_model) proposes; the rules below decide (budgets, smoothing, no jumping back to a track). With no AI
answer the map comes from the emotion marks ([उदास] ...) and the scene plan's hits / close-up moments.
Weight: 0 calm telling, 1 mild feeling, 2 strong (reveal, scare, confession), 3 heavy (death, the final twist).

Run on a finished episode (no GPU; the AI part needs Ollama, --no-ai uses the marks only):
    python app\\emotion_music.py "D:\\MukhtalifGPT\\Series\\kisse\\Episodes\\Kisse-Fathers-Old-Tin-Box" [--no-ai] [--out file]
"""
import glob, json, os, re, sys, time

# per series: what the emotion music may do. Gyaan, Kareeb (and Sukoon, which never comes here) keep one track.
SERIES = {
    "bhay": {"on": True, "base": "Dark", "sections": True, "max_tracks": 3, "min_section_s": 90, "crossfade_s": 3.5,
             "curve": True, "strength": 1.0, "stingers": True, "budget": {"3": 1, "2": 5, "1": 6},
             "drop_s": {"1": 0, "2": 1.0, "3": 2.0}, "soft_note": "drone", "return_mood": "Tension", "return_delay_s": 3},
    "rahasya": {"on": True, "base": "Tension", "sections": True, "max_tracks": 3, "min_section_s": 90, "crossfade_s": 3.5,
                "curve": True, "strength": 0.8, "stingers": True, "budget": {"3": 1, "2": 4, "1": 6},
                "drop_s": {"1": 0, "2": 1.0, "3": 2.0}, "soft_note": "drone", "return_mood": "Dark", "return_delay_s": 3},
    "kisse": {"on": True, "base": "Calm", "sections": True, "max_tracks": 3, "min_section_s": 90, "crossfade_s": 3.5,
              "curve": True, "strength": 0.7, "stingers": True, "budget": {"3": 1, "2": 3, "1": 5},
              "drop_s": {"1": 0, "2": 1.0, "3": 2.5}, "soft_note": "note", "return_mood": "Calm", "return_delay_s": 3},
    "romanch": {"on": True, "base": "Love", "sections": True, "max_tracks": 2, "min_section_s": 90, "crossfade_s": 3.5,
                "curve": True, "strength": 0.5, "stingers": True, "budget": {"3": 0, "2": 1, "1": 3},
                "drop_s": {"1": 0, "2": 1.0, "3": 1.0}, "soft_note": "", "return_mood": "Love", "return_delay_s": 2},
    "gyaan": {"on": False},
    "kareeb": {"on": False},
}
MAX_TRACKS = 3                       # your rule: never more than 3 tracks in one story (a series can only go lower)
HEAVY_KINDS = ("death", "twist", "betrayal", "loss")    # only these may be weight 3
KINDS = ("twist", "reveal", "death", "loss", "betrayal", "scare", "confession", "realisation", "relief")
TURN_GAP_S = 30                      # two stingers at least this far apart
# emotion mark -> music folders that fit it, best first (the first one the series has wins)
TAG_MOOD = {"डर": ("Dark", "Tension", "Eerie"), "रहस्य": ("Tension", "Dark", "Eerie"), "उदास": ("Sad", "Calm"),
            "प्यार": ("Love", "Calm"), "गुस्सा": ("Tension", "Dark"), "उत्साह": ("Happy", "Love"),
            "फुसफुसाहट": ("Eerie", "Calm", "Love")}
TAG_WEIGHT = {"डर": 1, "रहस्य": 1, "उदास": 1, "प्यार": 1, "गुस्सा": 1, "उत्साह": 1, "फुसफुसाहट": 0}
TAG_RE = re.compile(r"^\s*\[([^\]]+)\]\s*")

MAP_ASK = (
    "You are the music director of a {desc} video (channel '{label}'). The story is read aloud in Hindi and cut into "
    "numbered scenes below (time, what happens, the spoken text, and the narrator's feeling marks).\n"
    "For EACH scene choose the music MOOD from this list only: {moods}; and a WEIGHT for how heavy the emotion is "
    "there: 0 = calm telling or plain facts, 1 = a mild feeling (a memory, unease, tenderness), 2 = strong (a reveal, "
    "a scare, a confession, a realisation), 3 = heavy (a death, a betrayal, the final twist, a loss that lands). Most "
    "scenes are 0 or 1. The mood should stay the same over long stretches: change it only where the story really "
    "turns.\n"
    "Then list the TURNS: the few single lines where the story turns hard (at most {turns} in these scenes, often "
    "none). For each give \"n\" (scene number), \"words\" (the first 2-5 words of that line, copied EXACTLY from the "
    "scene text, in Devanagari, never translated), \"kind\" (one of: {kinds}) and \"weight\" (1-3).\n"
    "Answer ONLY JSON: {{\"scenes\": [{{\"n\": 1, \"mood\": \"...\", \"weight\": 0, \"why\": \"a few English words\"}}, ...], "
    "\"turns\": [{{\"n\": 3, \"words\": \"...\", \"kind\": \"twist\", \"weight\": 2}}]}} with every scene from "
    "{first} to {last}.\n\nSCENES:\n{scenes}")
ASK_BATCH = 10                       # scenes per question (a 17-scene story is two questions; keeps the reply short)


def _load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def settings(genre):
    s = dict(SERIES.get(genre) or {"on": False})
    if s.get("on"):
        s["max_tracks"] = max(1, min(MAX_TRACKS, int(s.get("max_tracks") or MAX_TRACKS)))
    return s


def series_moods(cfg, genre):
    """the music folders this series may use (tools.GENRES), plus the return mood; folders that exist come first"""
    import tools
    want = list((tools.GENRES.get(genre) or {}).get("music") or [])
    s = settings(genre)
    for m in (s.get("base"), s.get("return_mood")):
        if m and m not in want:
            want.append(m)
    md = cfg.get("music_dir") or ""
    have = [m for m in want if md and os.path.isdir(os.path.join(md, m))]
    return have + [m for m in want if m not in have] if have else want


def _norm(t):
    return re.sub(r"[^\w\s]", "", str(t)).split()


def line_tags(script_text, tim):
    """the feeling mark of each timing.json line: timing.json drops the [उदास] tags, so match the lines by their words"""
    tagged = []
    for ln in str(script_text or "").replace("\r\n", "\n").split("\n"):
        m = TAG_RE.match(ln)
        words = _norm(TAG_RE.sub("", ln))
        if words:
            tagged.append((m.group(1).strip() if m else "", " ".join(words)))
    out, k = [], 0
    for x in tim:
        w = " ".join(_norm(x.get("text")))
        tag = ""
        if w:
            for j in list(range(k, len(tagged))) + list(range(0, k)):     # in order; wrap round once
                if w[:40] and (w[:40] in tagged[j][1] or tagged[j][1][:40] in w):
                    tag, k = tagged[j][0], j
                    break
        out.append(tag)
    return out


def _scene_tags(shots, tim, tags):
    res = []
    for s in shots:
        a, b = float(s.get("start", 0)), float(s.get("end", 0))
        c = {}
        for x, t in zip(tim, tags):
            mid = (float(x["start"]) + float(x["end"])) / 2
            if t and a - 0.5 <= mid <= b + 0.5:
                c[t] = c.get(t, 0) + 1
        res.append(c)
    return res


def _mood_from_tags(counts, moods, base):
    best = sorted(counts.items(), key=lambda kv: -kv[1])
    for tag, _ in best:
        for m in TAG_MOOD.get(tag, ()):
            if m in moods:
                return m
    return base if base in moods else (moods[0] if moods else base)


def _ask(cfg, genre, shots, stags, moods, budget_hint, say, ask=None):
    """the AI's moods, weights and turns for all scenes (batches of ASK_BATCH) -> ({n: scene}, [turns], model)"""
    import tools
    G = tools.GENRES.get(genre) or tools.GENRES["bhay"]
    got, turns, model = {}, [], ""
    for k in range(0, len(shots), ASK_BATCH):
        part = list(range(k, min(len(shots), k + ASK_BATCH)))
        rows = []
        for i in part:
            s = shots[i]
            marks = ", ".join(f"{t} {n}" for t, n in sorted(stags[i].items(), key=lambda kv: -kv[1])) or "none"
            rows.append(f"{i + 1}. [{float(s.get('start', 0)):.0f}-{float(s.get('end', 0)):.0f} s] "
                        f"happens: {str(s.get('happens') or '').strip()[:200]}\n   marks: {marks}\n"
                        f"   text: {str(s.get('text') or '').strip()[:700]}")
        prompt = MAP_ASK.format(desc=G.get("desc", genre), label=G.get("label", genre), moods=", ".join(moods),
                                turns=budget_hint, kinds=", ".join(KINDS), first=part[0] + 1, last=part[-1] + 1,
                                scenes="\n".join(rows))
        try:
            t, model = (ask or tools._ask_step)(cfg, "director_model", prompt, 0.3, say)
            d = tools._loads(t)
        except Exception as e:
            say(f"   (emotion map: the AI gave no usable answer for scenes {part[0] + 1}-{part[-1] + 1}: {e} - "
                "using the feeling marks there)")
            continue
        for x in d.get("scenes") or [] if isinstance(d, dict) else []:
            try:
                n = int(x.get("n")) - 1
            except (TypeError, ValueError, AttributeError):
                continue
            if n in part:
                got[n] = x
        for x in d.get("turns") or [] if isinstance(d, dict) else []:
            if isinstance(x, dict) and not any(str(x.get("n")) == str(o.get("n")) and x.get("words") == o.get("words")
                                               for o in turns):
                turns.append(x)
    return got, turns, model


def _smooth(w, fixed):
    """weight changes by at most 1 from one scene to the next, except where a turn happens"""
    w = list(w)
    for i in range(1, len(w)):
        if i not in fixed and abs(w[i] - w[i - 1]) > 1:
            w[i] = w[i - 1] + (1 if w[i] > w[i - 1] else -1)
    return w


def make_sections(scenes, s, total):
    """scenes (each with start, end, mood, weight) -> music sections. A change of track needs a real change of mood
    that lasts min_section_s; a story never goes back to a track it already left; never more than max_tracks."""
    if not scenes:
        return []
    if not s.get("sections"):
        dur = {}
        for x in scenes:
            dur[x["mood"]] = dur.get(x["mood"], 0) + x["end"] - x["start"]
        secs = [{"from_scene": 0, "to_scene": len(scenes) - 1, "mood": max(dur, key=dur.get)}]
    else:
        secs = []
        for i, x in enumerate(scenes):                                  # 1. neighbours with the same mood merge
            if secs and secs[-1]["mood"] == x["mood"]:
                secs[-1]["to_scene"] = i
            else:
                secs.append({"from_scene": i, "to_scene": i, "mood": x["mood"]})
        span = lambda c: scenes[c["to_scene"]]["end"] - scenes[c["from_scene"]]["start"]

        def merge(j):                                                   # section j joins its longer neighbour
            if len(secs) < 2:
                return
            if j == 0:
                k = 1
            elif j == len(secs) - 1:
                k = j - 1
            else:
                k = j - 1 if span(secs[j - 1]) >= span(secs[j + 1]) else j + 1
            lo, hi = min(j, k), max(j, k)
            mood = secs[k]["mood"] if span(secs[k]) >= span(secs[j]) else secs[j]["mood"]
            secs[lo:hi + 1] = [{"from_scene": secs[lo]["from_scene"], "to_scene": secs[hi]["to_scene"], "mood": mood}]

        changed = True
        while changed and len(secs) > 1:
            changed = False
            short = [j for j in range(len(secs)) if span(secs[j]) < float(s.get("min_section_s", 90))]
            if short:                                                    # 2. too short for its own track
                merge(min(short, key=lambda j: span(secs[j])))
                changed = True
                continue
            seen = set()
            for j, c in enumerate(secs):                                 # 3. no jumping back to an earlier track
                if c["mood"] in seen and j > 0 and secs[j - 1]["mood"] != c["mood"]:
                    secs[j - 1]["to_scene"] = c["to_scene"]
                    del secs[j]
                    changed = True
                    break
                seen.add(c["mood"])
            if changed:
                continue
            for j in range(1, len(secs)):                               # 4. same mood side by side again: one section
                if secs[j]["mood"] == secs[j - 1]["mood"]:
                    secs[j - 1]["to_scene"] = secs[j]["to_scene"]
                    del secs[j]
                    changed = True
                    break
            if not changed and len(secs) > int(s.get("max_tracks", MAX_TRACKS)):   # 5. the cap
                merge(min(range(len(secs)), key=lambda j: span(secs[j])))
                changed = True
    for c in secs:
        sc = scenes[c["from_scene"]:c["to_scene"] + 1]
        c["start"] = round(float(sc[0]["start"]), 2) if c["from_scene"] else 0.0
        c["end"] = round(float(sc[-1]["end"]), 2) if c["to_scene"] < len(scenes) - 1 else round(float(total), 2)
        d = sum(x["end"] - x["start"] for x in sc) or 1
        c["weight"] = int(round(sum(x["weight"] * (x["end"] - x["start"]) for x in sc) / d))
    return secs


def _turns(raw, shots, scenes, tim, words_json, s, say):
    """check, time and limit the AI's turn lines -> (kept, dropped)"""
    import tools
    import self_review
    kept, dropped = [], []
    n_sc = len(shots)
    for x in raw:
        try:
            i = int(x.get("n")) - 1
            w = int(x.get("weight"))
        except (TypeError, ValueError):
            dropped.append({"turn": x, "why": "no scene number or weight"}); continue
        words = str(x.get("words") or "").strip()
        kind = str(x.get("kind") or "").strip().lower()
        if not 0 <= i < n_sc or not words:
            dropped.append({"turn": x, "why": "no such scene or no words"}); continue
        if not tools._words_in(shots[i].get("text", ""), words):
            dropped.append({"turn": x, "why": "its words are not in that scene's text"}); continue
        w = max(1, min(3, w))
        if w == 3 and kind not in HEAVY_KINDS and i < 0.8 * n_sc:
            w = 2                                                       # weight 3 is for a death / final twist
        at, method, conf = self_review.word_time(words_json, tim, words, shots[i].get("start", 0),
                                                  shots[i].get("end", 1e9))
        if at is None:
            dropped.append({"turn": x, "why": "its words were not found in the narration timing"}); continue
        line_end = next((float(t["end"]) for t in tim if float(t["start"]) - 0.6 <= at <= float(t["end"]) + 0.6), at + 3)
        kept.append({"scene": i, "words": words, "kind": kind or "twist", "weight": w, "at": round(float(at), 2),
                     "at_method": method, "line_end": round(line_end, 2)})
    kept.sort(key=lambda t: (-t["weight"], t["at"]))
    budget = {int(k): int(v) for k, v in (s.get("budget") or {}).items()}
    out = []
    for t in kept:                                   # heaviest first; over budget -> one level lighter, else dropped
        while t["weight"] > 0 and sum(1 for o in out if o["weight"] == t["weight"]) >= budget.get(t["weight"], 0):
            t["weight"] -= 1
        if t["weight"] < 1:
            dropped.append({"turn": t, "why": "over this series' budget of turns"}); continue
        if any(abs(o["at"] - t["at"]) < TURN_GAP_S for o in out):
            dropped.append({"turn": t, "why": f"less than {TURN_GAP_S} s from a heavier turn"}); continue
        out.append(t)
    out.sort(key=lambda t: t["at"])
    return out, dropped


def build(cfg, genre, narration, pictures, say=print, use_ai=True, script_path="", ask=None):
    """-> the emotion map (dict); see the top of this file"""
    s = settings(genre)
    shots = (_load(os.path.join(pictures or "", "shots.json"), {}) or {}).get("shots") or []
    if not shots:
        raise RuntimeError("no scene plan (shots.json)")
    tim = _load(os.path.join(narration, "timing.json"), []) or []
    words_json = _load(os.path.join(narration, "words.json"))
    script = ""
    for p in (script_path, os.path.join(narration, "script.txt")):   # the job's script keeps the [उदास] marks
        if p and os.path.exists(p):
            script = open(p, encoding="utf-8").read()
            if TAG_RE.search(script) or re.search(r"(?m)^\s*\[", script):
                break
    tags = line_tags(script, tim)
    stags = _scene_tags(shots, tim, tags)
    moods = series_moods(cfg, genre)
    base = s.get("base") if s.get("base") in moods else moods[0]
    budget = s.get("budget") or {}
    hint = max(1, sum(int(v) for v in budget.values()) // max(1, (len(shots) + ASK_BATCH - 1) // ASK_BATCH))
    got, raw_turns, model = (_ask(cfg, genre, shots, stags, moods, hint, say, ask) if use_ai else ({}, [], ""))
    import tools
    hits = bool((tools.GENRES.get(genre) or {}).get("hits"))
    scenes, notes = [], []
    for i, sh in enumerate(shots):
        a = got.get(i) or {}
        mood = str(a.get("mood") or "").strip()
        mood = next((m for m in moods if m.lower() == mood.lower()), "")
        try:
            w = int(a.get("weight"))
        except (TypeError, ValueError):
            w = None
        src = "ai" if mood and w is not None else "marks"
        if not mood:
            mood = _mood_from_tags(stags[i], moods, base)
        if w is None:
            n_lines = sum(stags[i].values())
            w = max([TAG_WEIGHT.get(t, 0) for t in stags[i]] or [0]) if n_lines else 0
        w = max(0, min(3, w))
        if hits and sh.get("hit"):
            w = max(w, 2)
        scenes.append({"n": i + 1, "start": round(float(sh.get("start", 0)), 2), "end": round(float(sh.get("end", 0)), 2),
                       "mood": mood, "weight": w, "from": src, "why": str(a.get("why") or "")[:80],
                       "marks": stags[i], "happens": str(sh.get("happens") or "")[:120]})
    turns, dropped = _turns(raw_turns, shots, scenes, tim, words_json, s, say) if s.get("stingers") else ([], [])
    turn_sc = {t["scene"] for t in turns}
    for t in turns:                                                     # the scene of a turn is at least that heavy
        scenes[t["scene"]]["weight"] = max(scenes[t["scene"]]["weight"], t["weight"])
    heavy_ok = {t["scene"] for t in turns if t["weight"] == 3}
    for i, x in enumerate(scenes):                                      # weight 3 only where a heavy turn is
        if x["weight"] == 3 and i not in heavy_ok:
            x["weight"] = 2
    for x, w in zip(scenes, _smooth([x["weight"] for x in scenes], turn_sc)):
        x["weight"] = w
    total = max([float(t["end"]) for t in tim] + [scenes[-1]["end"]])
    secs = make_sections(scenes, s, total)
    n_ai = sum(1 for x in scenes if x["from"] == "ai")
    return {"version": 1, "series": genre, "made": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "ai" if n_ai == len(scenes) else ("marks" if not n_ai else "ai+marks"), "model": model,
            "moods": moods, "settings": s, "scenes": scenes, "sections": secs, "turns": turns,
            "dropped_turns": dropped, "notes": notes, "words_json": bool(words_json)}


def report(m, say=print):
    fmt = lambda t: f"{int(t // 60)}:{int(t % 60):02d}"
    say(f"   emotion map ({m['source']}{', ' + m['model'] if m.get('model') else ''}): "
        f"{len(m['sections'])} music section(s), {len(m['turns'])} turn(s)")
    say("   weights per scene: " + " ".join(str(x["weight"]) for x in m["scenes"]))
    for c in m["sections"]:
        say(f"     {fmt(c['start'])}-{fmt(c['end'])}  {c['mood']:<8} weight {c['weight']}  "
            f"(scenes {c['from_scene'] + 1}-{c['to_scene'] + 1})")
    for t in m["turns"]:
        say(f"     turn at {fmt(t['at'])}  weight {t['weight']}  {t['kind']}: {t['words']}  ({t['at_method']})")
    if m.get("dropped_turns"):
        say(f"     ({len(m['dropped_turns'])} turn(s) the AI proposed were left out - see emotion_map.json)")


def step(ctx, name, pictures, genre=""):
    """job step (Experimental): write emotion_map.json next to shots.json. Never stops the run."""
    import tools
    cfg, say = ctx["cfg"], ctx["say"]
    s = settings(genre)
    if not s.get("on"):
        say(f"   (emotion music is off for {genre or 'this series'}: one track as before)")
        return True
    nar = tools.narration_dir(cfg, name, existing=True)
    out = os.path.join(pictures or "", "emotion_map.json")
    srcs = [os.path.join(pictures or "", "shots.json"), os.path.join(nar, "timing.json"), os.path.join(nar, "words.json")]
    if os.path.exists(out) and all(not os.path.exists(p) or os.path.getmtime(p) <= os.path.getmtime(out) for p in srcs):
        m = _load(out)
        if m and m.get("source") != "marks":         # a map made without the AI is made again (Ollama was busy?)
            say("   emotion map already made for this scene plan - kept")
            report(m, say)
            return True
    sp = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    m = build(cfg, genre, nar, pictures, say, script_path=sp)
    with open(out + ".part", "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(out + ".part", out)
    report(m, say)
    say(f"   -> {out}")
    return True


# ------------------------------------------------------------------ run by hand on a finished episode
def _episode_paths(ep):
    nar = sorted(glob.glob(os.path.join(ep, "02_Narration", "narrations", "*", "timing.json")))
    pic = sorted(glob.glob(os.path.join(ep, "03_Artwork", "*", "shots.json")))
    if not nar or not pic:
        raise SystemExit(f"no narration timing or scene plan in {ep}")
    return os.path.dirname(nar[0]), os.path.dirname(pic[0])


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description="make the emotion map of a finished episode (no sound is changed)")
    ap.add_argument("episode")
    ap.add_argument("--genre", default="")
    ap.add_argument("--no-ai", action="store_true", help="only the feeling marks and the scene plan, no Ollama")
    ap.add_argument("--out", default="", help="where to write the map (default: emotion_map.json next to shots.json)")
    ap.add_argument("--reply", default="", help="tests: a saved AI reply (JSON) instead of asking Ollama")
    a = ap.parse_args(argv)
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    import path_config
    cfg = path_config.load_config()
    cfg["experimental_mode"] = True
    try:
        import tinker
        cfg = tinker.apply_models(cfg, tinker.snapshot(cfg))
    except Exception:
        pass
    ep = os.path.abspath(a.episode)
    nar, pic = _episode_paths(ep)
    genre = a.genre or os.path.basename(os.path.dirname(os.path.dirname(ep))).lower()
    ask = None
    if a.reply:                                      # tests: the same saved answer for every question
        saved = _load(a.reply, {})
        ask = lambda *x, **k: (json.dumps(saved, ensure_ascii=False), "saved reply")
    m = build(cfg, genre, nar, pic, print, use_ai=not a.no_ai, script_path=os.path.join(ep, "01_Script", "script.txt"),
              ask=ask)
    out = a.out or os.path.join(pic, "emotion_map.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    report(m)
    print("   -> " + out)


if __name__ == "__main__":
    main(sys.argv[1:])
