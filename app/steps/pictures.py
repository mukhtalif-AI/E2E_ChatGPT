"""steps/pictures.py - artwork and pictures. Moved out of tools.py on 16 Oct 2026 (TOOLS_SPLIT_PLAN.md step 4f).

Do not import this file directly: import tools and use tools.<name>. Names that stay in tools.py, and names other files swap
(feedback, narrator, Tinker), are written as tools.X here on purpose.
"""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave

import tools

WIN = os.name == "nt"


def _parts(narration, script, n):
    """the story cut into n parts (at phrase boundaries, equal time) -> [{text, start, end}]"""
    t = os.path.join(narration, "timing.json") if narration else ""
    if t and os.path.exists(t):
        tm = json.load(open(t, encoding="utf-8"))
        total = tm[-1]["end"] if tm else 1
        parts, cur = [], []
        for x in tm:
            cur.append(x)
            if x["end"] >= total * (len(parts) + 1) / n and len(parts) < n - 1:
                parts.append(cur); cur = []
        if cur:
            parts.append(cur)
        return [{"text": " ".join(c["text"] for c in p), "start": p[0]["start"], "end": p[-1]["end"]} for p in parts]
    if narration and not script:
        s = os.path.join(narration, "script.txt")
        script = open(s, encoding="utf-8").read() if os.path.exists(s) else ""
    lines = [l.strip() for l in script.splitlines() if l.strip() and not re.fullmatch(r"\(?\.*\)?", l.strip())]
    if not lines:
        return [{"text": "", "start": 0, "end": 0}] * n
    k = max(1, round(len(lines) / n))
    return [{"text": " ".join(lines[i:i + k]), "start": None, "end": None} for i in range(0, len(lines), k)][:n]


def llm_prompts(ctx, narration, script, n, out, idea=""):
    cfg, say = ctx["cfg"], ctx["say"]
    os.makedirs(out, exist_ok=True)
    if not script and narration:                                # the AI wrote it: read the saved script
        sp = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(os.path.basename(narration.rstrip("\\/"))) + ".txt")
        script = open(sp, encoding="utf-8").read() if os.path.exists(sp) else ""
    parts = _parts(narration, script, max(1, int(n)))
    ask = ("You design calm, beautiful pictures for a Hindi sleep-story channel (Sukoon). For EACH numbered part "
           "of the story below, write ONE English image prompt (25-45 words): a peaceful visual scene that matches "
           "what is said - place, light, objects, mood. No text or letters in the picture, no close-up faces. "
           + (f"The viewer's own idea for the pictures: {idea}. " if idea else "")
           + 'Answer ONLY as JSON: {"prompts": ["...", "..."]} with exactly ' + str(len(parts)) + " prompts.\n\n"
           + "\n".join(f"{i + 1}. {p['text'][:900]}" for i, p in enumerate(parts)))
    prompts = []
    try:
        model = cfg.get("prompt_model") or cfg.get("llm")
        say(f"   asking {model} (Ollama prompt model) ...")
        prompts = [str(x) for x in json.loads(tools.model_chat(cfg, model, ask, 0.6, True, timeout=600)).get("prompts", [])]
    except Exception as e:
        say(f"   (the AI could not answer: {e} - using simple prompts)")
    while len(prompts) < len(parts):
        prompts.append((idea + ", " if idea else "") + "a peaceful quiet night scene, soft light, calm and cozy")
    shots = []
    for i, (p, pr) in enumerate(zip(parts, prompts)):
        f = f"{i + 1:03d}.png"
        shots.append({"file": f, "prompt": pr, "text": p["text"], "start": p["start"], "end": p["end"]})
        say(f"   {i + 1}: {pr}")
    json.dump({"source": narration or "script", "shots": shots}, open(os.path.join(out, "shots.json"), "w",
              encoding="utf-8"), ensure_ascii=False, indent=1)


def default_style(cfg):
    """your own Sukoon thumbnail look when the thumbnail index has it, else the Sukoon night preset"""
    return "sukoon_theme" if tools.load_index(cfg).get("themes", {}).get("sukoon") else "sukoon_night"


