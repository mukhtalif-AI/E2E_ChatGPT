"""steps/scenes.py - genre scene planning and sound cues. Moved out of tools.py on 16 Oct 2026 (TOOLS_SPLIT_PLAN.md step 4j).

Do not import this file directly: import tools and use tools.<name>. Names that stay in tools.py, and names other files swap
(feedback, narrator, Tinker), are written as tools.X here on purpose.
"""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave

import tools


def _sfx_names(cfg):
    import sfx_lib
    return sfx_lib.library(cfg)


def _sfx_names_old(cfg):
    d = os.path.join(os.path.dirname(cfg.get("music_dir") or r"D:\Audio asset\BGM"), "SFX")
    out = {}
    for f in glob.glob(os.path.join(d, "*.mp3")) + glob.glob(os.path.join(d, "*.wav")):
        k = re.sub(r"^(freesound_community|dragon-studio|brvhrtz|bryansantosbreton|dreamwriter07|emg5991)-", "",
                   os.path.splitext(os.path.basename(f))[0].lower())
        k = re.sub(r"-\d{4,}$", "", k).replace("-", " ").strip()
        out[k] = f
    return out


AMB_WORDS = ("wind", "thunder", "rain", "cricket", "drips", "fan", "tubelight", "drone", "pads", "water", "heartbeat")


PICTURE_RULES = {     # what the picture-prompt AI may write; the painter (Z-Image) cannot read "no X" and paints X
    "standard": "Write only what IS visible: never put 'no', 'not' or 'without' in a prompt and never mention text, "
                "letters, signs or writing. Show the feeling through light, shadow, posture and wrong details; leave "
                "gore, wounds and blood out of the picture.",
    "mature": "Write only what IS visible: never put 'no', 'not' or 'without' in a prompt and never mention text, "
              "letters, signs or writing. This is for adults: blood, wounds and disturbing details may be shown "
              "plainly where the story has them.",
}


def _pic_rules(cfg):
    return PICTURE_RULES.get(str(cfg.get("_picture_level") or "standard"), PICTURE_RULES["standard"])


def _anchor_text(v):
    """a character anchor as plain words (some models answer {silhouette: .., clothing: .., prop: ..})"""
    if isinstance(v, dict):
        return ". ".join(str(x).strip() for x in v.values() if str(x or "").strip())
    if isinstance(v, (list, tuple)):
        return ". ".join(str(x).strip() for x in v if str(x or "").strip())
    return str(v or "").strip()


SCENE_SIMILAR_MAX = 0.42        # word overlap (0-1) above which a scene prompt counts as a repeat of an earlier one


SCENE_VARY_HINTS = ("wide establishing view of the place from outside, an empty scene",
                    "extreme close-up of a single object from this moment, shallow depth of field, the object alone",
                    "view through a half-open doorway into the space, nobody in the doorway",
                    "high angle top-down view", "low angle view looking up, empty foreground",
                    "a hand or footsteps only, the rest in darkness")


_PEOPLE = re.compile(r"\b(wom[ae]n|m[ae]n|boy|girl|child|character|figure|person|people|sari|saree|silhouette|"
                     r"he|she|his|her|face|eyes)\b", re.I)


def _look_for_prompt(look, max_words=40):
    """the shared look without people/props sentences (a look that names a person puts that person in every picture)"""
    out = []
    for x in re.split(r"(?<=[.!?])\s+", str(look or "")):
        x = x.strip()
        if not x or _PEOPLE.search(x):
            continue
        if out and len(" ".join(out + [x]).split()) > max_words:
            break
        out.append(x)
    return " ".join(out).rstrip(".")


def _overlap(a, b):
    stop = {"the", "and", "with", "from", "that", "this", "into", "over", "under", "their", "there", "only", "single",
            "dark", "dim", "light", "scene", "shot", "view", "close", "wide", "while", "which", "seems", "hangs"}
    wa = {w for w in re.findall(r"[a-z]{4,}", str(a).lower()) if w not in stop}
    wb = {w for w in re.findall(r"[a-z]{4,}", str(b).lower()) if w not in stop}
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


def _vary_scene(cfg, say, desc, scene, earlier, text, i):
    """a rewrite with another subject when this scene repeats an earlier one (AI first, a fixed hint as fallback)"""
    import story
    try:
        t, _ = story.ask(cfg, tools.SCENE_VARY_ASK.format(desc=desc, earlier=earlier[:500], prompt=scene[:500],
                                                    text=str(text)[:700], rules=_pic_rules(cfg)), "auto", 0.7, True, say=lambda *a: None)
        new = str(json.loads(t).get("prompt") or "").strip()
        if new and _overlap(new, earlier) < SCENE_SIMILAR_MAX:
            return new, "rewritten by the AI"
    except Exception:
        pass
    return f"{SCENE_VARY_HINTS[i % len(SCENE_VARY_HINTS)]}: {scene}", "given a different framing"


def _words_in(text, words):
    """True when these words are (nearly) spoken in this text"""
    import difflib
    norm = lambda t: re.sub(r"[^\w\s]", "", str(t)).split()
    w, tw = norm(words), norm(text)
    if not w or not tw:
        return False
    target = " ".join(w)
    return any(difflib.SequenceMatcher(None, target, " ".join(tw[j:j + len(w)])).ratio() >= 0.75
               for j in range(len(tw)))


