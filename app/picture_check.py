"""Experimental picture check (16 Oct 2026): every scene picture is checked against the director's 'what happens'
line with yes/no questions; the code, not the model, decides pass or fail.

  1. checklists   the text model (scene_picture_model) turns each scene's 'what happens' into what a right picture
                  must show: main subject, things that must / must not be there, how many people may be visible,
                  shot size, whether it should frighten. One call for the whole story, kept in shots.json "checklist".
  2. check        the judge model (CHECK_MODEL, gemma3:12b; vision_llm if it fails) first says what it sees, then answers the questions as plain facts
                  (people count, subject shown yes/no, text yes/no ...). No example score in the ask, so it can't
                  copy one back (the old SCENE_ASK showed "score": 7 and got 7 for 164 of 165 pictures).
  3. repair       a failed scene gets a NEW prompt written by the text model from 'what happens' + what went wrong
                  (said positively: "an empty room", never "no people" - Z-Image ignores negatives), is painted
                  again and checked again; at most 2 rounds.
  4. flag         a scene still failing keeps its best picture, is marked "flag" in shots.json and on the Ratings
                  page, and the video is still made (the old picker stopped the whole job).

tools.pick_scenes hands Experimental jobs to pick_scenes() here; Working mode keeps the old picker.
"""
import json
import os
import random
import re
import shutil
import subprocess
import time

import tools

CHECK_MODEL = "gemma3:12b"     # picture judge (Tinker/cfg picture_check_model overrides): 16 Oct test on 16 Bhay
                               # pictures caught 7/7 wrong ones, 2 good ones failed, 7 s each (gemma4:12b 9/10 but 31 s)
MAX_ROUNDS = 1                 # repaint rounds for a failing scene, then flag it (17 Oct: round 2 fixed 7 of 41)
HORROR = {"bhay", "rahasya"}   # series whose scary scenes are checked for being scary

CHECKLIST_ASK = (
    "You prepare a picture check for a {desc} video. For EACH numbered scene below you get what its picture "
    "SHOULD SHOW and the story text read while it is on screen.\n"
    "For each scene write what a correct picture must contain, as an object with these keys:\n"
    "\"subject\": the main subject in 2-6 plain English words as a camera sees it: 'a young woman and a boy', not "
    "'sister teaching brother'; no names, no family words, no ideas or feelings,\n"
    "\"must_show\": a list of at most 3 short OBJECTS, PLACES or PEOPLE a camera can see. Never feelings, moods, "
    "expressions, sounds, smells, silence, names or ideas ('sad tone', 'eerie silence', 'smell of mud' are wrong),\n"
    "\"must_not_show\": a list of at most 3 short things that would make this picture wrong (for example people "
    "in a room that should be empty, a living person when it should be a photograph, a film negative or a shadow),\n"
    "\"max_people\": how many real people may stand in the scene, or null when the scene does not say. Use a number "
    "ONLY when the scene says it: 0 when it says the place is empty or deserted, otherwise the number of people it "
    "names, and never fewer than the people your own subject and must_show ask for. People inside a photograph, painting, film negative or mirror, shadows, silhouettes, faces behind glass "
    "and close-ups of eyes or hands are not counted. A street, a building or an object with nothing said about "
    "people is null,\n"
    "\"shot\": close-up, medium or wide,\n"
    "\"scary\": true when this moment should frighten the viewer, else false.\n"
    "Answer ONLY JSON: {{\"checks\": [...]}} with exactly {n} objects, in the scenes' order.\n\nSCENES:\n{scenes}")

CHECK_ASK = (
    "You check one picture made for a {desc} video. Look at the picture carefully before answering.\n"
    "THE PICTURE SHOULD SHOW: {should}\n"
    "MAIN SUBJECT: {subject}\n"
    "MUST SHOW: {must}\n"
    "MUST NOT SHOW: {mustnt}\n"
    "{ref_note}"
    "Answer ONLY JSON with these keys, in this order, describing what is really in the picture:\n"
    "\"seen\": one sentence on what the picture really shows (main subject, people, place),\n"
    "\"main_subject_seen\": the main subject of the picture in a few words,\n"
    "\"people_count\": how many real, whole people stand in the scene itself, background figures included (a whole "
    "number). Do NOT count people shown inside a photograph, painting, film negative, screen or mirror, shadows, "
    "silhouettes, a face behind glass, or a close-up of only eyes or hands,\n"
    "\"shows_subject\": true only if the picture clearly shows the MAIN SUBJECT, else false,\n"
    "\"missing\": the MUST SHOW items that are not visible (a list, empty if all are there),\n"
    "\"unwanted\": the MUST NOT SHOW items that are visible (a list, empty if none),\n"
    "\"shot\": close-up, medium or wide,\n"
    "\"text_or_letters\": true if there are readable words, a logo or a watermark on the picture, else false,\n"
    "\"broken_bodies\": true if any hand, finger, limb or face is extra, missing, melted or twisted, else false,\n"
    "\"scary\": how frightening the picture is from 1 (not at all) to 5 (very){same_key}")