def _script_sample(text, chars=6000):
    """beginning, middle and end of a script (the story usually lives in the middle)"""
    t = re.sub(r"\(\s*\.{2,}\s*\)\s*", " ", text or "")
    if len(t) <= chars:
        return t
    k = chars // 3
    m = len(t) // 2
    return t[:k] + "\n...\n" + t[m - k // 2:m + k // 2] + "\n...\n" + t[-k:]


def art_prompt(ctx, name, narration, script, out, idea="", count=3):
    """the episode's artwork from the WHOLE script (not just its first lines): central image, place, light, objects;
    `count` versions are painted, pick_art keeps the one that fits best"""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    os.makedirs(out, exist_ok=True)
    if not script:
        for sp in (os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt"), os.path.join(narration or "", "script.txt")):
            if os.path.exists(sp):
                script = open(sp, encoding="utf-8").read(); break
    meta = {}
    sj = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".story.json")
    if os.path.exists(sj):
        try:
            meta = json.load(open(sj, encoding="utf-8"))
        except Exception:
            meta = {}
    r = meta.get("recipe") or {}
    extra = ""
    if r.get("image") or r.get("place"):
        extra = f" (its central image: {r.get('image', '')}; its healing place: {r.get('place', '')}; topic: {r.get('topic', '')})"
    if idea:
        extra += f" - the creator's own idea for the picture: {idea}"
    prompt, check = "", []
    try:
        say(f"   reading the whole script ({story.nwords(script or '')} words){extra and ' + its recipe'} ...")
        previous_model = cfg.get("_llm_override")
        cfg["_llm_override"] = cfg.get("prompt_model") or cfg.get("llm")
        try:
            t, model = story.ask(cfg, tools.ART_ASK.format(extra=extra, text=_script_sample(script)), "local", 0.5, True, say=say)
        finally:
            if previous_model is None:
                cfg.pop("_llm_override", None)
            else:
                cfg["_llm_override"] = previous_model
        d = json.loads(t)
        prompt, check = str(d.get("prompt", "")).strip(), [str(x) for x in d.get("check", [])][:5]
        say(f"   designed by {model}")
    except Exception as e:
        say(f"   (the AI could not design it: {e} - using the recipe)")
    if not prompt:
        prompt = ", ".join(x for x in (idea, r.get("image"), r.get("place"), "a peaceful night scene, soft moonlight, "
                                       "warm lamp glow, calm and cozy") if x)
    say(f"   artwork: {prompt}")
    if check:
        say(f"   must show: {'; '.join(check)}")
    n = max(1, min(4, int(count or 3)))
    shots = [{"file": f"cand_{i + 1}.png", "prompt": prompt, "text": "", "start": None, "end": None} for i in range(n)]
    json.dump({"source": narration or "script", "shots": shots, "check": check, "art_prompt": prompt},
              open(os.path.join(out, "shots.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def pick_art(ctx, out):
    """the vision AI looks at every version and keeps the best as 001.png (the others stay in the folder)"""
    import base64
    cfg, say = ctx["cfg"], ctx["say"]
    # ComfyUI may still have its diffusion model loaded; unload it before the vision model needs VRAM.
    tools.free_gpu(ctx)
    sj = os.path.join(out, "shots.json")
    meta = json.load(open(sj, encoding="utf-8"))
    cands = [os.path.join(out, s["file"]) for s in meta["shots"] if os.path.exists(os.path.join(out, s["file"]))]
    if not cands:
        raise RuntimeError("no artwork was painted")
    want = "; ".join(meta.get("check") or []) or meta.get("art_prompt", "")[:300]
    best, scores = cands[0], {}
    if cands:
        tdir = os.path.join(out, "_small")
        os.makedirs(tdir, exist_ok=True)
        for f in cands:
            small = os.path.join(tdir, os.path.basename(f)[:-4] + ".jpg")
            subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", f, "-vf", "scale=896:-2", "-q:v", "4", small],
                           capture_output=True, creationflags=0x08000000 if WIN else 0)
            try:
                d = json.loads(tools.model_chat(cfg, cfg.get("vision_llm", cfg["llm"]), tools.PICK_ASK.format(want=want),
                                               0.1, True, image_path=small, timeout=300))
                scores[f] = float(d.get("score", 0))
                say(f"   {os.path.basename(f)}: {scores[f]:.0f}/10" + (f" - missing: {d.get('missing')}" if d.get("missing") else "")
                    + (f" - flaws: {d.get('flaws')}" if d.get("flaws") else ""))
            except Exception as e:
                say(f"   (vision AI could not judge {os.path.basename(f)}: {e})")
        if scores:
            best = max(scores, key=scores.get)
    if not scores:
        say("   no artwork candidate passed review; keeping all candidates for manual choice")
        tools.free_llm(ctx)
        return False
    if scores[best] < 6:
        say(f"   best artwork scored {scores[best]:.0f}/10; keeping candidates for repaint or manual choice")
        tools.free_llm(ctx)
        return False
    dst = os.path.join(out, "001.png")
    if os.path.exists(dst):
        os.replace(dst, os.path.join(out, time.strftime("_old_%H%M%S_001.png")))
    shutil.copyfile(best, dst)
    if os.path.exists(best[:-4] + ".prompt.txt"):
        shutil.copyfile(best[:-4] + ".prompt.txt", dst[:-4] + ".prompt.txt")
    json.dump({**meta, "chosen": os.path.basename(best), "scores": {os.path.basename(k): v for k, v in scores.items()}},
              open(sj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say(f"   artwork: {os.path.basename(best)} -> 001.png  (the other versions stay in {out} - use one instead any time)")
    tools.free_llm(ctx)
    return True


HOT_DRINK = re.compile(r"\b(tea|chai|coffee|cup|mug|kettle|teapot|cocoa|soup)\b|चाय|कप|कॉफी|केतली", re.I)


def no_steam(prompt, negative=""):
    """Hot drinks are painted WITHOUT steam: the video adds real, moving steam later (elements library)."""
    if not HOT_DRINK.search(prompt or ""):
        return prompt, negative
    prompt += ". The air above the cup is clear: no steam, no smoke, no vapour"
    negative = ", ".join(x for x in (negative, "steam, smoke, vapour, mist above the cup") if x)
    return prompt, negative


def comfy_images(ctx, out, style="sukoon_night", size="16:9", engine=None, seed=None, prompts=None, prefix="",
                 steps=None, negative="", ref="", denoise=None, silhouette=False, variants=1):
    cfg, say = ctx["cfg"], ctx["say"]
    tools.free_llm(ctx)                                     # graphics card to ComfyUI alone
    os.makedirs(out, exist_ok=True)
    w, h = tools.SIZES.get(size, tools.SIZES["16:9"])[:2]
    look = tools.look_text(cfg, style, ref)
    if look and (ref or style in tools.THEMES):
        say(f"   look of {'your thumbnail ' + os.path.basename(ref) if ref else 'your ' + tools.THEMES[style]}: {look[:160]}...")
    init = None
    if ref and os.path.exists(ref) and denoise not in (None, "", 0):
        if not tools.comfy_running(cfg):
            tools.start_comfy(cfg, say)
        init = tools.upload_image(cfg, ref)
        say(f"   starting from {os.path.basename(ref)} (keeps {int(round((1 - float(denoise)) * 100))}% of its look)")
    sj = os.path.join(out, "shots.json")
    if prompts:
        shots = []
        for i, pr in enumerate(prompts):
            base = f"{prefix or 'img'}_{time.strftime('%H%M%S')}_{i + 1}"
            shots.append({"file": base + ".png", "prompt": pr})
    else:
        _m = json.load(open(sj, encoding="utf-8"))
        shots = _m["shots"] + (_m.get("moments") or [])
    if not tools.comfy_running(cfg):
        tools.start_comfy(cfg, say)
    engine = engine or cfg.get("image_engine", "z_image_turbo")
    for i, s in enumerate(shots):
        if ctx["stopped"]():
            return False
        f = os.path.join(out, s["file"])
        if os.path.exists(f) and not prompts and (int(variants) < 2 or s.get("picked") or
                                                  os.path.exists(f[:-4] + "_b.png")):
            say(f"   {s['file']} already there"); continue
        subj = s["prompt"]
        if silhouette:
            subj = f"{subj}. The main subject is a solid black featureless silhouette against the glowing light"
        full = subj + (". " + look if look else "")
        full, neg = no_steam(full, negative)
        sd = int(seed) + i if seed not in (None, "") else int(s.get("seed") or random.randint(1, 2 ** 31))
        t0 = time.time()
        st = int(steps) if steps not in (None, "", 0) else 8
        if not os.path.exists(f) or prompts:
            tools.comfy_generate(cfg, full, f, engine, sd, w, h, steps=st, negative=neg, init_image=init,
                           denoise=float(denoise or 0.8))
        for k in range(1, int(variants)):             # more versions: the vision AI keeps the best (pick_scenes)
            fb = f[:-4] + "_" + "bcd"[k - 1] + ".png"
            if not os.path.exists(fb):
                tools.comfy_generate(cfg, full, fb, engine, sd + 1000 * k, w, h, steps=st, negative=neg)
        open(f[:-4] + ".prompt.txt", "w", encoding="utf-8").write(
            f"{full}\n\nseed {sd}  engine {engine}  {w}x{h}  steps {st}" + (f"\nnegative: {neg}" if neg else "") + "\n")
        say(f"   {s['file']}  ({time.time() - t0:.0f} s, seed {sd})")
    if not prompts:
        json.dump({**json.load(open(sj, encoding="utf-8")), "style": style, "size": size}, open(sj, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)


def _judge(cfg, img, ask, reference_path=None):
    small = img[:-4] + "_judge.jpg"
    subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", img, "-vf", "scale=768:-2", "-q:v", "4", small],
                   capture_output=True, creationflags=0x08000000 if WIN else 0)
    ref_small = None
    if reference_path and os.path.exists(reference_path):
        ref_small = reference_path[:-4] + "_refjudge.jpg"
        subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", reference_path, "-vf", "scale=768:-2",
                        "-q:v", "4", ref_small], capture_output=True, creationflags=0x08000000 if WIN else 0)
    try:
        paths = [ref_small, small] if ref_small and os.path.exists(ref_small) else [small]
        try:
            raw = tools.model_chat(cfg, cfg.get("vision_llm", cfg["llm"]), ask, 0.1,
                             True, image_paths=paths, timeout=300)
        except Exception:
            if len(paths) == 1:
                raise
            raw = tools.model_chat(cfg, cfg.get("vision_llm", cfg["llm"]), ask, 0.1,
                             True, image_path=small, timeout=300)
        d = json.loads(raw)
        return (float(d.get("score", 0)), str(d.get("flaws", ""))[:180],
                str(d.get("revision", ""))[:300].strip())
    finally:
        try:
            os.remove(small)
        except OSError:
            pass
        if ref_small:
            try:
                os.remove(ref_small)
            except OSError:
                pass


def pick_scenes(ctx, out, genre="bhay", redo=True):
    """every scene was painted in 2 versions: the vision AI keeps the one that shows the scene best and is clean
    (no text, no broken hands / faces); a scene where both are bad is painted twice more and judged again.
    The other versions go to _alt (nothing deleted)."""
    cfg, say = ctx["cfg"], ctx["say"]
    if cfg.get("experimental_mode") or cfg.get("_experimental_workspace") or cfg.get("picture_check") == "new":
        import picture_check                # Experimental: yes/no checks against 'what happens', flag instead of stop
        return picture_check.pick_scenes(ctx, out, genre, redo)
    sj = os.path.join(out, "shots.json")
    meta = json.load(open(sj, encoding="utf-8"))
    # Older/partially written scene plans can contain numeric placeholders.
    # The picker expects each entry to be a scene record, so ignore malformed
    # rows explicitly instead of failing later on s["prompt"] / s.get(...).
    scene_records = meta.get("shots") or []
    moment_records = meta.get("moments") or []
    if not isinstance(scene_records, list):
        scene_records = []
        meta["shots"] = scene_records
    if not isinstance(moment_records, list):
        moment_records = []
        meta["moments"] = moment_records
    malformed = sum(not isinstance(s, dict) for s in scene_records + moment_records)
    if malformed:
        say(f"   (skipping {malformed} malformed scene record(s) in shots.json; expected objects)")
        scene_records = [s for s in scene_records if isinstance(s, dict)]
        moment_records = [s for s in moment_records if isinstance(s, dict)]
        meta["shots"], meta["moments"] = scene_records, moment_records
    records = scene_records + moment_records
    if not records:
        raise RuntimeError("shots.json has no valid scene records; regenerate the scene plan before picking images")
    G = tools.GENRES.get(genre, tools.GENRES["bhay"])
    look = meta.get("look", "")[:220]
    alt = os.path.join(out, "_alt")
    os.makedirs(alt, exist_ok=True)

    character_refs = {}

    def choose(s, files):
        character = s.get("character", "")
        reference = character_refs.get(character) if character else None
        anchor_note = ("Two images are attached: first is the previously accepted frame for this same recurring "
                       "character; second is the candidate. Compare only silhouette, clothing and distinctive prop "
                       "continuity, while allowing this scene's camera angle and action to differ."
                       if reference else "Only the candidate image is attached.")
        ask = tools.SCENE_ASK.format(desc=G["desc"], want=s["prompt"][:350],
                               character_anchor=s.get("character_anchor") or "none for this scene",
                               look=look or "the same film look",
                               anchor_note=anchor_note)
        sc, reviews = {}, {}
        for f in files:
            try:
                sc[f], flaws, revision = _judge(cfg, f, ask, reference)
                reviews[f] = (flaws, revision)
            except Exception as e:
                say(f"   (vision AI could not judge {os.path.basename(f)}: {e})")
        if not sc:
            return None, 0.0, ""
        # _judge returns (score, flaws, revision), but sc stores only the
        # numeric score; review text lives separately in `reviews`.
        best = max(sc, key=sc.get)
        say(f"   {s['file']}: " + "  ".join(f"{os.path.basename(k)} {v:.0f}/10" for k, v in sc.items())
            + (f"  - flaws: {reviews[best][0]}" if reviews[best][0] and sc[best] < 7 else ""))
        return best, sc[best], reviews[best][1]

    tools.free_gpu(ctx)
    bad = []
    for s in records:
        if ctx["stopped"]():
            return False
        f = os.path.join(out, s["file"])
        files = [x for x in [f] + [f[:-4] + "_" + c + ".png" for c in "bcd"] if os.path.exists(x)]
        if s.get("picked") or not files:
            continue
        best, score, revision = choose(s, files)      # reviewer returns a targeted prompt correction when needed
        if best and best != f:
            os.replace(f, os.path.join(alt, time.strftime("%H%M%S_") + os.path.basename(f)))
            os.replace(best, f)
        for x in files:
            if x != f and os.path.exists(x):
                os.replace(x, os.path.join(alt, time.strftime("%H%M%S_") + os.path.basename(x)))
        s["picked"], s["score"], s["repair_hint"] = True, score, revision
        if s.get("character") and best and score >= 5:
            character_refs.setdefault(s["character"], f)
        if score < 6:
            bad.append(s)
    if bad and redo:
        say(f"   {len(bad)} scene(s) below 6/10 - applying the review's targeted correction and repainting")
        tools.free_llm(ctx)
        w, h = tools.SIZES.get(meta.get("size", "16:9"), tools.SIZES["16:9"])[:2]
        for s in bad:
            f = os.path.join(out, s["file"])
            pt = f[:-4] + ".prompt.txt"
            full = open(pt, encoding="utf-8").read().split("\n\n")[0] if os.path.exists(pt) else s["prompt"]
            revision = s.get("repair_hint") or "Use a clearly different, scene-appropriate camera angle and framing; keep the story's visual look and recurring character silhouette/clothing unchanged."
            repair_prompt = (full + ". Targeted visual correction: " + revision +
                             ". Preserve every correct subject, prop, palette, and character silhouette detail.")
            s["repair_prompt"] = repair_prompt
            for k, c in enumerate("b" if len(bad) > 3 else "bc"):
                tools.comfy_generate(cfg, repair_prompt, f[:-4] + "_" + c + ".png", cfg.get("image_engine", "z_image_turbo"),
                               random.randint(1, 2 ** 31), w, h, steps=8)
        tools.free_gpu(ctx)
        for s in bad:
            f = os.path.join(out, s["file"])
            files = [x for x in [f, f[:-4] + "_b.png", f[:-4] + "_c.png"] if os.path.exists(x)]
            best, score, revision = choose(s, files)
            if best and best != f:
                os.replace(f, os.path.join(alt, time.strftime("%H%M%S_") + os.path.basename(f)))
                os.replace(best, f)
            for x in files:
                if x != f and os.path.exists(x):
                    os.replace(x, os.path.join(alt, time.strftime("%H%M%S_") + os.path.basename(x)))
            s["score"], s["repair_hint"] = score, revision
            if s.get("character") and best and score >= 5:
                character_refs.setdefault(s["character"], f)
    unresolved = [s for s in records
                  if s.get("score") is None or s.get("score", 0) < 6]
    json.dump(meta, open(sj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if unresolved:
        say(f"   {len(unresolved)} scene(s) still below 6/10 or unreviewed; stopped before video rendering")
        tools.free_llm(ctx)
        tools.free_gpu(ctx)
        return False
    tools.free_llm(ctx)
    sc = [s.get("score") for s in records if s.get("score") is not None]
    if sc:
        say(f"   scenes judged: average {sum(sc) / len(sc):.1f}/10, lowest {min(sc):.0f}/10")
    tools.free_gpu(ctx)
    return True


def repaint(ctx, dir, file, prompt="", engine="", steps=None, negative=""):
    """one picture of a set again: old one kept as _old_<time>_<file>, new seed, optionally a new prompt"""
    cfg, say = ctx["cfg"], ctx["say"]
    f = os.path.join(dir, file)
    pt = f[:-4] + ".prompt.txt"
    old_prompt = open(pt, encoding="utf-8").read().split("\n\n")[0].strip() if os.path.exists(pt) else ""
    meta = open(pt, encoding="utf-8").read() if os.path.exists(pt) else ""
    m = re.search(r"(\d+)x(\d+)", meta)
    w, h = (int(m.group(1)), int(m.group(2))) if m else tools.SIZES["16:9"][:2]
    me = re.search(r"engine (\S+)", meta)
    engine = engine or (me.group(1) if me else cfg.get("image_engine"))
    full = prompt.strip() or old_prompt
    if not full:
        raise RuntimeError("no prompt for this picture - write one")
    if prompt.strip() and old_prompt and old_prompt not in prompt:         # keep the set's style words
        sj = os.path.join(dir, "shots.json")
        style = json.load(open(sj, encoding="utf-8")).get("style") if os.path.exists(sj) else None
        look = tools.STYLES.get(style or "", ("", ""))[1]
        if look and look not in full:
            full = full + ". " + look
    if os.path.exists(f):
        os.replace(f, os.path.join(dir, time.strftime("_old_%H%M%S_") + file))
    if not tools.comfy_running(cfg):
        tools.start_comfy(cfg, say)
    sd = random.randint(1, 2 ** 31)
    st = int(steps) if steps not in (None, "", 0) else 8
    full, negative = no_steam(full, negative)
    tools.comfy_generate(cfg, full, f, engine, sd, w, h, steps=st, negative=negative)
    open(pt, "w", encoding="utf-8").write(f"{full}\n\nseed {sd}  engine {engine}  {w}x{h}  steps {st}\n")
    say(f"   {file} painted again (seed {sd})")