# a bed whose file name names a sound source must have that source in the scene (story text or picture prompt):
# "crickets with birds" / "midnight train horn" / "river ambience" under a photo studio scene sounds wrong
AMB_SOURCES = {
    "bird": ("bird", "पक्षी", "चिड़िया", "चिडिया", "पंछी", "परिंद", "कौवा", "कौआ", "कोयल", "मोर", "dawn", "morning", "सुबह"),
    "train": ("train", "railway", "station", "ट्रेन", "रेल", "स्टेशन", "प्लेटफ़ॉर्म", "प्लेटफार्म"),
    "horn": ("horn", "हॉर्न", "train", "ट्रेन", "रेल", "traffic", "सड़क"),
    "river": ("river", "stream", "नदी", "धारा", "घाट", "नाला"),
    "sea": ("sea", "ocean", "beach", "समुद्र", "सागर", "लहर", "तट"),
    "wave": ("sea", "ocean", "beach", "wave", "समुद्र", "सागर", "लहर", "तट"),
    "dog": ("dog", "कुत्त"),
    "market": ("market", "bazaar", "crowd", "बाज़ार", "बाजार", "भीड़", "मेला", "मेले"),
    "crowd": ("market", "bazaar", "crowd", "बाज़ार", "बाजार", "भीड़", "मेला", "लोग"),
    "traffic": ("traffic", "road", "street", "सड़क", "गाड़ियों", "ट्रैफ़िक"),
    "temple": ("temple", "मंदिर", "आरती", "घंटी"),
    "church": ("church", "चर्च"),
    "kitchen": ("kitchen", "रसोई", "चूल्हा"),
    "hospital": ("hospital", "अस्पताल", "ward", "वार्ड"),
    "office": ("office", "दफ़्तर", "दफ्तर", "ऑफिस"),
    "cafe": ("cafe", "café", "कैफ़े", "कैफे", "ढाबा", "चाय की दुकान"),
    "village": ("village", "गाँव", "गांव", "खेत"),
    "forest": ("forest", "jungle", "woods", "जंगल"),
    "jungle": ("forest", "jungle", "woods", "जंगल"),
    "thunder": ("thunder", "storm", "lightning", "बिजली", "गरज", "तूफ़ान", "तूफान"),
    "rain": ("rain", "storm", "monsoon", "बारिश", "बरसात", "बूँद", "बूंद", "तूफ़ान", "तूफान"),
    "fire": ("fire", "flame", "bonfire", "आग", "अलाव", "लपट", "चिता", "चूल्हा"),
    "cricket": ("cricket", "night", "outside", "field", "झींगुर", "रात", "बाहर", "खेत"),
}


def _amb_fits(path, text, folder=False):
    """False when the bed's file name names a sound source (bird, train, river...) that this scene never has.
    folder=True (Experimental) also reads the category folder: 'Rain/soft_loop.wav' is a rain bed too."""
    n = os.path.basename(str(path or "")).lower()
    if folder:
        n = os.path.basename(os.path.dirname(str(path or ""))).lower() + "/" + n
    t = str(text or "").lower()
    return all(any(w in t for w in words) for src, words in AMB_SOURCES.items() if src in n)


# Experimental: what the director may pick, per series (17 Oct 2026 sound cross-check). Before, every file in the library
# was offered to every series and any library name was taken, so Kisse and Gyaan got horror pads, a sobbing loop became a
# bed, a fan loop became an effect and a door knock a 'hit'.
STINGER_CATS = ("braam", "riser", "impact", "whoosh")
STINGER_WORDS = ("braam", "drop", "riser", "impact", "whoosh", "swoosh", "sweep", "boom", "stinger")
NEUTRAL_AMB = ("fan_hum", "night_crickets")       # a quiet home or a night outside: any story series may use them
TONE_SERIES = ("bhay", "rahasya")                  # unsorted root files (dark pads, drones, heartbeats) only here
STORY_SERIES = ("bhay", "rahasya", "kisse", "romanch")
# effects: everyday sounds (a knock, footsteps, a phone) are shared by every series - the story gives them their weight;
# only the strong mood sounds below stay with the series that list them (user, 17 Oct 2026)
MOOD_CATS = ("whisper", "scream_far", "breathing", "chains", "dog_howl", "gunshot_far", "heartbeat", "laugh_child",
             "riser", "braam", "whoosh", "impact")
MUSIC_WORDS = ("neffex", "soundlings", "instrumental", "music", "between the spaces")
MAX_BEDS = 3                                       # Experimental: at most this many different beds in one episode
SHORT_SCENE = 45                                   # seconds: a scene this short between two same-bed scenes keeps that bed
MAX_BED_RUN = 150                                  # seconds one bed may play before a scene without it (a breather)
NOT_A_BED = ("heartbeat",)                         # a heartbeat is a moment, not a place: never a bed for minutes
THOUGHT_WORDS = ("सोचा", "सोच रह", "सोचने", "मन में", "मन ही मन", "याद आ", "महसूस", "लगा कि", "लगता था", "दिल में",
                 "i thought", "i wondered", "i remembered", "in my mind", "i felt", "i realised", "i realized")
BED_RULE = ("AMBIENCE RULE: the bed is the PLACE, not the mood. Keep the SAME bed for every scene in the same place; "
            "change it only when the story moves somewhere else. Use at most 3 different beds in the whole story, "
            "and \"\" when no listed bed fits the place or the scene is only thoughts, memories or feelings.\n")


def _sound_cat(path):
    """the SFX category folder of a file ('' for the unsorted root); treated copies keep the folder name"""
    import sfx_lib
    c = os.path.basename(os.path.dirname(str(path or "")))
    return c if c in sfx_lib.CATS else ""