REF_NOTE = ("Two pictures are attached: the FIRST is an earlier accepted picture of the same recurring character, "
            "the SECOND is the one to check. Answer every question about the SECOND picture.\n")
SAME_KEY = (",\n\"same_character\": true if the character in the second picture has the same body shape, clothing "
            "and look as in the first, else false")

REPAIR_ASK = (
    "A picture for one scene of a {desc} video came out wrong.\n"
    "THE SCENE SHOULD SHOW: {should}\nSTORY TEXT OF THE SCENE: {text}\n"
    "PROMPT THAT WAS USED: {prompt}\nWHAT THE PICTURE SHOWED INSTEAD: {seen}\nPROBLEMS: {problems}\n{tried}\n"
    "Write a new English image prompt (35-60 words) for the same moment that fixes these problems. Start with the "
    "shot size and the main subject, then the place and the light. Say what IS in the picture, never what is not: "
    "write 'an empty room' rather than 'no people', 'an old photograph lying on a table' rather than 'not a real "
    "woman'. Say how many people are visible (none, one, two). Use only people, objects, places, weather and light "
    "that the scene names. Answer ONLY JSON: {{\"prompt\": \"...\"}}")


def _bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "1", "y")


def _int(v, default=None):
    try:
        return int(round(float(str(v).strip().split()[0])))
    except (ValueError, IndexError, TypeError):
        words = {"none": 0, "zero": 0, "no": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
        return words.get(str(v).strip().lower(), default)


def _list(v):
    if isinstance(v, str):
        v = [x for x in re.split(r"[;,]\s*", v) if x.strip()]
    return [str(x).strip() for x in (v or []) if str(x).strip() and str(x).strip().lower() not in ("none", "n/a", "-")]


def should_show(s):
    """what the picture of this record should show: the director's 'what happens', else its own prompt"""
    return str(s.get("happens") or s.get("scene_prompt") or s.get("prompt") or "").strip()[:400]


def _ask_quick(cfg, key, prompt, temp, say=lambda *a: None):
    """tools._ask_step without Gemma 4's hidden thinking (it made every short answer take 1-2 minutes)"""
    c = dict(cfg, _no_think=True)
    try:
        return tools._ask_step(c, key, prompt, temp, say)
    finally:
        if c.get("_ollama_used"):                       # free_llm must still unload the model afterwards
            cfg["_ollama_used"] = True


def checklists(cfg, say, desc, records):
    """one checklist per record that has none yet (one text-model call), stored in record["checklist"]"""
    todo = [s for s in records if not isinstance(s.get("checklist"), dict)]
    if not todo:
        return
    rows = [f"{i + 1}. SHOULD SHOW: {should_show(s)}\n   TEXT: {str(s.get('text') or s.get('words') or '')[:400]}"
            for i, s in enumerate(todo)]
    got = []
    try:
        t, model = _ask_quick(cfg, "scene_picture_model",
                              CHECKLIST_ASK.format(desc=desc, n=len(todo), scenes="\n".join(rows)), 0.2, say)
        got = tools._loads(t).get("checks") or []
        say(f"   picture checklists written from each scene's 'what happens' line by {model}")
    except Exception as e:
        say(f"   (picture checklists failed: {e} - checking the main subject only)")
    for i, s in enumerate(todo):
        c = got[i] if i < len(got) and isinstance(got[i], dict) else {}
        s["checklist"] = {"subject": str(c.get("subject") or should_show(s)[:80]).strip(),
                          "must_show": _list(c.get("must_show"))[:3], "must_not_show": _list(c.get("must_not_show"))[:3],
                          "max_people": _int(c.get("max_people")) if c.get("max_people") is not None else None,
                          "shot": str(c.get("shot") or "").strip().lower(),
                          "scary": _bool(c.get("scary", False))}


def _small(cfg, img, tag):
    small = img[:-4] + tag + ".jpg"
    subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", img, "-vf", "scale=768:-2", "-q:v", "4", small],
                   capture_output=True, creationflags=0x08000000 if tools.WIN else 0)
    return small


