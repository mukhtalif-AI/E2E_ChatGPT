"""story.py - the AI writes Sukoon scripts that stay fresh, relevant and coherent.

  writer     Gemini (Google AI Studio key, fast) or local Ollama Gemma (offline fallback)
  ledger     D:\\MukhtalifGPT\\story_ledger.json - what every earlier script used (topic, central image, place,
             way of telling, opening/closing lines) -> the next one is told NOT to repeat it
  recipe     each episode gets a different way of telling + ONE central image + healing place + closing,
             chosen from the topic bank, least-recently-used first
  bank       D:\\MukhtalifGPT\\story_bank.json (made on first use, edit it freely): topics, seasonal topics,
             ways of telling, images, places, closings
  check      a second pass reads the script against a checklist (night setting, one image kept, no repeats,
             natural spoken Hindi, ending returns to the start) and fixes it
  ideas      3 short episode ideas to choose from; "copy prompt" for pasting into any chat site
"""
import glob, json, os, random, re, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SECRETS = os.path.join(ROOT, "secrets.json")                  # never in Code\ALL_CODE.txt, never shown in the UI

BANK = {
    "topics": [
        "heartbreak - it is okay to be heartbroken", "exam pressure - you are more than your marks",
        "trust issues after being let down", "being cheated on", "nostalgia - a trip back to childhood",
        "a really bad day", "loneliness in a new city", "fear about career and the future",
        "distance from family", "comparing yourself with others", "missing someone who passed away",
        "starting over after failure", "overthinking at night", "feeling not good enough",
        "friendships that faded", "pressure to have everything figured out", "missing home while working away",
        "a long-distance relationship", "feeling invisible", "forgiving yourself"],
    "seasonal": {
        "1": ["new year - letting go of last year", "loneliness after the holidays"],
        "2": ["exam pressure - you are more than your marks", "board exams fear"],
        "3": ["exam pressure - you are more than your marks", "results anxiety"],
        "4": ["results day - whatever the result, you are enough", "summer holidays of childhood"],
        "5": ["summer holidays of childhood - nani ka ghar", "results day - whatever the result, you are enough"],
        "6": ["first monsoon rain and old memories", "new college, new city"],
        "7": ["monsoon nights and missing someone", "new college, new city"],
        "8": ["friendships that faded", "siblings and the home we grew up in"],
        "9": ["festivals away from home", "pressure to have everything figured out"],
        "10": ["Diwali away from home", "festivals away from home"],
        "11": ["winter nights and loneliness", "missing home while working away"],
        "12": ["looking back at the year with kindness", "forgiving yourself"]},
    "tellings": [
        "talking softly to the listener as a close friend ('तुम')",
        "a letter read aloud to the listener's younger self",
        "a small story about someone else (a boy or girl in a small town) that slowly becomes the listener's own story",
        "a quiet conversation with the night and the moon",
        "remembering together - 'याद है...' - one shared memory at a time"],
    "images": [
        "a kite and its string", "monsoon rain on a tin roof", "a night train", "an old radio", "a diya flame",
        "a banyan tree", "old letters in a drawer", "a cup of chai going cold", "a paper boat", "an old bicycle",
        "a lantern", "grandmother's shawl", "fireflies in a jar", "the last page of a school notebook",
        "a terrace under the stars", "a window with rain drops"],
    "places": [
        "a village rooftop under the stars", "a train window at night", "a rainy balcony",
        "grandmother's courtyard at night", "a hill-station cottage in fog", "a riverbank ghat with floating diyas",
        "a quiet library after closing", "a boat on a still lake under the moon", "a terrace in summer night breeze"],
    "closings": [
        "counting breaths slowly from ten to one", "a small promise to themselves", "a soft blessing (दुआ)",
        "one gentle line repeated, slower each time", "the central image slowly dimming into sleep"]}

CATEGORIES = {
    "counselling": ("Counselling", "1 welcome into the night, settle the body and breath {sub}2 name the feeling and say "
                    "it is okay 3 understand it through a memory or small story 4 gentle counsel, a kinder way to see "
                    "it 5 healing visualisation at the place 6 slow down into sleep"),
    "nostalgia": ("Nostalgia", "1 welcome into the night, settle the body and breath {sub}2 open a door to the past "
                  "3 walk slowly through one remembered place, with all the senses (sounds, smells, people) 4 what "
                  "that time still gives us today 5 carry that warmth into the place 6 slow down into sleep"),
    "story": ("A small story", "1 welcome into the night, settle the body and breath {sub}2 begin a small, simple "
              "story (one person, one place) 3 the story's gentle problem 4 how it softly turns 5 what it means for "
              "the listener, resting at the place 6 slow down into sleep"),
    "guided": ("Guided sleep", "1 welcome into the night {sub}2 slow breathing, counted 3 relax the body part by part "
               "4 a gentle reassurance about the topic 5 long, slow visualisation at the place 6 slow down into sleep"),
    "letter": ("A letter", "1 welcome into the night {sub}2 'this letter is for you' 3 the letter: what the listener "
               "went through, seen with kindness 4 what they deserve to hear 5 resting at the place while it sinks "
               "in 6 slow down into sleep"),
}

SPOKEN_HINDI_CADENCE = (
    "Narration must sound natural when read aloud: write complete, grammatically connected sentences, not a string of "
    "dramatic fragments. Keep normal Hindi word order and put the subject before its action when that is how a person "
    "would naturally say it (for example, 'मैं झील के किनारे मछलियाँ देख रहा था'). Keep a noun with its adjectives; "
    "do not split a thought across lines just to create pauses. Start 'और', 'लेकिन', 'तो' or 'फिर' only when the "
    "meaning truly needs that connection; do not use them as filler between every sentence. Use ellipses and pause-only "
    "lines sparingly, only for a real dramatic or emotional beat. Prefer one natural, speakable sentence per line and "
    "blank lines only between scene or thought paragraphs. Read each line silently for conversational rhythm."
)

RULES = (
    "Write in warm, simple, spoken Hindi (Devanagari) with everyday Hindi/Urdu words - the way a calm friend talks "
    "softly at night; never bookish, never preachy. Prefer one complete, naturally connected sentence per line. Use a "
    "line with only (...) only where a longer pause is intended; use '...' sparingly for a genuine soft pause. First "
    "validate the feeling (it is okay to feel "
    "this), then gently understand, counsel, comfort and heal. It is night: the listener lies in bed - no daylight "
    "scenes. No lists, headings, emojis or stage directions. Keep it contemporary: mix in the everyday English words "
    "young Indians naturally say (phone, relax, stress, compare, overthink, okay, sorry, marks, career), written in "
    "Devanagari (फ़ोन, रिलैक्स, स्ट्रेस, कम्पेयर) - a natural sprinkle, never full English sentences. Each line 6 to 14 words (only the "
    "final good-night lines and counted breaths may be shorter). Counting breaths: each number on its own line ('एक...', "
    "then the (...) line, then 'दो...'), slowly, never several numbers in one line. No medical or clinical advice, no "
    "made-up facts or numbers. " + SPOKEN_HINDI_CADENCE)

CHECK = (
    "Check this Hindi Sukoon sleep script against the list and return the CORRECTED FULL SCRIPT ONLY, with no "
    "explanation or commentary after the final line, and with one "
    "complete conversational sentence per line and pause-only lines only where a longer pause is intended:\n"
    "1 it is night; nothing happens in daylight or sunshine\n2 the central image ({image}) is kept from start to end "
    "and the ending returns to it\n3 no line, phrase or idea is repeated (except the final good-night lines)\n"
    "4 natural spoken Hindi - fix awkward word order, fragments, filler conjunctions and choppy line breaks; read it "
    "aloud mentally and keep complete conversational sentences, nothing preachy\n5 the listener is addressed "
    "consistently\n6 the flow: welcome + breath -> it is okay -> understanding -> gentle counsel -> healing at "
    "{place} -> sleep and good night\n7 keep the length (about {words} words); do not summarise.\n\nSCRIPT:\n\n")

# ---- English episodes (read by the English narrator, not your voice) ------------------------------------
RULES_EN = (
    "Write in warm, simple, spoken English - the way a calm friend talks softly at night; never bookish, never "
    "preachy, no therapy jargon. Very short phrases: ONE phrase per line, and after EVERY line a line with only (...) . "
    "Use '...' inside lines for soft pauses. First validate the feeling (it is okay to feel this), then gently "
    "understand, counsel, comfort and heal. It is night: the listener lies in bed - no daylight scenes. No lists, "
    "headings, emojis or stage directions. Contractions are fine (it's, you're). A gentle Indian warmth is welcome "
    "(chai, monsoon, a grandmother's courtyard) but the English stays natural for listeners anywhere. Each line 6 to "
    "16 words (only the final good-night lines and counted breaths may be shorter). Counting breaths: each number on "
    "its own line ('one...', then the (...) line, then 'two...'), never several numbers in one line. No medical or "
    "clinical advice, no made-up facts or numbers.")
CHECK_EN = CHECK.replace("Hindi Sukoon", "English Sukoon").replace("natural spoken Hindi", "natural spoken English")
TELLINGS_EN = [
    "talking softly to the listener as a close friend ('you')",
    "a letter read aloud to the listener's younger self",
    "a small story about someone else (a boy or girl in a small town) that slowly becomes the listener's own story",
    "a quiet conversation with the night and the moon",
    "remembering together - 'do you remember...' - one shared memory at a time"]
CLOSINGS_EN = ["counting breaths slowly from ten to one", "a small promise to themselves", "a soft blessing",
               "one gentle line repeated, slower each time", "the central image slowly dimming into sleep"]
EN_END = re.compile(r"\b(sleep now|good ?night)\b", re.I)
COUNT_LINE = re.compile(r"^\s*(एक|दो|तीन|चार|पांच|पाँच|छह|छः|सात|आठ|नौ|दस|one|two|three|four|five|six|seven|eight|nine|ten)"
                        r"\s*[.…,!]*\s*$", re.I)


# ------------------------------------------------------------------ files
def _media(cfg, name):
    return os.path.join(cfg["media_dir"], name)