def _series_sounds(sfx, genre, amb, cue_names, hits_on):
    """-> (ambience names, cue names, hit names) this series may use"""
    import sfx_lib
    cats = set(sfx_lib.SERIES.get(genre, ([], 0))[0])
    amb_ok = cats | (set(NEUTRAL_AMB) if genre in STORY_SERIES else set())
    root_ok = genre in TONE_SERIES
    music = lambda k: any(w in os.path.basename(sfx[k]).lower() for w in MUSIC_WORDS)
    a = [k for k in amb if (_sound_cat(sfx[k]) in amb_ok if _sound_cat(sfx[k]) else root_ok and not music(k))
         and not any(w in k for w in NOT_A_BED)]
    shared = lambda cat: cat not in MOOD_CATS or cat in cats   # an everyday sound, or a mood sound this series lists
    c = [k for k in cue_names if (shared(_sound_cat(sfx[k])) if _sound_cat(sfx[k]) else root_ok and not music(k))]
    h = [k for k in cue_names if hits_on and (_sound_cat(sfx[k]) in STINGER_CATS or
                                              (not _sound_cat(sfx[k]) and any(w in k for w in STINGER_WORDS)))]
    return a, c, h


def _pick_name(name, allowed, fits=None):
    """the director's sound name -> a name from the allowed list, or '' (exact, then a near spelling, then a category)"""
    import difflib
    n = re.sub(r"\s+", " ", str(name or "").strip().lower())
    if not n:
        return ""
    if n in allowed:
        return n
    near = difflib.get_close_matches(n, allowed, 1, 0.8)
    if near:
        return near[0]
    cat = n.split(":")[0].strip().replace(" ", "_")
    same = sorted(k for k in allowed if k.startswith(cat + ":"))
    # a category name ("night_crickets"): the first file of it that fits the scene (17 Oct 2026: alphabetical order
    # gave Blue Ink Diary a "Midnight train horn with crickets" bed, which the train check then dropped)
    return next((k for k in same if not fits or fits(k)), "")


def _fix_cue_words(cfg, text, words, sfx):
    """the planner wrote the cue words in another language (English for a Hindi story): ask for the exact words"""
    import story
    try:
        t, _ = story.ask(cfg, tools.CUE_WORDS_ASK.format(sfx=os.path.splitext(os.path.basename(sfx))[0][:60], words=words,
                                                   text=str(text)[:900]), "auto", 0.2, True, say=lambda *a: None)
        w = str(json.loads(t).get("words") or "").strip()
        return w if w and _words_in(text, w) else ""
    except Exception:
        return ""


def _sentence_of(text, words):
    """the sentence of this scene that holds the cue words (for the sense check), else the scene's start"""
    for sen in re.split(r"(?<=[।.!?])\s+|\n+", str(text or "")):
        if sen.strip() and _words_in(sen, words):
            return sen.strip()
    return str(text or "")[:220]


CUE_PASS_ASK = (
    "SOUND PASS. These are the parts of a {desc} story ('{label}') that is read aloud. For each part, list only the "
    "sounds that REALLY happen in the story there (a door opens, footsteps, a phone rings, rain starts, a cup is put "
    "down, a page turns...): not feelings, thoughts, memories or things only looked at. For each sound give \"part\" "
    "(its number), \"words\" (2-4 consecutive words copied EXACTLY from that part where the sound happens, same "
    "language and script, never translated) and \"sfx\" (ONE name from SOUNDS that is that exact sound). At most {cpp} "
    "per part; most parts have none.\nSOUNDS: {names}\n"
    'Answer ONLY JSON: {{"cues": [{{"part": n, "words": "...", "sfx": "..."}}]}}\n\nPARTS:\n{parts}')


def _cue_pass(cfg, shots, cue_names, sfx, cpp, G, say, sound_name):
    """Experimental (17 Oct 2026): a second, small ask only for the sound effects, because the director's big ask
    usually returns none (7 of 10 runs). The usual checks stay: the words must be in the part's text, the name in the
    series' list, at most cpp per part, and the sense check runs after this."""
    import story
    need = [i for i, s in enumerate(shots) if len(s["cues"]) < cpp]
    if not need or not cue_names:
        return
    got = placed = no_name = no_words = 0
    for k in range(0, len(need), 8):
        part = need[k:k + 8]
        ask = CUE_PASS_ASK.format(desc=G.get("desc", ""), label=G.get("label", ""), cpp=cpp, names=", ".join(cue_names),
                                  parts="\n".join(f"{i + 1}. {shots[i]['text'][:900]}" for i in part))
        try:
            t, _model = story.ask(cfg, ask, "auto", 0.2, True, say=lambda *a: None)
            rows = json.loads(t).get("cues") or []
        except Exception as e:
            say(f"   (sound pass skipped for parts {part[0] + 1}-{part[-1] + 1}: {e})")
            continue
        for c in rows:
            if not isinstance(c, dict):
                continue
            try:
                i = int(c.get("part")) - 1
            except (TypeError, ValueError):
                continue
            if i not in part:
                continue
            got += 1
            w = re.sub(r"[*_~\"“”‘’]+", "", str(c.get("words") or "")).strip()
            key = sound_name("effect", c.get("sfx"), cue_names)
            if not key:
                no_name += 1
                continue
            if not w or not _words_in(shots[i]["text"], w):
                no_words += 1
                continue
            if len(shots[i]["cues"]) >= cpp or any(x["words"] == w for x in shots[i]["cues"]):
                continue
            shots[i]["cues"].append({"words": w, "file": sfx[key]})
            placed += 1
            say(f"   sound pass: scene {i + 1} '{w}' -> {os.path.basename(sfx[key])}")
    say(f"   sound pass: asked about {len(need)} part(s), the AI named {got} sound(s), {placed} added"
        + (f", {no_name} not in this series' list" if no_name else "")
        + (f", {no_words} with words not in the text" if no_words else ""))