def verdict(a, c, genre):
    """the model's plain answers + the checklist -> {"pass", "score", "problems"}; the code decides"""
    hard, soft = [], []
    if not _bool(a.get("shows_subject", True)):
        hard.append(f"does not show {c.get('subject') or 'the scene'} (shows {str(a.get('main_subject_seen') or '?')[:60]})")
    n, m = _int(a.get("people_count")), c.get("max_people")
    if n is not None and m is not None and n > m:
        hard.append(f"{n} people visible, the scene allows {m}")
    bad = [x for x in _list(a.get("unwanted")) if x]
    if bad:
        hard.append("shows " + ", ".join(bad)[:120])
    if _bool(a.get("text_or_letters", False)):
        if WRITING.search(" ".join([str(c.get("subject") or "")] + [str(x) for x in c.get("must_show") or []])):
            soft.append(("text or letters in the picture", 1.0))   # a diary, letter or note scene SHOULD show writing
        else:
            hard.append("text or letters in the picture")
    if _bool(a.get("broken_bodies", False)):
        hard.append("broken hands, limbs or faces")
    must = [str(x).lower() for x in c.get("must_show") or []]
    mustnt = [str(x).lower() for x in c.get("must_not_show") or []]
    # the judge sometimes lists MUST NOT SHOW items (or its own words) as missing: only real must-show items count
    miss = [x for x in _list(a.get("missing")) if str(x).lower() not in mustnt
            and (not must or any(_same(str(x), m) for m in must))]
    if miss:
        soft.append(("missing " + ", ".join(miss)[:120], min(3.0, 1.5 * len(miss))))
    want, got = c.get("shot") or "", str(a.get("shot") or "").strip().lower()
    if want in ("close-up", "medium", "wide") and got in ("close-up", "medium", "wide") and want != got:
        soft.append((f"{got} shot, the scene wants {want}", 1.0))
    sc = _int(a.get("scary"))
    if genre in HORROR and c.get("scary") and sc is not None and sc <= 2:
        soft.append((f"not scary ({sc}/5)", 1.5))
    if "same_character" in a and not _bool(a.get("same_character")):
        soft.append(("the recurring character looks different", 1.5))
    score = 10.0 - 3.0 * len(hard) - sum(w for _, w in soft)
    if hard:
        score = min(score, 5.0)
    return {"pass": not hard, "score": round(max(0.0, score), 1), "problems": hard + [t for t, _ in soft],
            "seen": str(a.get("seen") or "")[:240]}


WRITING = re.compile(r"diary|letter|note|script|writing|written|handwrit|ink|page|book|receipt|paper|text|"
                     r"number|numeral|sign|newspaper|currency|rupee|scroll|poem", re.I)


def _same(a, b):
    """does the judge's 'missing' item name this must-show item (any shared word of 4+ letters)"""
    wa = {w for w in re.findall(r"[a-z]{4,}", a.lower())}
    return bool(wa & set(re.findall(r"[a-z]{4,}", b.lower()))) or a.lower() in b.lower() or b.lower() in a.lower()


def check_model(cfg):
    return cfg.get("picture_check_model") or CHECK_MODEL


def _unload(ctx):
    """free_llm + the judge model, which may not be one of the hub's role models"""
    tools.free_llm(ctx)
    cfg = ctx["cfg"]
    try:
        tools._post(cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/") + "/api/generate",
                    {"model": check_model(cfg), "prompt": "", "keep_alive": 0}, 30)
    except Exception:
        pass


