"""Experimental-only storage layout for HubGPT jobs.

The stable HubGPT paths remain configured in config.json. Experimental jobs
get a per-episode workspace under the dedicated HubGPT root instead.
"""
import glob
import array
import json
import os
import re
import shutil
import struct
import time
import wave


def layout(cfg, key, default):
    return (cfg.get("layout") or {}).get(key, default)


def root(cfg):
    # The one data folder (config.json paths.experimental_root); without it, the main output folder.
    return os.path.abspath(cfg.get("experimental_root") or cfg.get("media_dir") or r"D:\MukhtalifGPT")


def legacy_roots(cfg):
    """Older data folders (config.json paths.legacy_roots, e.g. D:\\HubGPT): only read, never written,
    so files made before the move still open and old jobs still show."""
    here = os.path.normcase(root(cfg))
    out = []
    for p in (cfg.get("paths") or {}).get("legacy_roots") or []:
        if p and os.path.normcase(os.path.abspath(p)) != here and os.path.isdir(p):
            out.append(os.path.abspath(p))
    return out


def initialize(cfg):
    base = root(cfg)
    for path in (f"{layout(cfg, 'experimental_library', 'Library')}/{layout(cfg, 'experimental_scripts', 'Scripts')}",
                 f"{layout(cfg, 'experimental_library', 'Library')}/{layout(cfg, 'experimental_assets', 'Assets')}",
                 f"{layout(cfg, 'experimental_activity', 'Activity')}/{layout(cfg, 'logs', 'Logs')}",
                 f"{layout(cfg, 'experimental_activity', 'Activity')}/{layout(cfg, 'experimental_reports', 'Reports')}",
                 layout(cfg, 'experimental_series', 'Series'), layout(cfg, 'experimental_work', '_work')):
        os.makedirs(os.path.join(base, *path.split("/")), exist_ok=True)
    return base


def _safe(value, fallback="Other"):
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_-")
    return value[:60] or fallback


SERIES_PREFIX = {"bhay": "Bhay", "rahasya": "Rahasya", "kisse": "Kisse", "romanch": "Romanch", "kareeb": "Kareeb",
                 "gyaan": "Gyaan", "sukoon": "Sukoon"}


def _make_episode(episode_dir, L, job_id, kind, series, b, input_name="input.json"):
    for path in (L.get("experimental_input", "00_Input"), L.get("experimental_script", "01_Script"), L.get("experimental_narration", "02_Narration"), L.get("experimental_artwork", "03_Artwork"), L.get("episode_output", "Output")):
        os.makedirs(os.path.join(episode_dir, path), exist_ok=True)
    with open(os.path.join(episode_dir, L.get("experimental_input", "00_Input"), input_name), "w", encoding="utf-8") as f:
        json.dump({"job_id": job_id, "kind": kind, "series": series, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "request": {k: v for k, v in b.items() if k not in ("data",)}}, f, ensure_ascii=False, indent=2)


def _episode_of_narration(base, L, series, name):
    """the episode folder that already holds this voice (Video / Pictures pages on an existing narration)"""
    if not name:
        return ""
    for ep in glob.glob(os.path.join(base, L.get("experimental_series", "Series"), series, L.get("experimental_episode_collection", "Episodes"), "*")):
        if os.path.isfile(os.path.join(ep, L.get("experimental_narration", "02_Narration"), L.get("narrations", "narrations"), name, "timing.json")):
            return ep
    return ""


