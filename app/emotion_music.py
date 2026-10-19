"""emotion_music.py - music and effects that follow how heavy the story's emotion is (Experimental genre runs only).

Plan: EMOTION_MUSIC_PLAN.md in the hub folder. Step 1 (17 Oct 2026), the emotion map: after the scene plan (and the
Whisper word times) the step exp_emotion_map writes emotion_map.json next to shots.json:
  scenes    each director scene with a mood (one of the series' music folders) and a weight 0-3
  sections  neighbouring scenes merged into at most 3 music sections, as few track changes as possible
  turns     the few heaviest lines (twist, death, reveal...) with the time their first words are spoken
The AI (director_model) proposes; the rules below decide (budgets, smoothing, no jumping back to a track). With no AI
answer the map comes from the emotion marks ([उदास] ...) and the scene plan's hits / close-up moments.
Weight: 0 calm telling, 1 mild feeling, 2 strong (reveal, scare, confession), 3 heavy (death, the final twist).

Steps 3-5, the mix (mix(), called by horror_sound when the map exists; Working mode and shape_mix are untouched):
  curve     under the voice the music sits a little lower or higher by weight; in pauses of 1 s or more it lifts,
            and settles 0.3 s before the next word (real ducking); calm parts lose some of their highs (warmer)
  turns     weight 1: a 6 dB dip; 2: silence ~1 s before the line, through the line, back over 2.5 s with the
            ambience at half; 3: music, ambience and effects stop 2-2.5 s before the line, one soft low note after it,
            then the music comes back (in the return mood when the 3-track cap allows)
  sections  one track per section from that mood folder (ratings, screening, least used), 3.5 s equal-power
            crossfades placed in a pause; never more than 3 tracks, never back to an earlier one
  loudness  two-pass loudnorm in linear mode, so the dips and silences keep their shape

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
# every stinger in one story (18 Oct 2026, Bhay-Acoustic-Parasite-Mystery: 37 stinger-like sounds in 6.3 min, 23 of them
# under 5 s apart). A stinger is a turn of weight 2-3 (the music stops before the line), a scene hit (braam, impact) or an
# effect from a mood-sound folder (STING_WORDS). They all compete: the heaviest emotion first, then the earliest; one is
# kept only when no kept stinger is within min_gap_s, and at most per_min a minute of story (at least 1).
# bed_peak_db: an ambience bed may rise at most this far above its own usual level (beds are meant to be steady; a file
# full of hits used as a bed is flattened instead of stinging every few seconds). Tinker: emotion_music.STINGERS.
STINGERS = {
    "bhay": {"on": True, "min_gap_s": 20, "per_min": 1.5, "bed_peak_db": 6},
    "rahasya": {"on": True, "min_gap_s": 25, "per_min": 1.2, "bed_peak_db": 6},
    "kisse": {"on": True, "min_gap_s": 40, "per_min": 0.6, "bed_peak_db": 5},
    "romanch": {"on": True, "min_gap_s": 45, "per_min": 0.5, "bed_peak_db": 5},
    "gyaan": {"on": True, "min_gap_s": 40, "per_min": 0.6, "bed_peak_db": 5},
    "kareeb": {"on": True, "min_gap_s": 45, "per_min": 0.5, "bed_peak_db": 5},
    "default": {"on": True, "min_gap_s": 30, "per_min": 1.0, "bed_peak_db": 6},
}
# effects that sound like a voice (whisper, scream, laugh, breath ...) hide the narration when they play over it (18 Oct
# 2026, Bhay-Acoustic-Parasite-Mystery: an 8.6 s whisper started mid-sentence at 6:04, 10 dB under the voice). They play
# only in a pause of the narration: the first pause of at least min_pause_s that starts within search_s of the word,
# cut to end fade_s before the next word; no such pause (or less than min_play_s of room) -> left out.
# Tinker: emotion_music.VOICE_FX (words are matched in the file path, like STING_WORDS)
VOICE_FX = {"on": True, "min_pause_s": 0.8, "search_s": 5.0, "min_play_s": 1.2, "fade_s": 0.25, "early_s": 0.5,
            "words": ["whisper", "voice", "vocal", "scream", "shriek", "laugh", "giggle", "breath", "moan", "groan",
                      "sob", "crying", "cries", "child", "chant", "murmur", "talk", "speech", "humming", "sigh", "gasp"]}
STING_WORDS = ("braam", "impact", "hit", "sting", "riser", "whoosh", "boom", "scream", "whisper", "slam", "thud")
# emotion mark -> music folders that fit it, best first (the first one the series has wins)
TAG_MOOD = {"डर": ("Dark", "Tension", "Eerie"), "रहस्य": ("Tension", "Dark", "Eerie"), "उदास": ("Sad", "Calm"),
            "प्यार": ("Love", "Calm"), "गुस्सा": ("Tension", "Dark"), "उत्साह": ("Happy", "Love"),
            "फुसफुसाहट": ("Eerie", "Calm", "Love")}
# a mood folder with no track that fits it (audio_screen.MOOD_FIT) hands over to the nearest mood, best first
MOOD_NEAR = {"Tension": ("Dark", "Eerie"), "Dark": ("Eerie", "Tension"), "Eerie": ("Dark", "Tension"),
             "Sad": ("Calm", "Dark"), "Calm": ("Sad", "Love"), "Love": ("Calm", "Happy"), "Happy": ("Calm", "Love"),
             "angry": ("Tension", "Dark")}
# how the music comes in and moves around a turn (19 Oct 2026, Bhay-Infinite-Loop-Milestone-Number: the music rose
# from silence to its full level in about 1.5 s, and five weight-2 turns cut it to silence in 0.4 s for 8-10 s and
# brought it back in about 1 s - it sounded like the music stopped and then carried on).
#   fade_in_s   the opening swell from silence; slow at first, it may go on under the first words (all series)
#   curve       how gently every ramp starts and ends (1 = a plain S-curve, higher = a longer soft start)
#   turn_out_s  seconds the music takes to go down before a turn line; back_s: to come back after it, per weight
#   dip_db      weight 1 and 2 turns: how far the music goes down for the line (weight 3 still stops music,
#               ambience and effects, your 17 Oct rule)
# Tinker: emotion_music.EASE ("on": false = the old quick ramps and the weight-2 silence)
EASE = {"on": True, "fade_in_s": 8.0, "curve": 1.5, "turn_out_s": 1.5, "back_s": {"1": 2.5, "2": 4.0, "3": 5.0},
        "dip_db": {"1": -6.0, "2": -14.0}}
TAG_WEIGHT = {"डर": 1, "रहस्य": 1, "उदास": 1, "प्यार": 1, "गुस्सा": 1, "उत्साह": 1, "फुसफुसाहट": 0}
TAG_RE = re.compile(r"^\s*\[([^\]]+)\]\s*")
# how a mood reads in MAP_ASK (17 Oct 2026): Kisse-Memories-Old-Atlas-Cycle, a story of two boyhood friends, opened
# on "Love" (BGM\Love is romantic songs and blues) because the narrator's marks said प्यार
MOOD_NOTE = {"Love": "Love (romance between a couple only; friendship, family and old memories are Calm or Sad)"}

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
        prompt = MAP_ASK.format(desc=G.get("desc", genre), label=G.get("label", genre), moods=", ".join(MOOD_NOTE.get(m, m) for m in moods),
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
            dur = {}                                                    # the mood that fills most of the merged time
            for x in scenes[secs[lo]["from_scene"]:secs[hi]["to_scene"] + 1]:
                dur[x["mood"]] = dur.get(x["mood"], 0) + x["end"] - x["start"]
            mood = max((secs[k]["mood"], secs[j]["mood"]), key=lambda mo: dur.get(mo, 0))
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
        mood = str(a.get("mood") or "").strip().split("(")[0].strip()      # "Love (romance ...)" echoed back
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


# ------------------------------------------------------------------ steps 3-5: the music that follows the map
# Levels in dB, relative to the series' usual "under the story" level, by weight 0-3 (times the series' strength):
LEVELS = {"under": (-2.0, 0.0, 1.0, 1.5),      # while a word is being spoken
          "gap": (2.0, 3.0, 4.0, 5.0),          # in a pause of 1 s or more (the music settles 0.3 s before the next word)
          "dull": (0.7, 0.35, 0.0, 0.0)}        # how much of the music above 3 kHz is taken away (warm, dull)
DULL_HZ = 3000
CR = 20                                  # control points per second for every level curve
GAP_MIN_S, GAP_IN_S, GAP_OUT_S = 1.0, 0.2, 0.3
DIP_DB = -6.0                            # a weight-1 turn: the music dips this much
PRE_S, FADE_S = 0.3, 0.4                 # silence ends this long before the first word; music fades out over FADE_S
BACK_S = {1: 1.5, 2: 2.5, 3: 3.0}        # how long the music takes to come back after the line
# how bright / light a mood folder feels (17 Oct 2026, your Blue Ink Diary note): a return after a heavy line may never
# be brighter than the story's own mood after that line (a sad story stayed sad, the music went to a lighter Calm)
BRIGHT = {"Dark": 0, "Eerie": 0, "angry": 0, "Tension": 1, "Sad": 1, "Calm": 2, "Love": 3, "Happy": 4}


def drop_len(s, w, t):
    """seconds of silence before a heavy line: the series' value for that weight, longer when the story already pauses
    there and for a longer line, shorter for a quick one (17 Oct 2026: before, every silence had the same length)"""
    base = float((s.get("drop_s") or {}).get(str(w), 1.0))
    if base <= 0:
        return 0.0
    line = max(0.0, float(t.get("line_end", 0)) - float(t.get("at", 0)))
    f = 0.7 + 0.25 * min(2.0, float(t.get("pause_before") or 0)) + 0.05 * min(6.0, line)
    f *= 1.15 if w == 3 and t.get("kind") in ("death", "loss") else 1.0
    return round(min(1.6 * base, max(0.6 * base, base * f)), 2)
NOTE_DB = -22.0                          # the soft note after a heavy line, below the voice


def ease(x, curve=None):
    """0..1 -> 0..1 gain on an S-curve raised to EASE curve: it starts and ends gently (a swell, not a jump)"""
    import numpy as np
    p = float(EASE.get("curve", 1.5) if curve is None else curve)
    x = np.clip(np.asarray(x, dtype=np.float64), 0, 1)
    return ((1 - np.cos(np.pi * x)) / 2) ** max(0.5, p)


def swell_s(shape_fade, lead):
    """seconds of the opening swell: EASE fade_in_s (or the old fade-in, at most the lead, when EASE is off)"""
    old = float(min(max(float(shape_fade or 2.0), 0.5), lead))
    return max(old, float(EASE.get("fade_in_s", old))) if EASE.get("on", True) else old


def _curve(n, pts):
    """control curve of n points from (time s, value) pairs, linear in between"""
    import numpy as np
    t = np.arange(n) / CR
    px = [p[0] for p in pts]
    return np.interp(t, px, [p[1] for p in pts])


def _ramp_window(n, a, b, fade_in, fade_out, inside=1.0, eased=False):
    """1 outside, 'inside' between a and b, with ramps of fade_in before a and fade_out after b (linear, or eased)"""
    import numpy as np
    t = np.arange(n) / CR
    w = np.ones(n)
    k_in = np.clip((t - (a - fade_in)) / max(1e-3, fade_in), 0, 1)
    k_out = np.clip(((b + fade_out) - t) / max(1e-3, fade_out), 0, 1)
    k = np.minimum(k_in, k_out)
    if eased:
        k = ease(k)
    return w + (inside - 1.0) * k


def speech_frames(v, sr):
    """True where the voice speaks, at CR frames per second (same rule as shape_mix)"""
    import numpy as np
    hop = sr // CR
    fr = v[: len(v) // hop * hop].reshape(-1, hop).astype(np.float64)
    vdb = 10 * np.log10((fr ** 2).mean(1) + 1e-12)
    return vdb > np.percentile(vdb, 95) - 18, vdb


def gap_mask(speech_mix, n):
    """1 inside pauses of GAP_MIN_S or more (from GAP_IN_S after the last word to GAP_OUT_S before the next)"""
    import numpy as np
    g = np.zeros(n)
    s = np.zeros(n, bool)
    s[:len(speech_mix)] = speech_mix[:n]
    i = 0
    first = int(np.argmax(s)) if s.any() else n
    last = n - int(np.argmax(s[::-1])) if s.any() else 0
    while i < n:
        if s[i]:
            i += 1
            continue
        j = i
        while j < n and not s[j]:
            j += 1
        if i > first and j < last and (j - i) / CR >= GAP_MIN_S:
            a, b = i + int(GAP_IN_S * CR), j - int(GAP_OUT_S * CR)
            if b > a:
                g[a:b] = 1
        i = j
    k = max(1, int(0.4 * CR))                       # 0.4 s ramps
    return np.convolve(g, np.ones(k) / k, mode="same")


def gaps(speech_mix):
    """[(start s, end s)] of every pause of GAP_MIN_S or more"""
    out, i, n = [], 0, len(speech_mix)
    while i < n:
        if speech_mix[i]:
            i += 1
            continue
        j = i
        while j < n and not speech_mix[j]:
            j += 1
        if (j - i) / CR >= GAP_MIN_S:
            out.append((i / CR, j / CR))
        i = j
    return out


def plan_segments(m, s, lead, end, pauses):
    """the map's sections (+ a return in another mood after a heavy turn) -> track segments in mix time:
    [{"start", "end", "mood", "cut": "crossfade" | "return" | ""}]. Never more than s['max_tracks'] moods, never back."""
    secs = m.get("sections") or []
    if not secs:
        return []
    seg = []
    for i, c in enumerate(secs):
        st = 0.0 if i == 0 else lead + float(c["start"])
        if i:                                         # move the change into the nearest pause (within 5 s)
            near = [(abs((a + b) / 2 - st), (a + b) / 2) for a, b in pauses if abs((a + b) / 2 - st) <= 5]
            if near:
                st = min(near)[1]
        seg.append({"start": round(st, 2), "mood": c["mood"], "cut": "crossfade" if i else ""})
    rm = s.get("return_mood") or ""
    if s.get("stingers") and rm:
        for t in m.get("turns") or []:
            if int(t.get("weight", 0)) < 3:
                continue
            at = lead + float(t["line_end"]) + PRE_S + float(s.get("return_delay_s", 0))
            cur = max((x for x in seg if x["start"] <= at), key=lambda x: x["start"], default=None)
            used = {x["mood"] for x in seg if x["start"] <= at}
            scs = m.get("scenes") or []
            k = t.get("scene")
            if k is None:                              # the turn's scene by time
                k = next((j for j, sc in enumerate(scs) if float(sc.get("start", 0)) <= float(t["at"]) < float(sc.get("end", 0))), 0)
            after = [sc.get("mood") for sc in scs[int(k) + 1:] if sc.get("mood")]
            story = min((BRIGHT.get(x, 2) for x in after), default=BRIGHT.get(cur["mood"], 2) if cur else 2)
            if BRIGHT.get(rm, 2) > story:              # the story stays as dark / sad as before: no lighter return
                t["return_skipped"] = rm
                continue
            if cur and cur["mood"] != rm and rm not in used and len({x["mood"] for x in seg}) < int(s.get("max_tracks", MAX_TRACKS)):
                seg.append({"start": round(at, 2), "mood": rm, "cut": "return"})
                seg.sort(key=lambda x: x["start"])
    out = []
    for x in seg:                                      # same mood side by side: one segment
        if out and out[-1]["mood"] == x["mood"]:
            continue
        if any(o["mood"] == x["mood"] for o in out):   # never back to a track it left: keep the one playing
            continue
        out.append(dict(x))
    for i, x in enumerate(out):
        x["end"] = out[i + 1]["start"] if i + 1 < len(out) else end
    return out


def stinger_curves(m, s, lead, n):
    """(music multiplier, ambience/effects multiplier, [(note time, weight)]) for the turns, as CR curves"""
    import numpy as np
    mus, bed, notes = np.ones(n), np.ones(n), []
    if not s.get("stingers"):
        return mus, bed, notes
    k = float(s.get("strength", 1.0))
    ez = bool(EASE.get("on", True))                   # 19 Oct 2026: eased ramps, a weight-2 turn dips instead of stopping
    out_s = float(EASE.get("turn_out_s", 1.5)) if ez else FADE_S
    back_s = {w: float((EASE.get("back_s") or {}).get(str(w), BACK_S[w])) if ez else BACK_S[w] for w in BACK_S}
    for t in m.get("turns") or []:
        w = int(t.get("weight", 0))
        A, E = lead + float(t["at"]), lead + float(t["line_end"])
        if w <= 0:
            continue
        if w == 1:
            mus *= _ramp_window(n, A - 0.9, E, out_s if ez else 0.3, back_s[1], 10 ** (dip_db(1) * min(1.0, k) / 20), ez)
            continue
        drop = drop_len(s, w, t)
        s0 = A - PRE_S - drop                         # the silence (weight 2: the dip) starts here, lasts through the line
        back = E + PRE_S + (float(s.get("return_delay_s", 0)) if w == 3 else 0)
        inside = 10 ** (dip_db(2) * min(1.0, k) / 20) if (w == 2 and ez) else 0.0
        mus *= _ramp_window(n, s0, back, out_s, back_s[w], inside, ez)
        bed *= _ramp_window(n, s0, E + PRE_S, out_s, back_s[w], 0.5 if w == 2 else 0.0, ez)
        if w == 3 and s.get("soft_note"):
            notes.append((E + PRE_S, w))
    return mus, bed, notes


def dip_db(w):
    """how far the music goes down for a weight-1 / weight-2 turn line (dB, before the series' strength)"""
    if w == 1:
        return float((EASE.get("dip_db") or {}).get("1", DIP_DB)) if EASE.get("on", True) else DIP_DB
    return float((EASE.get("dip_db") or {}).get("2", -14.0))


def turn_says(s, t):
    """what a turn does to the music, for the job log"""
    w = int(t.get("weight", 0))
    if w == 1:
        return f"music dips {abs(dip_db(1) * min(1.0, float(s.get('strength', 1.0)))):.0f} dB"
    if w == 2 and EASE.get("on", True):
        return (f"music eases {abs(dip_db(2) * min(1.0, float(s.get('strength', 1.0)))):.0f} dB down from "
                f"{drop_len(s, 2, t):g} s before the line")
    if w == 2:
        return f"music stops before the line ({drop_len(s, 2, t):g} s of silence)"
    if w == 3:
        return f"music, ambience and effects stop ({drop_len(s, 3, t):g} s of silence)"
    return ""


def soft_note(kind, sr=48000):
    """one low soft note (Kisse: a warm D3 with a slow fade) or a low drone swell (Bhay / Rahasya), from sine waves"""
    import numpy as np
    if kind == "drone":
        t = np.arange(int(5.0 * sr)) / sr
        env = np.minimum(1, t / 1.5) * np.exp(-np.maximum(0, t - 1.5) / 1.4)
        y = sum(a * np.sin(2 * np.pi * f * t) for f, a in ((55.0, 1.0), (82.4, 0.45), (110.0, 0.2)))
    else:
        t = np.arange(int(4.5 * sr)) / sr
        env = np.minimum(1, t / 0.06) * np.exp(-t / 1.6)
        y = sum(a * np.sin(2 * np.pi * f * t) for f, a in ((146.8, 1.0), (293.7, 0.25), (440.4, 0.06)))
    y = y * env
    return (y / (np.abs(y).max() or 1)).astype(np.float32)


def render(v, tracks, segs, base_pts, m, s, lead, sr=48000, xfade=None, swell=0.0):
    """the music bed alone (no voice), stereo float32, and the curves used.
    v: voice (mono); tracks: {mood: (full stereo array, low-passed stereo array, level dB of the track)};
    segs: plan_segments(); base_pts: shape_mix-style level points [(s, dB relative to the voice)]."""
    import numpy as np
    n_s = int(round(base_pts[-1][0] * sr))
    n = int(np.ceil(n_s / sr * CR)) + 1
    sp_v, vdb = speech_frames(v, sr)
    vdb_act = float(np.median(vdb[sp_v])) if sp_v.any() else -30.0
    sp = np.zeros(n, bool)
    o = int(round(lead * CR))
    sp[o:o + len(sp_v)] = sp_v[: max(0, n - o)]
    base = _curve(n, base_pts)                                    # dB relative to the voice
    tt = np.arange(n) / CR
    w = np.zeros(n)
    sc = m.get("scenes") or []
    if sc:                                                        # weight per scene, then smoothed over 3 s
        px = [lead + float(x["start"]) for x in sc] + [lead + float(sc[-1]["end"])]
        idx = np.clip(np.searchsorted(px, tt, side="right") - 1, 0, len(sc) - 1)
        w = np.array([float(sc[i]["weight"]) for i in idx])
        k3 = 3 * CR
        w = np.convolve(np.pad(w, k3 // 2, mode="edge"), np.ones(k3) / k3, mode="valid")[:n]
    k = float(s.get("strength", 1.0)) if s.get("curve") else 0.0
    if sp_v.any():                                                # from the first word to the last one
        story = (tt >= lead + float(np.argmax(sp_v)) / CR) & (tt <= lead + (len(sp_v) - float(np.argmax(sp_v[::-1]))) / CR)
    else:
        story = np.zeros(n, bool)
    under = k * np.interp(w, [0, 1, 2, 3], LEVELS["under"])
    lift = k * np.interp(w, [0, 1, 2, 3], LEVELS["gap"])
    gm = gap_mask(sp, n)
    rel = np.where(story, under + gm * (lift - under), 0.0)
    dull = min(1.0, k) * np.interp(w, [0, 1, 2, 3], LEVELS["dull"])
    mus_k, bed, notes = stinger_curves(m, s, lead, n)
    xf = float(xfade if xfade is not None else s.get("crossfade_s", 3.5))
    out = np.zeros((n_s, 2), np.float32)
    used = []
    for i, g in enumerate(segs):
        if g["mood"] not in tracks:
            continue
        full, low, mdb = tracks[g["mood"]]
        a = g["start"] - (xf / 2 if g.get("cut") == "crossfade" else 0)
        nxt = segs[i + 1] if i + 1 < len(segs) else None
        b = g["end"] + (xf / 2 if nxt and nxt.get("cut") == "crossfade" else 0)
        a0, b0 = max(0, int(a * sr)), min(n_s, int(b * sr))
        if b0 <= a0:
            continue
        win = np.ones(n)                                          # equal-power crossfades in and out
        if g.get("cut") == "crossfade":
            x = np.clip((tt - a) / xf, 0, 1)
            win *= np.sin(x * np.pi / 2)
        if nxt and nxt.get("cut") == "crossfade":
            x = np.clip((b - tt) / xf, 0, 1)
            win *= np.sin(x * np.pi / 2)
        dl = dull.copy()
        if nxt and nxt.get("cut") == "crossfade":                  # the outgoing track turns dull while it fades
            dl = np.maximum(dl, np.clip((tt - (b - xf)) / xf, 0, 1))
        gain_db = base + rel + (vdb_act - mdb)
        lin = 10 ** (gain_db / 20) * mus_k * win
        if swell > 0:                                             # the opening swell from silence (EASE)
            lin = lin * ease(tt / swell)
        P = len(full)
        kx = min(int(3 * sr), P // 3)                             # each track loops with 3 s crossfades, like shape_mix
        Pl = P - kx
        fade = np.linspace(0, 1, kx, dtype=np.float32)[:, None]
        for c0 in range(a0, b0, 60 * sr):                         # one minute at a time
            c1 = min(b0, c0 + 60 * sr)
            pos = np.arange(c0, c1) - a0
            q, oo = pos // Pl, pos % Pl
            f_ = full[oo].copy()
            l_ = low[oo].copy()
            blend = (q > 0) & (oo < kx)
            if blend.any():
                ob = oo[blend]
                f_[blend] = full[ob] * fade[ob] + full[Pl + ob] * (1 - fade[ob])
                l_[blend] = low[ob] * fade[ob] + low[Pl + ob] * (1 - fade[ob])
            ts = np.arange(c0, c1) / sr * CR
            d = np.interp(ts, np.arange(n), dl).astype(np.float32)[:, None]
            gl = np.interp(ts, np.arange(n), lin).astype(np.float32)[:, None]
            out[c0:c1] += (l_ + (f_ - l_) * (1 - d)) * gl          # dull = only the part below DULL_HZ
        used.append(g)
    for at, wt in notes:                                          # the soft note after a heavy line
        y = soft_note(s.get("soft_note"), sr)
        i0 = int(at * sr)
        i1 = min(n_s, i0 + len(y))
        if i1 > i0:
            ny = float(np.sqrt(np.mean(y ** 2)))
            gnote = 10 ** ((vdb_act + NOTE_DB - 20 * np.log10(ny + 1e-9)) / 20)
            out[i0:i1] += (y[:i1 - i0] * gnote)[:, None]
    return out, {"weight": w, "rel_db": rel, "gap": gm, "music_k": mus_k, "bed": bed, "base_db": base,
                 "notes": notes, "used": used, "vdb_act": vdb_act}


def loudnorm2(cfg, raw, sr, ch, outp, run_cmd=None):
    """two-pass loudnorm in linear mode: the whole file is turned up or down, its quiet and loud parts keep their
    shape (one-pass loudnorm acts like a slow compressor and would fill the silences back in)"""
    import subprocess
    WIN = os.name == "nt"
    inp = ["-f", "f32le", "-ar", str(sr), "-ac", str(ch), "-i", raw]
    p = subprocess.run([cfg["ffmpeg"], "-hide_banner", "-nostats"] + inp +
                       ["-af", "loudnorm=I=-16:TP=-1.5:LRA=20:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, creationflags=0x08000000 if WIN else 0)
    try:
        j = json.loads(p.stderr[p.stderr.rindex("{"):p.stderr.rindex("}") + 1])
        af = ("loudnorm=I=-16:TP=-1.5:LRA=20:linear=true:measured_I={input_i}:measured_TP={input_tp}:"
              "measured_LRA={input_lra}:measured_thresh={input_thresh}:offset={target_offset}").format(**j)
    except (ValueError, KeyError):
        af = "loudnorm=I=-16:TP=-1.5:LRA=11"                       # measuring failed: the old one-pass way
    cmd = [cfg["ffmpeg"], "-v", "error", "-y"] + inp + ["-af", af, "-ar", str(sr), "-c:a", "pcm_s16le", outp]
    if run_cmd:
        return run_cmd(cmd) == 0
    return subprocess.run(cmd, creationflags=0x08000000 if WIN else 0).returncode == 0


def active(cfg, pictures, genre):
    """the emotion map to mix with, or None (Working mode, series off, no map yet)"""
    if not (cfg.get("experimental_mode") or cfg.get("_experimental_workspace")):
        return None
    s = settings(genre)
    if not s.get("on"):
        return None
    m = _load(os.path.join(pictures or "", "emotion_map.json"))
    return m if m and m.get("sections") else None


def _track_arrays(ctx, track):
    """(full, low-passed, active level dB) of a music file (sharp notes tamed the usual way)"""
    import numpy as np
    import tools
    cfg = ctx["cfg"]
    src = tools.tamed(ctx, track)
    full = tools._decode(cfg, src, 48000, 2)
    if len(full) < 48000 * 4:
        raise RuntimeError(f"the music track could not be read: {track}")
    import subprocess
    p = subprocess.run([cfg["ffmpeg"], "-v", "error", "-i", src, "-ac", "2", "-ar", "48000", "-af",
                        f"lowpass=f={DULL_HZ},lowpass=f={DULL_HZ}", "-f", "f32le", "-"], capture_output=True,
                       creationflags=0x08000000 if os.name == "nt" else 0)
    low = np.frombuffer(p.stdout, np.float32).reshape(-1, 2)[: len(full)]
    if len(low) < len(full):
        low = np.vstack([low, np.zeros((len(full) - len(low), 2), np.float32)])
    hop = 48000 // 2
    mf = full[: len(full) // hop * hop].mean(1).reshape(-1, hop).astype(np.float64)
    mdb = 10 * np.log10((mf ** 2).mean(1) + 1e-12)
    return full, low, float(np.median(mdb[mdb > np.percentile(mdb, 95) - 25]))


def folder_of(track, cfg=None):
    """the BGM mood folder a track comes from (a treated copy counts as its original)"""
    try:
        import audio_screen
        import tools
        track = audio_screen.original_of(cfg or {}, track) if cfg else track
    except Exception:
        pass
    return os.path.basename(os.path.dirname(str(track or ""))).lower()


def near_moods(mood, allowed=None):
    """the mood itself, then its neighbours (MOOD_NEAR) - only folders the series may use when 'allowed' is given"""
    out = [mood] + [m for m in MOOD_NEAR.get(mood, ()) if m != mood]
    return [m for m in out if m == mood or not allowed or m in allowed]


def misfit_note(cfg, say):
    """say why mood folders were passed over (feedback.pick_track fills cfg['_music_misfit'])"""
    for n in cfg.pop("_music_misfit", None) or []:
        say(f"   (no usable track in {n})")


def pick_tracks(ctx, segs, first_track, music_db, fixed=False):
    """a usable track per mood: the series' usual pick (your ratings, least used, screening) inside that mood folder.
    A folder with no track that fits its mood hands over to the nearest mood (MOOD_NEAR); with none, the section keeps
    the track before it. -> {mood: file}, music_db"""
    import tools
    cfg, say = ctx["cfg"], ctx["say"]
    md = cfg.get("music_dir") or ""
    allowed = series_moods(cfg, cfg.get("_music_series") or "") if cfg.get("_music_series") else None
    out = {}
    for g in segs:
        mood = g["mood"]
        if mood in out:
            continue
        if fixed:
            out[mood] = first_track
            continue
        if not out and first_track and folder_of(first_track, cfg) == mood.lower():
            out[mood] = first_track
            continue
        t = ""
        playing = list(out.values())[-1] if out else ""
        for m2 in near_moods(mood, allowed):
            if m2 != mood and not out and first_track and folder_of(first_track, cfg) == m2.lower():
                t = first_track                         # the opening track already came from this neighbour
            # 19 Oct 2026 (Bhay-Infinite-Loop-Milestone-Number): Tension had no fitting track and played a Dark one,
            # then the Dark section switched to another Dark track - a change of music with no change of mood
            if not t and playing and folder_of(playing, cfg) == m2.lower():
                say(f"   section mood {mood}: the music playing is already from {m2} - it carries on, no switch")
                t = playing
                break
            for _ in range(0 if t else 4):
                c = tools._horror_track(cfg, [m2]) if glob.glob(os.path.join(md, m2, "*.mp3")) else ""
                if not c:
                    break
                if c not in out.values():
                    ok, _db = tools._usable_music(ctx, c, [m2], music_db)
                    # _usable_music falls back to a softened Calm track when the folder has nothing: not for a section
                    if ok and os.path.exists(ok) and ok not in out.values() and folder_of(ok, cfg) == m2.lower():
                        t = ok
                        break
            misfit_note(cfg, say)
            if t:
                if m2 != mood:
                    say(f"   section mood {mood}: nothing in {mood} sounds like it - using {m2}: {os.path.basename(t)}")
                break
        if not t:
            prev = list(out.values())[-1] if out else first_track
            say(f"   (no other usable track in {mood} - the music stays on {os.path.basename(prev or '')})")
            t = prev
        out[mood] = t
    return out


def mix(ctx, name, pictures, genre, track="", tail="1min", music_db=None, chosen=False):
    """steps 3-5: voice + music that follows the emotion map -> narration_music.wav + music_shape.json, like
    shape_mix. -> {"ok", "bed": CR curve for ambience and effects, "tracks"} or None to use shape_mix instead"""
    import numpy as np
    import tools
    cfg, say = ctx["cfg"], ctx["say"]
    m = active(cfg, pictures, genre)
    if not m:
        return None
    s = settings(genre)
    shots = (_load(os.path.join(pictures or "", "shots.json"), {}) or {}).get("shots") or []
    sp_plan = stinger_plan(genre, shots, m)                    # all stingers of the story compete (18 Oct 2026)
    m["turns"] = [t for k, t in enumerate(m.get("turns") or [])
                  if int(t.get("weight", 0)) < 2 or k in sp_plan["turns"]]
    d = tools.narration_dir(cfg, name, existing=True)
    vp = os.path.join(d, "narration.wav")
    if not os.path.exists(vp):
        raise RuntimeError("no narration.wav to put music under")
    sh = dict(tools.load_audio_index(cfg).get("shape") or {})
    lead = float(np.clip(sh.get("lead_s", 4.0), 2, 15))
    fade_in = float(np.clip(sh.get("fade_in_s", 2.0), 0.5, lead))
    swell = swell_s(sh.get("fade_in_s", 2.0), lead) if EASE.get("on", True) else 0.0
    story = float(sh.get("story_db", -18.0)) if music_db in (None, "") else -abs(float(music_db))
    intro = float(np.clip(sh.get("intro_db", story + 4), story - 4, story + 10))
    outro = float(np.clip(sh.get("outro_db", story + 3), story - 4, story + 10))
    tail_s = {"1min": 60.0, "5min": 300.0, "usual": float(np.clip(sh.get("tail_s", 60), 30, 3600))}.get(tail, 60.0)
    if str(tail).startswith("secs:"):
        tail_s = float(np.clip(float(tail[5:] or tools.END_SECONDS), 1, 3600))
    sr = 48000
    v = tools._decode(cfg, vp, sr, 1)
    if str(tail).startswith("total:"):
        tail_s = max(60.0, float(tail[6:]) * 60 - lead - len(v) / sr)
    fade_out = float(np.clip(sh.get("fade_out_s", 8.0), 4, tail_s * 0.6))
    end = lead + len(v) / sr + tail_s
    sp_v, _ = speech_frames(v, sr)
    vs = lead + float(np.argmax(sp_v)) / CR
    ve = lead + (len(sp_v) - float(np.argmax(sp_v[::-1]))) / CR
    rise = min(4.0, 0.3 * max(0.1, end - ve))
    base_pts = [(0, -80.0), (fade_in, intro), (vs, intro), (vs + 3, story), (ve, story), (ve + rise, outro),
                (max(ve + rise, end - fade_out), outro), (end, -80.0)]
    if swell:                                                   # the level starts at intro; ease() does the swell
        base_pts = [(0, intro)] + [p for p in base_pts[2:] if p[0] > 0]
    pauses = [(lead + a, lead + b) for a, b in gaps(sp_v)]
    for t in m.get("turns") or []:                             # the story's own pause just before each heavy line
        A = lead + float(t["at"])
        t["pause_before"] = round(max([b - a for a, b in pauses if abs(b - A) <= 0.8] or [0.0]), 2)
    if chosen or not s.get("sections"):                        # a track you chose on the form: that one only
        segs = [{"start": 0.0, "end": end, "mood": (m["sections"][0]["mood"]), "cut": ""}]
    else:
        segs = plan_segments(m, s, lead, end, pauses)
    files = pick_tracks(ctx, segs, track, music_db, fixed=chosen or not s.get("sections"))
    if len(set(files.values())) < len(files):                 # two moods ended on the same file: one segment
        merged = []
        for g in segs:
            if merged and files[merged[-1]["mood"]] == files[g["mood"]]:
                merged[-1]["end"] = g["end"]
            else:
                merged.append(dict(g))
        segs = merged
    arrays = {}
    for mood in {g["mood"] for g in segs}:
        arrays[mood] = _track_arrays(ctx, files[mood])
    bed_music, info = render(v, arrays, segs, base_pts, m, s, lead, sr, swell=swell)
    a_v = int(lead * sr)
    mixd = bed_music
    mixd[a_v:a_v + len(v)] += v[: max(0, len(mixd) - a_v), None]
    peak = float(np.abs(mixd).max()) or 1.0
    raw = os.path.join(d, "_emotion_mix.f32")
    with open(raw, "wb") as fo:
        fo.write((mixd * min(1.0, 0.98 / peak)).astype("<f4").tobytes())
    outp = os.path.join(d, "narration_music.wav")
    if os.path.exists(outp):
        os.replace(outp, os.path.join(d, time.strftime("narration_music_old_%H%M%S.wav")))
    ok = loudnorm2(cfg, raw, sr, 2, outp, ctx.get("run_cmd"))
    try:
        os.remove(raw)
    except OSError:
        pass
    fmt = lambda t: f"{int(t // 60)}:{int(t % 60):02d}"
    tracks = [{"file": files[g["mood"]], "mood": g["mood"], "start": round(g["start"], 2), "end": round(g["end"], 2),
               "cut": g.get("cut", "")} for g in segs]
    for x in tracks:
        say(f"   music {fmt(x['start'])}-{fmt(x['end'])}: {x['mood']} - {os.path.basename(x['file'])}"
            f"{' (crossfade ' + str(s.get('crossfade_s')) + ' s)' if x['cut'] == 'crossfade' else ''}"
            f"{' (comes back in this mood after the heavy line)' if x['cut'] == 'return' else ''}")
    for e in sp_plan["dropped"]:
        say(f"   stinger left out at {fmt(lead + e['at'])}: {e['what']} ({e['why']})")
    if swell:
        say(f"   music swells in over {swell:g} s")
    for t in m.get("turns") or []:
        say(f"   turn {fmt(lead + t['at'])} weight {t['weight']}: {turn_says(s, t)} - {t['words']}"
            + (f" (no lighter {t['return_skipped']} return: the story stays {m['sections'][-1]['mood'] if m.get('sections') else ''})"
               if t.get("return_skipped") else ""))
    json.dump({"lead_s": lead, "track": tracks[0]["file"] if tracks else track, "tracks": tracks, "intro_db": intro,
               "story_db": story, "outro_db": outro, "tail_s": tail_s, "emotion": True,
               "strength": s.get("strength"), "turns": len(m.get("turns") or [])},
              open(os.path.join(d, "music_shape.json"), "w", encoding="utf-8"), indent=1)
    if ok:
        say(f"   -> {outp}  ({end / 60:.1f} min, music follows the emotion map)")
    return {"ok": ok, "bed": info["bed"].tolist(), "tracks": tracks, "lead": lead}


def sting_settings(genre):
    x = dict(STINGERS.get("default") or {})
    x.update(STINGERS.get(genre) or {})
    return x


def _is_sting_file(path):
    p = str(path or "").replace("\\", "/").lower()
    return any(re.search(r"(^|[/_ .-])" + w, p) for w in STING_WORDS)


def is_voice_fx(path):
    p = str(path or "").replace("\\", "/").lower()
    return bool(VOICE_FX.get("on", True)) and any(re.search(r"(^|[/_ .-])" + re.escape(str(w).lower()), p)
                                                    for w in VOICE_FX.get("words") or [])


def voice_fx_slot(at, length, pauses):
    """where a voice-like effect may play: (start, seconds) in a pause of the narration (story times), or None.
    pauses: [(start, end)] of the narration's silences"""
    v = VOICE_FX
    mp, early = float(v.get("min_pause_s", 0.8)), float(v.get("early_s", 0.5))
    for a, b in sorted(pauses):
        if b - a < mp or a < at - early:
            continue
        if a > at + float(v.get("search_s", 5.0)):
            break
        st = a + 0.1
        room = min(float(length), (b - float(v.get("fade_s", 0.25))) - st)
        if room >= float(v.get("min_play_s", 1.2)):
            return st, room
    return None


def stinger_plan(genre, shots, m=None, total_s=None):
    """which stingers of one story are kept: {"turns": {turn index}, "hits": {scene index}, "cues": {(scene, cue)},
    "kept": [...], "dropped": [...]} (story times, no lead). m: the emotion map whose turns are mixed, or None.
    Same inputs, same answer: mix() uses it for the turns, horror_sound for the hits and effects."""
    st = sting_settings(genre)
    shots = shots or []
    scenes = (m or {}).get("scenes") or []
    wt = lambda i: float(scenes[i]["weight"]) if 0 <= i < len(scenes) else 1.0
    ev = []
    for k, t in enumerate((m or {}).get("turns") or []):
        if int(t.get("weight", 0)) >= 2:
            ev.append({"kind": "turn", "id": k, "at": float(t["at"]), "prio": 10 * int(t["weight"]) + 2,
                       "what": f"turn w{t['weight']} {t.get('words', '')}"})
    for i, sh in enumerate(shots):
        if sh.get("hit") and i > 0:
            ev.append({"kind": "hit", "id": i, "at": float(sh.get("start", 0)) - 0.3, "prio": 10 * max(2.0, wt(i)) + 1,
                       "what": "hit " + re.split(r"[\\/]", str(sh["hit"]))[-1]})
        for j, c in enumerate(sh.get("cues") or []):
            if c.get("at") is not None and _is_sting_file(c.get("file")):
                ev.append({"kind": "cue", "id": (i, j), "at": float(c["at"]), "prio": 10 * wt(i),
                           "what": "effect " + re.split(r"[\\/]", str(c.get("file") or ""))[-1]})
    if total_s is None:
        total_s = max([float(x.get("end", 0)) for x in shots] + [60.0])
    cap = max(1, int(round(float(st.get("per_min", 1.0)) * total_s / 60.0)))
    gap = float(st.get("min_gap_s", 30))
    kept, dropped = [], []
    for e in sorted(ev, key=lambda e: (-e["prio"], e["at"])):
        if not st.get("on", True):
            kept.append(e)
        elif len(kept) >= cap:
            dropped.append(dict(e, why=f"over {cap} stingers for {total_s / 60:.1f} min"))
        elif any(abs(o["at"] - e["at"]) < gap for o in kept):
            dropped.append(dict(e, why=f"less than {gap:g} s from a stronger one"))
        else:
            kept.append(e)
    kept.sort(key=lambda e: e["at"])
    pick = lambda kind: {e["id"] for e in kept if e["kind"] == kind}
    return {"turns": pick("turn"), "hits": pick("hit"), "cues": pick("cue"), "kept": kept,
            "dropped": sorted(dropped, key=lambda e: e["at"]), "cap": cap, "gap": gap}


def tame_bed(amb, sr, peak_db):
    """an ambience bed with its loud moments held to peak_db over its usual level -> (bed, number of peaks held)"""
    import numpy as np
    if peak_db is None or len(amb) < sr:
        return amb, 0
    h = max(1, sr // 20)                                       # 50 ms frames
    n = len(amb) // h
    mono = amb[: n * h].astype(np.float64).mean(1) if amb.ndim > 1 else amb[: n * h].astype(np.float64)
    db = 10 * np.log10((mono.reshape(n, h) ** 2).mean(1) + 1e-12)
    live = db[db > -70]
    if not len(live):
        return amb, 0
    ceil = float(np.median(live)) + float(peak_db)
    over = np.maximum(0.0, db - ceil)
    k = 6                                                      # hold 0.3 s around a peak, then ease over 0.2 s
    held = np.array([over[max(0, i - k):i + k + 1].max() for i in range(n)])
    held = np.convolve(held, np.ones(4) / 4, mode="same")
    peaks = int(np.sum((over[1:] > 0) & (over[:-1] <= 0)))
    if not held.any():
        return amb, 0
    g = 10 ** (-held / 20)
    gs = np.interp(np.arange(len(amb)), np.arange(n) * h + h / 2, g).astype(np.float32)
    out = amb * (gs[:, None] if amb.ndim > 1 else gs)
    return out.astype(np.float32), peaks


def bed_at(res, sr, a, b):
    """the ambience / effects multiplier for mix samples a..b (stereo column), from mix()'s result"""
    import numpy as np
    bed = np.asarray((res or {}).get("bed") or [], np.float32)
    if not len(bed):
        return 1.0
    t = np.arange(a, b) / sr * CR
    return np.interp(t, np.arange(len(bed)), bed).astype(np.float32)[:, None]


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