def _cue_check(cfg, shots, genre, G, say):
    """Experimental (17 Oct 2026 sound cross-check): the self-review's effect check (does the sound really happen at
    those words?) runs on the plan, before the mix, and cues it calls wrong or unneeded are dropped. Before, it ran
    only after the video was made (Kisse 1016_142933: 7 of 8 effects flagged afterwards)."""
    try:
        import self_review
    except Exception:
        return
    rows = [(s, c) for s in shots for c in s["cues"]]
    cues = [{"line": _sentence_of(s["text"], c["words"]), "words": c["words"], "sound": os.path.basename(c["file"]),
             "file": c["file"]} for s, c in rows]
    sense = self_review._cue_sense(cfg, cues, genre, G, say)
    if not sense:                                     # the model could not answer: the plan stays as it is
        return
    bad = {i for i, v in sense.items() if v.get("verdict") != "ok"}
    for i in sorted(bad):
        if i < len(rows):
            s, c = rows[i]
            say(f"   effect {os.path.basename(c['file'])} on '{c['words']}' left out: {sense[i]['verdict']}"
                f" ({sense[i].get('why') or '-'})")
    for s in shots:
        s["cues"] = [c for c in s["cues"] if not any(c is rows[i][1] for i in bad if i < len(rows))]


def sfx_fetch(ctx, genre=""):
    """top the sound effects library up for a series (Freesound, CC0 only, registered) - skipped without a key"""
    import sfx_lib, story
    key = story.secret("freesound_key")
    cats = sfx_lib.missing(ctx["cfg"], genre or None)
    if not cats:
        return ctx["say"]("   the sound effects library is complete for this series")
    if not key:
        return ctx["say"](f"   {len(cats)} kinds of sound effects missing - add a free Freesound key in Settings to fetch them")
    n = sfx_lib.fetch(ctx["cfg"], key, cats, ctx["say"])
    ctx["say"](f"   {n} new sound effects (CC0) - register: {os.path.join(sfx_lib.root(ctx['cfg']), 'SFX_INDEX.csv')}")
    return True


def _word_time(tim, words, a=None, b=None):
    """narration time (s) where these words are spoken (timing.json lines; position inside the line by word count)"""
    norm = lambda t: re.sub(r"[^\w\s]", "", str(t)).split()
    w = norm(words)
    if not w:
        return None
    for x in tim:
        if a is not None and (float(x["end"]) < float(a) - 1 or float(x["start"]) > float(b) + 1):
            continue
        tw = norm(x["text"])
        for j in range(len(tw)):
            if tw[j:j + len(w)] == w:
                return float(x["start"]) + (float(x["end"]) - float(x["start"])) * j / max(1, len(tw))
    return None


_SENT_END = re.compile(r"[।?!॥]\s*[\"'”’)]*\s*$")


# an English word in a picture prompt -> words that must be in the scene's own text (Hindi, or English loan words)
# for it to stay; weather may also come from the scene before or after
INVENTED = {
    "rain": ("बारिश", "बरसात", "बूँद", "बूंद", "भीग", "मानसून", "रेन"),
    "storm": ("तूफ़ान", "तूफान", "आंधी", "आँधी", "बारिश"),
    "lightning": ("बिजली", "कड़क", "तूफ़ान", "तूफान"),
    "thunder": ("बिजली", "गरज", "कड़क", "तूफ़ान", "तूफान"),
    "fog": ("कोहरा", "कोहरे", "धुंध", "कुहासा"), "mist": ("कोहरा", "कोहरे", "धुंध", "कुहासा"),
    "snow": ("बर्फ", "बर्फ़"),
    "moon": ("चाँद", "चांद", "चाँदनी", "चांदनी", "चंद्र", "पूर्णिमा"),
    "candle": ("मोमबत्ती", "दीया", "दीये", "दीपक", "दिया जल", "लौ"),
    "lantern": ("लालटेन", "दीया", "दीपक", "लैंप"),
    "bangle": ("चूड़ी", "चूड़ियाँ", "चूड़ियां", "कंगन"),
    "mirror": ("आईना", "आइना", "आईने", "शीशा", "शीशे", "दर्पण", "मिरर"),
    "doll": ("गुड़िया", "खिलौना", "खिलौने"),
    "clock": ("घड़ी", "घड़ियाँ", "टिक"),
    "letter": ("चिट्ठी", "पत्र", "ख़त", "खत", "लेटर"),
    "umbrella": ("छाता", "छतरी"),
    "knife": ("चाकू", "छुरी", "खंजर"),
    "blood": ("खून", "ख़ून", "रक्त", "लहू"),
    "skull": ("खोपड़ी", "कंकाल"), "skeleton": ("कंकाल", "हड्डी"),
    "crow": ("कौआ", "कौवा", "कौए"), "owl": ("उल्लू",), "cat": ("बिल्ली",), "dog": ("कुत्त",),
    "rose": ("गुलाब",), "flower": ("फूल", "गुलाब"), "flowers": ("फूल", "गुलाब"),
    "swing": ("झूला", "झूले"),
    "book": ("किताब", "पुस्तक", "डायरी", "कॉपी", "बुक"),
    "train": ("ट्रेन", "रेल", "स्टेशन"), "river": ("नदी", "घाट", "धारा"),
    "sea": ("समुद्र", "सागर", "लहर", "तट"), "ocean": ("समुद्र", "सागर"),
    "forest": ("जंगल", "वन"), "temple": ("मंदिर",),
    "graveyard": ("कब्र", "क़ब्र", "कब्रिस्तान", "श्मशान"), "cemetery": ("कब्र", "क़ब्र", "कब्रिस्तान", "श्मशान"),
}


