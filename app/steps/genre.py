"""steps/genre.py - genre story, sound, video and reels. Moved out of tools.py on 16 Oct 2026 (TOOLS_SPLIT_PLAN.md step 4k).

Do not import this file directly: import tools and use tools.<name>. Names that stay in tools.py, and names other files swap
(feedback, narrator, Tinker), are written as tools.X here on purpose.
"""
import glob, json, os, random, re, shutil, subprocess, time, urllib.error, urllib.parse, urllib.request, wave

import tools


# ------------------------------------------------------------------ भय (Bhay) horror stories
def horror_story(ctx, name, topic="", minutes=15, notes="", engine="auto", recipe=None, genre="bhay", level="standard",
                 auto_name=False, by_scene=None):
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    script, info = story.write_genre(cfg, genre, say, topic, float(minutes or 15), notes, engine, recipe, ctx["stopped"],
                                     level, by_scene)
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
             "level": info.get("level") or level, "genre_fit": info.get("genre_fit")}
    story.add_ledger(cfg, entry)
    json.dump(entry, open(p[:-4] + ".story.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    try:            # every version of the story (plan, draft, review, final) so a fault can be traced to its step
        if info.get("versions"):
            json.dump({"plan": info.get("plan") or "", **info["versions"]},
                      open(p[:-4] + ".versions.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if info.get("by_scene"):    # the scene list (who is there, how old, then/now) - the picture step reads it too
            json.dump(info["by_scene"], open(p[:-4] + ".scenes.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except (OSError, TypeError, ValueError) as e:
        say(f"   (story versions not saved: {e})")
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


def _mood_series(genre):
    """Bhay, Rahasya, Kisse, Romanch (the emotion-music series); Gyaan, Kareeb and Sukoon keep the old pick"""
    try:
        import emotion_music
        return bool(emotion_music.settings(genre).get("on"))
    except Exception:
        return False


def _story_mood(ctx, name, pictures, folders):
    """Experimental (17 Oct 2026, decision C): the music folder for the story's start. The emotion map's first section
    when there is one, else the most common feeling mark in the script; '' = no clue (all the series' folders, as before)"""
    import emotion_music
    from collections import Counter
    cfg = ctx["cfg"]
    md = cfg.get("music_dir") or ""
    have = [f for f in folders if glob.glob(os.path.join(md, f, "*.mp3"))]
    m = emotion_music._load(os.path.join(pictures or "", "emotion_map.json")) or {}
    secs = m.get("sections") or []
    if secs and secs[0].get("mood") in have:
        return secs[0]["mood"]
    try:
        with open(os.path.join(tools.ctx_folder(ctx, "scripts"), tools.slug(name) + ".txt"), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    tags = Counter(t.group(1).strip() for t in (emotion_music.TAG_RE.match(ln) for ln in lines) if t)
    return emotion_music._mood_from_tags(dict(tags), have, "") if tags and have else ""


def horror_sound(ctx, name, pictures, track="", genre="bhay", tail="1min", music_db=None):
    """music bed (shaped like your episodes, a bit lower) + per-scene ambience loops + rare hits -> narration_music.wav"""
    import numpy as np
    cfg, say = ctx["cfg"], ctx["say"]
    if _is_exp(cfg) and not str(tail).startswith("total:"):  # Experimental, 'Whole video' 0: the voice + a short ending
        tail = f"secs:{_end_seconds(cfg)}"
    folders = tools.GENRES.get(genre, tools.GENRES["bhay"])["music"]
    chosen = bool(track)                                    # a track picked on the form: emotion music keeps it alone
    pick = folders
    if _is_exp(cfg) and not chosen and _mood_series(genre):  # the story's mood picks the folder, not the biggest folder
        mood = _story_mood(ctx, name, pictures, folders)
        if mood:
            pick = [mood]
            say(f"   music mood from the story: {mood}")
        cfg["_music_series"], cfg["_music_episode"] = genre, name
    track = track or tools._horror_track(cfg, pick)
    if not track and _is_exp(cfg):                         # 18 Oct 2026: no track in that folder fits its mood
        import emotion_music
        emotion_music.misfit_note(cfg, say)
        for m2 in (emotion_music.near_moods(pick[0], folders)[1:] if len(pick) == 1 else []) + [folders]:
            track = tools._horror_track(cfg, m2 if isinstance(m2, list) else [m2])
            emotion_music.misfit_note(cfg, say)
            if track:
                say(f"   music mood {pick[0] if len(pick) == 1 else '/'.join(pick)} has no fitting track - using "
                    f"{os.path.basename(os.path.dirname(track))}")
                break
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
    exp = _is_exp(cfg)
    sp_plan = None
    if exp:                                                 # one bed per place: runs, no restart, crossfade at changes
        import emotion_music
        added += _bed_runs(cfg, shots, mix, lead, sr, voice_db, bed, say,
                           peak_db=emotion_music.sting_settings(genre).get("bed_peak_db"))
        # 18 Oct 2026: turns, scene hits and mood effects share one limit (gap + per minute, strongest first);
        # mix() already applied it to the turns with the same answer
        sp_plan = emotion_music.stinger_plan(genre, shots, emotion_music.active(cfg, pictures, genre) if emo else None)
        if not emo:
            for e in sp_plan["dropped"]:
                say(f"   stinger left out at {int((lead + e['at']) // 60)}:{int((lead + e['at']) % 60):02d}: "
                    f"{e['what']} ({e['why']})")
    for i, s in enumerate(shots):
        a = int((lead + float(s["start"])) * sr)
        b = int((lead + float(shots[i + 1]["start"] if i + 1 < len(shots) else s["end"] + 4)) * sr)
        b = min(b, len(mix))
        if not exp and s.get("ambience") and os.path.exists(s["ambience"]) and b > a:
            amb = tools._decode(cfg, s["ambience"], sr, 2)
            if len(amb) > sr:
                need = b - a
                rep_ = np.concatenate([amb] * (need // len(amb) + 1))[:need]
                g = 10 ** ((voice_db - 22 - 20 * np.log10(float(np.sqrt(np.mean(amb ** 2))) + 1e-9)) / 20)
                env = np.minimum(1, np.minimum(np.arange(need) / (2 * sr), (need - np.arange(need)) / (2 * sr)))[:, None]
                mix[a:b] += (rep_ * g * env * bed(a, b)).astype(np.float32)
                added += 1
        if s.get("hit") and os.path.exists(s["hit"]) and i > 0 and (sp_plan is None or i in sp_plan["hits"]):
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
    pauses = None                                       # the narration's silences, for voice-like effects
    if exp:
        try:
            import emotion_music
            sp_n, _ = emotion_music.speech_frames(tools._decode(cfg, os.path.join(d, "narration.wav"), sr, 1), sr)
            pauses = emotion_music.gaps(sp_n)
        except Exception as e_:
            say(f"   (pauses of the narration not measured: {e_})")
    for si, s in enumerate(shots):
        for ci, c in enumerate(s.get("cues") or []):
            if sp_plan is not None and c.get("at") is not None and (si, ci) not in sp_plan["cues"] \
                    and emotion_music._is_sting_file(c.get("file")):
                continue                                    # a mood effect too close to a stronger stinger
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
            if pauses is not None and emotion_music.is_voice_fx(c["file"]):   # 18 Oct 2026: never over the speech
                slot = emotion_music.voice_fx_slot(float(at), len(h) / sr, pauses)
                name_fx = os.path.basename(c["file"])
                if not slot:
                    say(f"   effect {name_fx} left out: it sounds like a voice and there is no pause near "
                        f"'{c.get('words')}' to play it in")
                    continue
                at, room = slot
                h = h[: int(room * sr)]
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


def _bed_loop(amb, need, sr):
    """the bed repeated to 'need' samples with a ~1 s equal-power seam (a plain repeat clicks / restarts audibly)"""
    import numpy as np
    if len(amb) >= need:
        return amb[:need]
    xf = min(sr, len(amb) // 4)
    ramp = np.sin(np.linspace(0, np.pi / 2, xf))[:, None].astype(np.float32)
    out = np.zeros((need, amb.shape[1]), np.float32)
    pos, first = 0, True
    while pos < need:
        seg = amb.astype(np.float32).copy()
        if not first:
            seg[:xf] *= ramp
        seg[-xf:] *= ramp[::-1]
        n = min(len(seg), need - pos)
        out[pos:pos + n] += seg[:n]
        pos += len(amb) - xf
        first = False
    return out


def _bed_runs(cfg, shots, mix, lead, sr, voice_db, bed, say, xfade=3.0, edge=2.0, peak_db=None):
    """Experimental (17 Oct 2026 sound cross-check): scenes in a row with the same bed share ONE loop that keeps
    running (before, every scene restarted its bed with a 2 s fade out and in). A change of bed is a 3 s equal-power
    crossfade; the start and end of a bed fade over 2 s as before."""
    import numpy as np
    runs = []                                           # [file, a, b] in samples, consecutive scenes merged
    for i, s in enumerate(shots):
        a = int((lead + float(s["start"])) * sr)
        b = min(len(mix), int((lead + float(shots[i + 1]["start"] if i + 1 < len(shots) else s["end"] + 4)) * sr))
        f = s.get("ambience") if s.get("ambience") and os.path.exists(s["ambience"]) else ""
        if runs and runs[-1][0] == f and runs[-1][2] >= a - sr:
            runs[-1][2] = b
        elif b > a:
            runs.append([f, a, b])
    added, half = 0, int(xfade * sr / 2)
    told = set()
    for j, (f, a, b) in enumerate(runs):
        if not f:
            continue
        amb = tools._decode(cfg, f, sr, 2)
        if len(amb) <= sr:
            continue
        if peak_db is not None:                             # 18 Oct 2026: a bed full of hits is held steady
            import emotion_music
            amb, n_pk = emotion_music.tame_bed(amb, sr, peak_db)
            if n_pk >= 3 and f not in told:
                told.add(f)
                say(f"   ambience {os.path.basename(f)}: {n_pk} loud hits in the file held to {peak_db:g} dB over its "
                    f"usual level (a bed should stay steady)")
        cross_in = j > 0 and runs[j - 1][0] and runs[j - 1][2] >= a - sr
        cross_out = j + 1 < len(runs) and runs[j + 1][0] and runs[j + 1][1] <= b + sr
        a2 = max(0, a - half) if cross_in else a
        b2 = min(len(mix), b + half) if cross_out else b
        need = b2 - a2
        if need <= 0:
            continue
        loop = _bed_loop(amb, need, sr)
        g = 10 ** ((voice_db - 22 - 20 * np.log10(float(np.sqrt(np.mean(amb ** 2))) + 1e-9)) / 20)
        t = np.arange(need, dtype=np.float32)
        if cross_in:
            fi = np.sin(np.pi / 2 * np.minimum(1, t / (2 * half)))
        else:
            fi = np.minimum(1, t / (edge * sr))
        if cross_out:
            fo = np.sin(np.pi / 2 * np.minimum(1, (need - t) / (2 * half)))
        else:
            fo = np.minimum(1, (need - t) / (edge * sr))
        mix[a2:b2] += (loop * g * (fi * fo)[:, None] * bed(a2, b2)).astype(np.float32)
        added += 1
    beds = [r for r in runs if r[0]]
    if beds:
        say(f"   ambience: {len(beds)} bed run(s) over {len(shots)} scenes "
            f"({len({r[0] for r in beds})} different), crossfaded where the place changes")
    return added


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


def _channel(cfg, genre, say=None):
    """Experimental: the series' channel name + logo path from tools.CHANNEL ({} = the old text label)"""
    c = (tools.CHANNEL.get(genre) or {}) if _is_exp(cfg) else {}
    if not c.get("on"):
        return {}
    name, logo = str(c.get("name") or "").strip(), str(c.get("logo") or "").strip()
    if logo and not (os.path.isabs(logo) or (len(logo) > 2 and logo[1] == ":")):
        logo = os.path.join(cfg.get("experimental_root") or "", "Library", "Assets", logo)
    if logo and not os.path.exists(logo):
        if say:
            say(f"   channel logo not found ({logo}) - the name is used instead")
        logo = ""
    if not (name or logo):
        return {}
    return {"name": name, "logo": logo, "thumb": c.get("thumb", True) is not False}


def _logo_on_thumb(png, logo):
    """the channel logo in the top-right corner of the episode thumbnail (a copy of the first picture)"""
    from PIL import Image
    im = Image.open(png).convert("RGBA")
    lg = Image.open(logo).convert("RGBA")
    h = int(im.height * 0.24)
    lg = lg.resize((max(1, int(lg.width * h / lg.height)), h), Image.LANCZOS)
    m = int(im.height * 0.035)
    im.alpha_composite(lg, (im.width - lg.width - m, m))
    im.convert("RGB").save(png)


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
    ch = _channel(cfg, genre, say)
    if ch:                                                 # Experimental: the series' own channel name + logo
        plan["channel"] = {"name": ch["name"], "logo": ch["logo"]}
        say(f"   channel: {ch['name'] or '-'}" + (f", logo {os.path.basename(ch['logo'])}" if ch["logo"] else ""))
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
            if ch.get("logo") and ch.get("thumb"):
                _logo_on_thumb(os.path.join(ep, os.path.basename(ep) + ".png"), ch["logo"])
    except Exception as e:
        say(f"   (episode folder copy: {e})")
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
