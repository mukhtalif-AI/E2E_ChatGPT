"""steps/script.py - scripts, translation, elements, emotion marks. Moved out of tools.py on 16 Oct 2026 (TOOLS_SPLIT_PLAN.md step 4h).

Do not import this file directly: import tools and use tools.<name>. Names that stay in tools.py, and names other files swap
(feedback, narrator, Tinker), are written as tools.X here on purpose.
"""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave

import tools


def translate(cfg, text):
    text = (text or "").strip()
    if not text:
        return ""
    out = tools.model_chat(cfg, cfg["llm"], tools.TRANSLATE_ASK + text, 0.3, timeout=900).strip()
    out = re.sub(r"^```\w*\s*|\s*```$", "", out).strip()
    if not re.search("[ऀ-ॿ]", out):
        raise RuntimeError("the AI did not answer in Hindi - try again")
    return out


def llm_translate(ctx, name):
    p = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    en = open(p, encoding="utf-8").read()
    if len(re.findall("[ऀ-ॿ]", en)) > len(re.findall("[A-Za-z]", en)):
        ctx["say"]("   the script is already Hindi - nothing to translate"); return
    ctx["say"](f"   asking {ctx['cfg']['llm']} (Ollama) ...")
    hi = translate(ctx["cfg"], en)
    open(p[:-4] + ".en.txt", "w", encoding="utf-8").write(en)
    open(p, "w", encoding="utf-8").write(hi)
    for ln in hi.splitlines()[:12]:
        ctx["say"]("   " + ln)
    ctx["say"](f"   (English original kept: {os.path.basename(p[:-4])}.en.txt)")


# ------------------------------------------------------------------ the AI writes a Sukoon script (see story.py)
def _clean_script(text):
    import story
    return story.clean(text)