def _short_name(base, L, series, b):
    """<Series>-<title>: the typed name, else the story topic, else <Series>-<MMDD-HHMM>; -2, -3 when taken"""
    from tools import slug
    prefix = SERIES_PREFIX.get(series, series.title())
    recipe = b.get("recipe") if isinstance(b.get("recipe"), dict) else {}
    title = str(b.get("name") or "").strip() or str(b.get("topic") or "").strip() or str(recipe.get("topic") or "").strip()
    if title:
        name = re.sub(r"-{2,}", "-", re.sub(r"[\s_]+", "-", slug(title, 40))).strip("-.")
        if not re.match(re.escape(prefix) + r"\s*-", name, re.I):
            name = f"{prefix}-{name}"
    else:
        name = f"{prefix}-{time.strftime('%m%d-%H%M')}"
    name = name[:48].rstrip("-.")
    episodes = os.path.join(base, L.get("experimental_series", "Series"), series, L.get("experimental_episode_collection", "Episodes"))
    scripts = os.path.join(base, L.get("experimental_library", "Library"), L.get("scripts", "scripts"))
    candidate, n = name, 2
    while os.path.exists(os.path.join(episodes, candidate)) or os.path.exists(os.path.join(scripts, candidate + ".txt")):
        candidate = f"{name}-{n}"
        n += 1
    return candidate


_TITLE_FILLER = {"the", "a", "an", "of", "and", "in", "on", "at", "to", "for", "with", "from", "by", "is", "was",
                 "his", "her", "my", "our", "their", "its", "that", "this"}


def latin_title(title, n=32):
    """'The Clink of the Steel Tiffin' -> 'Clink-Steel-Tiffin': Latin letters only, filler words dropped, Windows-safe"""
    import unicodedata
    text = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode()
    words = re.findall(r"[A-Za-z0-9]+", text.replace("'", ""))
    out = ""
    for w in [w for w in words if w.lower() not in _TITLE_FILLER] or words:
        w = w[:1].upper() + w[1:]
        if out and len(out) + 1 + len(w) > n:
            break
        out = f"{out}-{w}" if out else w[:n]
    return out


def title_name(ctx, series, current, titles):
    """After the script step: rename the episode from its placeholder (<Series>-<MMDD-HHMM> or the topic) to
    <Series>-<short English title>, -2, -3 when taken. Renames the episode folder; returns the new name or ''."""
    meta = ((ctx.get("job") or {}).get("params") or {}).get("_experimental_workspace") or {}
    ep = meta.get("episode_dir") or ""
    if not ep or os.path.basename(os.path.normpath(ep)) != current or not os.path.isdir(ep):
        return ""
    short = next((x for x in (latin_title(t) for t in titles) if x), "")
    if not short:
        return ""
    prefix = SERIES_PREFIX.get(series, str(series).title())
    name = f"{prefix}-{short}"[:48].rstrip("-.")
    if name.lower() == current.lower():
        return ""
    cfg = ctx["cfg"]
    parent = os.path.dirname(os.path.normpath(ep))
    scripts = cfg.get("scripts_dir") or os.path.join(cfg.get("media_dir", ""), "scripts")
    candidate, n = name, 2
    while (os.path.exists(os.path.join(parent, candidate)) or os.path.exists(os.path.join(scripts, candidate + ".txt"))
           or os.path.exists(os.path.join(scripts, candidate + ".story.json"))):
        candidate = f"{name}-{n}"
        n += 1
    try:
        os.rename(ep, os.path.join(parent, candidate))
    except OSError as exc:
        ctx["say"](f"   kept the name {current}: the episode folder could not be renamed ({exc})")
        return ""
    return candidate


