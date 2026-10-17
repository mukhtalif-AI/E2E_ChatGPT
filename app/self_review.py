"""self_review.py - the AI checks its own Experimental videos and pre-fills ratings you can override.

Checks (each one is optional):
  sfx_timing  Whisper word timestamps of the narration (words.json, made once by word_times.py in the video
              engine's Python) -> where each cue's words are really spoken, compared with where the effect was put.
  voice       how closely what Whisper hears matches the script (misread or skipped words), plus lines the
              voice check flagged; subtitles: how many subtitle lines are on screen while their words are spoken.
  pictures    the vision model rates each scene picture for quality and for fitting the scene / series look.
  script      the story model rates the script against the series' aim (hook, clarity, spoken Hindi, pacing, ending).
  sounds      music and effects: only from your own earlier ratings of those files (the AI cannot hear them).

Also used by every Experimental genre run (exp_cue_times): the effects are placed at the spoken word instead of
the old guess (position inside the sentence by word count), which is why effects were sometimes early or late.
"""
import difflib
import json
import os
import re
import subprocess
import time
import unicodedata

APP = os.path.dirname(os.path.abspath(__file__))
OK_S = 0.35            # an effect within this many seconds of its words counts as on time


def _load(path, default=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _dump(path, data):
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ------------------------------------------------------------------ text matching (keeps Hindi vowel signs)
def _tok(t):
    t = unicodedata.normalize("NFC", str(t)).lower().replace("़", "").replace("ँ", "ं")
    return "".join(c for c in t if not unicodedata.category(c)[0] in "PSZC")


def tokens(text):
    return [x for x in (_tok(w) for w in re.sub(r"\[[^\]]*\]", " ", str(text)).split()) if x]


def _sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def estimate_old(tim, s, words):
    """where horror_sound used to put a cue: the sentence holding the words, position by word count"""
    norm = lambda t: re.sub(r"[^\w\s]", "", str(t)).split()
    w = norm(words)
    if not w:
        return None
    for x in tim or []:
        if float(x["end"]) < float(s.get("start", 0)) - 1 or float(x["start"]) > float(s.get("end", 1e9)) + 1:
            continue
        tw = norm(x["text"])
        for j in range(len(tw)):
            if tw[j:j + len(w)] == w:
                return round(float(x["start"]) + (float(x["end"]) - float(x["start"])) * j / max(1, len(tw)), 2)
    return None


def _line_of(tim, words, a, b):
    """(line, token index) of the timing line that holds these words, fuzzy, inside the scene a..b"""
    w = tokens(words)
    if not w:
        return None, -1
    best = (0.0, None, -1)
    near = [x for x in tim or [] if not (float(x["end"]) < float(a) - 1 or float(x["start"]) > float(b) + 1)]
    # two neighbouring lines joined as well: words that run over a line break were never found before (17 Oct 2026)
    pairs = [{"text": f"{x['text']} {y['text']}", "start": x["start"], "end": y["end"]}
             for x, y in zip(near, near[1:]) if float(y["start"]) - float(x["end"]) < 1.5]
    for x in near + pairs:
        tw = tokens(x["text"])
        for j in range(len(tw)):
            sc = _sim(" ".join(w), " ".join(tw[j:j + len(w)]))
            if sc > best[0]:
                best = (sc, x, j)
    return (best[1], best[2]) if best[0] >= 0.6 else (None, -1)


def _chars_estimate(line, j):
    """better than word count: position by characters spoken before the words"""
    tw = tokens(line["text"])
    before = sum(len(t) for t in tw[:j]) + j * 0.5
    total = sum(len(t) for t in tw) + len(tw) * 0.5
    return float(line["start"]) + (float(line["end"]) - float(line["start"])) * before / max(1.0, total)


def word_time(words_json, tim, words, a, b):
    """-> (time, method, confidence). method: 'spoken word' (Whisper), 'characters' (estimate) or None"""
    line, j = _line_of(tim, words, a, b)
    w = tokens(words)
    ws = (words_json or {}).get("words") or []
    if line is not None and ws and w:
        t0, t1 = float(line["start"]) - 0.6, float(line["end"]) + 0.6
        cand = [x for x in ws if t0 <= float(x["start"]) <= t1]
        target = "".join(w)
        best = (0.0, None)
        for i in range(len(cand)):
            for m in (len(w) - 1, len(w), len(w) + 1):
                if m < 1 or i + m > len(cand):
                    continue
                sc = _sim(target, "".join(_tok(x["w"]) for x in cand[i:i + m]))
                # prefer the occurrence closest to where the sentence says it is, when two are equally good
                if sc > best[0] + 1e-6:
                    best = (sc, cand[i])
        if best[0] >= 0.62:
            return round(float(best[1]["start"]), 2), "spoken word", round(best[0], 2)
    if line is not None:
        return round(_chars_estimate(line, j), 2), "characters", 0.0
    return None, None, 0.0


# ------------------------------------------------------------------ Whisper word timestamps
def _lang(nar):
    try:
        with open(os.path.join(nar, "lang.txt"), encoding="utf-8") as f:
            v = f.read().strip().lower()
        return {"english": "en", "hindi": "hi"}.get(v, v[:2] or "hi")
    except OSError:
        return "hi"


def ensure_words(ctx, nar):
    """words.json next to narration.wav (made once; again when the narration changes). None when it fails."""
    cfg, say = ctx["cfg"], ctx["say"]
    wav = os.path.join(nar or "", "narration.wav")
    out = os.path.join(nar or "", "words.json")
    if not os.path.isfile(wav):
        say("   (no narration.wav - word timing skipped)")
        return None
    if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(wav):
        return _load(out)
    py, studio = cfg.get("studio_python"), cfg.get("studio_dir")
    if not py or not os.path.exists(py):
        say("   (the video engine's Python is missing - word timing skipped)")
        return None
    try:
        import tools
        tools.free_llm(ctx)                             # Whisper needs the graphics card for a minute
    except Exception:
        pass
    say("   Whisper listens for the exact time of every word (once per narration)")
    rc = ctx["run_cmd"]([py, "-u", "-s", os.path.join(APP, "word_times.py"), wav, out, _lang(nar)], cwd=studio)
    if rc != 0 or not os.path.isfile(out):
        say("   (word timing failed - the old estimate is used)")
        return None
    return _load(out)


# ------------------------------------------------------------------ step in every Experimental genre run
def cue_times(ctx, name, pictures, genre=""):
    """put each sound effect (and close-up picture) at the time its words are spoken: writes "at" into shots.json"""
    import tools
    cfg, say = ctx["cfg"], ctx["say"]
    sj = os.path.join(pictures or "", "shots.json")
    meta = _load(sj)
    if not meta:
        say("   (no scene plan - nothing to time)")
        return True
    nar = tools.narration_dir(cfg, name, existing=True)
    tim = _load(os.path.join(nar, "timing.json"), []) or []
    words = ensure_words(ctx, nar)
    moved, n, by = [], 0, {}
    for s in meta.get("shots") or []:
        if not isinstance(s, dict):
            continue
        for c in s.get("cues") or []:
            if not isinstance(c, dict):
                continue
            old = estimate_old(tim, s, c.get("words", ""))
            at, method, conf = word_time(words, tim, c.get("words", ""), s.get("start", 0), s.get("end", 1e9))
            if at is None:
                continue
            if c.get("in_gap") and method == "spoken word":   # a sound taken out of the speech: in the pause before
                ends = [float(x.get("end", x["start"])) for x in (words or {}).get("words") or [] if float(x["start"]) < at - 0.05]
                if ends and at - max(ends) >= 0.3:
                    at = round(max(ends) + min(0.15, (at - max(ends)) / 4), 2)
            c.update({"at": at, "at_method": method, "at_confidence": conf, "at_old_estimate": old})
            n += 1
            by[method] = by.get(method, 0) + 1
            if old is not None and abs(at - old) > OK_S:
                moved.append((abs(at - old), c.get("words", ""), at - old))
    for mo in meta.get("moments") or []:
        if not isinstance(mo, dict) or not mo.get("words"):
            continue
        at, method, _ = word_time(words, tim, mo["words"], float(mo.get("at", 0)) - 20, float(mo.get("at", 0)) + 20)
        if at is not None and method == "spoken word":
            mo["at_old_estimate"], mo["at"], mo["at_method"] = mo.get("at"), at, method
    _dump(sj, meta)
    total = sum(len(s.get("cues") or []) for s in meta.get("shots") or [] if isinstance(s, dict))
    say(f"   {n} sound effect(s) timed: " + ", ".join(f"{v} by {k}" for k, v in by.items()) if n else
        (f"   ⚠ {total} sound effect cue(s) planned, but their words were not found in the narration - not placed"
         if total else "   no sound effect cues in this plan"))
    if n and n < total:
        say(f"   ⚠ {total - n} cue(s) not placed: their words were not found in the narration")
    if moved:
        moved.sort(reverse=True)
        say(f"   {len(moved)} moved more than {OK_S} s compared with the old estimate; biggest: "
            + "; ".join(f"'{w}' {d:+.1f} s" for _, w, d in moved[:4]))
    return True


# ------------------------------------------------------------------ the self-review
def _score_from_share(x):
    return 5 if x >= 0.9 else 4 if x >= 0.75 else 3 if x >= 0.55 else 2 if x >= 0.35 else 1


def _small(cfg, img):
    out = os.path.splitext(img)[0] + "_review.jpg"
    subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", img, "-vf", "scale=768:-2", "-q:v", "4", out],
                   capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0)
    return out