def bank(cfg):
    p = _media(cfg, "story_bank.json")
    if not os.path.exists(p):
        json.dump(BANK, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    try:
        b = json.load(open(p, encoding="utf-8"))
        return {k: b.get(k) or v for k, v in BANK.items()}
    except Exception:
        return BANK


def _own_ledger(cfg):
    p = _media(cfg, "story_ledger.json")
    if not os.path.exists(p):
        _seed_ledger(cfg, p)
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return []


def _other_ledgers(cfg):
    """the other ledgers on this PC (read only): Working's D:\\MukhtalifGPT\\story_ledger.json, Experimental's
    <root>\\Library\\story_ledger.json and the older data folders (paths.legacy_roots). Each mode used to see only its
    own, so stories written in the other mode or before the data-folder move were offered again (17 Oct 2026)."""
    paths = cfg.get("paths") or {}
    roots = [paths.get("hub_output_root"), cfg.get("experimental_root") or paths.get("experimental_root")] \
        + list(paths.get("legacy_roots") or [])
    own = os.path.normcase(os.path.abspath(_media(cfg, "story_ledger.json")))
    seen, out = {own}, []
    for r in roots:
        if not r:
            continue
        for p in (os.path.join(r, "story_ledger.json"), os.path.join(r, "Library", "story_ledger.json")):
            k = os.path.normcase(os.path.abspath(p))
            if k in seen or not os.path.exists(p):
                continue
            seen.add(k)
            try:
                out += [e for e in json.load(open(p, encoding="utf-8")) if isinstance(e, dict)]
            except Exception:
                pass
    return out


def ledger(cfg):
    """every script written so far, oldest first: this mode's own ledger + the others (same story listed once)"""
    L, keys = [], set()
    for e in _own_ledger(cfg) + _other_ledgers(cfg):
        s = e.get("summary") or {}
        k = (str(e.get("topic", "")).strip().lower(), str(s.get("opening_line", ""))[:60] or e.get("name", ""))
        if k in keys:
            continue
        keys.add(k)
        L.append(e)
    return sorted(L, key=lambda e: str(e.get("date", "")))


def _seed_ledger(cfg, p):
    """first use: your existing episodes (and hub scripts) go into the ledger, so new scripts avoid them"""
    seen, L = set(), []
    files = glob.glob(os.path.join(cfg.get("episodes_dir", ""), "*", "script.txt")) + \
        glob.glob(os.path.join(cfg["scripts_dir"], "*.txt"))
    for f in files:
        if re.search(r"(_old_\d+|_full|\.en)\.txt$", f):
            continue
        try:
            t = open(f, encoding="utf-8-sig").read()
        except OSError:
            continue
        lines = [l.strip() for l in t.splitlines() if l.strip() and l.strip() != "(...)" and re.search("[ऀ-ॿ]", l)]
        if len(lines) < 10 or t[:300] in seen:
            continue
        seen.add(t[:300])
        name = os.path.basename(os.path.dirname(f)) if f.endswith("script.txt") else os.path.basename(f)[:-4]
        L.append({"name": name, "date": time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(f))),
                  "topic": name, "recipe": {}, "model": "earlier episode", "words": nwords(t),
                  "summary": {"opening_line": lines[0][:120], "closing_line": lines[-1][:120]}})
    try:
        json.dump(L, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except OSError:
        pass


def add_ledger(cfg, entry):
    L = _own_ledger(cfg)                                       # written only to this mode's own file
    L.append(entry)
    json.dump(L, open(_media(cfg, "story_ledger.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def secret(key):
    try:
        return json.load(open(SECRETS, encoding="utf-8")).get(key, "")
    except Exception:
        return ""


def save_settings(cfg, body):
    s = {}
    if os.path.exists(SECRETS):
        try:
            s = json.load(open(SECRETS, encoding="utf-8"))
        except Exception:
            s = {}
    if body.get("gemini_key"):
        s["gemini_key"] = body["gemini_key"].strip()
    if body.get("gemini_model"):
        s["gemini_model"] = body["gemini_model"].strip()
    if body.get("pixabay_key"):
        s["pixabay_key"] = body["pixabay_key"].strip()
    if body.get("forget_key"):
        s.pop("gemini_key", None)
    if body.get("freesound_key"):
        s["freesound_key"] = body["freesound_key"].strip()
    if body.get("forget_freesound"):
        s.pop("freesound_key", None)
    if body.get("forget_pixabay"):
        s.pop("pixabay_key", None)
    if body.get("local_model"):
        s["local_model"] = body["local_model"].strip()
    if body.get("story_engine") in ("auto", "local"):
        s["story_engine"] = body["story_engine"]
    json.dump(s, open(SECRETS, "w", encoding="utf-8"), indent=1)
    return status(cfg)


def local_model(cfg):
    if cfg.get("_llm_override"):
        return cfg["_llm_override"]
    selected = secret("local_model")
    installed = local_models(cfg)
    if selected in installed:
        return selected
    preferred = cfg.get("story_model") or cfg.get("llm")
    return preferred if preferred in installed or not installed else installed[0]


def local_models(cfg):
    """Models currently installed in Ollama."""
    try:
        url = cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/") + "/api/tags"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=3) as r:
            return sorted({m.get("name", "") for m in json.loads(r.read()).get("models", []) if m.get("name")})
    except Exception:
        return []


def status(cfg):
    return {"gemini": bool(secret("gemini_key")), "pixabay": bool(secret("pixabay_key")), "freesound": bool(secret("freesound_key")), "gemini_model": secret("gemini_model") or GEMINI_MODELS[0],
            "local": local_model(cfg), "local_models": local_models(cfg),
            "story_engine": secret("story_engine") or "auto", "ledger": len(ledger(cfg)),
            "bank": _media(cfg, "story_bank.json"), "ledger_file": _media(cfg, "story_ledger.json")}


# ------------------------------------------------------------------ writers
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.5-flash-lite"]
_BUSY = {}                                                   # model -> time it last said "busy" (skipped 10 min)


_MODELS = {}


def gemini_models(key):
    """the text models THIS key may use right now, newest first (Google retires names: 2.0 / 2.5-flash went away
    for new users) - asked once per day, so the hub never sticks to a retired name"""
    if _MODELS.get("t", 0) > time.time() - 86400:
        return _MODELS["v"]
    found = []
    try:
        req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                     headers={"x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=20) as r:
            for m in json.loads(r.read()).get("models", []):
                n = m.get("name", "").replace("models/", "")
                if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                    continue
                if not n.startswith("gemini") or re.search(r"image|tts|audio|live|embed|vision|thinking|computer|robotics", n):
                    continue
                found.append(n)
    except Exception:
        return GEMINI_MODELS

    def rank(n):
        v = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        ver = float(v.group(1)) if v else 0
        return (("flash" in n) * 2 + ("pro" in n) * 1, -("lite" in n), ver, -("preview" in n or "exp" in n), -len(n))
    found.sort(key=rank, reverse=True)
    _MODELS.update(t=time.time(), v=found[:6] or GEMINI_MODELS)
    return _MODELS["v"]


def gemini(prompt, temp=0.9, as_json=False, timeout=None):
    """Gemini, answering fast or not at all: a busy model (503 / 429) is skipped for 10 minutes and the next one is
    tried at once; short questions wait at most 60 s, whole scripts 180 s; then the local model takes over."""
    key = secret("gemini_key")
    if not key:
        raise RuntimeError("no Gemini key - add it in Settings")
    timeout = timeout or (60 if as_json or len(prompt) < 4000 else 180)
    models = [secret("gemini_model")] + gemini_models(key)
    models = [x for i, x in enumerate(models) if x and x not in models[:i]]
    fresh = [m for m in models if time.time() - _BUSY.get(m, 0) > 600]
    last, t_start, tried = None, time.time(), []
    for m in (fresh or models)[:3]:
        if time.time() - t_start > timeout * 1.5:
            break
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temp, "maxOutputTokens": 16384,
                                     **({"thinkingConfig": {"thinkingBudget": 0 if as_json else 1024}}
                                        if "2.5" in m else {}),
                                     **({"responseMimeType": "application/json"} if as_json else {})}}
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
            json.dumps(body).encode(), {"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read())
            parts = d["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts).strip(), m
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:300]
            last = f"{m}: HTTP {e.code} " + ("busy" if e.code in (429, 503) else msg[:120].replace(chr(10), " "))
            tried.append(last)
            if e.code in (400, 401, 403):
                if "API key" in msg or e.code in (401, 403):
                    raise RuntimeError("Gemini refused the key - check it in Settings")
            if e.code in (429, 500, 503):
                _BUSY[m] = time.time()
            if e.code == 404:                                    # retired name: never again today
                _BUSY[m] = time.time() + 86400
            continue                                            # 404 model / 429 limit / 5xx -> next model
        except (KeyError, IndexError) as e:
            last = f"{m}: empty answer ({e})"; tried.append(last)
        except OSError as e:
            last = f"{m}: {e}"; tried.append(last)
            if "timed out" in str(e):
                _BUSY[m] = time.time()
                break                                           # the next model would wait just as long
    raise RuntimeError("Gemini did not answer (" + ("; ".join(tried) or str(last)) + ")")


def local_chat(cfg, prompt, temp=0.85, as_json=False, ctx_len=8192):
    model = local_model(cfg)
    cfg["_ollama_used"] = True
    body = {"model": model, "prompt": prompt, "stream": False,
            "options": {"temperature": temp, "num_ctx": ctx_len, "repeat_penalty": 1.12}}
    # Ollama exposes native thinking for supported Qwen3 text models. Gemma 3 has no
    # advertised think control, so leave its request unchanged.
    if model.lower().startswith("qwen3") and not any(tag in model.lower() for tag in ("vl", "vision")):
        body["think"] = True
    # Gemma 4 thinks by default in Ollama: ~3000 hidden tokens (60-100 s on the 3060) before a short answer.
    # A step that only needs a quick answer sets cfg["_no_think"] (picture checklists and prompt rewrites).
    if cfg.get("_no_think") and model.lower().startswith(("gemma4", "qwen3")):
        body["think"] = False
    if as_json:
        body["format"] = "json"
    req = urllib.request.Request(cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/") + "/api/generate",
                                 json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1500) as r:
        return (json.loads(r.read()).get("response") or "").strip(), model


def ask(cfg, prompt, engine="auto", temp=0.85, as_json=False, say=print, ctx_len=8192):
    """-> (text, model). auto = Gemini if a key is set (falls back to local if Gemini fails)."""
    if engine == "auto" and secret("story_engine") == "local":
        engine = "local"
    if engine in ("auto", "gemini") and secret("gemini_key"):
        try:
            t, m = gemini(prompt, temp, as_json)
            return _strip(t), m
        except RuntimeError as e:
            if engine == "gemini":
                raise
            say(f"   (Gemini: {e} - using the local model)")
    t, m = local_chat(cfg, prompt, temp, as_json, ctx_len)
    return _strip(t), m


def _strip(t):
    return re.sub(r"^```\w*\s*|\s*```$", "", t or "").strip()


# ------------------------------------------------------------------ recipe + prompt
def _least_used(options, used, rng):
    count = {o: 0 for o in options}
    for i, u in enumerate(reversed(used)):                     # recent use weighs more
        if u in count:
            count[u] += 1.0 / (1 + i * 0.3)
    low = min(count.values()) if count else 0
    pool = [o for o, c in count.items() if c <= low + 1e-9]
    return rng.choice(pool) if pool else rng.choice(options)


def pick_topic(cfg, seed=None):
    rng = random.Random(seed)
    b, L = bank(cfg), ledger(cfg)
    season = b["seasonal"].get(str(int(time.strftime("%m"))), [])
    pool = (season * 2 + b["topics"]) if rng.random() < 0.5 else b["topics"]
    return _least_used(list(dict.fromkeys(pool)), [e.get("topic") for e in L], rng)


def recipe(cfg, topic="", seed=None, category="", also=None, lang="hi"):
    """also: recipes already offered (e.g. the other 'ideas') - treated as just used, so each idea differs"""
    rng = random.Random(seed)
    b = dict(bank(cfg))
    if lang == "en":                                          # English wording for the Hindi-only bank entries
        b["tellings"] = b.get("tellings_en") or TELLINGS_EN
        b["closings"] = b.get("closings_en") or CLOSINGS_EN
    L = ledger(cfg) + [{"recipe": x, "topic": x.get("topic")} for x in (also or [])]
    r = {"topic": topic or _least_used(list(dict.fromkeys(b["seasonal"].get(str(int(time.strftime("%m"))), [])
                                                              + b["topics"])), [e.get("topic") for e in L], rng),
         "category": category if category in CATEGORIES else
         _least_used(list(CATEGORIES), [e.get("recipe", {}).get("category") for e in L], rng)}
    for k, key in (("telling", "tellings"), ("image", "images"), ("place", "places"), ("closing", "closings")):
        r[k] = _least_used(b[key], [e.get("recipe", {}).get(k) for e in L], rng)
    r["lang"] = lang
    return r


def is_english(text):
    """a script is English when most of its letters are Latin"""
    lat = len(re.findall(r"[A-Za-z]", text or ""))
    dev = len(re.findall(r"[ऀ-ॿ]", text or ""))
    return lat > 3 * max(dev, 1)


def _examples(cfg, chars, lang="hi"):
    if lang == "en":                                          # your own English scripts first (once you have some)
        mine = []
        for p in glob.glob(os.path.join(cfg["scripts_dir"], "*.txt")):
            try:
                t = open(p, encoding="utf-8-sig").read()
            except OSError:
                continue
            if is_english(t) and len(t.split()) >= 500 and not re.search(r"(_full|_old_\d+)\.txt$", p):
                mine.append((os.path.getmtime(p), t))
        mine.sort(reverse=True)
        if mine:
            return [t[:chars].strip() for _, t in mine[:2]]
    found = []
    for p in glob.glob(os.path.join(cfg["episodes_dir"], "*", "script.txt")):
        try:
            t = open(p, encoding="utf-8-sig").read()
        except OSError:
            continue
        if len(t.split()) >= 700 and "sukoon" in p.lower():
            found.append((len(t), re.sub(r"[\U0001F300-\U0001FAFF]", "", t)))
    found.sort(reverse=True)
    return [t[:chars].strip() for _, t in found[:2]]


def _avoid(cfg, n=10):
    lines = []
    for e in [e for e in ledger(cfg) if e.get("genre") in (None, "", "sukoon")][-n:]:   # Sukoon scripts only
        s = e.get("summary", {})
        lines.append(f"- {e.get('topic', '')}: image {e.get('recipe', {}).get('image', '')}, place "
                     f"{e.get('recipe', {}).get('place', '')}; story '{s.get('memory_story', '')}'; opened with "
                     f"'{s.get('opening_line', '')}'; phrases {', '.join(s.get('key_phrases', [])[:5])}")
    return "\n".join(lines)


_WPM = {}


def narration_lang(d):
    try:
        return open(os.path.join(d, "lang.txt"), encoding="utf-8").read().strip() or "hi"
    except OSError:
        return "hi"


def _experimental_narrations(cfg, series=""):
    """narration folders of earlier Experimental episodes (Series/<series>/Episodes/*/02_Narration/narrations/*);
    the current episode's own folder is empty when its script is written, so it alone measures nothing"""
    try:
        import experimental_workspace
        base = experimental_workspace.root(cfg)
    except Exception:
        return []
    L = cfg.get("layout") or {}
    return glob.glob(os.path.join(base, "Series", series or "*", "Episodes", "*",
                                  L.get("experimental_narration", "02_Narration"), "narrations", "*"))


def wpm(cfg, lang="hi", series=""):
    """script words per minute (pauses included), measured on past narrations: YOUR voice for Hindi, the English
    narrator for English (until there are English narrations: a calm-narration default). In Experimental mode the
    series' own earlier episodes count first (each series has its own trained voice and pace)."""
    exp = bool(cfg.get("experimental_mode"))
    key = f"{lang}|{series if exp else ''}|{int(exp)}"
    c = _WPM.get(key, {})
    if c.get("t", 0) > time.time() - 600:
        return c["v"]
    rates = []
    if exp:
        dirs = (_experimental_narrations(cfg, series) if series else []) or _experimental_narrations(cfg)
        dirs += glob.glob(os.path.join(cfg.get("sukoonvoice_media", ""), "narrations", "*"))
    else:
        narration_roots = {cfg.get("narrations_dir") or os.path.join(cfg.get("sukoonvoice_media", ""), "narrations"),
                           os.path.join(cfg.get("sukoonvoice_media", ""), "narrations")}
        dirs = [d for root in narration_roots for d in glob.glob(os.path.join(root, "*"))]
    for d in dict.fromkeys(dirs):
        if narration_lang(d) != lang:
            continue
        sp, tp = os.path.join(d, "script.txt"), os.path.join(d, "timing.json")
        try:
            words = nwords(open(sp, encoding="utf-8-sig").read())
            end = json.load(open(tp, encoding="utf-8"))[-1]["end"]
            if words >= 150 and end > 60:
                rates.append(words / (end / 60))
        except Exception:
            continue
    rates.sort()
    v = rates[len(rates) // 2] if rates else (100.0 if lang == "hi" else 110.0)
    _WPM[key] = {"t": time.time(), "v": round(v, 1), "n": len(rates)}
    return _WPM[key]["v"]


def estimate(cfg, text, lang=None):
    lang = lang or ("en" if is_english(text) else "hi")
    w = nwords(text or "")
    r = wpm(cfg, lang)
    return {"words": w, "minutes": round(w / r, 1) if r else 0, "wpm": r, "lang": lang,
            "measured_on": _WPM.get(lang, {}).get("n", 0)}


def build_prompt(cfg, r, minutes=12, notes="", subscribe=True, big=True):
    if r.get("lang") == "en":
        return build_prompt_en(cfg, r, minutes, notes, subscribe, big)
    words = int(float(minutes) * wpm(cfg))
    ex = _examples(cfg, 6000 if big else 1600)
    avoid = _avoid(cfg)
    return (
        "You write scripts for 'Sukoon', a Hindi YouTube channel of night-time counselling sleep stories (a calm "
        "male voice; listeners lying in bed, healing from something). Write tonight's complete script.\n\n"
        f"TOPIC: {r['topic']}\n" + (f"NOTES FROM THE CREATOR: {notes}\n" if notes else "")
        + f"WAY OF TELLING: {r['telling']}\nCENTRAL IMAGE (carry it from the first part to the last, the ending "
        f"returns to it): {r['image']}\nHEALING VISUALISATION PLACE: {r['place']}\nCLOSING: {r['closing']}, then two "
        "or three very soft, slow lines that let the listener drift into sleep - inviting and tender, never orders, "
        "no repeated 'सो जाओ... सो जाओ...'; the very last line a whispered 'शुभ रात्रि...'\n"
        f"LENGTH: about {words} Hindi words (without the (...) lines) = about {minutes:.0f} minutes spoken.\n\n"
        f"KIND OF EPISODE: {CATEGORIES.get(r.get('category'), CATEGORIES['counselling'])[0]}\n"
        "FLOW (one gentle conversation, no headings): "
        + CATEGORIES.get(r.get("category"), CATEGORIES["counselling"])[1].format(
            sub="- then, once, humbly: if this voice brings even a little peace, subscribe and become part of this "
                "Sukoon family (no numbers) " if subscribe else "") + ".\n\n"
        f"STYLE RULES: {RULES}\n\n"
        + (f"EARLIER EPISODES - do NOT reuse their images, places, stories, openings or phrases:\n{avoid}\n\n" if avoid else "")
        + "HOW THE CREATOR WRITES (copy the voice, rhythm and format - not the words):\n\n"
        + "\n\n---\n\n".join(ex) + "\n\n=====\nNow write tonight's script. Output only spoken story lines. Stop at the final story line; "
        "do not add an explanation, interpretation, editor's note, or comments about the story.\n")


def build_prompt_en(cfg, r, minutes=12, notes="", subscribe=True, big=True):
    words = int(float(minutes) * wpm(cfg, "en"))
    ex = _examples(cfg, 5000 if big else 1500, "en")
    hindi_ex = bool(ex) and not is_english(ex[0])
    avoid = _avoid(cfg)
    return (
        "You write scripts for 'Sukoon', a YouTube channel of night-time counselling sleep stories, now in ENGLISH "
        "(a calm male narrator; listeners lying in bed, healing from something). Write tonight's complete script in "
        "English.\n\n"
        f"TOPIC: {r['topic']}\n" + (f"NOTES FROM THE CREATOR: {notes}\n" if notes else "")
        + f"WAY OF TELLING: {r['telling']}\nCENTRAL IMAGE (carry it from the first part to the last, the ending "
        f"returns to it): {r['image']}\nHEALING VISUALISATION PLACE: {r['place']}\nCLOSING: {r['closing']}, then two "
        "or three very soft, slow lines that let the listener drift into sleep - inviting and tender, never orders, "
        "no repeated 'sleep now... sleep now...'; the very last line a whispered 'good night...'\n"
        f"LENGTH: about {words} English words (without the (...) lines) = about {minutes:.0f} minutes spoken.\n\n"
        f"KIND OF EPISODE: {CATEGORIES.get(r.get('category'), CATEGORIES['counselling'])[0]}\n"
        "FLOW (one gentle conversation, no headings): "
        + CATEGORIES.get(r.get("category"), CATEGORIES["counselling"])[1].format(
            sub="- then, once, humbly: if this voice brings even a little peace, subscribe and become part of this "
                "Sukoon family (no numbers) " if subscribe else "") + ".\n\n"
        f"STYLE RULES: {RULES_EN}\n\n"
        + (f"EARLIER EPISODES - do NOT reuse their images, places, stories, openings or phrases:\n{avoid}\n\n" if avoid else "")
        + (("HOW THE CREATOR WRITES (his Hindi episodes - copy the warmth, rhythm and format in ENGLISH, never "
            "translate them):\n\n" if hindi_ex else "HOW THE CREATOR WRITES (copy the voice, rhythm and format - not "
            "the words):\n\n") + "\n\n---\n\n".join(ex) + "\n\n" if ex else "")
        + "=====\nNow write tonight's script in English. Output only spoken story lines. Stop at the final story line; "
        "do not add an explanation, interpretation, editor's note, or comments about the story.\n")


_EDITORIAL_TAIL = re.compile(
    r"(?i)\b(?:the repetition of|the repeated (?:phrase|line|words)|this repetition|"
    r"the ending is meant to|this ending is meant to|the final line is meant to|"
    r"the story is meant to|this story is intended to|the intention is|"
    r"note:|editor['’]s note|as requested,|i hope you (?:enjoy|like)|"
    r"the (?:story|script|ending) above)\b"
)


def _strip_editorial_tail(text, lang="hi"):
    """Drop model commentary appended after a finished story, including inline explanations."""
    text = (text or "").replace("\r", "")
    match = _EDITORIAL_TAIL.search(text)
    if not match:
        return text
    text = text[:match.start()]
    if lang != "en":
        hindi = list(re.finditer(r"[\u0900-\u097F]", text))
        if hindi:
            # Remove an orphaned English fragment like 'reflected.' before the note as well.
            end = hindi[-1].end()
            while end < len(text) and text[end] in "।.!?…\"”'’":
                end += 1
            text = text[:end]
    return text.rstrip()


def clean_en(text):
    text = _strip_editorial_tail(text, "en")
    lines = []
    for ln in (text or "").replace("\r", "").split("\n"):
        ln = ln.strip().strip("*#").strip()
        if not ln or re.fullmatch(r"\(?\s*\.{2,}\s*\)?", ln):
            continue
        if re.match(r"^(part|section|episode|\d+[.)])\s", ln, re.I) and len(ln) < 60:
            continue
        if not re.search(r"[A-Za-z]", ln):
            continue
        ln = ln.replace("(...)", " ").strip()
        if len(ln.split()) > 16:
            lines += [x.strip() for x in re.split(r"(?<=[.!?])\s+", ln) if x.strip()]
        elif ln:
            lines.append(ln)
    end = next((i for i, l in enumerate(lines) if EN_END.search(l)), len(lines))
    merged, carry = [], ""
    for i, ln in enumerate(lines):
        if i >= end:
            if carry:
                merged.append(carry); carry = ""
            merged.append(ln); continue
        ln = (carry + " " + ln).strip() if carry else ln
        carry = ""
        if len(ln.split()) <= 4 and i + 1 < end and not COUNT_LINE.match(ln):
            carry = ln.rstrip(".") + "..." if not ln.endswith("...") else ln
            continue
        merged.append(ln)
    if carry:
        merged.append(carry)
    out = []
    for ln in merged:
        out += [ln, "(...)"]
    return "\n".join(out) + "\n"


def clean_for(text, lang):
    return clean_en(text) if lang == "en" else clean(text)


def clean(text):
    text = _strip_editorial_tail(text, "hi")
    lines = []
    for ln in (text or "").replace("\r", "").split("\n"):
        ln = ln.strip().strip("*#").strip()
        if not ln or re.fullmatch(r"\(?\s*\.{2,}\s*\)?", ln):
            continue
        if re.match(r"^(भाग|part|section|\d+[.)])\s", ln, re.I) and len(ln) < 60:
            continue
        if not re.search("[ऀ-ॿ]", ln):
            continue
        ln = ln.replace("(...)", " ").strip()
        if len(ln.split()) > 14:
            lines += [x.strip() for x in re.split(r"(?<=[।!?])\s+", ln) if x.strip()]
        elif ln:
            lines.append(ln)
    # very short lines (<= 4 words) are joined with the next one: smoother for the voice, fairer for the checker;
    # the good-night ending keeps its short lines
    end = next((i for i, l in enumerate(lines) if "सो जाओ" in l or "गुड नाईट" in l or "शुभ रात्रि" in l), len(lines))
    merged, carry = [], ""
    for i, ln in enumerate(lines):
        if i >= end:
            if carry:
                merged.append(carry); carry = ""
            merged.append(ln); continue
        ln = (carry + " " + ln).strip() if carry else ln
        carry = ""
        if len(ln.split()) <= 4 and i + 1 < end and not COUNT_LINE.match(ln):
            carry = ln.rstrip("।.") + "..." if not ln.endswith("...") else ln
            continue
        merged.append(ln)
    if carry:
        merged.append(carry)
    out = []
    for ln in merged:
        out += [ln, "(...)"]
    return "\n".join(out) + "\n"


def nwords(script):
    return len([w for w in script.split() if w != "(...)"])


# ------------------------------------------------------------------ the whole thing
def write(cfg, say, topic="", minutes=12, notes="", subscribe=True, engine="auto", rec=None, check=True,
          stopped=lambda: False, category="", lang="hi"):
    """-> (script, info). Gemini: one go + check pass. Local: part by part (small model), no check pass."""
    if engine == "auto" and secret("story_engine") == "local":
        engine = "local"
    r = dict(rec or {}) or recipe(cfg, topic, category=category, lang=lang)
    r["lang"] = lang = r.get("lang") or lang
    r.setdefault("category", category if category in CATEGORIES else "counselling")
    if topic:
        r["topic"] = topic
    use_gemini = engine in ("auto", "gemini") and bool(secret("gemini_key"))
    say(f"   topic: {r['topic']}")
    say(f"   kind: {CATEGORIES.get(r['category'], ('?',))[0]} | {'English' if lang == 'en' else 'Hindi'} | ~{minutes:.0f} min "
        f"≈ {int(float(minutes) * wpm(cfg, lang))} words ({'English narrator' if lang == 'en' else 'your voice'}: "
        f"{wpm(cfg, lang)} words/min)")
    say(f"   recipe: {r['telling']} | image: {r['image']} | place: {r['place']} | closing: {r['closing']}")
    say(f"   writer: {'Gemini' if use_gemini else local_model(cfg) + ' (local)'}  | {len(ledger(cfg))} earlier scripts to avoid")
    t0 = time.time()
    if use_gemini:
        text, model = ask(cfg, build_prompt(cfg, r, minutes, notes, subscribe, True), "auto", 0.95, say=say)
        script = clean_for(text, lang)
        say(f"   written by {model}: {nwords(script)} words ({time.time() - t0:.0f} s)")
        if check and not stopped():
            t1 = time.time()
            check_engine = "local" if model == local_model(cfg) else "auto"
            fixed, _ = ask(cfg, (CHECK_EN if lang == "en" else CHECK).format(image=r["image"], place=r["place"],
                                                                            words=nwords(script)) + script,
                           check_engine, 0.4, say=say)
            fixed = clean_for(fixed, lang)
            if nwords(fixed) >= nwords(script) * 0.8:
                script = fixed
                say(f"   checked + fixed: {nwords(script)} words ({time.time() - t1:.0f} s)")
            else:
                say("   (check pass came back too short - kept the first version)")
    else:
        model = local_model(cfg)
        script = _local_parts(cfg, say, r, minutes, notes, subscribe, stopped)
        if check and not stopped():
            t1 = time.time()
            try:
                fixed, _ = ask(cfg, (CHECK_EN if lang == "en" else CHECK).format(
                    image=r["image"], place=r["place"], words=nwords(script)) + script,
                    "local", 0.4, say=say)
                fixed = clean_for(fixed, lang)
                if nwords(fixed) >= nwords(script) * 0.8:
                    script = fixed
                    say(f"   checked + fixed: {nwords(script)} words ({time.time() - t1:.0f} s)")
                else:
                    say("   (check pass came back too short - kept the local draft)")
            except Exception as e:
                say(f"   (editorial check unavailable - kept the local draft: {e})")
    info = {"recipe": r, "model": model, "minutes": minutes, "notes": notes, "lang": lang}
    return script, info


def _local_parts(cfg, say, r, minutes, notes, subscribe, stopped):
    en = r.get("lang") == "en"
    words = int(float(minutes) * wpm(cfg, r.get("lang", "hi")))
    goals = ["welcome into the night, settle body and breath", "name the feeling, it is okay to feel it",
             f"understand it through a memory or small story, told as {r['telling']}",
             "gentle counsel, a kinder way to see it", f"healing visualisation at {r['place']}",
             f"slow down into sleep: {r['closing']}"]
    share = [0.14, 0.17, 0.2, 0.2, 0.15, 0.14]
    ex = "\n\n---\n\n".join(_examples(cfg, 1600, r.get("lang", "hi")))
    avoid = _avoid(cfg, 6)
    done = ""
    for i, g in enumerate(goals):
        if stopped():
            break
        target = int(words * share[i])
        extra = ""
        if i == 0 and subscribe:
            extra = "At the end of this part, softly ask once to subscribe and join the Sukoon family (no numbers). "
        if i == 5:
            extra = ("End with two or three very soft, inviting lines (no orders, no repeated 'sleep now') and "
                     "finally a whispered 'good night...'. " if en else
                     "End with two or three very soft, inviting lines (no orders, no repeated 'सो जाओ') and finally "
                     "a whispered 'शुभ रात्रि...'. ")
        ask_ = (f"{RULES_EN if en else RULES}\n\nHow the creator writes (copy voice and format, not words"
                f"{'; write in ENGLISH' if en else ''}):\n\n{ex}\n\n=====\n"
                f"Topic tonight: {r['topic']}. Central image carried through every part: {r['image']}. "
                + (f"Notes: {notes}. " if notes else "")
                + (f"Do not reuse from earlier episodes:\n{avoid}\n" if avoid else "")
                + "Parts: " + " | ".join(f"{k + 1}. {x}" for k, x in enumerate(goals))
                + f"\n\nWrite ONLY part {i + 1} ({g}), about {target} {'English' if en else 'Hindi'} words. {extra}"
                + "Output only spoken story lines; do not add an explanation or comments about the story. "
                + ("Continue from here without repeating:\n\n" + done[-900:] if done else "This is the beginning.")
                + f"\n\nPart {i + 1}:\n")
        part = ""
        for _ in range(2):
            part = clean_for(local_chat(cfg, ask_, 0.85)[0], r.get("lang", "hi"))
            if nwords(part) >= target * 0.6:
                break
        keep, cnt = [], 0
        for ln in part.splitlines():
            if ln != "(...)":
                cnt += len(ln.split())
                if cnt > target * 1.5 and keep:
                    break
            keep.append(ln)
        part = "\n".join(keep) + ("\n(...)" if keep and keep[-1] != "(...)" else "") + "\n"
        say(f"   part {i + 1}/6: {nwords(part)} words")
        done += ("\n" if done else "") + part
    return done


def summarise(cfg, script, say=print):
    """short record for the ledger (what this script used) - so later scripts avoid it"""
    ask_ = ("Read this script and answer ONLY JSON: {\"title_hi\": \"\", \"title_en\": \"\", "
            "\"memory_story\": \"(one English sentence)\", \"opening_line\": \"\", \"closing_line\": \"\", "
            "\"key_phrases\": [\"5 short characteristic phrases, in the script's language\"], "
            "\"names\": [\"the people's names used in it, as written\"], \"place\": \"(the main place, short English)\"}"
            "\n\n" + script[:12000])
    try:
        t, _ = ask(cfg, ask_, "auto", 0.2, True, say=say)
        return json.loads(t)
    except Exception as e:
        say(f"   (no summary for the ledger: {e})")
        lines = [l for l in script.splitlines() if l != "(...)"]
        return {"opening_line": lines[0] if lines else "", "closing_line": lines[-1] if lines else ""}


def ideas(cfg, topic="", n=3, engine="auto", category="", lang="hi"):
    """n different episode ideas (each with its own recipe) to choose from"""
    out = []
    for i in range(n):
        r = recipe(cfg, topic, seed=int(time.time() * 1000) + i * 7919, category=category,
                   also=[o["recipe"] for o in out], lang=lang)
        if any(o["recipe"] == r for o in out):
            continue
        out.append({"recipe": r})
    ask_ = (f"For a {'English' if lang == 'en' else 'Hindi'} night-time counselling sleep channel (Sukoon), give each of these episode recipes a "
            "title and a two-sentence story idea (English, gentle, specific). Answer ONLY JSON: "
            '{"ideas": [{"title_hi": "", "title_en": "", "idea": ""}, ...]} in the same order.\n\n'
            + "\n".join(f"{k + 1}. kind: {CATEGORIES[o['recipe']['category']][0]}; topic: {o['recipe']['topic']}; told as {o['recipe']['telling']}; central image "
                        f"{o['recipe']['image']}; healing place {o['recipe']['place']}" for k, o in enumerate(out)))
    try:
        t, _ = ask(cfg, ask_, engine, 0.8, True)
        for o, x in zip(out, json.loads(t).get("ideas", [])):
            o.update({k: x.get(k, "") for k in ("title_hi", "title_en", "idea")})
    except Exception as e:
        for o in out:
            o["idea"] = f"(no AI description: {e})"
    return out


SHORTEN = (
    "This is a Hindi Sukoon sleep script of about {have} words. Shorten it to about {want} words so it fits the "
    "video. Keep the story whole: the welcome, the feeling, the memory/story, the counsel, the visualisation and the "
    "good-night ending must all stay, and the central image must still run from start to end. Remove repetition and "
    "the least important lines first, then shorten long passages - never cut the ending. Keep the exact format (one "
    "phrase per line, a (...) line after every line) and the creator's voice. Return the full shortened script "
    "only.\n\nSCRIPT:\n\n")


SHORTEN_STORY = (
    "This is a Hindi audio-story script of about {have} words. Shorten it to about {want} words so it fits the "
    "video. Keep the story whole: the hook, the people, every important turn, the climax and the ending must stay. "
    "Remove repetition and the least important lines first, never cut the ending. Keep the exact format (one phrase "
    "per line, the (...) lines) and the voice. Return the full shortened script only.\n\nSCRIPT:\n\n")


def shorten(cfg, script, minutes, engine="auto", say=print, story_mode=False):
    """script -> about `minutes` of speech, story kept (AI). Returns the new script (or the old one if it failed)."""
    lang = "en" if is_english(script) else "hi"
    want = int(float(minutes) * wpm(cfg, lang))
    have = nwords(script)
    if have <= want * 1.05:
        return script
    best = script
    for attempt in range(2):
        try:
            ask_ = (SHORTEN_STORY if story_mode else SHORTEN).format(have=have, want=want)
            if lang == "en":
                ask_ = ask_.replace("Hindi Sukoon", "English Sukoon")
            t, model = ask(cfg, ask_ + script, engine, 0.4, say=say)
        except Exception as e:
            say(f"   (could not shorten: {e})"); break
        new = clean_horror(t) if story_mode else clean_for(t, lang)
        n = nwords(new)
        say(f"   shortened by {model}: {have} -> {n} words (wanted {want})")
        if want * 0.75 <= n <= want * 1.15 and (story_mode or "गुड नाईट" in new or "सो जाओ" in new or "शुभ रात्रि" in new
                                                or EN_END.search(new)):
            return new
        if n < nwords(best) and n >= want * 0.6:
            best = new
    return best


# ================================================================== भय (Bhay) - Hindi horror stories
HORROR_BANK = {
    "topics": [
        "a night shift alone in an old government hospital", "the last night bus on an empty highway",
        "a haveli bought too cheaply", "the well behind grandmother's village house", "a hostel room nobody takes",
        "a wedding procession that passes at 3 am", "the railway platform where the last train never leaves",
        "a phone call from a number that was disconnected years ago", "the doll in the attic of a rented flat",
        "a forest road where the same milestone keeps coming back", "a school that is locked every night at 7",
        "the neighbour who knocks every night at the same minute", "a photo studio's oldest negative",
        "a temple bell that rings with no one there", "a lift that stops at a floor the building doesn't have",
        "a family that sets one extra plate every night", "the security guard of an unfinished mall",
        "a lake where the reflection is a second late"],
    "tellings": [
        "first person: the narrator lived it and is telling it for the first time",
        "a friend's story, told by the narrator who still has doubts",
        "pages of a diary found in an old trunk, read aloud",
        "a calm investigator reconstructing what happened, piece by piece",
        "told to the listener as 'you' - you are the one walking into it"],
    "fears": [
        "something that copies a familiar voice", "a presence that only appears in reflections",
        "time repeating itself", "a place that remembers", "a promise made to something not human",
        "a figure that comes one step closer every night", "the dead keeping an old routine",
        "the house wants someone to stay"],
    "endings": [
        "a final quiet detail that changes everything", "the narrator realises it is not over",
        "an explanation that turns out to be worse", "the listener is invited to check their own surroundings",
        "it seems resolved, then one last line undoes it"]}

RULES_HORROR = (
    "Write in gripping, simple, spoken Hindi (Devanagari) - the way a good storyteller talks late at night, not "
    "bookish. Everyday English words young Indians use are fine, written in Devanagari. SLOW-BURN: ordinary life "
    "first, then small wrong details, dread that builds, a strong climax, and an unsettling ending. Show with the "
    "senses (sounds, smells, cold, light), real Indian places and names. No gore, no cheap shocks, no religion "
    "insulted, no real people or real recent crimes. Prefer one complete, naturally connected sentence per line; put a line with only (...) "
    "where the listener should hold their breath (after a reveal, before a turn) - not after every line. The first "
    "3 lines must hook. Prefer complete, naturally ordered spoken Hindi sentences over fragments; keep connected words "
    "and thoughts together. Do not begin every line with 'और', 'लेकिन', 'तो' or 'फिर'. Use ellipses and pause-only "
    "lines sparingly for genuine suspense. Vary sentence length naturally; most sentences should be about 8 to 20 words. "
    "No headings, lists, emojis or stage directions. "
    + SPOKEN_HINDI_CADENCE)


# ------------------------------------------------------------------ no repeats (17 Oct 2026)
# Each series lists only 8-18 story seeds. Once every seed had been used, the least-used one came back word for word
# (रहस्य 'the school topper who never existed' on 14 Oct and again on 17 Oct, with the same names आर्यन and विक्रम),
# and the 'do not reuse' list gave only the topic and one English line, so openings, names and plot shapes repeated.
STOCK_NAMES = ["विक्रम", "रमेश", "आर्यन", "अर्जुन", "समीर", "रोहन", "राहुल"]   # already in several earlier scripts


def _used_topics(L):
    return {str(e.get("topic", "")).strip().lower() for e in L if e.get("topic")}


def _fresh_topic(cfg, name, channel, examples, L, also=(), engine="auto"):
    """every listed seed of this series is used: the AI invents a new one, unlike every earlier story ("" if it fails)"""
    used = [f"- {e.get('topic', '')}: {(e.get('summary') or {}).get('memory_story', '')}" for e in L[-30:]] \
        + [f"- {t}" for t in also]
    prompt = (f"You plan episodes for '{name}', {channel}. These stories were ALREADY made - the new one must not "
              "repeat or closely resemble any of them (not the same situation, kind of person, central object, place "
              "or twist):\n" + "\n".join(used) + "\n\nThe kind of story seeds this series uses: "
              + "; ".join(examples[:5]) + ".\nGive ONE fresh story seed for the next episode, set in India. Answer "
              'ONLY JSON: {"topic": "(one short English line, under 15 words)"}')
    c = dict(cfg, _no_think=True)                              # a one-line answer: no 60-100 s of hidden thinking
    try:
        t, _ = ask(c, prompt, engine, 1.0, True)
        topic = str(json.loads(t).get("topic") or "").strip()
        if topic and topic.lower() not in _used_topics(L) | {a.lower() for a in also}:
            return topic
    except Exception:
        pass
    finally:
        if c.get("_ollama_used"):
            cfg["_ollama_used"] = True
    return ""


def _pick_seed(cfg, name, channel, topics, L, rng, also=(), engine="auto"):
    """an unused seed from the series list; when all are used, a new one from the AI (never the same seed again)"""
    used = _used_topics(L) | {str(a).strip().lower() for a in also}
    left = [t for t in topics if t.strip().lower() not in used]
    if left:
        return rng.choice(left)
    return (_fresh_topic(cfg, name, channel, topics, L, also, engine)
            or _least_used(topics, [e.get("topic") for e in L] + list(also), rng))


def _story_avoid(cfg, genre, n=12):
    """what the next script must not repeat: this series' recent stories in detail + openings and names of all series"""
    L = ledger(cfg)
    lines = []
    for e in [e for e in L if e.get("genre") == genre][-n:]:
        s = e.get("summary") or {}
        bits = [f"'{s.get('title_en') or s.get('title_hi') or ''}'", e.get("topic", ""), s.get("memory_story", "")]
        if s.get("opening_line"):
            bits.append("opened with: " + str(s["opening_line"])[:100])
        if s.get("names"):
            bits.append("names: " + ", ".join(str(x) for x in s["names"][:6]))
        lines.append("- " + " | ".join(b for b in bits if b and b != "''"))
    openings = list(dict.fromkeys(str((e.get("summary") or {}).get("opening_line", ""))[:90]
                                  for e in [e for e in L if e.get("genre")][-10:]))
    names = list(dict.fromkeys(STOCK_NAMES + [str(x) for e in L[-25:] for x in ((e.get("summary") or {}).get("names") or [])]))
    out = ""
    if lines:
        out += ("EARLIER STORIES OF THIS SERIES - do NOT reuse their plot, situation, central object, twist, title, "
                "names or opening:\n" + "\n".join(lines) + "\n\n")
    if any(openings):
        out += ("RECENT OPENING LINES (all series) - open in a clearly different way, not with a similar question or "
                "phrase:\n" + "\n".join("- " + o for o in openings if o) + "\n\n")
    out += ("CHARACTER NAMES: choose fresh names (unless the creator's notes give them); do not use "
            + ", ".join(names[:20]) + ".\n\n")
    return out


def horror_recipe(cfg, topic="", seed=None, also=(), engine="auto"):
    rng = random.Random(seed)
    b = HORROR_BANK
    try:
        b = {**HORROR_BANK, **(json.load(open(_media(cfg, "horror_bank.json"), encoding="utf-8")) or {})}
    except Exception:
        if not os.path.exists(_media(cfg, "horror_bank.json")):
            json.dump(HORROR_BANK, open(_media(cfg, "horror_bank.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    L = [e for e in ledger(cfg) if e.get("genre") == "horror"]
    r = {"genre": "horror", "topic": topic or _pick_seed(cfg, "भय", "a Hindi horror audio-story channel", b["topics"],
                                                          L, rng, also, engine)}
    for k, key in (("telling", "tellings"), ("fear", "fears"), ("ending", "endings")):
        r[k] = _least_used(b[key], [e.get("recipe", {}).get(k) for e in L], rng)
    return r


def horror_prompt(cfg, r, minutes=15, notes=""):
    words = int(float(minutes) * wpm(cfg))
    avoid = _story_avoid(cfg, "horror")
    return ("You write for 'भय' (Bhay), a Hindi horror audio-story channel: one male narrator, listened to at night "
            "with headphones. Write tonight's complete story.\n\n"
            f"STORY SEED: {r['topic']}\n" + (f"NOTES FROM THE CREATOR: {notes}\n" if notes else "")
            + f"TOLD AS: {r['telling']}\nTHE FEAR AT ITS HEART: {r['fear']}\nENDING: {r['ending']}\n"
            f"LENGTH: about {words} Hindi words (without the (...) lines) = about {minutes:.0f} minutes spoken.\n"
            "SHAPE: hook (2-3 lines) -> once, briefly: if you like such stories, subscribe to भय (no numbers) -> "
            "ordinary life and the place -> first wrong detail -> it grows -> the night it happens -> climax -> "
            "the ending.\n\n"
            f"STYLE RULES: {RULES_HORROR}\n\n"
            + avoid
            + "Now write the story. Output only spoken story lines. Stop at the final narrative line; do not append "
            "an explanation, interpretation, editor's note, or comments about the story.\n")


def clean_horror(text):
    text = _strip_editorial_tail(text, "hi")
    out = []
    for ln in (text or "").replace("\r", "").split("\n"):
        ln = ln.strip().strip("*#").strip()
        if not ln:
            continue
        if re.fullmatch(r"\(?\s*\.{2,}\s*\)?", ln):
            if out and out[-1] != "(...)":
                out.append("(...)")
            continue
        if re.match(r"^(भाग|part|chapter|अध्याय|\d+[.)])\s", ln, re.I) and len(ln) < 60:
            continue
        if not re.search("[ऀ-ॿ]", ln):
            continue
        ln = ln.replace("(...)", " ").strip()
        ln = _trim_dangling(ln)
        if len(ln.split()) > 18:
            out += [x.strip() for x in re.split(r"(?<=[।!?])\s+", ln) if x.strip()]
        else:
            out.append(ln)
    return "\n".join(out) + "\n"


# words a spoken Hindi sentence cannot stop on: "... नेगेटिव थी एक..." sounds like the next line is missing
# (not की/का/के/को/में/से: "ये कहानी है ... स्टूडियो की..." is a normal inverted Hindi sentence)
DANGLING_HI = {"एक", "और", "या", "कि", "जो", "ने", "सबसे", "बहुत", "वाली", "वाला", "वाले", "इस", "उस", "किसी", "कोई"}


def _trim_dangling(ln):
    """a line that trails off ('...' / '…') on a word like एक/की/और: drop those words, keep the pause"""
    m = re.search(r"(\s*(?:\.{2,}|…)\s*)$", ln)
    if not m:
        return ln
    words = ln[:m.start()].split()
    n = len(words)
    while len(words) > 3 and words[-1].strip(",") in DANGLING_HI:
        words.pop()
    return ln if len(words) == n else " ".join(words).rstrip(",") + "..."


# ------------------------------------------------------------------ plan first, then a logic check (Experimental)
# One long pass with no outline gave Rahasya-Aryan-Malhotra (17 Oct) four twists that undo each other, clues never
# explained and facts that change ("three months" -> "five years"). Experimental genre stories now: a short plan
# (fixed facts, clues with their answers, ONE twist, beats) -> the script written from it -> a logic check that
# rewrites only the lines that break the plan (instead of the full "preserve the events" rewrite).
STORY_PLAN = {"on": True, "outline_think": False, "check_think": True, "ctx": 16384, "max_fixes": 12}

OUTLINE_ASK = (
    "Before tonight's story is written, plan it. The brief for the story is below.\n\n{brief}\n\n"
    "Make a plan that a listener can follow and that adds up at the end:\n"
    "- fixed facts: every date, year, age, sum of money, duration and place the story will use, each decided once "
    "(the script will never change them);\n"
    "- people: the few names and who they are (no more than the story needs);\n"
    "- clues: 3-5 strange details planted in the story, and for EACH the exact answer the ending gives it;\n"
    "- ONE twist that those clues support (never a second twist that undoes the first, never a twist that comes "
    "from nowhere), and the reveal that explains every clue;\n"
    "- beats: 8-12 short steps in the order they are told, following the brief's telling and shape; the last two "
    "beats bring in nothing new, they only close what is open.\n"
    "Keep it to what happens (English is fine), no dialogue. Answer ONLY JSON: {\"facts\": [\"\"], "
    "\"people\": [\"name - who\"], \"clues\": [{\"clue\": \"\", \"answer\": \"\"}], \"twist\": \"\", "
    "\"reveal\": \"\", \"beats\": [\"\"]}")

QUOTE_RULE = ("Quotes: say who is speaking before a quote, close every quote on the same line, and put the narrator's "
              "own next sentence on a new line, so a listener always knows who is talking.")

LOGIC_ASK = (
    "You check a finished Hindi {series} audio story for LOGIC only, against the plan it was written from. "
    "Look for: a fact that differs from the plan or from an earlier line (dates, years, ages, durations, money, "
    "names, who knew what); a clue that is planted but never answered; a second twist that cancels the planned one; "
    "something that happens with no cause; a quote that is never closed, so the narrator's next line sounds like "
    "the quote. Do NOT touch style, wording or length, and do not report things that are fine.\n"
    "For each problem give the line number, the line as written, why, and the corrected Hindi line (same voice, "
    "about the same length; it may add a short clause that answers a clue). At most {max_fixes} lines, the most "
    "important first. Answer ONLY JSON: {\"problems\": [{\"n\": 0, \"line\": \"\", \"why\": \"\", \"fix\": \"\"}]} "
    "(an empty list when the story adds up).\n\nPLAN:\n{plan}\n\nSTORY (numbered lines):\n{story}")


def _plan_on(cfg):
    return bool(cfg.get("experimental_mode") or cfg.get("_experimental_workspace")) and bool(STORY_PLAN.get("on", True))


def _json_ask(cfg, prompt, engine, temp, think, say):
    """-> dict. JSON ask at the plan's context size; a reply that is empty or not clean JSON is asked once more as
    text and the {...} is cut out of it (Gemma 4 with thinking sometimes answers empty in JSON mode)."""
    c = cfg if think else dict(cfg, _no_think=True)
    ctx = int(STORY_PLAN.get("ctx") or 8192)
    try:
        for as_json in (True, False):
            t, _m = ask(c, prompt, engine, temp, as_json, say=say, ctx_len=ctx)
            a, b = (t or "").find("{"), (t or "").rfind("}")
            if a >= 0 and b > a:
                try:
                    return json.loads(t[a:b + 1])
                except ValueError:
                    pass
        raise RuntimeError("no JSON in the answer")
    finally:
        if c is not cfg and c.get("_ollama_used"):
            cfg["_ollama_used"] = True            # free_llm still unloads the model


def _plan_text(o):
    out = []
    if o.get("facts"):
        out.append("Fixed facts (the same everywhere): " + "; ".join(str(x) for x in o["facts"] if x))
    if o.get("people"):
        out.append("People: " + "; ".join(str(x) for x in o["people"] if x))
    for i, c in enumerate(o.get("clues") or []):
        if isinstance(c, dict) and c.get("clue"):
            out.append(f"Clue {i + 1}: {c['clue']} -> answered at the end by: {c.get('answer', '')}")
    if o.get("twist"):
        out.append(f"The one twist: {o['twist']}")
    if o.get("reveal"):
        out.append(f"The reveal: {o['reveal']}")
    out += [f"Beat {i + 1}: {b}" for i, b in enumerate(o.get("beats") or []) if b]
    return "\n".join(out)


def story_outline(cfg, prompt, series, engine, say):
    """the plan for a story from its writing prompt -> plan text, or '' (then the story is written the old way)"""
    t0 = time.time()
    k = prompt.rfind("Now write")
    brief = prompt[:k] if k > 0 else prompt
    try:
        o = _json_ask(cfg, OUTLINE_ASK.replace("{brief}", brief.strip()), engine, 0.8,
                      STORY_PLAN.get("outline_think"), say)
        if not isinstance(o, dict) or len([b for b in (o.get("beats") or []) if b]) < 4 or not o.get("twist"):
            raise RuntimeError("the plan had no twist or fewer than 4 beats")
    except Exception as e:
        say(f"   (story plan unavailable - written without one: {e})")
        return ""
    txt = _plan_text(o)
    say(f"   story plan ({time.time() - t0:.0f} s): {len(o.get('beats') or [])} beats, {len(o.get('clues') or [])} clues, "
        f"{len(o.get('facts') or [])} fixed facts")
    for ln in txt.splitlines():
        say("      " + ln[:220])
    return txt


def with_plan(prompt, plan):
    """the writing prompt with the plan to follow (and the quote rule) just before 'Now write'"""
    block = ("PLAN TO FOLLOW (write the story from it, beat by beat in this order; keep every fixed fact exactly; "
             "only this one twist, no extra twist; every clue gets its answer before the end; nothing new in the "
             "last part):\n" + plan + "\n\n" + QUOTE_RULE + "\n\n")
    k = prompt.rfind("Now write")
    return prompt[:k] + block + prompt[k:] if k > 0 else prompt + "\n\n" + block


def logic_check(cfg, script, plan, series, engine, say, cleaner):
    """only the lines that break the plan are rewritten -> script (unchanged when the check fails)"""
    t0 = time.time()
    lines = script.splitlines()
    idx = [i for i, l in enumerate(lines) if l.strip() and l.strip() != "(...)"]
    story_ = "\n".join(f"{n + 1}: {lines[i]}" for n, i in enumerate(idx))
    mx = int(STORY_PLAN.get("max_fixes") or 12)
    try:
        o = _json_ask(cfg, LOGIC_ASK.replace("{series}", series).replace("{max_fixes}", str(mx))
                      .replace("{plan}", plan).replace("{story}", story_), engine, 0.2,
                      STORY_PLAN.get("check_think", True), say)
    except Exception as e:
        say(f"   (logic check unavailable - kept the script as written: {e})")
        return script
    probs = o.get("problems") if isinstance(o, dict) else None
    done, skipped = 0, 0
    for p in (probs if isinstance(probs, list) else [])[:mx]:
        try:
            n = int(p.get("n"))
            fix = re.sub(r"^\s*\d+\s*[:.)]\s*", "", str(p.get("fix") or "")).strip()
            said = str(p.get("line") or "").strip()
        except (TypeError, ValueError, AttributeError):
            skipped += 1
            continue
        i = idx[n - 1] if 1 <= n <= len(idx) else None
        if said and (i is None or said[:25] not in lines[i]):     # the number is off: find the line by its text
            hit = [j for j in idx if said[:25] and said[:25] in lines[j]]
            i = hit[0] if len(hit) == 1 else None
        old = lines[i] if i is not None else ""
        if i is None or not fix or fix == old.strip() or not re.search("[ऀ-ॿ]", fix) \
                or len(fix.split()) > 2 * len(old.split()) + 15:
            skipped += 1
            continue
        lines[i] = fix
        done += 1
        say(f"      logic fix {n} ({str(p.get('why') or '')[:90]}): {old.strip()[:70]} -> {fix[:90]}")
    out = cleaner("\n".join(lines)) if done else script
    say(f"   logic check ({time.time() - t0:.0f} s): {done} line(s) fixed" + (f", {skipped} answer(s) not usable" if skipped else "")
        + f"; {nwords(out)} words")
    return out


def write_horror(cfg, say, topic="", minutes=15, notes="", engine="auto", rec=None, stopped=lambda: False,
                 level="standard"):
    r = dict(rec or {}) or horror_recipe(cfg, topic, engine=engine)
    if topic:
        r["topic"] = topic
    say(f"   story seed: {r['topic']}")
    say(f"   told as: {r['telling']} | fear: {r['fear']} | ending: {r['ending']}")
    say(f"   ~{minutes:.0f} min ≈ {int(float(minutes) * wpm(cfg, 'hi', 'bhay'))} words "
        f"(your voice: {wpm(cfg, 'hi', 'bhay')} words/min)")
    t0 = time.time()
    prompt = horror_prompt(cfg, r, minutes, notes)
    if level == "mature":
        prompt = prompt.replace("No gore, no cheap shocks", genre_rules(cfg, "bhay").get("mature") or LEVELS["mature"]["bhay"]
                                + " No cheap shocks")
    plan = story_outline(cfg, prompt, "भय", engine, say) if _plan_on(cfg) else ""
    if plan:
        prompt = with_plan(prompt, plan)
        t0 = time.time()
    text, model = ask(cfg, prompt, engine, 0.95, say=say, ctx_len=int(STORY_PLAN["ctx"]) if plan else 8192)
    script = _long_enough(cfg, text, int(float(minutes) * wpm(cfg, "hi", "bhay")), prompt, engine, say, clean_horror)
    say(f"   written by {model}: {nwords(script)} words ({time.time() - t0:.0f} s)")
    if plan and not stopped():
        script = logic_check(cfg, script, plan, "भय (horror)", "local" if model == local_model(cfg) else "auto", say,
                             clean_horror)
    elif not stopped():
        try:
            review_engine = "local" if model == local_model(cfg) else "auto"
            fixed, _ = ask(cfg, "Check this complete Hindi horror story: natural spoken Hindi, steadily building dread, "
                           "clear cause and effect, a fair hook, no repeated ideas, and an ending that lands. Preserve "
                           "the events and approximate length. Return the corrected full story only, with no notes or "
                           "commentary after the final line.\n\nSTORY:\n\n"
                           + script, review_engine, 0.4, say=say)
            fixed = clean_horror(fixed)
            if nwords(fixed) >= nwords(script) * 0.8:
                script = fixed
                say(f"   checked + fixed: {nwords(script)} words")
            else:
                say("   (check pass came back too short - kept the first version)")
        except Exception as e:
            say(f"   (editorial check unavailable - kept the first version: {e})")
    return script, {"recipe": r, "model": model, "minutes": minutes, "genre": "horror"}


def horror_ideas(cfg, topic="", n=3, engine="auto"):
    out = []
    for i in range(n):
        r = horror_recipe(cfg, topic, seed=int(time.time() * 1000) + i * 7919,
                          also=[o["recipe"]["topic"] for o in out], engine=engine)
        if any(o["recipe"]["topic"] == r["topic"] for o in out) and not topic:
            continue
        out.append({"recipe": r})
    ask_ = ("For a Hindi horror audio-story channel (भय), give each of these story seeds a Hindi title and a "
            "two-sentence English idea (slow-burn, specific, Indian setting). Answer ONLY JSON: "
            '{"ideas": [{"title_hi": "", "title_en": "", "idea": ""}, ...]} in the same order.\n\n'
            + "\n".join(f"{k + 1}. {o['recipe']['topic']}; told as {o['recipe']['telling']}; fear: {o['recipe']['fear']}"
                        for k, o in enumerate(out)))
    try:
        t, _ = ask(cfg, ask_, engine, 0.8, True)
        for o, x in zip(out, json.loads(t).get("ideas", [])):
            o.update({k: x.get(k, "") for k in ("title_hi", "title_en", "idea")})
    except Exception as e:
        for o in out:
            o["idea"] = f"(no AI description: {e})"
    return out


# ================================================================== every other series (same machine as भय)
GENRE_TEXT = {
    "rahasya": {"name": "रहस्य", "channel": "a Hindi thriller & mystery audio-story channel",
                "rules": ("Write in gripping, simple, spoken Hindi (Devanagari) like a storyteller who knows the "
                          "answer and reveals it slowly. A real-feeling mystery: a puzzle in the first lines, clues "
                          "planted fairly, rising tension, one strong twist that the clues support, a satisfying "
                          "reveal. Real Indian places and names, no real people or recent real crimes, no gore."),
                "shape": "hook -> once: subscribe to रहस्य (no numbers) -> the people and the place -> the strange event "
                         "-> clues and wrong turns -> the twist -> the reveal -> a last line that lingers",
                "topics": ["a letter that arrives 20 years late", "the missing page of a railway register",
                           "a locked shop that is open every Sunday night", "the twin who came back from abroad",
                           "a will that names a stranger", "the photographer who appears in every old wedding album",
                           "a village where every clock stopped at 3:17", "the tenant who paid ten years in advance",
                           "a phone found in a lake that still receives calls", "the school topper who never existed"]},
    "kisse": {"name": "किस्से", "channel": "a Hindi emotional-stories audio channel (stories that stay with you)",
              "rules": ("Write in warm, simple, spoken Hindi (Devanagari), everyday words. A real-life story of "
                        "ordinary people: family, friendship, love, sacrifice, regret, small kindness. Show feelings "
                        "through small details and moments, not speeches. It should move the listener, end with "
                        "warmth or a quiet ache, never preachy."),
              "shape": "hook -> once: subscribe to किस्से (no numbers) -> the people -> the bond -> the turn / the loss "
                       "-> the moment of truth -> the ending that stays",
              "topics": ["a father who saved for 20 years in a tin box", "two friends and one bicycle",
                         "the last letter from a grandmother", "a mother learning to use a phone for her son",
                         "an auto driver and a lost child", "a sister who gave up her studies",
                         "the teacher nobody remembered except one student", "a love that waited at a railway station",
                         "a son who came home too late", "the neighbour aunty's tiffin"]},
    "romanch": {"name": "रोमांच", "channel": "a Hindi romance audio-story channel for adults (tasteful, sensual, never explicit)",
                "rules": ("Write in warm, intimate, spoken Hindi (Devanagari). Adult romance between consenting "
                          "adults: longing, chemistry, closeness, tension, glances, touch of hands - sensual but "
                          "NEVER explicit or graphic; fade out before anything explicit. Respectful, emotional, "
                          "romantic. No minors, no non-consent."),
                "shape": "hook -> once: subscribe to रोमांच (no numbers) -> two people meet -> the pull between them "
                         "-> the obstacle -> closeness -> a tender ending",
                "topics": ["two strangers sharing an umbrella in the Mumbai rain", "an old flame met again at a wedding",
                           "a night train and the person in the opposite berth", "letters between two pen friends",
                           "a dance class partner", "the café where she always sat by the window",
                           "a power cut on a monsoon night", "a road trip to the mountains"]},
    "kareeb": {"name": "करीब", "channel": "a Hindi ASMR audio channel: very soft, close, whispered, for relaxation and sleep",
               "rules": ("Write in very soft, simple, spoken Hindi (Devanagari) as if whispered close to the "
                         "listener's ear. Personal attention, gentle sounds described (tapping, rain, pages, tea), "
                         "slow pacing, lots of (...) pauses, short lines, no story drama, nothing loud."),
               "shape": "soft welcome -> once, softly: subscribe to करीब (no numbers) -> settling in -> gentle "
                        "attention / sounds -> slow breathing -> drifting to sleep",
               "topics": ["a rainy night by the window", "making tea slowly for you", "reading you a few pages",
                          "a quiet night in a hill cottage", "brushing away the day's worries",
                          "an evening at grandmother's house", "a calm night train", "stars on the terrace"]},
    "gyaan": {"name": "ज्ञान", "channel": "a Hindi knowledge channel: one interesting subject explained simply",
              "rules": ("Write in clear, simple, spoken Hindi (Devanagari) with everyday English terms where Indians "
                        "use them. Explain one subject well: a curious hook, simple examples and comparisons, correct "
                        "facts only (no invented numbers or claims you are not sure of - say 'माना जाता है' when "
                        "uncertain), a clear takeaway. Friendly, never lecture-like."),
              "shape": "curious hook -> once: subscribe to ज्ञान (no numbers) -> the question -> simple explanation "
                       "step by step -> a surprising fact -> what it means for the listener -> short summary",
              "topics": ["why we dream", "how the Indian railway timetable works", "why the sky is blue",
                         "how memory works", "what black holes are", "why we feel déjà vu",
                         "how UPI moves money", "why sleep matters", "how monsoon forms", "what placebo really is"]},
}


GENRE_TELLINGS = {
    "rahasya": ["a calm investigator reconstructing the case piece by piece", "first person: the one who found the truth",
                "told from the side of a witness who saw only half", "two timelines that meet at the end"],
    "kisse": ["first person, years later, looking back", "a narrator telling the story of someone they loved",
              "told through small moments across many years", "a letter read aloud"],
    "romanch": ["first person: she / he remembers that night", "two voices, his side and her side",
                "told slowly, one meeting at a time", "told to the listener as 'you'"],
    "kareeb": ["whispered directly to the listener as 'you'", "a soft role-play: a friend sitting beside you",
               "describing sounds and textures slowly, one at a time"],
    "gyaan": ["a friendly explainer talking to one curious listener", "a question at a time, each answered simply",
              "a small story that opens the topic, then the explanation"],
}
# the turn of each story, least-used first (what Bhay's fear + ending do) - so the same plot shape does not come
# back each time (किस्से 16-17 Oct: three stories in a row were 'an old object reveals a hidden sacrifice')
GENRE_ANGLES = {
    "rahasya": ["the culprit is someone the listener meets in the first minute", "nobody was killed: the mystery is a "
                "disappearance or a swap", "the narrator is hiding something until the end", "a small everyday object "
                "is the key clue", "the twist is about why, not who", "a boring old record holds the answer",
                "two separate cases turn out to be one"],
    "kisse": ["the truth comes out in one conversation, not in a found letter, diary or box", "it happens now, in the "
              "present, not remembered years later", "a funny everyday moment that slowly turns serious", "the narrator "
              "was the one who was wrong", "a stranger's kindness on one ordinary day", "two people who never say it - one "
              "choice says it", "it ends in a reunion, not a death"],
    "romanch": ["they meet again after years", "it starts as a rivalry", "one of them leaves the city tomorrow",
                "told through small daily routines", "a misunderstanding they laugh about later",
                "an open but hopeful ending"],
    "gyaan": ["open with an everyday scene from Indian life", "open with a common myth, then correct it",
              "build it around one surprising experiment", "tell how people found this out, in short"],
}
LEVELS = {
    "standard": "",
    "mature": {
        "bhay": "MATURE: this is for adult listeners - real terror is allowed: violent deaths, blood and disturbing "
                "images may be described plainly when the story needs them; keep it frightening, not silly.",
        "rahasya": "MATURE: adult crime thriller - murder, violence, dark motives and crime details may be described "
                   "plainly when the story needs them.",
        "romanch": "MATURE: adult romance for 18+ listeners - desire, attraction and intimacy are central; describe "
                   "the tension, touch and emotions sensually; keep the most intimate moments suggestive.",
        "kisse": "MATURE: adult themes (death, betrayal, addiction, abuse survived) may be faced honestly.",
        "kareeb": "", "gyaan": ""},
}


def genre_rules(cfg, gid):
    """the writing rules of a series - your own version wins: D:\\MukhtalifGPT\\genre_rules.json (made on first use,
    edit it freely: rules, shape, topics, tellings, mature per series)"""
    p = _media(cfg, "genre_rules.json")
    if not os.path.exists(p):
        json.dump({k: {**v, "tellings": GENRE_TELLINGS.get(k, []), "mature": LEVELS["mature"].get(k, "")}
                   for k, v in GENRE_TEXT.items()}, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    G = dict(GENRE_TEXT.get(gid, {}))
    G.setdefault("tellings", GENRE_TELLINGS.get(gid, []))
    G.setdefault("angles", GENRE_ANGLES.get(gid, []))
    G.setdefault("mature", LEVELS["mature"].get(gid, ""))
    try:
        G.update(json.load(open(p, encoding="utf-8")).get(gid) or {})
    except Exception:
        pass
    return G


def _rules_for(G, level):
    """a series' writing rules; at the mature level its 'no gore' goes (the mature line allows what it forbade)"""
    r = str(G.get("rules") or "")
    if level == "mature" and G.get("mature"):
        r = re.sub(r",?\s*\bno gore\b", "", r, flags=re.I).replace(" .", ".").strip()
    return r


def _long_enough(cfg, text, words, prompt, engine, say, cleaner):
    """small local models stop early: ask them to go on until the script has its length"""
    script = cleaner(text)
    for _ in range(3):
        if nwords(script) >= words * 0.75:
            break
        say(f"   only {nwords(script)} of ~{words} words - asking it to continue ...")
        more, _m = ask(cfg, prompt + "\n\nSO FAR (continue from here, do not repeat, same format):\n\n"
                       + script[-3000:] + "\n\nContinue:", engine, 0.9, say=say)
        add = cleaner(more)
        if nwords(add) < 20:
            break
        script = script.rstrip("\n") + "\n" + add
    return script


def genre_recipe(cfg, gid, topic="", seed=None, also=(), engine="auto"):
    if gid == "bhay":
        return horror_recipe(cfg, topic, seed, also, engine)
    rng = random.Random(seed)
    G = genre_rules(cfg, gid)
    L = [e for e in ledger(cfg) if e.get("genre") == gid]
    r = {"genre": gid, "topic": topic or _pick_seed(cfg, G["name"], G["channel"], G["topics"], L, rng, also, engine),
         "telling": _least_used(G.get("tellings") or HORROR_BANK["tellings"],
                                [e.get("recipe", {}).get("telling") for e in L], rng)}
    if G.get("angles"):
        r["angle"] = _least_used(G["angles"], [e.get("recipe", {}).get("angle") for e in L], rng)
    return r


def write_genre(cfg, gid, say, topic="", minutes=12, notes="", engine="auto", rec=None, stopped=lambda: False,
                level="standard"):
    if gid == "bhay":
        return write_horror(cfg, say, topic, minutes, notes, engine, rec, stopped, level)
    G = genre_rules(cfg, gid)
    r = dict(rec or {}) or genre_recipe(cfg, gid, topic, engine=engine)
    if topic:
        r["topic"] = topic
    words = int(float(minutes) * wpm(cfg, "hi", gid))
    avoid = _story_avoid(cfg, gid)
    say(f"   {G['name']}: {r['topic']}  | told as: {r['telling']}" + (f"  | turn: {r['angle']}" if r.get("angle") else "")
        + f"  | ~{words} words")
    prompt = (f"You write for '{G['name']}', {G['channel']}: one male narrator. Write tonight's complete script.\n\n"
              f"TOPIC: {r['topic']}\n" + (f"NOTES FROM THE CREATOR: {notes}\n" if notes else "")
              + f"TOLD AS: {r['telling']}\n" + (f"THE TURN THIS TIME: {r['angle']}\n" if r.get("angle") else "")
              + f"LENGTH: about {words} Hindi words (without the (...) lines) = about "
              f"{minutes:.0f} minutes spoken.\nSHAPE: {G['shape']}\n\nSTYLE RULES: {_rules_for(G, level)} "
              + SPOKEN_HINDI_CADENCE + " "
              + (G.get("mature", "") + " " if level == "mature" else "") + "This is NOT a sleep or meditation script: "
              "no breathing exercises, no 'close your eyes', no good-night ending (unless the series is ASMR). Prefer one "
              "complete, naturally connected sentence per line; use a line with only (...) only for an intentional longer pause. No headings, lists, emojis or "
              "stage directions.\n\n" + avoid
              + "Now write it. Output only spoken script lines. Stop at the final narrative line; do not append an "
              "explanation, interpretation, editor's note, or comments about the story.\n")
    plan = story_outline(cfg, prompt, G["name"], engine, say) if _plan_on(cfg) else ""
    if plan:
        prompt = with_plan(prompt, plan)
    t0 = time.time()
    text, model = ask(cfg, prompt, engine, 0.9, say=say, ctx_len=int(STORY_PLAN["ctx"]) if plan else 8192)
    script = _long_enough(cfg, text, words, prompt, engine, say, clean_horror)
    say(f"   written by {model}: {nwords(script)} words ({time.time() - t0:.0f} s)")
    if plan and not stopped():
        script = logic_check(cfg, script, plan, G["name"], "local" if model == local_model(cfg) else "auto", say,
                             clean_horror)
    elif not stopped():
        try:
            review_engine = "local" if model == local_model(cfg) else "auto"
            review = ("Review this complete Hindi " + G["name"] + " audio story. Preserve its events, voice, and length. "
                      "Check the story shape, cause and effect, character consistency, repeated ideas, natural spoken "
                      "Hindi, and a satisfying ending. For knowledge content, remove unsupported factual claims. "
                      "Return the corrected full script only, with no notes or commentary after the final line.\n\nSCRIPT:\n\n" + script)
            fixed, _ = ask(cfg, review, review_engine, 0.25, say=say)
            fixed = clean_horror(fixed)
            if nwords(fixed) >= nwords(script) * 0.8:
                script = fixed
                say(f"   editorial review complete: {nwords(script)} words")
            else:
                say("   editorial review was too short; kept the original draft")
        except Exception as e:
            say(f"   editorial review unavailable; kept the original draft ({e})")
    return script, {"recipe": r, "model": model, "minutes": minutes, "genre": gid, "level": level}


def genre_ideas(cfg, gid, topic="", n=3, engine="auto"):
    if gid == "bhay":
        return horror_ideas(cfg, topic, n, engine)
    out = []
    for i in range(n):
        r = genre_recipe(cfg, gid, topic, seed=int(time.time() * 1000) + i * 7919,
                         also=[o["recipe"]["topic"] for o in out], engine=engine)
        if not topic and any(o["recipe"]["topic"] == r["topic"] for o in out):
            continue
        out.append({"recipe": r})
    G = GENRE_TEXT[gid]
    ask_ = (f"For {G['channel']} ({G['name']}), give each of these topics a Hindi title and a two-sentence English "
            'idea. Answer ONLY JSON: {"ideas": [{"title_hi": "", "title_en": "", "idea": ""}, ...]} in the same order.\n\n'
            + "\n".join(f"{k + 1}. {o['recipe']['topic']}; told as {o['recipe']['telling']}" for k, o in enumerate(out)))
    try:
        t, _ = ask(cfg, ask_, engine, 0.8, True)
        for o, x in zip(out, json.loads(t).get("ideas", [])):
            o.update({k: x.get(k, "") for k in ("title_hi", "title_en", "idea")})
    except Exception as e:
        for o in out:
            o["idea"] = f"(no AI description: {e})"
    return out
