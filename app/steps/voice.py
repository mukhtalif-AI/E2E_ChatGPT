"""steps/voice.py - voice, English voice, word rack, GPU. Moved out of tools.py on 16 Oct 2026 (TOOLS_SPLIT_PLAN.md step 4g).

Do not import this file directly: import tools and use tools.<name>. Names that stay in tools.py, and names other files swap
(feedback, narrator, Tinker), are written as tools.X here on purpose.
"""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave

import tools


# ------------------------------------------------------------------ the word rack (words your voice stumbles on)
def rack_path(cfg):
    return os.path.join(cfg["sukoonvoice_media"], "word_rack.json")


def word_rack(cfg):
    """{word: {count, examples, narrations, last, alt, solved}} - written by the voice engine after every narration"""
    try:
        return json.load(open(rack_path(cfg), encoding="utf-8"))
    except Exception:
        return {}


def hard_words(cfg, min_count=2):
    rk = word_rack(cfg)
    return sorted([w for w, r in rk.items() if r.get("count", 0) >= min_count], key=lambda w: -rk[w]["count"])


def rack_save(cfg, body):
    """Settings: mark words solved after retraining (count back to 0, history kept) or set your own alternative"""
    rk = word_rack(cfg)
    for w in body.get("solved") or []:
        if w in rk:
            rk[w]["solved_on"] = time.strftime("%Y-%m-%d"); rk[w]["count"] = 0
    for w, alt in (body.get("alt") or {}).items():
        if w in rk:
            rk[w]["alt"] = alt.strip()
    json.dump(rk, open(rack_path(cfg), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"words": [dict(v, word=k) for k, v in sorted(rk.items(), key=lambda kv: -kv[1].get("count", 0))]}


def avoid_hard_words(ctx, name, genre=""):
    """lines with words your voice keeps stumbling on are rewritten with the same meaning and feeling but other
    words (your own alternative from the rack first); the script before is kept in _old"""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    hard = hard_words(cfg)
    p = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    if not hard or not os.path.exists(p):
        return True
    rk = word_rack(cfg)
    lines = open(p, encoding="utf-8").read().replace("\r\n", "\n").split("\n")
    has = lambda l: [w for w in hard if re.search(r"(?<![\u0900-\u097F])" + re.escape(w) + r"(?![\u0900-\u097F])", l)]
    idx = [i for i, l in enumerate(lines) if has(l)]
    if not idx:
        return True
    changed = 0
    for i in idx:                                         # your own alternative: a plain swap, no AI needed
        for w in has(lines[i]):
            if rk[w].get("alt"):
                lines[i] = re.sub(r"(?<![\u0900-\u097F])" + re.escape(w) + r"(?![\u0900-\u097F])", rk[w]["alt"], lines[i])
                changed += 1
    idx = [i for i in idx if has(lines[i])]
    if idx:
        ask = ("Rewrite each numbered Hindi line so it does NOT use these words: " + ", ".join(hard) + ". Keep the SAME "
               "meaning, feeling and length, the same '...' pauses and [marks]; use simple spoken Hindi words instead "
               "(a synonym or a slightly changed sentence). Answer JSON only: {\"1\": \"...\", ...}\n\n" +
               "\n".join(f"{j + 1}. {lines[i].strip()}" for j, i in enumerate(idx)))
        try:
            t, m = story.ask(cfg, ask, "auto", 0.3, as_json=True, say=say)
            d = json.loads(t[t.find("{"):t.rfind("}") + 1])
            for j, i in enumerate(idx):
                nl = str(d.get(str(j + 1), "")).strip()
                if nl and not has(nl) and 0.6 < len(nl) / max(1, len(lines[i].strip())) < 1.6:
                    lines[i] = nl; changed += 1
        except Exception as e:
            say(f"   (could not rewrite the hard words: {e})")
    if changed:
        old = os.path.join(os.path.dirname(p), "_old")
        os.makedirs(old, exist_ok=True)
        shutil.copy2(p, os.path.join(old, f"{tools.slug(name)}_before_hardwords_{time.strftime('%Y%m%d_%H%M')}.txt"))
        open(p + ".part", "w", encoding="utf-8").write("\n".join(lines))
        os.replace(p + ".part", p)
    say(f"   words your voice stumbles on ({', '.join(hard[:8])}{'…' if len(hard) > 8 else ''}): {changed} place(s) said another way")
    return True


def practice_story(ctx, minutes=3):
    """a short story that uses every hard word several times - record it, tag it to its series, retrain"""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    hard = hard_words(cfg, 1)
    if not hard:
        return say("   the word rack is empty - nothing to practise yet")
    ask = (f"Write a natural spoken Hindi story of about {int(float(minutes) * 140)} words (Devanagari only) that uses "
           "EACH of these words at least three times, in different sentences and positions: " + ", ".join(hard[:40]) +
           ". One phrase per line, simple and warm. Only the story.")
    t, m = story.ask(cfg, ask, "auto", 0.8, say=say)
    name = f"Practice - hard words - {time.strftime('%Y-%m-%d %H%M')}"
    p = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    open(p, "w", encoding="utf-8").write(t.strip() + "\n")
    miss = [w for w in hard[:40] if w not in t]
    say(f"   {p}\n   record it in your own voice (Audacity), save the project in a recordings folder, tag it to the "
        f"series it suits in Settings, and train that series again" + (f"\n   (not used by the AI: {', '.join(miss)})" if miss else ""))
    return True


def free_gpu(ctx):
    cfg, say = ctx["cfg"], ctx["say"]
    if tools.comfy_running(cfg):
        try:
            tools._post(cfg["comfy_url"] + "/free", {"unload_models": True, "free_memory": True}, 30)
            say("   ComfyUI: pictures model unloaded")
        except Exception as e:
            say(f"   ComfyUI could not unload its models: {e}")
    free_llm(ctx)          # also the AI models this job used (free_llm checks that), so the voice gets the whole card


def free_llm(ctx):
    """Unload loaded Ollama role models so ComfyUI or TTS can use the GPU."""
    cfg, say = ctx["cfg"], ctx["say"]
    if not ctx.get("include_llm") and not cfg.pop("_ollama_used", False):
        return
    models = {cfg.get("llm"), cfg.get("prompt_model"), cfg.get("vision_llm"), cfg.get("story_model"),
              cfg.get("director_model"), cfg.get("scene_picture_model")}     # Tinker scene models too
    try:
        import story
        models.add(story.secret("local_model"))
    except Exception:
        pass
    models.discard(None)
    models.discard("")
    if not models:
        say("   Ollama: no selected models to unload")
        return
    errors = []
    for model in sorted(models):
        try:
            tools._post(cfg.get("ollama_url", "http://127.0.0.1:11434").rstrip("/") + "/api/generate",
                  {"model": model, "prompt": "", "keep_alive": 0}, 30)
            say(f"   Ollama: unloaded {model}; server stays running")
        except Exception as e:
            errors.append(f"{model}: {e}")
    if errors:
        say("   Ollama unload issue: " + "; ".join(errors))


def sv_python(cfg):
    f = os.path.join(cfg["sukoonvoice_dir"], "python.txt")
    if os.path.exists(f):
        p = open(f, encoding="utf-8-sig").read().strip().strip('"')
        if p:
            return p
    return cfg["gpu_python"]


def _story_voice_style(ctx, script_path, genre):
    """Choose a tone reference from the finished AI story; fall back to its selected series."""
    cfg, say = ctx["cfg"], ctx["say"]
    fallback = tools.GENRES.get(genre, tools.GENRES["bhay"])["voice"]
    ref_dir = cfg.get("voice_references_dir", os.path.join(cfg.get("sukoonvoice_media", ""), "dataset", "refs"))
    available = {os.path.splitext(f)[0].lower(): os.path.splitext(f)[0]
                 for f in os.listdir(ref_dir) if f.lower().endswith(".wav")
                 and not f.lower().startswith(("room_tone", "smile"))} if os.path.isdir(ref_dir) else {}
    choices = {}
    for gid, values in tools.GENRES.items():
        reference = values["voice"]
        if reference.lower() in available:
            choices[gid] = {"reference": available[reference.lower()], "description": values["desc"]}
    if len(choices) < 2:
        say(f"   story-tone match skipped; keeping the {genre} voice reference ({fallback})")
        return fallback
    try:
        script = open(script_path, encoding="utf-8-sig", errors="replace").read()[:9000]
        prompt = ("Choose which existing voice reference best fits the emotional narration tone of this completed "
                  "Hindi story. Consider its actual events and emotional arc, not only its series label. Do not change "
                  "the story's genre or rewrite it. Choose one candidate id exactly. Return JSON only: "
                  '{"id":"...","reason":"short phrase"}\nCandidates: ' +
                  json.dumps(choices, ensure_ascii=False) + "\nSelected series: " + str(genre) +
                  "\nSTORY:\n" + script)
        model = cfg.get("prompt_model") or cfg.get("llm")
        answer = tools.model_chat(cfg, model, prompt, temperature=0.1, as_json=True, timeout=120)
        data = json.loads(answer[answer.find("{"):answer.rfind("}") + 1])
        chosen = choices.get(str(data.get("id", "")).lower())
        if chosen:
            say(f"   story-tone match: {chosen['reference']} ({str(data.get('reason') or choices[str(data['id']).lower()]['description'])[:100]})")
            return chosen["reference"]
        say(f"   story-tone match returned no available reference; keeping {fallback}")
    except Exception as e:
        say(f"   story-tone match unavailable ({str(e)[:100]}); keeping {fallback}")
    finally:
        free_llm(ctx)
    return fallback


def sv_speak(ctx, name, sets, redo="", style=""):
    cfg = ctx["cfg"]
    script = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    if not os.path.exists(script):
        old = os.path.join(tools.narration_dir(cfg, name, existing=True), "script.txt")
        if not os.path.exists(old):
            raise RuntimeError(f"no script for {name}")
        script = old
    if not os.path.isdir(os.path.join(cfg["sukoonvoice_dir"], "libs", "cb")):
        raise RuntimeError("the voice engine is not set up yet - run setup_hub.bat once")
    if style == "__auto_story_theme__":
        style = _story_voice_style(ctx, script, str((ctx.get("job") or {}).get("params", {}).get("genre") or "bhay"))
    hv = os.path.join(tools.narration_dir(cfg, name, existing=bool(redo)), "hub_voice.json")
    if redo and os.path.exists(hv):                  # remaking lines: the SAME voice settings as the narration had
        try:
            saved = json.load(open(hv, encoding="utf-8"))
            keys = {x.split("=")[0] for x in sets}
            sets = [x for x in saved.get("sets", []) if x.split("=")[0] not in keys] + list(sets)
            style = style or saved.get("style", "")
        except Exception:
            pass
    elif not redo:
        os.makedirs(os.path.dirname(hv), exist_ok=True)
        json.dump({"sets": [x for x in sets if not x.startswith("takes=")], "style": style}, open(hv, "w", encoding="utf-8"),
                  ensure_ascii=False)
    cmd = [sv_python(cfg), "-s", "-u", "run.py", "speak", script, "--name", tools.slug(name)]
    for s in sets:
        cmd += ["--set", s]
    if redo:
        cmd += ["--redo", redo]
    if style:
        cmd += ["--style", style]
    env = {"SV_LOGNAME": f"hub_{ctx['job']['id']}.txt"}
    # Keep generated audio in HubGPT's output tree. HubGPT Voice model/training data stays shared.
    output_root = os.path.dirname(tools.narration_dir(cfg, name, existing=bool(redo)))
    env["SV_NARRATIONS"] = output_root
    rc = ctx["run_cmd"](cmd, cwd=cfg["sukoonvoice_dir"], env=env)
    out = tools.narration_dir(cfg, name, existing=bool(redo))
    if rc == 0:
        rep = os.path.join(out, "report.txt")
        if os.path.exists(rep):
            for ln in open(rep, encoding="utf-8").read().splitlines()[3:]:
                if ln.strip():
                    ctx["say"]("   " + ln)
    return rc == 0


def en_dir(cfg):
    return cfg.get("english_dir") or os.path.join(os.path.dirname(cfg["studio_dir"]), "english")


def en_python(cfg):
    f = os.path.join(en_dir(cfg), "python.txt")
    if os.path.exists(f):
        p = open(f, encoding="utf-8-sig").read().strip().strip('"')
        if p:
            return p
    return cfg["gpu_python"]


def en_speak(ctx, name, redo="", speaker=""):
    """the English narrator (Qwen3-TTS, engines\\english) reads the script; same narration folder layout as yours"""
    cfg = ctx["cfg"]
    script = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    if not os.path.exists(script):
        script = os.path.join(tools.narration_dir(cfg, name, existing=True), "script.txt")
    if not os.path.exists(script):
        raise RuntimeError(f"no script for {name}")
    d = en_dir(cfg)
    if not os.path.isdir(os.path.join(d, "libs", "qwen")):
        raise RuntimeError("the English narrator is not set up yet - run setup_hub.bat once")
    output_dir = os.path.dirname(tools.narration_dir(cfg, name, existing=bool(redo)))
    cmd = [en_python(cfg), "-s", "-u", "run.py", "speak", script, "--out",
           output_dir, "--name", tools.slug(name), "--ffmpeg", cfg["ffmpeg"]]
    if redo:
        cmd += ["--redo", redo]
    if speaker:
        cmd += ["--speaker", speaker]
    rc = ctx["run_cmd"](cmd, cwd=d)
    out = tools.narration_dir(cfg, name, existing=bool(redo))
    rep = os.path.join(out, "report.txt")
    if rc == 0 and os.path.exists(rep):
        for ln in open(rep, encoding="utf-8").read().splitlines()[3:]:
            if ln.strip():
                ctx["say"]("   " + ln)
    return rc == 0


def en_setup(ctx):
    cfg = ctx["cfg"]
    return ctx["run_cmd"]([en_python(cfg), "-s", "-u", "run.py", "setup", "--yes"], cwd=en_dir(cfg)) == 0


def en_test(ctx, engine=""):
    cfg = ctx["cfg"]
    cmd = [en_python(cfg), "-s", "-u", "run.py", "test", "--ffmpeg", cfg["ffmpeg"]]
    if engine:
        cmd += ["--engine", engine]
    return ctx["run_cmd"](cmd, cwd=en_dir(cfg)) == 0