WEATHER = {"rain", "storm", "lightning", "thunder", "fog", "mist", "snow", "moon"}


def _wre(w):
    return r"\b" + w + r"(s|es|y|light|lit|drops?)?\b"


def _beat_parts(narration, seconds=tools.SCENE_SECONDS):
    """the story cut into scenes of about `seconds` that end where a sentence ends (।?!), never on a line that
    trails off with '...' -> [{text, start, end}]"""
    tm = json.load(open(os.path.join(narration, "timing.json"), encoding="utf-8"))
    if not tm:
        return []
    T = max(10.0, float(seconds))
    ends = [bool(_SENT_END.search(str(x["text"]).strip())) for x in tm]
    out, s, n = [], 0, len(tm)
    while s < n:
        best, cut = None, n - 1
        for i in range(s, n):
            dur = float(tm[i]["end"]) - float(tm[s]["start"])
            if ends[i] and dur >= 0.4 * T:
                best = i
            if ends[i] and dur >= 0.7 * T:
                cut = i
                break
            if dur >= 1.5 * T:                     # no sentence end for a long time: the last one, else this line
                cut = best if best is not None else i
                break
        out.append(tm[s:cut + 1])
        s = cut + 1
    if len(out) > 1 and float(out[-1][-1]["end"]) - float(out[-1][0]["start"]) < 0.4 * T:
        tail = out.pop()                           # a short tail joins the scene before it
        out[-1] += tail
    return [{"text": " ".join(str(c["text"]).strip() for c in p), "start": p[0]["start"], "end": p[-1]["end"]}
            for p in out]


def _invented(prompt, text, near=""):
    """the English things a picture prompt names that the scene's story text does not have (weather may come
    from the scene before or after)"""
    t, nb = str(text or "").lower(), str(near or "").lower()
    bad = []
    for w, ok in INVENTED.items():
        if not re.search(_wre(w), str(prompt or ""), re.I):
            continue
        hay = t + " " + nb if w in WEATHER else t
        if not any(k in hay for k in ok) and not re.search(r"\b" + w, t):
            bad.append(w)
    return bad


def _strip_words(prompt, bad):
    """drop the comma/sentence pieces of a prompt that name these words"""
    pieces = re.split(r"(?<=[,.;])\s+", str(prompt or ""))
    keep = [x for x in pieces if not any(re.search(_wre(w), x, re.I) for w in bad)]
    return " ".join(keep).strip(" ,;")


def _model_cfg(cfg, key):
    """(cfg for story.ask, engine): a model chosen on Tinker for this step always runs locally in Ollama"""
    m = str(cfg.get(key) or "").strip()
    return (dict(cfg, _llm_override=m), "local") if m else (cfg, "auto")


def _ask_step(cfg, key, prompt, temp, say=lambda *a: None):
    c, eng = _model_cfg(cfg, key)
    try:
        return story_ask(c, prompt, eng, temp, say)
    finally:
        if c is not cfg and c.get("_ollama_used"):
            cfg["_ollama_used"] = True


def story_ask(cfg, prompt, engine, temp, say):
    import story
    ctx_len = 16384 if len(prompt) > 9000 else 8192     # long stories cut into many scenes need a longer context
    try:
        t, m = story.ask(cfg, prompt, engine, temp, True, say=say, ctx_len=ctx_len)
    except TypeError:                                    # an older story.py without ctx_len
        t, m = story.ask(cfg, prompt, engine, temp, True, say=say)
    if not str(t or "").strip():                         # Gemma 4 sometimes answers nothing in JSON mode: ask once more as text
        try:
            t, m = story.ask(cfg, prompt, engine, temp, False, say=say, ctx_len=ctx_len)
        except TypeError:
            t, m = story.ask(cfg, prompt, engine, temp, False, say=say)
    return t, m


def _loads(t):
    """JSON from a model reply: plain, in ``` fences or with words around it (Gemma 4)"""
    t = re.sub(r"^```\w*\s*|\s*```$", "", str(t or "").strip())
    try:
        return json.loads(t)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        return json.loads(m.group(0))
    raise ValueError("the model gave an empty reply" if not t else "no JSON in the model's reply: " + t[:120])


def _clean_scene(cfg, say, desc, i, prompt, happens, text, near):
    """a picture prompt with invented props or weather -> rewritten by the AI, else those pieces are cut out"""
    bad = _invented(prompt, text, near)
    if not bad:
        return prompt
    try:
        t, _ = _ask_step(cfg, "scene_picture_model", tools.SCENE_FIX_ASK.format(
            desc=desc, bad=", ".join(bad), prompt=prompt[:600], happens=happens[:300], text=str(text)[:900]), 0.5)
        new = str(_loads(t).get("prompt") or "").strip()
        if new and not _invented(new, text, near):
            say(f"   scene {i + 1}: the picture added {', '.join(bad)} (not in this part of the story) - rewritten")
            return new
    except Exception:
        pass
    new = _strip_words(prompt, bad)
    if len(new.split()) < 8:
        hb = _invented(happens, text, near)
        new = (_strip_words(happens, hb) if hb else happens) or new
    say(f"   scene {i + 1}: the picture added {', '.join(bad)} (not in this part of the story) - removed")
    return new or prompt