def prepare(kind, body, cfg, job_id):
    """Build an isolated job config and plan without changing the stable CFG."""
    from tools import plan, slug

    import tinker

    b = dict(body)
    base = initialize(cfg)
    L = cfg.get("layout") or {}
    # Tinker settings: this job keeps the snapshot taken now, also after a hub restart.
    snap = tinker.snapshot(cfg)
    if snap:
        b["_tinker"] = snap
    series = slug(b.get("genre") or b.get("series") or ("sukoon" if kind == "episode" else "Other"))
    try:                                   # which prompt / model versions this run uses, for its ratings later
        import feedback
        b["_prompt_versions"] = feedback.prompt_versions(cfg, snap, b.get("genre") or "")
    except Exception as exc:
        b["_prompt_versions"] = {"error": str(exc)}
    episode_kinds = {"episode", "genre", "horror", "experimental", "voice", "pictures", "image", "slideshow", "studio"}
    episode_dir = ""
    genre_run = kind in ("genre", "horror")
    reused = ""                            # a genre tool run (scenes / pictures / video) on a voice that already exists
    if kind in episode_kinds:
        if genre_run:
            stages = set(b.get("stages") or ["script", "voice"])
            if not stages & {"script", "voice"}:
                reused = _episode_of_narration(base, L, series, slug(b.get("name") or ""))
            if reused:
                episode_dir = reused
            else:                          # short name at queue time; renamed from the story title after the script step
                if "script" in stages and not str(b.get("name") or "").strip() and not str(b.get("script") or "").strip():
                    b["auto_name"] = True      # no typed name: horror_story renames to <Series>-<English title>
                b["name"] = _short_name(base, L, series, b)
                episode_dir = os.path.join(base, L.get("experimental_series", "Series"), series, L.get("experimental_episode_collection", "Episodes"), b["name"])
        else:
            original = b.get("name") or b.get("title") or b.get("topic") or kind
            short = slug(original)
            # The job id makes every run its own durable episode folder.
            episode_name = short if kind == "experimental" else f"{job_id}_{short}"
            b["name"] = episode_name
            episode_dir = os.path.join(base, L.get("experimental_series", "Series"), series, L.get("experimental_episode_collection", "Episodes"), episode_name)
            _make_episode(episode_dir, L, job_id, kind, series, b)

    job_cfg = tinker.apply_models(cfg, snap)
    job_cfg["experimental_mode"] = True
    job_cfg["experimental_root"] = base
    job_cfg["media_dir"] = os.path.join(base, L.get("experimental_library", "Library"))
    job_cfg["episodes_dir"] = os.path.join(base, L.get("experimental_series", "Series"), series, L.get("experimental_episode_collection", "Episodes")) if episode_dir else os.path.join(base, L.get("experimental_series", "Series"), "_Loose")
    job_cfg["sukoonvoice_media"] = os.path.join(episode_dir, L.get("experimental_narration", "02_Narration")) if episode_dir else os.path.join(base, "Library", "Narration")
    os.makedirs(job_cfg["media_dir"], exist_ok=True)
    job_cfg["layout"] = L
    for alias, key in (("scripts_dir", "scripts"), ("pictures_dir", "pictures"), ("videos_dir", "videos"), ("images_dir", "images"), ("temporary_dir", "temporary")):
        job_cfg[alias] = os.path.join(job_cfg["media_dir"], L.get(key, {"temporary": "_tmp"}.get(key, key)))
    for name in (L.get("scripts", "scripts"), L.get("pictures", "pictures"), L.get("videos", "videos"), L.get("images", "images"), L.get("temporary", "_tmp")):
        os.makedirs(os.path.join(job_cfg["media_dir"], name), exist_ok=True)

    try:
        tinker.enter(snap)
        plan = plan(kind, b, job_cfg)
    finally:
        if snap:
            tinker.leave()
    if genre_run and episode_dir:
        # made after planning, so the planner's own name check does not see this folder and add " (2)"
        _make_episode(episode_dir, L, job_id, kind, series, b, "input_" + job_id + ".json" if reused else "input.json")
        if reused:                         # the planner puts Output under Episodes\<voice name>: keep it in this episode
            plan = _replace_paths(plan, os.path.join(job_cfg["episodes_dir"], slug(b.get("name") or "")) + os.sep,
                                  episode_dir + os.sep)
    if kind == "experimental" and b.get("narration_stack_trial", True):
        # Experimental-only diagnostic stage. Keep the stable planner and voice engine untouched.
        for index, step in enumerate(plan.get("steps", [])):
            if step.get("fn") == "sv_speak":
                plan["steps"].insert(index + 1, {
                    "fn": "exp_audio_review",
                    "label": "experimental narration stack: per-line audio signal review",
                    "args": {"name": b.get("name", ""), "series": series,
                             "director": "writer mode: {}; configured local model: {}".format(
                                 b.get("writer") or "auto", cfg.get("llm") or "not set")},
                })
                break
    _feedback_steps(plan, b, series)
    if episode_dir:
        # Route generated cover art and scene assets into the episode workspace.
        old = job_cfg["pictures_dir"]
        new = os.path.join(episode_dir, L.get("experimental_artwork", "03_Artwork"))
        plan = _replace_paths(plan, old, new)
        old_runs = os.path.join(job_cfg["media_dir"], "experimental_runs")
        run_store = os.path.join(episode_dir, L.get("experimental_input", "00_Input"), "ExperimentalRun")
        plan = _replace_paths(plan, old_runs, run_store)
        if kind == "image":
            art_dir = os.path.join(episode_dir, L.get("experimental_artwork", "03_Artwork"))
            for step in plan.get("steps", []):
                args = step.get("args") or {}
                if args.get("out"):
                    args["out"] = art_dir
            plan["outputs"] = [art_dir]
        elif kind == "slideshow":
            old_video = job_cfg["videos_dir"]
            out_dir = os.path.join(episode_dir, L.get("episode_output", "Output"))
            plan = _replace_paths(plan, old_video, out_dir)
            plan["outputs"] = [out_dir]
        plan["outputs"] = [new if p == old else p for p in plan.get("outputs", [])]
    if genre_run and b.get("replan"):      # Video page "Plan the scenes and paint the pictures again"
        steps = plan.get("steps", [])
        video = next((s["args"].get("out", "") for s in steps if s.get("fn") == "horror_video"), "")
        for s in steps:
            if s.get("fn") == "horror_scenes":
                s["args"]["replan"] = True
                s["args"]["video"] = video
    metadata = {"root": base, "series": series, "episode_dir": episode_dir,
                "media_dir": job_cfg["media_dir"], "sukoonvoice_media": job_cfg["sukoonvoice_media"], "layout": L,
                "log": os.path.join(base, L.get("experimental_activity", "Activity"), L.get("logs", "Logs"), f"job_{job_id}.log")}
    b["_experimental_workspace"] = metadata
    job_cfg["_experimental_workspace"] = metadata
    return plan, b, job_cfg


