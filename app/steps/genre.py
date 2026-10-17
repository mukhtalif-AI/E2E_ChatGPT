"""steps/genre.py - genre story, sound, video and reels. Moved out of tools.py on 16 Oct 2026 (TOOLS_SPLIT_PLAN.md step 4k).

Do not import this file directly: import tools and use tools.<name>. Names that stay in tools.py, and names other files swap
(feedback, narrator, Tinker), are written as tools.X here on purpose.
"""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave

import tools


# ------------------------------------------------------------------ भय (Bhay) horror stories
def horror_story(ctx, name, topic="", minutes=15, notes="", engine="auto", recipe=None, genre="bhay", level="standard", auto_name=False):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    script, info = story.write_genre(cfg, genre, say, topic, float(minutes or 15), notes, engine, recipe, ctx["stopped"],
                                     level)
    if story.nwords(script) < 150:
        raise RuntimeError("the story came out too short - try again")
    summ = story.summarise(cfg, script, say)
    if auto_name and cfg.get("experimental_mode"):
        # Experimental: <Series>-<short English title>; the episode folder is renamed now, before anything is in it
        import experimental_workspace
        final_name = experimental_workspace.title_name(ctx, genre, name, [summ.get("title_en"),
                                                                          info["recipe"].get("topic"), topic])
        if final_name and final_name != name:
            tools._rename_job_values(ctx.get("job") or {}, name, final_name)
            for k, v in list(cfg.items()):   # this run's paths (narration folder) point into the episode folder
                if isinstance(v, str) and name in v:
                    cfg[k] = v.replace(name, final_name)
            name = final_name
            say(f"   episode named from the story title: {name}")
    elif auto_name:
        prefix = {"bhay": "Bhay", "rahasya": "Rahasya", "kisse": "Kisse", "romanch": "Romanch",
                  "kareeb": "Kareeb", "gyaan": "Gyaan"}.get(genre, genre.title())
        title = summ.get("title_en") or summ.get("title_hi") or info["recipe"].get("topic") or topic or "Story"
        final_name = tools.unique_genre_name(cfg, ctx["media"], genre, f"{prefix} - {tools.slug(title, 50)}")
        old_name = name
        if final_name != old_name:
            tools._rename_job_values(ctx.get("job") or {}, old_name, final_name)
            name = final_name
            say(f"   episode folder named from the generated title: {name}")
    p = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
    open(p, "w", encoding="utf-8").write(script)
    entry = {"name": name, "date": time.strftime("%Y-%m-%d %H:%M"), "genre": "horror" if genre == "bhay" else genre,
             "topic": info["recipe"]["topic"],
             "recipe": info["recipe"], "model": info["model"], "words": story.nwords(script), "summary": summ,
             "level": info.get("level") or level}
    story.add_ledger(cfg, entry)
    json.dump(entry, open(p[:-4] + ".story.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if summ.get("title_hi"):
        say(f"   title: {summ.get('title_hi')}  /  {summ.get('title_en', '')}")
    for ln in [l for l in script.splitlines() if l != "(...)"][:4]:
        say("      " + ln)
    say(f"   story saved: {p}  ({story.nwords(script)} words)")
    return True


def write_horror_stories(ctx, n=3, topics=None, minutes=15, engine="auto", genre="bhay", level="standard"):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    made = 0
    for i in range(int(n)):
        if ctx["stopped"]():
            return False
        topic = (topics or [])[i] if topics and i < len(topics) else ""
        say(f"\n   --- story {i + 1}/{n}")
        try:
            script, info = story.write_genre(cfg, genre, say, topic, float(minutes), "", engine, None, ctx["stopped"], level)
        except Exception as e:
            say(f"   !! {e}"); continue
        if story.nwords(script) < 150:
            say("   (too short - skipped)"); continue
        summ = story.summarise(cfg, script, say)
        pre = {"bhay": "Bhay", "rahasya": "Rahasya", "kisse": "Kisse", "romanch": "Romanch", "kareeb": "Kareeb",
               "gyaan": "Gyaan"}.get(genre, genre)
        name = f"{pre} - {tools.slug(summ.get('title_en') or info['recipe']['topic'], 50)}"
        p = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt")
        if os.path.exists(p):
            p = p[:-4] + time.strftime("_%H%M%S.txt")
        open(p, "w", encoding="utf-8").write(script)
        entry = {"name": os.path.basename(p)[:-4], "date": time.strftime("%Y-%m-%d %H:%M"),
                 "genre": "horror" if genre == "bhay" else genre,
                 "topic": info["recipe"]["topic"], "recipe": info["recipe"], "model": info["model"],
                 "words": story.nwords(script), "summary": summ, "status": "written (no voice yet)",
                 "level": level}
        story.add_ledger(cfg, entry)
        json.dump(entry, open(p[:-4] + ".story.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        say(f"   saved: {os.path.basename(p)}  ({story.nwords(script)} words)  {summ.get('title_hi', '')}")
        made += 1
    say(f"\n   {made} stories in {os.path.join(ctx['media'], 'scripts')} - open one on the भय page")
    return made > 0


def _horror_track(cfg, folders=("Dark", "Tension", "Eerie")):
    """a music bed from the series' mood folders in BGM, the least used one first"""
    md = cfg.get("music_dir") or ""
    cands = [f for sub in folders for f in glob.glob(os.path.join(md, sub, "*.mp3"))
             if "crate" not in f.lower()]
    up = os.path.join(cfg["media_dir"], "bhay_music_used.json")
    try:
        used = json.load(open(up, encoding="utf-8"))
    except Exception:
        used = {}
    if not cands:
        return ""
    skip = float(cfg.get("music_harsh_skip", 19))         # the most piercing tracks are never picked automatically
    ok = [f for f in cands if tools.harshness(cfg, f) < skip] or cands
    t = min(ok, key=lambda f: (used.get(f, 0), random.random()))
    used[t] = used.get(t, 0) + 1
    json.dump(used, open(up, "w", encoding="utf-8"), indent=1)
    return t


def _usable_music(ctx, track, folders, music_db=None):
    """never a silent bed: a track you cannot hear under the voice is swapped; with none left, a softened library
    track (speech band dipped, 4 dB lower) with a clear warning in the job log"""
    cfg, say = ctx["cfg"], ctx["say"]

    def audible(t):
        try:
            import audio_screen
            r = audio_screen.screen(cfg, audio_screen.original_of(cfg, t), "music")
            vb = (r.get("m") or {}).get("voice_band")
            return (vb is None or vb >= float(audio_screen.LIMITS["music"].get("voice_band_min", 0))), vb
        except Exception:
            return True, None
    if track and os.path.exists(track):
        ok, vb = audible(track)
        if ok:
            return track, music_db
        say(f"   ⚠ WARNING: music '{os.path.basename(track)}' is almost only deep rumble ({(vb or 0) * 100:.1f}% above "
            "300 Hz) - it would sound like no music under your voice; picking another track")
        for _ in range(4):
            alt = tools._horror_track(cfg, folders)
            if alt and alt != track and os.path.exists(alt) and audible(alt)[0]:
                return alt, music_db
    say(f"   ⚠ WARNING: no usable music in the {', '.join(folders)} mood folders (add or rate tracks on the Sounds page)")
    fb = tools.auto_track(cfg)
    if fb and os.path.exists(fb):
        try:
            import audio_screen
            fb = audio_screen.treated(cfg, fb, "music")
        except Exception:
            pass
        base = abs(float(music_db)) if music_db not in (None, "") else \
            abs(float((tools.load_audio_index(cfg).get("shape") or {}).get("story_db", -18.0)))
        say(f"   ⚠ using '{os.path.basename(fb)}' from your library instead, softened and 4 dB lower")
        return fb, base + 4
    say("   ⚠ WARNING: the music library has no track at all - this episode has no background music")
    return track, music_db


def horror_sound(ctx, name, pictures, track="", genre="bhay", tail="1min", music_db=None):
    """music bed (shaped like your episodes, a bit lower) + per-scene ambience loops + rare hits -> narration_music.wav"""
    import numpy as np
    cfg, say = ctx["cfg"], ctx["say"]
    if _is_exp(cfg) and not str(tail).startswith("total:"):  # Experimental, 'Whole video' 0: the voice + a short ending
        tail = f"secs:{_end_seconds(cfg)}"
    folders = tools.GENRES.get(genre, tools.GENRES["bhay"])["music"]
    chosen = bool(track)                                    # a track picked on the form: emotion music keeps it alone
    track = track or tools._horror_track(cfg, folders)
    track, music_db = _usable_music(ctx, track, folders, music_db)
    say(f"   music: {os.path.basename(track)}")
    tools._series_note(cfg, name, genre=genre, track=track, pictures=pictures)
    emo = None                                              # Experimental: music that follows the emotion map
    if _is_exp(cfg):
        try:
            import emotion_music
            emo = emotion_music.mix(ctx, name, pictures, genre, track, tail, music_db, chosen=chosen)
        except Exception as e:
            say(f"   (emotion music skipped: {e} - one track as before)")
            emo = None
    if emo is not None and not emo.get("ok"):
        return False
    if emo is None and not tools.shape_mix(ctx, name, track, tail, music_db=music_db):
        return False
    bed = (lambda a, b: __import__("emotion_music").bed_at(emo, sr, a, b)) if emo else (lambda a, b: 1.0)
    d = tools.narration_dir(cfg, name, existing=True)
    lead = float(json.load(open(os.path.join(d, "music_shape.json"), encoding="utf-8")).get("lead_s", 0))
    mix_p = os.path.join(d, "narration_music.wav")
    sr = 48000
    if not os.path.exists(os.path.join(pictures or "", "shots.json")):
        say("   (no scenes planned yet - music only; ambience + effects come with the video)")
        return True
    mix = tools._decode(cfg, mix_p, sr, 2).copy()
    shots = json.load(open(os.path.join(pictures, "shots.json"), encoding="utf-8"))["shots"]
    voice_db = 20 * np.log10(float(np.sqrt(np.mean(mix.astype(np.float64) ** 2))) + 1e-9)
    added = 0
    for i, s in enumerate(shots):
        a = int((lead + float(s["start"])) * sr)
        b = int((lead + float(shots[i + 1]["start"] if i + 1 < len(shots) else s["end"] + 4)) * sr)
        b = min(b, len(mix))
        if s.get("ambience") and os.path.exists(s["ambience"]) and b > a:
            amb = tools._decode(cfg, s["ambience"], sr, 2)
            if len(amb) > sr:
                need = b - a
                rep_ = np.concatenate([amb] * (need // len(amb) + 1))[:need]
                g = 10 ** ((voice_db - 22 - 20 * np.log10(float(np.sqrt(np.mean(amb ** 2))) + 1e-9)) / 20)
                env = np.minimum(1, np.minimum(np.arange(need) / (2 * sr), (need - np.arange(need)) / (2 * sr)))[:, None]
                mix[a:b] += (rep_ * g * env * bed(a, b)).astype(np.float32)
                added += 1
        if s.get("hit") and os.path.exists(s["hit"]) and i > 0:
            h = tools._decode(cfg, s["hit"], sr, 2)[: 6 * sr]
            g = 10 ** ((voice_db - 9 - 20 * np.log10(float(np.sqrt(np.mean(h ** 2))) + 1e-9)) / 20)
            st = max(0, a - int(0.3 * sr))
            e = min(len(mix), st + len(h))
            mix[st:e] += (h[:e - st] * g * bed(st, e)).astype(np.float32)
            added += 1
    tim = json.load(open(os.path.join(d, "timing.json"), encoding="utf-8")) if os.path.exists(os.path.join(d, "timing.json")) else []
    norm = lambda t: re.sub(r"[^\w\s]", "", str(t)).split()
    cue_db = {"bhay": -10, "rahasya": -11, "kisse": -13, "romanch": -15, "kareeb": -16}.get(genre, -14)
    used, ncue = [], 0
    for s in shots:
        for c in s.get("cues") or []:
            w = norm(c.get("words"))
            if not w or not os.path.exists(c.get("file", "")):
                continue
            at = c.get("at")                            # Experimental runs: already timed at the spoken word
            for x in ([] if at is not None else tim):   # the sentence that has these words, and where in it they are
                if float(x["end"]) < float(s["start"]) - 1 or float(x["start"]) > float(s["end"]) + 1:
                    continue
                tw = norm(x["text"])
                for j in range(len(tw)):
                    if tw[j:j + len(w)] == w:
                        at = float(x["start"]) + (float(x["end"]) - float(x["start"])) * j / max(1, len(tw))
                        break
                if at is not None:
                    break
            if at is None:
                continue
            h = tools._decode(cfg, c["file"], sr, 2)[: 8 * sr]
            if not len(h):
                continue
            g = 10 ** ((voice_db + cue_db - 20 * np.log10(float(np.sqrt(np.mean(h ** 2))) + 1e-9)) / 20)
            fade = np.minimum(1, (len(h) - np.arange(len(h))) / (0.4 * sr))[:, None]
            st = int((lead + at) * sr)
            e = min(len(mix), st + len(h))
            if emo and float(np.mean(bed(st, st + 1))) < 0.5:   # inside a drop before a heavy line: keep it silent
                continue
            if e > st:
                mix[st:e] += (h[:e - st] * fade[:e - st] * g * bed(st, e)).astype(np.float32)
                used.append(c["file"]); ncue += 1
        used += [x for x in (s.get("ambience"), s.get("hit")) if x]
    if ncue:
        say(f"   {ncue} sound effects placed at the words where they happen")
    try:
        import sfx_lib
        sfx_lib.mark_used(cfg, used, name)
    except Exception:
        pass
    pk = float(np.abs(mix).max()) or 1
    raw = os.path.join(d, "_bhay.f32")
    open(raw, "wb").write((mix * min(1.0, 0.97 / pk)).astype("<f4").tobytes())
    os.replace(mix_p, os.path.join(d, "narration_music_plain.wav"))
    if emo:                                                 # one gain for the whole file: the drops stay silent
        import emotion_music
        rc = 0 if emotion_music.loudnorm2(cfg, raw, sr, 2, mix_p, ctx["run_cmd"]) else 1
    else:
        rc = ctx["run_cmd"]([cfg["ffmpeg"], "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", raw,
                             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(sr), "-c:a", "pcm_s16le", mix_p])
    try:
        os.remove(raw)
    except OSError:
        pass
    say(f"   {added} ambience / sound effects placed -> {mix_p}")
    return rc == 0


def _subtitle_text_for_timing(text, timing):
    """Use one TXT line per timed phrase when counts match; otherwise split by original phrase size."""
    original = [str(x.get("text") or "") for x in timing]
    rows = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not rows:
        return original
    if len(rows) == len(timing):
        return rows
    words = str(text).split()
    weights = [max(1, len(str(x.get("text") or "").split())) for x in timing]
    if not words or not timing:
        return original
    result, offset = [], 0
    total_weight = sum(weights)
    for i, weight in enumerate(weights):
        remaining = len(words) - offset
        count = remaining if i == len(weights) - 1 else round(len(words) * weight / total_weight)
        if len(words) >= len(weights):
            count = max(1, count)
        count = min(remaining, count)
        result.append(" ".join(words[offset:offset + count]))
        offset += count
    if offset < len(words):
        result[-1] = (result[-1] + " " + " ".join(words[offset:])).strip()
    return result


def _is_exp(cfg):
    return bool(cfg.get("experimental_mode") or cfg.get("_experimental_workspace"))


def _end_seconds(cfg):
    try:
        return max(1.0, float(cfg.get("end_seconds") or tools.END_SECONDS))
    except (TypeError, ValueError):
        return float(tools.END_SECONDS)


def _subscribe_sounds(sr=48000):
    """a soft mouse click and a soft two-tone chime, made from sine waves (no sound file, nothing to license).
    Kept low and mellow (A5 then F5, few overtones, soft attack) so it is not piercing."""
    import numpy as np
    t = np.arange(int(0.04 * sr)) / sr
    click = np.random.default_rng(7).normal(0, 1, len(t)) * np.exp(-t / 0.002) * 0.25 \
        + np.sin(2 * np.pi * 1100 * t) * np.exp(-t / 0.006)
    t = np.arange(int(2.4 * sr)) / sr
    bell = np.zeros(len(t))
    for delay, f0, amp in ((0.0, 880.0, 1.0), (0.22, 698.5, 0.85)):           # A5 then F5: a soft ding-dong
        tt = t - delay
        on = tt >= 0
        for ratio, a, dec in ((1, 1, 1.3), (2.0, 0.18, 0.5), (3.0, 0.05, 0.25)):
            bell[on] += amp * a * np.sin(2 * np.pi * f0 * ratio * tt[on]) * np.exp(-tt[on] / dec) * np.minimum(1, tt[on] / 0.008)
    for _ in range(2):                                                          # gentle low-pass: rounds off the edge
        bell = np.convolve(bell, np.ones(5) / 5, mode="same")
    return (click / np.abs(click).max()).astype(np.float32), (bell / np.abs(bell).max()).astype(np.float32)


def _subscribe_end(ctx, narration, audio, total, lines):
    """Experimental: the Subscribe + bell ending over the last seconds of the video. Its click and bell sounds go into
    a copy of the audio (narration_music_end.wav) -> (plan entry, audio to use); (None, audio) when there is no room"""
    import wave
    import numpy as np
    cfg, say = ctx["cfg"], ctx["say"]
    S = dict(tools.SUBSCRIBE_END)
    last = max([float(x["end"]) for x in lines] or [0.0])
    start = max(last + 2.4, total - S["dur"] - 0.6)          # near the very end; the picture fades out first (1.8 s)
    if start + S["dur"] > total + 0.05:
        say(f"   (no Subscribe ending: only {total - last:.1f} s after the voice; it needs about {S['dur'] + 1.2:.0f} s "
            f"- Tinker > Models > Ending after the voice)")
        return None, audio
    S["start"] = round(start, 3)
    sr = 48000
    try:
        a = tools._decode(cfg, audio, sr, 2).copy()
        click, bell = _subscribe_sounds(sr)
        for at, snd, gain in ((S["sub_click"], click, 0.12), (S["bell_click"], click, 0.12), (S["bell_click"] + 0.08, bell, 0.13)):
            i = int((start + at) * sr)
            j = min(len(a), i + len(snd))
            if j > i:
                a[i:j] += snd[:j - i, None] * gain
        pk = float(np.abs(a).max())
        if pk > 0.98:
            a *= 0.98 / pk
        outp = os.path.join(narration, "narration_music_end.wav")
        with wave.open(outp, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())
    except Exception as e:
        say(f"   (Subscribe sounds skipped: {e}; the animation still plays)")
        return S, audio
    say(f"   Subscribe + bell ending at {int(start // 60)}:{start % 60:04.1f} ({S['dur']:g} s)")
    return S, outp


def horror_video(ctx, name, pictures, narration, out, title="", genre="bhay", subtitle_text=""):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    tools._series_note(cfg, name, genre=genre, pictures=pictures, title=title)
    shots = json.load(open(os.path.join(pictures, "shots.json"), encoding="utf-8"))["shots"]
    lead = float((json.load(open(os.path.join(narration, "music_shape.json"), encoding="utf-8")) or {}).get("lead_s", 0)) \
        if os.path.exists(os.path.join(narration, "music_shape.json")) else 0.0
    audio = tools._audio_of(narration)
    total = tools._wav_len(audio)
    if not title:
        sj = os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".story.json")
        title = ((json.load(open(sj, encoding="utf-8")).get("summary") or {}).get("title_hi", "")) if os.path.exists(sj) else ""
    scenes = []
    for i, s in enumerate(shots):
        f = os.path.join(pictures, s["file"])
        if not os.path.exists(f):
            continue
        st = 0.0 if not scenes else lead + float(s["start"])
        scenes.append({"image": f, "start": st, "end": (lead + float(shots[i + 1]["start"])) if i + 1 < len(shots) else total,
                       "flash": [lead + float(s["start"]) + 1.5] if s.get("lightning") else []})
    for mo in json.load(open(os.path.join(pictures, "shots.json"), encoding="utf-8")).get("moments") or []:
        f = os.path.join(pictures, mo["file"])
        t = lead + float(mo["at"]) - 0.4                   # the close-up comes just as the words are said
        host = next((x for x in scenes if x["start"] < t < x["end"] - 2), None)
        if not os.path.exists(f) or not host:
            continue
        dur = min(6.0, host["end"] - t)
        rest = {"image": host["image"], "start": t + dur, "end": host["end"], "flash": []}
        host["end"] = t
        scenes.append({"image": f, "start": t, "end": t + dur, "flash": [], "moment": True})
        if rest["end"] - rest["start"] >= 2.0:
            scenes.append(rest)
        else:
            scenes[-1]["end"] = rest["end"]
        say(f"   close-up {mo['file']} at {int(t // 60)}:{int(t % 60):02d}")
    scenes.sort(key=lambda x: x["start"])
    tm = json.load(open(os.path.join(narration, "timing.json"), encoding="utf-8"))
    texts = _subtitle_text_for_timing(subtitle_text, tm) if subtitle_text else [x["text"] for x in tm]
    lines = [{"start": x["start"] + lead, "end": x["end"] + lead, "text": texts[i]}
             for i, x in enumerate(tm) if i < len(texts) and texts[i].strip()]
    G = tools.GENRES.get(genre, tools.GENRES["bhay"])
    sub = None
    if _is_exp(cfg) and str(cfg.get("subscribe_end") or "on") != "off":
        sub, audio = _subscribe_end(ctx, narration, audio, total, lines)
    plan = {"audio": audio, "fps": 24, "title": title, "label": G["label"], "mood": G["mood"], "duration": total,
            "scenes": scenes, "lines": lines}
    if sub:
        plan["subscribe"] = sub
    os.makedirs(os.path.dirname(out), exist_ok=True)
    pj = os.path.join(pictures, "video_plan.json")
    json.dump(plan, open(pj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ep = os.path.dirname(os.path.dirname(out))
    try:                                                   # the episode folder: script, title, thumbnail picture
        sp = os.path.join(narration, "script.txt")
        if os.path.exists(sp):
            shutil.copyfile(sp, os.path.join(ep, "script.txt"))
        open(os.path.join(ep, "title.txt"), "w", encoding="utf-8").write((title or name) + "\n")
        if scenes:
            shutil.copyfile(scenes[0]["image"], os.path.join(ep, os.path.basename(ep) + ".png"))
    except OSError:
        pass
    rc = ctx["run_cmd"]([cfg["studio_python"], "-u", "-s", os.path.join(cfg["studio_dir"], "engine", "horror_video.py"),
                         pj, out], cwd=cfg["studio_dir"])
    if rc == 0:
        say(f"   video: {out}")
    return rc == 0


def genre_reels(ctx, name, pictures, out, count=2, length=30, genre="bhay"):
    """9:16 reels from a series video: the AI picks the strongest spoken lines, each reel starts just before one
    (same pictures, camera, sound and subtitles, drawn upright), the line itself is the hook on top"""
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    pj = os.path.join(pictures, "video_plan.json")
    if not os.path.exists(pj):
        raise RuntimeError("make the video first")
    plan = json.load(open(pj, encoding="utf-8"))
    lines = [l for l in plan["lines"] if 4 < len(l["text"].split()) <= 30 and l["start"] > 15
             and l["start"] < plan["duration"] - length - 5]
    if not lines:
        say("   the story is too short for reels"); return True
    n = max(1, min(6, int(count)))
    pick = []
    try:
        t, _ = story.ask(cfg, f"These are spoken lines of a {tools.GENRES[genre]['desc']}. Pick the {n} lines that would "
                              "make someone scrolling at night stop and listen (strong, complete, intriguing, far apart). "
                              'Answer ONLY JSON: {"best": [line numbers]}\n\n'
                              + "\n".join(f"{i}. {l['text'][:200]}" for i, l in enumerate(lines)), "auto", 0.3, True, say=say)
        pick = [int(i) for i in json.loads(t).get("best", []) if 0 <= int(i) < len(lines)]
    except Exception as e:
        say(f"   (AI choice skipped: {e})")
    if len(pick) < n:
        pick += [round(len(lines) * (k + 1) / (n + 1)) for k in range(n)]
    chosen = []
    for i in pick:
        if all(abs(lines[i]["start"] - lines[j]["start"]) > length for j in chosen):
            chosen.append(i)
        if len(chosen) >= n:
            break
    os.makedirs(out, exist_ok=True)
    ok = True
    for k, i in enumerate(sorted(chosen, key=lambda i: lines[i]["start"])):
        l = lines[i]
        a = max(0.0, l["start"] - 1.0)
        hook = re.sub(r"\.{2,}|…", " ", l["text"]).strip(" .।")
        hook = " ".join(hook.split()[:9]) + ("…" if len(hook.split()) > 9 else "")
        rp = dict(plan, window=[a, min(plan["duration"], a + float(length))], size=[1080, 1920], title=hook)
        rj = os.path.join(pictures, f"reel_{k + 1}.json")
        json.dump(rp, open(rj, "w", encoding="utf-8"), ensure_ascii=False)
        f = os.path.join(out, f"reel{k + 1}_{tools.slug(hook.split()[0] if hook else 'reel', 20)}.mp4")
        if os.path.exists(f):
            os.replace(f, os.path.join(out, time.strftime("_old_%H%M%S_") + os.path.basename(f)))
        say(f"   reel {k + 1}: {int(a // 60)}:{int(a % 60):02d}  {hook}")
        ok = ctx["run_cmd"]([cfg["studio_python"], "-u", "-s", os.path.join(cfg["studio_dir"], "engine", "horror_video.py"),
                             rj, f], cwd=cfg["studio_dir"]) == 0 and ok
    return ok