def _picture_prompts(cfg, say, desc, parts, sc):
    """step 2 of the director: one picture prompt per scene, written from its 'what happens' line"""
    rows = []
    for i, p in enumerate(parts):
        x = sc[i] if i < len(sc) and isinstance(sc[i], dict) else {}
        hap = str(x.get("happens") or "").strip() or "(not given - read the text)"
        rows.append(f"{i + 1}. WHAT HAPPENS: {hap}\n   TEXT: {str(p['text'])[:900]}")
    try:
        t, model = _ask_step(cfg, "scene_picture_model",
                             tools.SCENE_PICTURE_ASK.format(desc=desc, n=len(parts), scenes="\n".join(rows), rules=_pic_rules(cfg)), 0.6, say)
        pr = []
        for x in (_loads(t).get("prompts") or []):         # some models answer [{"prompt": "..."}] instead of ["..."]
            if isinstance(x, dict):
                x = x.get("prompt") or next((v for v in x.values() if isinstance(v, str)), "")
            pr.append(str(x or "").strip())
        say(f"   picture prompts written from each scene's 'what happens' line by {model}")
        return pr
    except Exception as e:
        say(f"   (picture prompts from the 'what happens' lines failed: {e} - kept the director's own prompts)")
        return []


def _set_aside_scenes(out, video, say):
    """'Plan the scenes again': the old plan, pictures, prompts and _alt go to <scenes>\\_old_<time> and the old
    video gets an _old_<time> name, so nothing is lost and the new run paints and renders fresh"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    keep = os.path.join(out, "_old_" + ts)
    moved = 0
    for f in os.listdir(out):
        if f.startswith("_old_"):
            continue
        os.makedirs(keep, exist_ok=True)
        shutil.move(os.path.join(out, f), os.path.join(keep, f))
        moved += 1
    if moved:
        say(f"   planning again: the old scenes ({moved} files/folders) moved to {keep}")
    if video and os.path.isfile(video):
        old = os.path.splitext(video)[0] + "_old_" + ts + ".mp4"
        os.replace(video, old)
        say(f"   the old video kept as {os.path.basename(old)}")


def horror_scenes(ctx, name, narration, out, count=0, genre="bhay", replan=False, video=""):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    if os.path.exists(os.path.join(out, "shots.json")):
        if not (ctx.get("replan") or replan):
            return say("   the scenes are already planned for this voice - kept (pictures that exist are kept too)")
        _set_aside_scenes(out, video, say)
    os.makedirs(out, exist_ok=True)
    tm = json.load(open(os.path.join(narration, "timing.json"), encoding="utf-8"))
    total = tm[-1]["end"] if tm else 600
    n = int(count) or max(8, min(18, round(total / 70)))
    # Experimental: scenes end at sentence ends, two-step director (hub jobs carry experimental_mode, tests the workspace)
    exp = bool(cfg.get("experimental_mode") or cfg.get("_experimental_workspace"))
    if exp and not int(count):
        try:
            secs = float(cfg.get("scene_seconds") or tools.SCENE_SECONDS)
        except (TypeError, ValueError):
            secs = tools.SCENE_SECONDS
        parts = _beat_parts(narration, secs)[:40] or tools._parts(narration, "", n)
        n = len(parts)
        say(f"   {n} scenes, each ending where a sentence ends (about {secs:g} s each)")
    else:
        parts = tools._parts(narration, "", n)
    import sfx_lib
    sfx = tools._sfx_names(cfg)
    amb = [k for k in sfx if sfx_lib.kind_of(cfg, sfx[k]) == "amb"]
    cue_names = [k for k in sfx if k not in amb]
    per_min = sfx_lib.SERIES.get(genre, ([], 0))[1]
    cpp = max(0, round(per_min * (total / max(1, n)) / 60))
    hits = [k for k in sfx if k not in amb and any(w in k for w in ("braam", "door", "drop", "whoosh", "sweep", "footsteps",
                                                                     "slam", "riser", "thunder", "bell", "sobbing"))]
    G = tools.GENRES.get(genre, tools.GENRES["bhay"])
    if not G.get("hits"):
        hits = []
    if exp:                                           # only this series' sounds, and the code holds the director to them
        amb, cue_names, hits = _series_sounds(sfx, genre, amb, cue_names, bool(G.get("hits")))
        say(f"   sounds offered to the director: {len(amb)} ambience beds, {len(cue_names)} effects, {len(hits)} hits "
            "(this series' own lists)")
    dropped = {"ambience": [], "effect": [], "hit": []}

    def sound_name(kind, raw, allowed, fits=None):
        """Experimental: a name from this series' list (near spellings fixed), else '' and the name is logged"""
        raw = str(raw or "").strip()
        if not exp:
            return raw.lower() if raw.lower() in sfx else ""
        k = _pick_name(raw, allowed, fits)
        if raw and not k:
            dropped[kind].append(raw)
        return k
    try:                                              # the story's level (mature allows blood in the pictures too)
        cfg["_picture_level"] = json.load(open(os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".story.json"),
                                               encoding="utf-8")).get("level") or "standard"
    except Exception:
        cfg["_picture_level"] = "standard"
    if cfg["_picture_level"] != "standard":
        say(f"   pictures follow the story's level: {cfg['_picture_level']}")
    ask = tools.SCENES_ASK.format(desc=G["desc"], label=G["label"], amb=", ".join(amb) or "-", hits=", ".join(hits) or "-", n=len(parts),
                            cues=", ".join(cue_names) if cpp else "- (none for this series)", cpp=cpp,
                            mom=max(2, round(total / 150)), rules=_pic_rules(cfg),
                            parts="\n".join(f"{i + 1}. ({int(p['start'] // 60)}:{int(p['start'] % 60):02d}) {p['text'][:1200]}"
                                            for i, p in enumerate(parts)))
    if exp:
        ask = ask.replace("\nAMBIENCE: ", "\n" + BED_RULE + "AMBIENCE: ", 1)
    cut = [i + 1 for i, p in enumerate(parts) if len(p["text"]) > 1200]
    if cut:
        say(f"   ⚠ scene(s) {', '.join(map(str, cut))} are longer than 1200 letters - the director only reads the start")
    d = {}
    try:
        t, model = _ask_step(cfg, "director_model", ask, 0.6, say)
        if exp:                                       # kept, so a plan with no sound can be looked into afterwards
            try:
                with open(os.path.join(out, "director_reply.txt"), "w", encoding="utf-8") as f:
                    f.write(str(t or ""))
            except OSError:
                pass
        d = _loads(t)
        say(f"   planned by {model}")
    except Exception as e:
        say(f"   (the AI could not plan the scenes: {e} - simple pictures)")
    sc = d.get("scenes") or []
    if exp and len(sc) < len(parts):
        say(f"   ⚠ the director planned {len(sc)} of {len(parts)} scenes - the rest get pictures from their own text")
    two = _picture_prompts(cfg, say, G["desc"], parts, sc) if exp else []
    dcfg = _model_cfg(cfg, "director_model")[0]
    look = d.get("look", "")
    fixed = tools.SERIES_LOOK.get(genre) or {} if exp else {}
    if fixed.get("on") and fixed.get("look"):          # the series' own look (Tinker table tools.SERIES_LOOK)
        era = str(look).split(",")[0].strip().rstrip(".")
        era = era if fixed.get("keep_era") and len(era.split()) <= 8 and re.search(r"\d|aesthetic|era|period", era, re.I) \
            and not _PEOPLE.search(era) else ""
        look = (era + ", " if era else "") + fixed["look"]
        say("   the series' fixed picture look is used (tools.SERIES_LOOK)")
    character_anchors = d.get("character_anchors") or {}
    if look:
        say(f"   the look of this story: {look}")
    shots, seen, fixed_cues, dropped_cues, capped_beds = [], [], 0, 0, 0
    look_p = _look_for_prompt(look)
    if exp and look_p:
        whole = " ".join(p["text"] for p in parts)
        lb = [w for w in _invented(look_p, whole) if w in WEATHER]
        if lb:
            look_p = _strip_words(look_p, lb)
            say(f"   the look named {', '.join(lb)}, which this story never has - removed from the look")
    for i, p in enumerate(parts):
        x = sc[i] if i < len(sc) and isinstance(sc[i], dict) else {}
        happens = str(x.get("happens") or "").strip()
        own = x.get("prompt")
        if isinstance(own, dict):
            own = own.get("prompt") or next((v for v in own.values() if isinstance(v, str)), "")
        scene = str((two[i] if i < len(two) else "") or own or happens
                    or "a dark empty Indian village lane at night, one weak lamp, fog").strip()
        if exp:
            near = " ".join(parts[j]["text"] for j in (i - 1, i + 1) if 0 <= j < len(parts))
            scene = _clean_scene(cfg, say, G["desc"], i, scene, happens, p["text"], near)
        character = str(x.get("character") or "").strip()[:60]
        anchor = _anchor_text(character_anchors.get(character)) if character else ""
        sim = max(((_overlap(scene, e), e) for e in seen), default=(0, ""))
        if sim[0] >= SCENE_SIMILAR_MAX:                  # same subject as an earlier picture -> another subject
            scene, how = _vary_scene(dcfg, say, G["desc"], scene, sim[1], p["text"], i)
            say(f"   scene {i + 1} repeated an earlier picture ({sim[0]:.0%} the same words) - {how}")
        seen.append(scene)
        # the moment first (image models weigh the start most), the shared style after -> varied but coherent
        pr = f"{scene}. Setting and style: {look_p}" if look_p else scene
        if anchor:
            pr += f" Character continuity anchor: {anchor}. Keep this character design unchanged."
        cues = []
        for c in (x.get("cues") or [] if not exp or cpp > 0 else []):
            if not (isinstance(c, dict) and str(c.get("words", "")).strip()):
                continue
            k = sound_name("effect", c.get("sfx"), cue_names)
            if not k:
                continue
            w, f = str(c.get("words", "")), sfx[k]
            if exp:                                    # '*' and quote marks around the words never match the timing
                w = re.sub(r"[*_~\"“”‘’]+", "", w).strip()
            if not _words_in(p["text"], w):              # e.g. "Rain" for a Hindi story: never found -> never placed
                w2 = _fix_cue_words(dcfg, p["text"], w, f)
                if not w2:
                    dropped_cues += 1
                    continue
                fixed_cues += 1
                w = w2
            cues.append({"words": w, "file": f})
            if len(cues) >= max(cpp, 0):
                break
        amb_f = sfx.get(sound_name("ambience", x.get("ambience"), amb,
                                   lambda k: _amb_fits(sfx[k], p["text"] + " " + scene, exp)), "")
        hit_f = sfx.get(sound_name("hit", x.get("hit"), hits), "")
        if exp and hit_f and i == 0:                   # a hit is never played on the first scene (nothing before it)
            dropped["hit"].append(os.path.basename(hit_f) + " (scene 1)")
            hit_f = ""
        if amb_f and not _amb_fits(amb_f, p["text"] + " " + scene, exp):
            prev = next((s["ambience"] for s in reversed(shots) if s.get("ambience")), "")
            keep = prev if prev and _amb_fits(prev, p["text"] + " " + scene, exp) else ""
            say(f"   scene {i + 1}: bed {os.path.basename(amb_f)} names a sound this scene does not have - "
                + (f"kept the previous bed {os.path.basename(keep)}" if keep else "no bed (music only)"))
            amb_f = keep
        used = {s["ambience"] for s in shots if s.get("ambience")}
        if exp and amb_f and amb_f not in used and len(used) >= MAX_BEDS:   # a 4th bed -> the place's bed stays
            prev = next((s["ambience"] for s in reversed(shots) if s.get("ambience")), "")
            amb_f = prev if prev and _amb_fits(prev, p["text"] + " " + scene, exp) else ""
            capped_beds += 1
        shots.append({"file": f"{i + 1:03d}.png", "prompt": pr, "scene_prompt": scene, "character_anchor": anchor,
                      "character": character, "happens": happens,
                      "text": p["text"], "start": p["start"],
                      "end": p["end"], "ambience": amb_f,
                      "hit": hit_f, "lightning": bool(x.get("lightning")),
                      "cues": cues})
        # Lead with the scene-specific text in logs; the shared look prefix made every
        # line appear identical when only the first 90 characters were printed.
        scene_preview = scene
        say(f"   {i + 1}: {scene_preview[:150]}{'…' if len(scene_preview) > 150 else ''}"
            f"{'  | ' + os.path.basename(shots[-1]['ambience']) if shots[-1]['ambience'] else ''}"
            f"{'  | ' + str(len(shots[-1]['cues'])) + ' cue(s)' if shots[-1]['cues'] else ''}"
            f"{'  | hit ' + os.path.basename(shots[-1]['hit']) if shots[-1]['hit'] else ''}")
    if fixed_cues or dropped_cues:
        say(f"   sound effect words checked against the narration: {fixed_cues} re-copied from the story text, "
            f"{dropped_cues} dropped (that sound does not happen there)")
    if exp and cpp > 0:
        _cue_pass(cfg, shots, cue_names, sfx, cpp, G, say, sound_name)
    if exp and any(s["cues"] for s in shots):
        _cue_check(cfg, shots, genre, G, say)
    if exp and shots:
        flick = 0
        for i in range(1, len(shots) - 1):           # A B A with a short B -> A A A (no jump to a bed and straight back)
            a, b, c = shots[i - 1]["ambience"], shots[i]["ambience"], shots[i + 1]["ambience"]
            if a and a == c and b != a and shots[i]["end"] - shots[i]["start"] <= SHORT_SCENE \
                    and _amb_fits(a, shots[i]["text"] + " " + shots[i]["scene_prompt"], True):
                shots[i]["ambience"] = a
                flick += 1
        inner = 0
        for s in shots:                               # a scene that is only inner thought has no place: no bed
            sens = [x for x in re.split(r"(?<=[।.!?])\s+|\n+", s["text"]) if x.strip()]
            th = sum(1 for x in sens if any(w in x.lower() for w in THOUGHT_WORDS))
            if s["ambience"] and sens and th / len(sens) >= 0.5:
                s["ambience"] = ""
                inner += 1
        breaks, run = 0, 0.0
        for j, s in enumerate(shots):                 # one bed playing on and on gets a scene's breather
            same = j > 0 and s["ambience"] and s["ambience"] == shots[j - 1]["ambience"]
            run = run + (s["end"] - s["start"]) if same else (s["end"] - s["start"] if s["ambience"] else 0.0)
            if same and run > MAX_BED_RUN:
                s["ambience"], run = "", 0.0
                breaks += 1
        beds = [s["ambience"] for s in shots]
        changes = sum(1 for j in range(1, len(beds)) if beds[j] != beds[j - 1])
        say(f"   ambience: {len({b for b in beds if b})} different bed(s) in {len(shots)} scenes, {changes} change(s)"
            + (f"; {capped_beds} extra bed(s) over the limit of {MAX_BEDS} kept the previous one" if capped_beds else "")
            + (f"; {flick} short scene(s) kept the bed around them" if flick else "")
            + (f"; {inner} inner-thought scene(s) without a bed" if inner else "")
            + (f"; {breaks} breather(s) after {MAX_BED_RUN} s of one bed" if breaks else ""))
    for kind, names in dropped.items():
        if names:
            say(f"   ⚠ {len(names)} {kind} name(s) from the director are not in this series' list - left out: "
                + ", ".join(sorted(set(names))[:6]))
    moments = []
    for i, p in enumerate(parts):                          # the object that carries the feeling, at its words
        x = sc[i] if i < len(sc) and isinstance(sc[i], dict) else {}
        mo = x.get("moment") if isinstance(x.get("moment"), dict) else {}
        if not mo.get("prompt") or not mo.get("words"):
            continue
        if exp:
            mb = _invented(mo["prompt"], p["text"])
            if mb:
                mo = dict(mo, prompt=_strip_words(mo["prompt"], mb))
                say(f"   close-up '{mo['words']}' named {', '.join(mb)} (not in this part) - removed")
                if len(str(mo["prompt"]).split()) < 6:
                    continue
        at = _word_time(tm, mo["words"], p["start"], p["end"])
        if at is None or at > total - 6:
            continue
        moments.append({"file": f"m{len(moments) + 1:02d}.png", "at": round(at, 2), "words": mo["words"],
                        "character": "",
                        "prompt": f"Close-up detail: {mo['prompt']}" + (f". Setting and style: {look_p}" if look_p else "")})
        say(f"   moment at {int(at // 60)}:{int(at % 60):02d} ('{mo['words']}'): {str(mo['prompt'])[:90]}")
    json.dump({"source": narration, "look": look, "shots": shots, "moments": moments},
              open(os.path.join(out, "shots.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return True