def llm_story(ctx, name, topic="", minutes=12, notes="", subscribe=True, engine="auto", recipe=None, category="",
              lang="hi"):
    """topic (or none: the hub picks one) -> a fresh Sukoon script; ledger keeps what it used."""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    script, info = story.write(cfg, say, topic, float(minutes or 12), notes, subscribe, engine, recipe,
                               stopped=ctx["stopped"], category=category, lang=lang)
    if ctx["stopped"]():
        return False
    if story.nwords(script) < 150:
        raise RuntimeError("the script came out too short - try again")
    p = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if os.path.exists(p):
        os.replace(p, p[:-4] + time.strftime("_old_%H%M%S.txt"))
    open(p, "w", encoding="utf-8").write(script)
    summ = story.summarise(cfg, script, say)
    entry = {"name": name, "date": time.strftime("%Y-%m-%d %H:%M"), "topic": info["recipe"]["topic"], "lang": lang,
             "recipe": info["recipe"], "model": info["model"], "words": story.nwords(script), "summary": summ}
    story.add_ledger(cfg, entry)
    json.dump(entry, open(p[:-4] + ".story.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if summ.get("title_hi"):
        say(f"   title: {summ.get('title_hi')}  /  {summ.get('title_en', '')}")
    for ln in [l for l in script.splitlines() if l != "(...)"][:4]:
        say("      " + ln)
    say(f"   script saved: {p}  ({story.nwords(script)} words, ~{story.estimate(cfg, script)['minutes']:.0f} min) "
        "- edit it any time in Library > Scripts")
    return True


def fit_script(ctx, name, total_min, engine="auto", story_mode=False):
    """the video is shorter than the speech -> the AI shortens the script (story kept); the full one is kept"""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    p = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    text = open(p, encoding="utf-8").read()
    est = story.estimate(cfg, text)
    cap = float(total_min) - tools.MIN_TAIL
    say(f"   script: {est['words']} words ≈ {est['minutes']} min of your voice ({est['wpm']} words/min) | video "
        f"{float(total_min):.0f} min -> speech up to {cap:.1f} min")
    if est["minutes"] <= cap + 0.2:
        say("   fits - nothing to shorten"); return True
    full = p[:-4] + "_full.txt"
    if not os.path.exists(full):
        open(full, "w", encoding="utf-8").write(text)
    new = story.shorten(cfg, text, cap, engine, say, story_mode)
    e2 = story.estimate(cfg, new)
    if e2["minutes"] > cap + 0.2:
        say(f"   still too long at ~{e2['minutes']} min; kept the original script and stopped before narration")
        return False
    open(p, "w", encoding="utf-8").write(new)
    say(f"   now {e2['words']} words ≈ {e2['minutes']} min (the full script is kept: {os.path.basename(full)})")
    return True


def _queue_fetch(ctx, kinds, why):
    import elements_auto
    miss = elements_auto.missing(ctx["cfg"], kinds)
    have = [k for k in kinds if k not in miss]
    if have:
        ctx["say"](f"   ready in the library: {', '.join(have)}")
    if not miss:
        return
    if not __import__("story").secret("pixabay_key"):
        ctx["say"](f"   missing: {', '.join(miss)} - add a Pixabay key in Settings so the hub can fetch them"); return
    if ctx.get("add_job"):
        ctx["add_job"]("elements_fetch", "Fetch elements: " + ", ".join(miss),
                       [{"fn": "elements_fetch", "label": "find + download + register + loop new element clips",
                         "args": {"kinds": miss}}])
        ctx["say"](f"   missing: {', '.join(miss)} -> fetching in the background ({why})")


def elements_check(ctx, name="", art=""):
    import elements_auto
    text = ""
    sp = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    if os.path.exists(sp):
        text += open(sp, encoding="utf-8").read()
    sj = os.path.join(art or "", "shots.json")
    if art and os.path.exists(sj):
        text += "\n" + " ".join(x.get("prompt", "") for x in json.load(open(sj, encoding="utf-8")).get("shots", []))
    kinds = elements_auto.needs(text)
    ctx["say"](f"   this episode mentions: {', '.join(kinds) or 'no moving elements'}")
    if kinds:
        _queue_fetch(ctx, kinds, "for this and the next episodes")
    return True


def elements_fetch(ctx, kinds=None):
    import elements_auto
    n = elements_auto.fetch(ctx["cfg"], kinds or [], ctx["say"], ctx["cfg"].get("studio_python"))
    ctx["say"](f"   {n} new clip(s); register: {os.path.join(elements_auto.lib_dir(ctx['cfg']), 'ELEMENTS_INDEX.csv')}")
    return True


def write_stories(ctx, n=5, topics=None, minutes=12, engine="auto", category="", lang="hi"):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    made = 0
    for i in range(int(n)):
        if ctx["stopped"]():
            return False
        topic = (topics or [])[i] if topics and i < len(topics) else ""
        say(f"\n   --- script {i + 1}/{n}")
        try:
            script, info = story.write(cfg, say, topic, float(minutes), "", True, engine, None, stopped=ctx["stopped"],
                                       category=category, lang=lang)
        except Exception as e:
            say(f"   !! {e}"); continue
        if story.nwords(script) < 150:
            say("   (too short - skipped)"); continue
        summ = story.summarise(cfg, script, say)
        title = summ.get("title_en") or info["recipe"]["topic"]
        name = f"Sukoon {'EN ' if lang == 'en' else ''}- {tools.slug(title, 50)}"
        p = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
        if os.path.exists(p):
            p = p[:-4] + time.strftime("_%H%M%S.txt")
        open(p, "w", encoding="utf-8").write(script)
        entry = {"name": os.path.basename(p)[:-4], "date": time.strftime("%Y-%m-%d %H:%M"), "topic": info["recipe"]["topic"],
                 "lang": lang,
                 "recipe": info["recipe"], "model": info["model"], "words": story.nwords(script), "summary": summ,
                 "status": "written (no voice yet)"}
        story.add_ledger(cfg, entry)
        json.dump(entry, open(p[:-4] + ".story.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        say(f"   saved: {os.path.basename(p)}  ({story.nwords(script)} words)  {summ.get('title_hi', '')}")
        made += 1
    say(f"\n   {made} scripts in {os.path.join(ctx['media'], 'scripts')} - open one in Sukoon episode to make it")
    try:
        import elements_auto
        allk = set()
        for f in glob.glob(os.path.join(tools.ctx_folder(ctx, "scripts"), "*.txt"))[-max(1, made):]:
            allk |= set(elements_auto.needs(open(f, encoding="utf-8").read()))
        if allk:
            _queue_fetch(ctx, sorted(allk), "for these scripts")
    except Exception as e:
        say(f"   (elements check skipped: {e})")
    return made > 0


# ------------------------------------------------------------------ steps
def write_script(ctx, name, text):
    p = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    open(p, "w", encoding="utf-8").write(text.replace("\r\n", "\n"))
    ctx["say"](f"   {p}  ({len(text.split())} words)")


EMOTIONS = ["डर", "फुसफुसाहट", "रहस्य", "उदास", "प्यार", "गुस्सा", "उत्साह", "सामान्य"]


EMO_BATCH = 15     # lines per emotion-marks request (Experimental)


def _emo_salvage(t):
    """the complete {"n": {...}} entries of a cut or broken JSON reply"""
    d = {}
    for m in re.finditer(r'"(\d+)"\s*:\s*(\{[^{}]*\})', str(t or "")):
        try:
            v = json.loads(m.group(2))
        except ValueError:
            continue
        if isinstance(v, dict):
            d[m.group(1)] = v
    return d


def _emo_reply(cfg, prompt, say):
    """emotion-marks JSON from the model: JSON mode, then once as text; a broken reply keeps its complete entries"""
    import story
    err = None
    for as_json in (True, False):
        try:
            t, _ = story.ask(cfg, prompt, "auto", 0.2, as_json=as_json, say=say, ctx_len=16384)
        except TypeError:                                    # an older story.py without ctx_len
            t, _ = story.ask(cfg, prompt, "auto", 0.2, as_json=as_json, say=say)
        try:
            d = tools._loads(t)
            if isinstance(d, dict):
                return d
        except ValueError as e:
            err = e
        d = _emo_salvage(t)
        if d:
            say(f"   (the AI's reply was cut or broken; kept the {len(d)} complete lines)")
            return d
    raise err or ValueError("no usable JSON in the model's reply")


def mark_emotions(ctx, name, genre="", emotions=True, pauses=True):
    """Add sparse context-aware delivery tags and punctuation-only pause cues."""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    p = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    if not os.path.exists(p):
        return say("   (no script to mark)")
    lines = open(p, encoding="utf-8").read().replace("\r\n", "\n").split("\n")
    tag = re.compile(r"^\s*\[(" + "|".join(EMOTIONS) + r")\]")
    content = {i: tag.sub("", line, count=1).strip() for i, line in enumerate(lines)}
    idx = [i for i, line in content.items() if re.search("[\u0900-\u097F]", line)
           and not line.startswith(("(", "["))]
    if not idx:
        return say("   (no Hindi story lines to mark)")
    marks, paced = {}, {}
    # Experimental: small batches - one 60-line reply ran past the local model's context and came back cut
    exp = tools._is_exp(cfg)
    step = EMO_BATCH if exp else 60
    for k in range(0, len(idx), step):
        part = idx[k:k + step]
        first, last = idx.index(part[0]), idx.index(part[-1])
        lo, hi = max(0, first - 2), min(len(idx), last + 3)
        context = "\n".join(f"{idx[j] + 1}. {content[idx[j]]}" for j in range(lo, hi))
        targets = "\n".join(f"{j + 1}. {content[i]}" for j, i in enumerate(part))
        prompt = ("You are a restrained Hindi narration director" +
                  (f" for {tools.GENRES[genre]['desc']}" if genre in tools.GENRES else "") + ". " +
                  tools.DIRECTION.get(genre, tools.DIRECTION[""]) + "\nRead the surrounding passage for meaning. For every TARGET line, " +
                  ("choose a justified delivery feeling from " + ", ".join(EMOTIONS) +
                   "; use सामान्य for no special feeling. " if emotions else "do not assign emotions. ") +
                  ("Only when genuinely useful, add '...' for a held beat or ',' for a short breath; most lines get none. "
                   if pauses else "Do not change punctuation. ") +
                  "Words such as पर, तो, कि, फिर, ही, भी, बस, अब, वो and कोई have no automatic cue: decide from the whole "
                  "sentence and nearby passage. Keep dependent clauses connected; do not create suspense by fragmenting text. "
                  "Never rewrite, add, delete, reorder, or inflect spoken words. Return JSON only, keyed by target number; "
                  "omit fields when unchanged. Example: {\"1\":{\"e\":\"डर\",\"t\":\"same words with punctuation only\"}}\n\n"
                  "SURROUNDING PASSAGE (context only):\n" + context + "\n\nTARGET LINES:\n" + targets)
        try:
            if exp:
                d = _emo_reply(cfg, prompt, say)
            else:
                t, _ = story.ask(cfg, prompt, "auto", 0.2, as_json=True, say=say)
                d = json.loads(t[t.find("{"):t.rfind("}") + 1])
        except Exception as e:
            say(f"   (emotion marks skipped for lines {k + 1}-{k + len(part)}: {e})")
            continue
        for j, i in enumerate(part):
            v = d.get(str(j + 1), "")
            if not isinstance(v, dict):
                continue
            e = str(v.get("e", "")).strip()
            if emotions and e in EMOTIONS and e != "सामान्य" and not tag.match(lines[i]):
                marks[i] = e
            candidate = str(v.get("t", "")).strip()
            original_words = re.findall(r"[^\W_]+", content[i].casefold(), flags=re.UNICODE)
            candidate_words = re.findall(r"[^\W_]+", candidate.casefold(), flags=re.UNICODE)
            original_punctuation = re.findall(r"[^\w\s]", content[i], flags=re.UNICODE)
            candidate_punctuation = re.findall(r"[^\w\s]", candidate, flags=re.UNICODE)
            punctuation_pos, added_punctuation = 0, []
            for mark in candidate_punctuation:
                if punctuation_pos < len(original_punctuation) and mark == original_punctuation[punctuation_pos]:
                    punctuation_pos += 1
                else:
                    added_punctuation.append(mark)
            preserved = punctuation_pos == len(original_punctuation)
            allowed_additions = all(mark in (",", ".") for mark in added_punctuation)
            if pauses and candidate and candidate.count("...") <= 2 and candidate_words == original_words \
                    and preserved and allowed_additions and candidate != content[i]:
                paced[i] = candidate
    if not marks and not paced:
        return say("   no safe emotion or pause cues were returned; original script kept")
    old = os.path.join(os.path.dirname(p), "_old")
    os.makedirs(old, exist_ok=True)
    shutil.copy2(p, os.path.join(old, f"{tools.slug(name)}_before_emotions_{time.strftime('%Y%m%d_%H%M%S')}.txt"))
    for i, text in paced.items():
        existing = tag.match(lines[i])
        prefix = f"[{existing.group(1)}] " if existing else ""
        lines[i] = prefix + text
    for i, emotion in marks.items():
        lines[i] = f"[{emotion}] " + lines[i].lstrip()
    open(p + ".part", "w", encoding="utf-8").write("\n".join(lines))
    os.replace(p + ".part", p)
    counts = {}
    for emotion in marks.values():
        counts[emotion] = counts.get(emotion, 0) + 1
    say(f"   {len(marks)} of {len(idx)} lines marked: " +
        (", ".join(f"{key} {value}" for key, value in counts.items()) or "no new emotion tags") +
        f"; {len(paced)} lines got a context-checked pause cue - saved in the script")