PIC_ASK = ("You review one picture from a {desc} video (series '{label}'). The scene it should show: {want}\n"
           "The story's shared look: {look}\n"
           "Rate from 1 to 5 (1 very bad, 2 bad, 3 average, 4 good, 5 very good):\n"
           "\"quality\": technical quality - sharp, clean, correct hands/faces/anatomy, no text, logo or watermark, "
           "no artefacts;\n\"theme\": how well it shows this scene and fits the series' mood and the shared look.\n"
           'Answer ONLY JSON: {{"quality": n, "theme": n, "flaws": "a few words, or empty"}}')
SCRIPT_ASK = ("You are a strict editor of {desc} stories for a Hindi YouTube narration channel ('{label}'). "
              "Rate this script from 1 to 5 (1 very bad, 2 bad, 3 average, 4 good, 5 very good) for: a hook in the "
              "first lines, a clear story, natural spoken Hindi that is easy to narrate, pacing, the series' feeling, "
              "and the ending. Be honest; most scripts are 3.\n"
              'Answer ONLY JSON: {{"score": n, "strengths": "one line", "problems": "one line"}}\n\nSCRIPT:\n{script}')


CUE_ASK = ("These sound effects were added to a {desc} narration ('{label}'). For each one, judge from the sentence "
           "whether the sound really happens at those words and fits ('ok'), is the wrong kind of sound "
           "('wrong_sound'), or is not needed there ('not_needed'). Read the meaning, not single words.\n"
           'Answer ONLY JSON: {{"cues": [{{"i": n, "verdict": "ok|wrong_sound|not_needed", "why": "a few words"}}]}}\n\n{rows}')