def check(cfg, img, s, desc, genre, reference=None):
    """the vision model's answers for one picture -> verdict dict"""
    c = s.get("checklist") or {}
    ref = reference if reference and os.path.exists(reference) and reference != img else None
    ask = CHECK_ASK.format(desc=desc, should=should_show(s), subject=c.get("subject") or should_show(s)[:80],
                           must="; ".join(c.get("must_show") or []) or "-",
                           mustnt="; ".join(c.get("must_not_show") or []) or "-",
                           ref_note=REF_NOTE if ref else "", same_key=SAME_KEY if ref else "")
    small = _small(cfg, img, "_check")
    ref_small = _small(cfg, ref, "_refcheck") if ref else None
    model = check_model(cfg)
    try:
        try:
            raw = tools.model_chat(cfg, model, ask, 0.1, True, image_paths=[ref_small, small] if ref else [small],
                                   timeout=300)
            if not str(raw or "").strip():          # Gemma sometimes answers nothing in JSON mode: once as text
                raw = tools.model_chat(cfg, model, ask, 0.1, False, image_paths=[ref_small, small] if ref else [small],
                                       timeout=300)
        except Exception:
            if not ref:
                fallback = cfg.get("vision_llm")
                if not fallback or fallback == model:
                    raise
                model = fallback                    # the judge model is missing or can't see: the vision model
            raw = tools.model_chat(cfg, model, CHECK_ASK.format(
                desc=desc, should=should_show(s), subject=c.get("subject") or should_show(s)[:80],
                must="; ".join(c.get("must_show") or []) or "-", mustnt="; ".join(c.get("must_not_show") or []) or "-",
                ref_note="", same_key=""), 0.1, True, image_path=small, timeout=300)
        a = tools._loads(raw)
        if not isinstance(a, dict) or "shows_subject" not in a:
            raise ValueError("the answer had no checks: " + str(raw)[:120])
        v = verdict(a, c, genre)
        v["answers"] = {k: a.get(k) for k in ("main_subject_seen", "people_count", "shows_subject", "missing",
                                              "unwanted", "shot", "text_or_letters", "broken_bodies", "scary",
                                              "same_character") if k in a}
        return v
    finally:
        for x in (small, ref_small):
            if x:
                try:
                    os.remove(x)
                except OSError:
                    pass


def _best(results):
    """{file: verdict} -> best file: a passing picture first, then the higher score; ties keep the first one"""
    return max(results, key=lambda f: (results[f]["pass"], results[f]["score"])) if results else None


def _rewrite(cfg, say, desc, s, v, full, tried=()):
    """a new scene prompt from 'what happens' + what went wrong (said positively), else None"""
    try:
        t, _ = _ask_quick(cfg, "scene_picture_model", REPAIR_ASK.format(
            desc=desc, should=should_show(s), text=str(s.get("text") or "")[:700], prompt=full[:600],
            seen=v.get("seen") or "-", problems="; ".join(v.get("problems") or []),
            tried=("THESE REWRITES WERE ALSO PAINTED AND STILL CAME OUT WRONG, so try a clearly different wording: "
                   + " | ".join(t[:300] for t in tried) + "\n") if tried else ""), 0.5)
        new = str(tools._loads(t).get("prompt") or "").strip()
    except Exception as e:
        say(f"   ({s['file']}: the AI could not rewrite the prompt: {e})")
        return None
    if s.get("text"):
        bad = tools._invented(new, s["text"])
        if bad:
            new = tools._strip_words(new, bad)
    return new if len(new.split()) >= 8 else None


def _full_prompt(f, s, new_scene):
    """the painted prompt with its scene part swapped for the new one (look, style and character anchor kept)"""
    pt = f[:-4] + ".prompt.txt"
    full = open(pt, encoding="utf-8").read().split("\n\n")[0].strip() if os.path.exists(pt) else str(s.get("prompt") or "")
    old = str(s.get("repair_scene") or s.get("scene_prompt") or "").strip()
    if old and old in full:
        return full.replace(old, new_scene, 1)
    m = re.search(r"\.\s*Setting and style:", full)
    return new_scene + (full[m.start():] if m else ". " + full)


def _move_alt(alt, files, keep):
    for x in files:
        if x != keep and os.path.exists(x):
            ts = time.strftime("%H%M%S_")
            os.replace(x, os.path.join(alt, ts + os.path.basename(x)))
            if os.path.exists(x[:-4] + ".prompt.txt"):
                os.replace(x[:-4] + ".prompt.txt", os.path.join(alt, ts + os.path.basename(x)[:-4] + ".prompt.txt"))


def _log(say, s, results, best):
    v = results[best]
    others = "  ".join(f"{os.path.basename(k)} {r['score']:.0f}{'' if r['pass'] else ' x'}" for k, r in results.items()
                       if k != best)
    say(f"   {s['file']}: {'ok' if v['pass'] else 'FAILED'} {v['score']:.0f}/10"
        + (f" - {'; '.join(v['problems'])}" if v["problems"] else "") + (f"   (other versions: {others})" if others else ""))