def exp_audio_review(ctx, name, series="", director=""):
    """Write transparent signal diagnostics for an Experimental narration; never auto-grade or alter audio."""
    from tools import narration_dir

    cfg = ctx["cfg"]
    narration = narration_dir(cfg, name, existing=True)
    rows = []
    for path in sorted(glob.glob(os.path.join(narration, "*.wav"))):
        try:
            with wave.open(path, "rb") as wav:
                channels, width, rate, frames = (wav.getnchannels(), wav.getsampwidth(),
                                                  wav.getframerate(), wav.getnframes())
                raw = wav.readframes(frames)
            if width != 2 or not frames:
                rows.append({"file": os.path.basename(path), "status": "skipped",
                             "reason": "signal report currently reads 16-bit PCM WAV only"})
                continue
            samples = array.array("h")
            samples.frombytes(raw)
            if __import__("sys").byteorder != "little":
                samples.byteswap()
            peak = max((abs(v) for v in samples), default=0)
            rms = (sum(v * v for v in samples) / max(1, len(samples))) ** 0.5
            silent = sum(abs(v) <= 32 for v in samples) / max(1, len(samples))
            edge_start = abs(samples[0]) / 32768 if samples else 0
            edge_end = abs(samples[-1]) / 32768 if samples else 0
            rows.append({"file": os.path.basename(path), "duration_s": round(frames / rate, 2),
                         "sample_rate": rate, "channels": channels,
                         "peak_dbfs": round(20 * __import__("math").log10(max(1, peak) / 32768), 1),
                         "rms_dbfs": round(20 * __import__("math").log10(max(1, rms) / 32768), 1),
                         "clipped_sample_pct": round(100 * sum(abs(v) >= 32760 for v in samples) / len(samples), 4),
                         "near_silence_pct": round(100 * silent / len(samples), 2),
                         "start_edge_pct_full_scale": round(100 * edge_start, 2),
                         "end_edge_pct_full_scale": round(100 * edge_end, 2)})
        except (OSError, EOFError, wave.Error, struct.error) as exc:
            rows.append({"file": os.path.basename(path), "status": "skipped", "reason": str(exc)})

    report = {
        "schema": 1,
        "stack": {
            "tts_active": "Existing HubGPT Voice / Chatterbox series voice",
            "tts_candidate": "IndicF5 (not wired or installed in this checkout)",
            "speech_verification": "Existing Hindi Whisper check inside the voice retry loop",
            "faster_whisper": "Used by the video engine; not used here as a second speech verdict",
            "story_director": director,
            "audio_evaluator": "Signal measurements below; listen to judge naturalness, emotion, and pacing",
            "correction_controller": "Existing pronunciation/stutter retakes; signal measurements do not trigger edits",
            "series": series,
        },
        "interpretation": "Measurements are diagnostic cues, not a naturalness score or automatic pass/fail.",
        "files": rows,
    }
    out = os.path.join(narration, "experimental_audio_review.json")
    os.makedirs(narration, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    ctx["say"]("   signal review: {} WAV file(s); no audio was modified".format(len(rows)))
    ctx["say"]("   report: " + out)
    return True


def _feedback_steps(plan, b, series):
    """Experimental genre runs: effects placed at the spoken word, then the AI self-review after the video."""
    steps = plan.get("steps", [])
    if any(s.get("fn") == "horror_sound" for s in steps) and b.get("sound_words", True):
        # 17 Oct: 'ठक... ठक...' in the script is heard as an effect, not read aloud
        for index, step in enumerate(steps):
            if step.get("fn") == "sv_speak" and not (step.get("args") or {}).get("redo"):
                a = step.get("args") or {}
                steps.insert(index, {"fn": "exp_sound_words",
                                     "label": "sound words in the script (ठक... ट्रिंग...) become effects, not speech",
                                     "args": {"name": a.get("name", ""), "genre": b.get("genre") or series}})
                break
        for index, step in enumerate(steps):
            if step.get("fn") == "horror_sound":
                a = step.get("args") or {}
                steps.insert(index, {"fn": "exp_sound_word_cues",
                                     "label": "effects where the script had sound words",
                                     "args": {"name": a.get("name", ""), "pictures": a.get("pictures", "")}})
                break
    for index, step in enumerate(steps):
        if step.get("fn") == "horror_sound" and b.get("word_timed_sfx", True):
            a = step.get("args") or {}
            steps.insert(index, {"fn": "exp_cue_times",
                                 "label": "sound effects timed to the spoken words (Whisper word times)",
                                 "args": {"name": a.get("name", ""), "pictures": a.get("pictures", ""),
                                          "genre": a.get("genre", "")}})
            break
    for index, step in enumerate(steps):
        if step.get("fn") == "horror_sound" and b.get("emotion_music", True):
            a = step.get("args") or {}
            import emotion_music
            if emotion_music.settings(a.get("genre", "") or series).get("on"):
                steps.insert(index, {"fn": "exp_emotion_map",
                                     "label": "emotion map: how heavy each part of the story is (music sections, turns)",
                                     "args": {"name": a.get("name", ""), "pictures": a.get("pictures", ""),
                                              "genre": a.get("genre", "") or series}})
            break
    for index, step in enumerate(steps):
        if step.get("fn") == "horror_video":
            a = step.get("args") or {}
            steps.insert(index + 1, {"fn": "exp_after_video",
                                     "label": "AI self-review of the video (pre-fills ratings you can change)"
                                     if b.get("self_review", True) else "remember what this video was made from",
                                     "args": {"video": a.get("out", ""), "narration": a.get("narration", ""),
                                              "pictures": a.get("pictures", ""), "genre": a.get("genre", series),
                                              "name": a.get("name", ""), "review": bool(b.get("self_review", True))}})
            break


def exp_cue_times(ctx, name, pictures, genre=""):
    import self_review
    try:
        return self_review.cue_times(ctx, name, pictures, genre)
    except Exception as exc:                # never stop a run over this; horror_sound keeps the old estimate
        ctx["say"]("   (word timing skipped: {})".format(exc))
        return True


def exp_sound_words(ctx, name, genre=""):
    """before the voice: sound words out of the spoken script (sound_words.py), the script as it was kept in _old"""
    import sound_words
    try:
        return sound_words.step(ctx, name, genre)
    except Exception as exc:                # never stop a run over this; the script is read as written
        ctx["say"]("   (sound words not checked: {})".format(exc))
        return True


def exp_sound_word_cues(ctx, name, pictures):
    import sound_words
    try:
        return sound_words.cues(ctx, name, pictures)
    except Exception as exc:
        ctx["say"]("   (sound word effects skipped: {})".format(exc))
        return True


def exp_emotion_map(ctx, name, pictures, genre=""):
    """EMOTION_MUSIC_PLAN.md step 1: writes emotion_map.json next to shots.json; the sound is not changed yet"""
    import emotion_music
    try:
        return emotion_music.step(ctx, name, pictures, genre)
    except Exception as exc:                # never stop a run over this; the music stays as before
        ctx["say"]("   (emotion map skipped: {})".format(exc))
        return True


def exp_after_video(ctx, video, narration, pictures, genre="", name="", review=True):
    """write Output/review_sources.json (what the video was made from), then the optional self-review"""
    job = ctx.get("job") or {}
    params = job.get("params") or {}
    req = {k: v for k, v in params.items() if not k.startswith("_")}
    try:
        import feedback
        settings = feedback._settings_of(dict(req, _tinker=params.get("_tinker")))
    except Exception:
        settings = {}
    out_dir = os.path.dirname(video)
    os.makedirs(out_dir, exist_ok=True)
    src = {"job_id": job.get("id"), "series": genre, "name": name, "video": video, "narration": narration,
           "pictures": pictures, "settings": settings, "prompt_versions": params.get("_prompt_versions") or {},
           "made": time.strftime("%Y-%m-%d %H:%M:%S")}
    tmp = os.path.join(out_dir, "review_sources.json.part")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(src, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(out_dir, "review_sources.json"))
    if not review or not os.path.isfile(video):
        return True
    return exp_self_review(ctx, video, narration, pictures, genre, name)


def exp_self_review(ctx, video, narration="", pictures="", genre="", name="", checks=None):
    import self_review
    try:
        return self_review.run(ctx, video, narration, pictures, genre, name, checks) is not False
    except Exception as exc:                # a failed review never fails the video
        import traceback
        ctx["say"]("   (self-review stopped: {})\n{}".format(exc, traceback.format_exc()[-800:]))
        return True


def exp_sound_fetch(ctx, kind, folder, items, allow_by=False):
    import sound_library
    return sound_library.fetch(ctx, kind, folder, items, allow_by)


def exp_sound_topup(ctx, series="", per=3):
    import sound_library
    return sound_library.topup(ctx, series, per)


def exp_music_topup(ctx, series="", per=2, allow_by=False):
    import sound_library
    return sound_library.topup_music(ctx, series, per, allow_by)


def exp_sound_screen(ctx):
    import audio_screen
    return audio_screen.screen_all(ctx)


def _replace_paths(value, old, new):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_replace_paths(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _replace_paths(v, old, new) for k, v in value.items()}
    return value


def job_config(cfg, params):
    meta = (params or {}).get("_experimental_workspace") or {}
    if not meta:
        return cfg, None
    import tinker
    out = tinker.apply_models(cfg, (params or {}).get("_tinker"))
    out.update({"experimental_mode": True, "experimental_root": meta.get("root"),
                "media_dir": meta.get("media_dir"), "sukoonvoice_media": meta.get("sukoonvoice_media"),
                "layout": meta.get("layout") or cfg.get("layout", {})})
    for alias, key in (("scripts_dir", "scripts"), ("pictures_dir", "pictures"), ("videos_dir", "videos"),
                       ("images_dir", "images"), ("temporary_dir", "temporary")):
        out[alias] = os.path.join(out["media_dir"], layout(out, key, "_tmp" if key == "temporary" else key))
    if meta.get("episode_dir"):
        out["episodes_dir"] = os.path.dirname(meta["episode_dir"])
    return out, meta.get("media_dir")


def organize(job):
    meta = (job.get("params") or {}).get("_experimental_workspace") or {}
    L = meta.get("layout") or {}
    ep = meta.get("episode_dir")
    if not ep or not os.path.isdir(ep):
        return
    name = os.path.basename(ep)
    candidates = [os.path.join(meta.get("media_dir", ""), L.get("scripts", "scripts"), name + ".txt")]
    script = next((p for p in candidates if os.path.isfile(p)), "")
    if script:
        os.makedirs(os.path.join(ep, L.get("experimental_script", "01_Script")), exist_ok=True)
        shutil.copy2(script, os.path.join(ep, L.get("experimental_script", "01_Script"), "script.txt"))
        meta_path = os.path.splitext(script)[0] + ".story.json"
        if os.path.isfile(meta_path):
            shutil.copy2(meta_path, os.path.join(ep, L.get("experimental_script", "01_Script"), "story.json"))
    # The configured episode output directory remains the final video/reel destination.
    with open(os.path.join(ep, L.get("experimental_input", "00_Input"), "activity.json"), "w", encoding="utf-8") as f:
        json.dump({"id": job.get("id"), "title": job.get("title"), "status": job.get("status"),
                   "created": job.get("created"), "ended": job.get("ended"), "outputs": job.get("outputs", []),
                   "notes": job.get("notes", [])}, f, ensure_ascii=False, indent=2)


def sync_jobs(jobs, cfg):
    base = initialize(cfg)
    selected = [j for j in jobs if (j.get("params") or {}).get("_experimental_workspace")]
    path = os.path.join(base, layout(cfg, "experimental_activity", "Activity"), "jobs.json")
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        # steps are kept: after a hub restart this file is read back and unfinished jobs carry on from it
        json.dump(selected[-300:], f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def is_experimental(job):
    return bool((job.get("params") or {}).get("_experimental_workspace"))


def find_run(name, jobs, cfg):
    for job in jobs:
        meta = (job.get("params") or {}).get("_experimental_workspace") or {}
        ep = meta.get("episode_dir") or ""
        if os.path.basename(ep).casefold() == str(name).casefold():
            return meta
    base = root(cfg)
    for ep in glob.glob(os.path.join(base, layout(cfg, "experimental_series", "Series"), "*", layout(cfg, "experimental_episode_collection", "Episodes"), "*")):
        if os.path.basename(ep).casefold() == str(name).casefold():
            return {"episode_dir": ep, "root": base, "media_dir": os.path.join(base, layout(cfg, "experimental_library", "Library")),
                    "sukoonvoice_media": os.path.join(ep, layout(cfg, "experimental_narration", "02_Narration"))}
    return {}


def library(cfg):
    """Return the library shape consumed by the Experimental page from its own workspace."""
    base = initialize(cfg)
    L = cfg.get("layout") or {}
    scripts, narrations, pictures, videos, episodes = [], [], [], [], []
    script_paths = set(glob.glob(os.path.join(base, layout(cfg, "experimental_library", "Library"), layout(cfg, "experimental_scripts", "Scripts"), "*.txt")))
    for ep in glob.glob(os.path.join(base, layout(cfg, "experimental_series", "Series"), "*", layout(cfg, "experimental_episode_collection", "Episodes"), "*")):
        if not os.path.isdir(ep):
            continue
        n = os.path.basename(ep)
        script = os.path.join(ep, L.get("experimental_script", "01_Script"), "script.txt")
        if not os.path.isfile(script):
            script = os.path.join(ep, layout(cfg, "scripts", "scripts"), n + ".txt")
        if os.path.isfile(script):
            script_paths.add(script)
        nar_parent = os.path.join(ep, layout(cfg, "experimental_narration", "02_Narration"), layout(cfg, "narrations", "narrations"))
        for nd in glob.glob(os.path.join(nar_parent, "*")):
            voice = os.path.join(nd, "narration.wav")
            if os.path.isfile(voice):
                narrations.append({"name": os.path.basename(nd), "dir": nd, "voice": voice,
                                   "music": os.path.join(nd, "narration_music.wav") if os.path.isfile(os.path.join(nd, "narration_music.wav")) else "",
                                   "seconds": 0, "time": os.path.getmtime(voice), "flagged": 0,
                                   "lang": "hi", "script": os.path.join(nd, "script.txt")})
        art = os.path.join(ep, layout(cfg, "experimental_artwork", "03_Artwork"))
        ims = sorted(glob.glob(os.path.join(art, "**", "*.png"), recursive=True))
        if ims:
            pictures.append({"name": n, "dir": art, "images": ims[:60], "time": max(os.path.getmtime(p) for p in ims), "shots": []})
        output = os.path.join(ep, layout(cfg, "episode_output", "Output"))
        vids = [p for p in glob.glob(os.path.join(output, "*.mp4")) if not p.endswith(".part.mp4")]
        reels = glob.glob(os.path.join(output, layout(cfg, "episode_reels", "reels"), "*.mp4"))
        cover = next(iter(glob.glob(os.path.join(ep, layout(cfg, "experimental_artwork", "03_Artwork"), "*.*"))), "")
        for p in vids:
            videos.append({"file": p, "kind": "episode", "time": os.path.getmtime(p)})
        for p in reels:
            videos.append({"file": p, "kind": "reel", "time": os.path.getmtime(p)})
        episodes.append({"name": n, "dir": ep, "video": vids[0] if vids else "", "reels": reels, "art": cover,
                         "narration": os.path.join(ep, layout(cfg, "experimental_narration", "02_Narration"), layout(cfg, "narrations", "narrations"), n),
                         "script": script if os.path.isfile(script) else "", "checked": False,
                         "lang": "hi", "time": max([os.path.getmtime(p) for p in vids + reels] or [os.path.getmtime(ep)])})
    for f in sorted(script_paths, key=os.path.getmtime, reverse=True)[:300]:
        with open(f, encoding="utf-8", errors="replace") as src:
            head = src.read(600)
        scripts.append({"name": os.path.basename(f)[:-4], "file": f, "time": os.path.getmtime(f),
                        "preview": " ".join(x for x in head.splitlines() if x.strip())[:160]})
    loose_images = glob.glob(os.path.join(base, layout(cfg, "experimental_library", "Library"), layout(cfg, "images", "images"), "*", "*"))
    return {"scripts": scripts, "narrations": sorted(narrations, key=lambda x: -x["time"]),
            "picture_sets": sorted(pictures, key=lambda x: -x["time"]),
            "images": loose_images[:120], "artwork": [], "videos": sorted(videos, key=lambda x: -x["time"]),
            "episodes": sorted(episodes, key=lambda x: -x["time"])}


def library_stamp(cfg):
    base = initialize(cfg)
    files = glob.glob(os.path.join(base, layout(cfg, "experimental_series", "Series"), "*", layout(cfg, "experimental_episode_collection", "Episodes"), "**", "*"), recursive=True)
    files += glob.glob(os.path.join(base, layout(cfg, "experimental_library", "Library"), layout(cfg, "experimental_scripts", "Scripts"), "*.txt"))
    valid = [p for p in files if os.path.isfile(p)]
    latest = max((os.path.getmtime(p) for p in valid), default=0)
    return {"stamp": f"{len(valid)}-{int(latest)}"}