def _cue_sense(cfg, cues, series, G, say):
    """{index: {"verdict", "why"}} from the story model; {} when it cannot answer"""
    if not cues:
        return {}
    import story
    rows = "\n".join(f"{i}. sentence: {c['line'][:220]} | words: {c['words']} | sound: "
                     f"{os.path.splitext(c['sound'])[0][:60]} (folder {os.path.basename(os.path.dirname(c['file']))})"
                     for i, c in enumerate(cues[:60]))
    try:
        raw, _model = story.ask(cfg, CUE_ASK.format(desc=G.get("desc", ""), label=G.get("label", series), rows=rows),
                                "auto", 0.1, True, say=say)
        out = {}
        for x in json.loads(raw).get("cues") or []:
            v = str(x.get("verdict", ""))
            if v in ("ok", "wrong_sound", "not_needed"):
                out[int(x.get("i"))] = {"verdict": v, "why": str(x.get("why", ""))[:160]}
        bad = sum(v["verdict"] != "ok" for v in out.values())
        say(f"   the story model checked {len(out)} effect(s) for sense: {bad} look wrong or unneeded")
        return out
    except Exception as e:
        say(f"   (effects not checked for sense: {e})")
        return {}


def run(ctx, video, narration, pictures, genre="", name="", checks=None):
    import feedback
    import tools
    cfg, say = ctx["cfg"], ctx["say"]
    checks = set(checks or feedback.CHECKS)
    parts = feedback.episode_parts(cfg, video)
    parts["narration"] = narration or parts["narration"]
    parts["pictures"] = pictures or parts["pictures"]
    series = genre or parts["series"]
    G = tools.GENRES.get(series, tools.GENRES.get("bhay", {}))
    nar = parts["narration"]
    tim = _load(os.path.join(nar or "", "timing.json"), []) or []
    report = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "video": video, "series": series, "aspects": {},
              "cues": [], "pictures": [], "skipped": []}
    A = report["aspects"]

    def aspect(key, score, note):
        if score:
            A[key] = {"score": int(score), "note": note}
            feedback.save_ai(cfg, "video", video, key, int(score), note, series=series)
        say(f"   {feedback.ASPECT_LABELS.get(key, key)}: {feedback.LEVELS.get(int(score), '-') if score else 'not rated'}"
            f" - {note}")

    words = None
    if checks & {"sfx_timing", "voice"}:
        words = ensure_words(ctx, nar)
        if not words:
            report["skipped"].append("word timing (Whisper) failed")

    # 1. SFX timing
    if "sfx_timing" in checks and words:
        cues = feedback.cues_of(parts)
        ok = verified = 0
        worst = None
        sense = _cue_sense(cfg, cues, series, G, say)
        for i, c in enumerate(cues):
            if c["at"] is None:
                real, method = None, None
            else:
                real, method, conf = word_time(words, tim, c["words"], c["at"] - 30, c["at"] + 30)
            row = {**c, "spoken_at": real if method == "spoken word" else None, "sense": sense.get(i)}
            if sense.get(i) and sense[i]["verdict"] in ("wrong_sound", "not_needed"):
                row["verdict"] = sense[i]["verdict"]
                feedback.save_ai(cfg, "cue", c["id"], "timing", 2, sense[i]["why"], verdict=row["verdict"],
                                 path=c["id"].split("#")[0], series=series,
                                 extra={"words": c["words"], "file": c["file"], "line": c["line"]})
                report["cues"].append(row)
                continue
            if c["at"] is None or method != "spoken word":
                row["verdict"] = "unverified"
            else:
                d = round(c["at"] - real, 2)
                row["offset_s"] = d
                row["verdict"] = "ok" if abs(d) <= OK_S else ("late" if d > 0 else "early")
                verified += 1
                ok += row["verdict"] == "ok"
                if row["verdict"] != "ok" and (not worst or abs(d) > abs(worst["offset_s"])):
                    worst = row
                feedback.save_ai(cfg, "cue", c["id"], "timing", 5 if row["verdict"] == "ok" else 2 if abs(d) < 1 else 1,
                                 f"placed {d:+.2f} s from the spoken words" if row["verdict"] != "ok" else "on the word",
                                 verdict=row["verdict"], offset_s=d, path=c["id"].split("#")[0], series=series,
                                 extra={"words": c["words"], "file": c["file"], "line": c["line"]})
            report["cues"].append(row)
        wrong = sum(r.get("verdict") in ("wrong_sound", "not_needed") for r in report["cues"])
        if sense:
            report["cue_sense_ok"] = round(1 - wrong / max(1, len(sense)), 2)
        if verified:
            note = f"{ok} of {verified} effects within {OK_S} s of their words"
            if all(c.get("method") == "spoken word" for c in cues):
                note += " (placed by word timing in this run)"
            if wrong:
                note += f"; {wrong} effect(s) look wrong or unneeded"
            if worst:
                note += f"; worst '{worst['words']}' {abs(worst['offset_s']):.1f} s {'late' if worst['offset_s'] > 0 else 'early'}"
            aspect("sfx_timing", _score_from_share(ok / verified), note)
        elif cues:
            report["skipped"].append("SFX timing: no cue words were found in the transcript")
    # 2. voice and subtitles
    if "voice" in checks and words and tim:
        heard = [_tok(x["w"]) for x in words.get("words") or [] if _tok(x["w"])]
        said = tokens(" ".join(x["text"] for x in tim))
        ratio = difflib.SequenceMatcher(None, said, heard, autojunk=False).ratio() if said and heard else 0
        flagged = 0
        try:
            import repair
            flagged = len(repair.check_voice({"narration": nar}))
        except Exception:
            pass
        sc = 5 if ratio >= 0.9 else 4 if ratio >= 0.82 else 3 if ratio >= 0.72 else 2 if ratio >= 0.55 else 1
        if flagged and sc > 1 and flagged / max(1, len(tim)) > 0.1:
            sc -= 1
        aspect("voice", sc, f"Whisper hears {ratio * 100:.0f}% of the script words as written"
               + (f"; {flagged} line(s) flagged by the voice check" if flagged else "")
               + " (tone and emotion need your ear)")
        synced = 0
        for x in tim:
            near = "".join(_tok(w["w"]) for w in words.get("words") or []
                           if float(x["start"]) - 0.3 <= float(w["start"]) <= float(x["end"]) + 0.3)
            synced += _sim("".join(tokens(x["text"])), near) >= 0.6
        share = synced / max(1, len(tim))
        aspect("subtitles", _score_from_share(share), f"{synced} of {len(tim)} subtitle lines are on screen while they are spoken")
    # 2b. speech clarity: does the voice stand out from music + effects in the speech band?
    if "clarity" in checks and nar and tim:
        try:
            import audio_screen
            c = audio_screen.speech_clarity(cfg, nar, parts.get("lead", 0), tim)
        except Exception as e:
            c = {"error": str(e)}
        if c.get("error"):
            report["skipped"].append("speech clarity: " + c["error"])
        else:
            cues = feedback.cues_of(parts)

            def near(t):
                hits = [x["sound"] for x in cues if x["at"] is not None and t - 1 <= x["at"] <= t + 8]
                return (" (effect: " + ", ".join(hits[:2]) + ")") if hits else ""
            lead = float(parts.get("lead", 0) or 0)
            fmt = lambda t: f"{int((t + lead) // 60)}:{int((t + lead) % 60):02d}"      # video time
            db = lambda x: f"{abs(x):.0f} dB {'above' if x >= 0 else 'BELOW'}"
            note = (f"voice is {db(c['median_db'])} music + effects in the speech band (typical line), "
                    f"{db(c['p10_db'])} in the worst tenth; {c['below_bad']} line(s) under "
                    f"{audio_screen.LIMITS['clarity']['bad_db']:.0f} dB")
            if c["worst"] and c["worst"][0]["snr_db"] < audio_screen.LIMITS["clarity"]["ok_db"]:
                note += "; hardest to hear at " + ", ".join(fmt(x["start"]) + near(x["start"]) for x in c["worst"][:3])
            if c.get("piercing"):
                note += "; highs louder than the voice at " + ", ".join(fmt(x["start"]) + near(x["start"]) for x in c["piercing"][:3])
            report["clarity"] = {k: c[k] for k in ("median_db", "p10_db", "below_bad", "worst", "piercing")}
            aspect("clarity", audio_screen.clarity_score(c), note)
    # 3. pictures
    if "pictures" in checks:
        meta = _load(os.path.join(parts["pictures"] or "", "shots.json"), {}) or {}
        recs = [s for s in (meta.get("shots") or []) + (meta.get("moments") or []) if isinstance(s, dict) and s.get("file")]
        recs = [s for s in recs if os.path.isfile(os.path.join(parts["pictures"], s["file"]))][:30]
        try:
            tools.free_gpu(ctx)
        except Exception:
            pass
        q, t = [], []
        for s in recs:
            if ctx["stopped"]():
                return False
            f = os.path.join(parts["pictures"], s["file"])
            small = _small(cfg, f)
            try:
                raw = tools.model_chat(cfg, cfg.get("vision_llm", cfg.get("llm")), PIC_ASK.format(
                    desc=G.get("desc", ""), label=G.get("label", series), want=str(s.get("prompt", ""))[:400],
                    look=(meta.get("look") or "")[:220]), 0.1, True, image_path=small, timeout=300)
                d = json.loads(raw)
                qs, ts = max(1, min(5, int(round(float(d.get("quality", 0)))))), max(1, min(5, int(round(float(d.get("theme", 0))))))
                flaws = str(d.get("flaws", ""))[:160]
            except Exception as e:
                say(f"   (vision model could not rate {s['file']}: {e})")
                continue
            finally:
                try:
                    os.remove(small)
                except OSError:
                    pass
            q.append(qs)
            t.append(ts)
            feedback.save_ai(cfg, "picture", f, "quality", qs, flaws, series=series)
            feedback.save_ai(cfg, "picture", f, "theme", ts, flaws, series=series)
            report["pictures"].append({"file": f, "quality": qs, "theme": ts, "flaws": flaws, "judge_0_10": s.get("score")})
            say(f"   {s['file']}: quality {qs}/5, theme {ts}/5" + (f" - {flaws}" if flaws else ""))
        if q:
            low = sorted(report["pictures"], key=lambda x: x["quality"] + x["theme"])[:2]
            aspect("picture_quality", round(sum(q) / len(q)), f"{len(q)} pictures, average {sum(q) / len(q):.1f}/5; weakest "
                   + ", ".join(os.path.basename(x["file"]) for x in low))
            aspect("picture_theme", round(sum(t) / len(t)), f"average {sum(t) / len(t):.1f}/5 for fitting scene and series")
        elif recs:
            report["skipped"].append("pictures: the vision model gave no answer")
    # 4. script
    if "script" in checks and parts.get("script"):
        try:
            import story
            with open(parts["script"], encoding="utf-8", errors="replace") as f:
                text = f.read()[:12000]
            raw, model = story.ask(cfg, SCRIPT_ASK.format(desc=G.get("desc", ""), label=G.get("label", series), script=text),
                                   "auto", 0.2, True, say=say)
            d = json.loads(raw)
            sc = max(1, min(5, int(round(float(d.get("score", 0))))))
            note = "; ".join(x for x in (("+ " + str(d.get("strengths", ""))[:200]) if d.get("strengths") else "",
                                         ("- " + str(d.get("problems", ""))[:240]) if d.get("problems") else "") if x)
            aspect("script", sc, note + f" ({model})")
            feedback.save_ai(cfg, "script", parts["script"], "overall", sc, note, series=series)
        except Exception as e:
            report["skipped"].append(f"script: {e}")
            say(f"   (script not rated: {e})")
    # 5. music and effects: from your own ratings of the files only
    if "sounds" in checks:
        if parts.get("track"):
            sc = feedback.file_scores(cfg, "music").get(feedback.norm(parts["track"]))
            if sc:
                aspect("bgm", round(sc["avg"]), f"from your ratings of {os.path.basename(parts['track'])}")
            else:
                report["skipped"].append("background music: rate it once by ear; the AI cannot judge musical fit")
        used = feedback.sounds_used(parts)
        scores = feedback.file_scores(cfg, "sfx")
        known = [scores[feedback.norm(f)]["avg"] for f in used if feedback.norm(f) in scores]
        if known:
            aspect("sfx", round(sum(known) / len(known)), f"from your ratings of {len(known)} of the {len(used)} effects used")
        elif report.get("cue_sense_ok") is not None:
            aspect("sfx", _score_from_share(report["cue_sense_ok"]),
                   f"{report['cue_sense_ok'] * 100:.0f}% of the effects fit their sentence (story model; it cannot hear them)")
        try:
            import sound_library
            cr = sound_library.credits_for(cfg, (parts.get("tracks") or ([parts["track"]] if parts.get("track") else [])) + used)
            if cr:
                out = os.path.join(os.path.dirname(video), "credits.txt")
                with open(out, "w", encoding="utf-8") as f:
                    f.write("Music and sound credits (put these in the video description)\n\n" + "\n".join(cr) + "\n")
                report["credits"] = out
                say(f"   {len(cr)} sound(s) need a credit - written to {out}")
        except Exception as e:
            say(f"   (credits not checked: {e})")
    if A:
        scores = [v["score"] for k, v in A.items()]
        aspect("overall", round(sum(scores) / len(scores)), "AI estimate: the average of the aspects above")
    out = os.path.join(os.path.dirname(video), "self_review.json")
    _dump(out, report)
    say(f"   self-review saved: {out} (open the video's Review in the Library to change any rating)")
    try:
        tools.free_llm(ctx)
    except Exception:
        pass
    return True