def pick_scenes(ctx, out, genre="bhay", redo=True):
    """every scene picture is checked against its 'what happens' line; the best version is kept, failing scenes are
    re-prompted and painted again (at most MAX_ROUNDS), what still fails is flagged and the video is still made"""
    cfg, say = ctx["cfg"], ctx["say"]
    sj = os.path.join(out, "shots.json")
    meta = json.load(open(sj, encoding="utf-8"))
    for k in ("shots", "moments"):
        meta[k] = [s for s in (meta.get(k) or []) if isinstance(s, dict)] if isinstance(meta.get(k), list) else []
    records = meta["shots"] + meta["moments"]
    if not records:
        raise RuntimeError("shots.json has no valid scene records; regenerate the scene plan before picking images")
    G = tools.GENRES.get(genre, tools.GENRES["bhay"])
    desc = G["desc"]
    alt = os.path.join(out, "_alt")
    os.makedirs(alt, exist_ok=True)
    save = lambda: json.dump(meta, open(sj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    tm = {"start": time.time(), "checklists": 0.0, "checks": 0.0, "rewrites": 0.0, "repaints": 0.0,
          "n_checks": 0, "n_repaints": 0}          # where this step's time goes, said at the end
    tools.free_gpu(ctx)
    t0 = time.time()
    checklists(cfg, say, desc, [s for s in records if not s.get("picked")])
    tm["checklists"] += time.time() - t0
    save()
    tools.free_llm(ctx)
    refs = {}
    for s in records:                                   # earlier accepted frames of recurring characters
        if s.get("picked") and s.get("character") and s.get("check", {}).get("pass"):
            refs.setdefault(s["character"], os.path.join(out, s["file"]))

    def judge(s, files):
        f = os.path.join(out, s["file"])
        results = {}
        for x in files:
            t0 = time.time()
            try:
                results[x] = check(cfg, x, s, desc, genre, refs.get(s.get("character")))
            except Exception as e:
                say(f"   (vision AI could not check {os.path.basename(x)}: {e})")
            tm["checks"] += time.time() - t0
            tm["n_checks"] += 1
        if not results:
            return None
        best = _best(results)
        _log(say, s, results, best)
        if best != f:
            if os.path.exists(f):
                ts = time.strftime("%H%M%S_")
                os.replace(f, os.path.join(alt, ts + os.path.basename(f)))
                if os.path.exists(f[:-4] + ".prompt.txt"):
                    shutil.copyfile(f[:-4] + ".prompt.txt", os.path.join(alt, ts + s["file"][:-4] + ".prompt.txt"))
            os.replace(best, f)
            bp = best[:-4] + ".prompt.txt"
            if os.path.exists(bp):
                os.replace(bp, f[:-4] + ".prompt.txt")
        _move_alt(alt, files, f)
        v = dict(results[best])
        v["file_checked"] = os.path.basename(best)
        return v

    def apply(s, v):
        s["picked"] = True
        s["check"] = {k: v[k] for k in ("pass", "score", "problems", "seen", "answers") if k in v}
        s["score"] = v["score"]                        # kept for older readers (Ratings page 'judge')
        if v["pass"] and s.get("character"):
            refs.setdefault(s["character"], os.path.join(out, s["file"]))

    failing = []
    for s in records:
        if ctx["stopped"]():
            return False
        f = os.path.join(out, s["file"])
        files = [x for x in [f] + [f[:-4] + "_" + c + ".png" for c in "bcd"] if os.path.exists(x)]
        if s.get("picked") or not files:
            continue
        v = judge(s, files)
        if v is None:
            s["picked"], s["check"] = True, {"pass": False, "score": None, "problems": ["not checked (no answer)"]}
            continue
        apply(s, v)
        if not v["pass"]:
            failing.append(s)
    save()
    first_failed = len(failing)

    w, h = tools.SIZES.get(meta.get("size", "16:9"), tools.SIZES["16:9"])[:2]
    engine = cfg.get("image_engine", "z_image_turbo")
    for rnd in range(1, MAX_ROUNDS + 1 if redo else 1):
        if not failing or ctx["stopped"]():
            break
        say(f"   repaint round {rnd}/{MAX_ROUNDS}: {len(failing)} scene(s) failed the check - new prompts from "
            f"'what happens' + what went wrong")
        jobs = []
        for s in failing:                               # 1. new prompts (text model)
            f = os.path.join(out, s["file"])
            pt = f[:-4] + ".prompt.txt"
            full = open(pt, encoding="utf-8").read().split("\n\n")[0].strip() if os.path.exists(pt) else s["prompt"]
            t0 = time.time()
            new = _rewrite(cfg, say, desc, s, s["check"], full, s.get("repair_tried") or [])
            tm["rewrites"] += time.time() - t0
            if not new:
                continue
            jobs.append((s, new, _full_prompt(f, s, new)))
            s.setdefault("repair_tried", []).append(new)
            say(f"   {s['file']}: {new[:150]}{'…' if len(new) > 150 else ''}")
        _unload(ctx)
        if not jobs:
            break
        if not tools.comfy_running(cfg):                # 2. paint (ComfyUI alone on the card)
            tools.start_comfy(cfg, say)
        tries = "bc" if len(jobs) <= 3 else "b"
        painted = {}
        for s, new, full in jobs:
            if ctx["stopped"]():
                return False
            f = os.path.join(out, s["file"])
            full, neg = tools.no_steam(full, "")
            for c in tries:
                fb = f"{f[:-4]}_{c}.png"
                sd = random.randint(1, 2 ** 31)
                t0 = time.time()
                tools.comfy_generate(cfg, full, fb, engine, sd, w, h, steps=8, negative=neg)
                say(f"   repainted {os.path.basename(fb)}  ({time.time() - t0:.0f} s, seed {sd})")
                tm["repaints"] += time.time() - t0
                tm["n_repaints"] += 1
                open(fb[:-4] + ".prompt.txt", "w", encoding="utf-8").write(
                    f"{full}\n\nseed {sd}  engine {engine}  {w}x{h}  steps 8  (picture check repaint {rnd})\n")
                painted.setdefault(s["file"], []).append(fb)
        tools.free_gpu(ctx)
        still = []
        for s, new, full in jobs:                       # 3. check again (old picture competes too)
            f = os.path.join(out, s["file"])
            files = [f] + [x for x in painted.get(s["file"], []) if os.path.exists(x)]
            old_scene = s.get("repair_scene")
            s["repair_scene"] = new                     # the prompt part a next round replaces
            v = judge(s, files)
            if v is None:
                s["repair_scene"] = old_scene
                still.append(s)
                continue
            if v.get("file_checked") == s["file"]:      # the old picture is still the best: keep its prompt part
                s["repair_scene"] = old_scene
            else:
                s["repair_prompt"] = full
            apply(s, v)
            if not v["pass"]:
                still.append(s)
        save()
        failing = still

    flagged = [s for s in records if isinstance(s.get("check"), dict) and not s["check"].get("pass")]
    for s in flagged:
        s["flag"] = "; ".join((s.get("check") or {}).get("problems") or ["failed the picture check"])
        try:
            import feedback
            feedback.save_ai(cfg, "picture", os.path.join(out, s["file"]), "theme", 2,
                             "picture check: " + s["flag"][:200], series=genre)
        except Exception:
            pass
    for s in records:
        if s not in flagged:
            s.pop("flag", None)
    save()
    sc = [s["score"] for s in records if s.get("score") is not None]
    if sc:
        say(f"   pictures checked: {len(records) - len(flagged)} of {len(records)} passed, average "
            f"{sum(sc) / len(sc):.1f}/10")
    if flagged:
        say(f"   ⚠ {len(flagged)} scene(s) still wrong after {MAX_ROUNDS} repaints, kept their best picture and "
            f"flagged (Ratings page): " + "; ".join(f"{s['file']} ({s['flag'][:80]})" for s in flagged))
    _unload(ctx)
    tools.free_gpu(ctx)
    mins = lambda k: f"{tm[k] / 60:.1f} min"
    rest = time.time() - tm["start"] - tm["checklists"] - tm["checks"] - tm["rewrites"] - tm["repaints"]
    say(f"   picture check time: checklists {mins('checklists')}, checks {mins('checks')} ({tm['n_checks']} pictures), "
        f"prompt rewrites {mins('rewrites')}, repaints {mins('repaints')} ({tm['n_repaints']} pictures), "
        f"model loads and the rest {rest / 60:.1f} min; {first_failed} scene(s) failed the first check, "
        f"repaints fixed {max(0, first_failed - len(flagged))}, {len(flagged)} flagged")
    return True
