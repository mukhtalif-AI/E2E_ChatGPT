"""speak.py - an English Sukoon script read by the Qwen3-TTS narrator (built-in voice, same voice every line).

    run.py speak <script.txt> --out <narrations folder> [--name N] [--redo 3,17] [--speaker Ryan] [--engine qwen]

Script format = the Sukoon format: one phrase per line, (...) = long pause, blank line = paragraph,
'...' = soft pause; headings / (notes) are skipped; a line of only dashes ends the script.
Every line is spoken with the same voice + calm direction (fixed seed); a line that comes out much too short
(swallowed words) or too long (dragged / strange pause) is spoken again (up to `takes` tries, the one closest to
the normal pace is kept). Same output as the Hindi voice engine, so music, video, reels and Repair work alike:
  <out>\\<name>\\ narration.wav (48 kHz) script.txt timing.json _lines.json report.txt lang.txt lines\\NNNN.wav
Crash-safe: lines already made are kept; delete one (or --redo n) and it is made again.
"""
import json, os, re, subprocess, time
import numpy as np
from . import audio
from .config import C

LATIN = re.compile(r"[A-Za-z]")


def say(t):
    print(t, flush=True)


def read_script(path):
    P = C["pauses"]
    raw = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    parts = [p for p in re.split(r"\n\s*-{3,}\s*\n", "\n" + raw + "\n") if p.strip()]
    text = parts[0] if parts else ""
    text = re.sub(r"[ \t]*\n?[ \t]*\(\s*(\.{2,}|…)\s*\)[ \t]*\n?", "\n⏸\n", text)   # (...) = long pause
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text).replace("…", "...")
    units = []

    def pause(v):
        if units:
            units[-1]["pause"] = max(units[-1]["pause"], float(v))

    for line in text.split("\n"):
        l = line.strip().strip("*#").strip().strip('"“”').strip()
        if not l:
            pause(P["paragraph"]); continue
        if l == "⏸":
            pause(P["marker"]); continue
        if re.match(r"^(part|episode|chapter|section)\s*\d+\s*[:.\-]", l, re.I) or not LATIN.search(l):
            pause(P["paragraph"]); continue
        if units and len(l.split()) <= 2 and units[-1]["pause"] <= P["line"] and not re.search(r"[.?!]$", units[-1]["text"]):
            units[-1]["text"] += " " + l
            continue
        e = l.rstrip()
        p = P["dots"] if e.endswith("...") else P["comma"] if e.endswith(",") else \
            P["sentence"] if re.search(r"[.?!]$", e) else P["line"]
        units.append({"text": l, "pause": float(p)})
        pause(P["line"])
    if units:
        units[-1]["pause"] = 0.0
    return units


NUMW = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}
ENDING = re.compile(r"\b(sleep now|good ?night|sleep well|drift (off|away))\b", re.I)


def slow_counts(units):
    """counted breaths ("one... two... three...") one number at a time, a breath-long pause after each"""
    pc = float(C["pauses"].get("count", 2.4))
    out = []
    for u in units:
        pieces = [x.strip(" ,.") for x in re.split(r"\.\.\.|…|,", u["text"])]
        if sum(x.lower() in NUMW for x in pieces) < 2:
            out.append(u); continue
        rest = []
        for x in pieces:
            if not x:
                continue
            if x.lower() in NUMW:
                if rest:
                    out.append({"text": "... ".join(rest) + "...", "pause": C["pauses"]["dots"]}); rest = []
                out.append({"text": x + "...", "pause": pc, "count": True})
            else:
                rest.append(x)
        if rest:
            out.append({"text": "... ".join(rest) + "...", "pause": u["pause"]})
        else:
            out[-1]["pause"] = max(out[-1]["pause"], u["pause"])
    return out


def gentle_ending(plan):
    n = len(plan)
    start = next((i for i in range(max(0, n - 8), n) if ENDING.search(plan[i]["text"])), None)
    if start is None:
        return plan
    for i in range(start, n):
        plan[i]["ending"] = True
        if i + 1 < n:
            plan[i]["pause"] = max(plan[i]["pause"] * 1.8, 2.5)
    if start:
        plan[start - 1]["pause"] = max(plan[start - 1]["pause"], 2.5)
    return plan


def _words(t):
    return len(re.findall(r"[A-Za-z0-9']+", t))


def _engine(name):
    import importlib
    return importlib.import_module(f"en.engines.{name}.engine").Engine()


def main(script, out_root, name=None, redo="", speaker=None, engine=None, ffmpeg=None):
    import soundfile as sf
    plan = read_script(script)
    if not plan:
        raise SystemExit("the script is empty (no English lines found)")
    plan = gentle_ending(slow_counts(plan))
    name = name or os.path.splitext(os.path.basename(script))[0]
    out = os.path.join(out_root, re.sub(r'[<>:"/\\|?*]', "_", name))
    ldir = os.path.join(out, "lines")
    os.makedirs(ldir, exist_ok=True)
    speaker = speaker or C["speaker"]
    engine = engine or C["engine"]
    stamp = os.path.join(out, "voice_used.txt")
    used = f"qwen3-tts|{speaker}|{C['instruct']}|{C['seed']}"           # engine change does not change the voice
    if os.path.exists(stamp) and open(stamp, encoding="utf-8").read().strip() != used and os.listdir(ldir):
        old = os.path.join(out, time.strftime("lines_old_%Y%m%d_%H%M"))
        os.replace(ldir, old); os.makedirs(ldir)
        say(f"   voice or direction changed - old lines kept in {os.path.basename(old)}\\")
    open(stamp, "w", encoding="utf-8").write(used)
    open(os.path.join(out, "lang.txt"), "w", encoding="utf-8").write("en\n")
    redo_n = [int(x) for x in re.findall(r"\d+", redo or "")]
    for n in redo_n:
        p = os.path.join(ldir, f"{n:04d}.wav")
        if os.path.exists(p):
            os.replace(p, p[:-4] + time.strftime("_old_%H%M%S.wav"))
    infos_path = os.path.join(out, "_lines.json")
    infos = json.load(open(infos_path, encoding="utf-8")) if os.path.exists(infos_path) else {}
    todo = [i for i in range(len(plan)) if not os.path.exists(os.path.join(ldir, f"{i + 1:04d}.wav"))]
    words = sum(_words(p["text"]) for p in plan)
    say(f"   {len(plan)} lines, {words} words; {len(todo)} to make  (narrator {speaker}, engine {engine})")
    if todo:
        eng = _engine(engine)
        B = max(1, int(C["batch"])) if hasattr(eng, "say_many") else 1
        # pace of this voice in words per second (only words, no pauses) - learned from the lines already made
        rates = [_words(v["text"]) / v["dur"] for v in infos.values() if v.get("dur") and v.get("ok")]
        t0, done_s = time.time(), 0.0
        for g in range(0, len(todo), B):
            group = todo[g:g + B]
            best = {i: None for i in group}
            for take in range(int(C["takes"])):
                need = [i for i in group if best[i] is None or not best[i][2]]
                if not need:
                    break
                bonus = 1000 * take + (500 + int(time.time()) % 400 if any(i + 1 in redo_n for i in need) else 0)
                its = [{"mode": "custom", "speaker": speaker,
                        "instruct": C["instruct"] + (" Almost a whisper, very slow, fading into sleep." if plan[i].get("ending")
                                                     else " One slow number, like a breath." if plan[i].get("count") else ""),
                        "seed": int(C["seed"]) + bonus} for i in need]
                texts = [plan[i]["text"] for i in need]
                if hasattr(eng, "say_many"):
                    wavs, sr = eng.say_many(texts, its)
                else:
                    outs = [eng.say(t, it) for t, it in zip(texts, its)]
                    wavs, sr = [w for w, _ in outs], outs[0][1]
                wps = float(np.median(rates)) if len(rates) >= 4 else 2.3
                for i, w in zip(need, wavs):
                    a = audio.trim(np.asarray(w, np.float32), sr)
                    d = len(a) / sr
                    want = _words(plan[i]["text"]) / wps
                    r = d / max(0.3, want)
                    ok = 0.6 <= r <= 1.6 and d > 0.4
                    if best[i] is None or abs(np.log(max(r, 1e-3))) < abs(np.log(max(best[i][3], 1e-3))):
                        best[i] = (a, sr, ok, r, d, want, take + 1)
                if take == 0:
                    rates += [_words(plan[i]["text"]) / best[i][4] for i in group if best[i] and best[i][2]]
            for i in group:
                a, sr, ok, r, d, want, takes = best[i]
                a = audio.level(a, "")
                p = os.path.join(ldir, f"{i + 1:04d}.wav")
                sf.write(p + ".part.wav", a.astype(np.float32), sr, subtype="PCM_16")
                os.replace(p + ".part.wav", p)
                infos[str(i + 1)] = {"text": plan[i]["text"], "dur": round(d, 2), "want": round(want, 2),
                                     "ok": bool(ok), "takes": takes}
                done_s += d
            json.dump(infos, open(infos_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            el = time.time() - t0
            k = min(g + B, len(todo))
            say(f"   line {k}/{len(todo)}  {done_s:5.0f} s voice in {el:4.0f} s ({el / max(done_s, 1e-6):.2f} s work per"
                f" voice-s, ~{el / k * (len(todo) - k) / 60:.0f} min left)")
    # join with Sukoon pauses
    timing, t, pieces, sr = [], 0.5, [], 24000
    for i, p in enumerate(plan):
        a, sr = sf.read(os.path.join(ldir, f"{i + 1:04d}.wav"), dtype="float32")
        if a.ndim > 1:
            a = a.mean(1)
        if p.get("ending"):
            a = a * 10 ** (-5 / 20)                        # a whispered good night, not a command
        elif p.get("count"):
            a = a * 10 ** (-2 / 20)
        timing.append({"n": i + 1, "start": round(t, 3), "end": round(t + len(a) / sr, 3), "text": p["text"]})
        g = p["pause"] if i + 1 < len(plan) else 0.0
        pieces += [a, np.zeros(int(g * sr), np.float32)]
        t += len(a) / sr + g
    full = np.concatenate([np.zeros(int(0.5 * sr), np.float32)] + pieces + [np.zeros(int(1.0 * sr), np.float32)])
    raw = os.path.join(out, "_joined.wav")
    sf.write(raw, full, sr, subtype="FLOAT")
    final = os.path.join(out, "narration.wav")
    if os.path.exists(final) and todo:
        os.replace(final, os.path.join(out, time.strftime("narration_old_%Y%m%d_%H%M%S.wav")))
    r = subprocess.run([ffmpeg or C["ffmpeg"], "-v", "error", "-y", "-i", raw, "-af",
                        f"loudnorm=I={C['loudness_lufs']}:TP=-2:LRA=11", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
                        final + ".part.wav"])
    if r.returncode == 0:
        os.replace(final + ".part.wav", final); os.remove(raw)
    else:
        os.replace(raw, final)
    open(os.path.join(out, "script.txt"), "w", encoding="utf-8").write(
        "".join(p["text"] + ("\n\n" if p["pause"] >= C["pauses"]["paragraph"] else "\n") for p in plan))
    json.dump(timing, open(os.path.join(out, "timing.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = [(int(k), v) for k, v in infos.items() if not v.get("ok", True) and int(k) <= len(plan)]
    rep = [f"narration of {script}", f"voice: Qwen3-TTS {speaker} (English)", f"length {len(full) / sr / 60:.1f} min", ""]
    if bad:
        rep.append("lines whose length still looked odd after all takes (listen; Repair or --redo n,n):")
        rep += [f"  {k}: {v['text']}  ({v['dur']} s, expected ~{v['want']} s)" for k, v in sorted(bad)]
    else:
        rep.append("every line passed the check.")
    open(os.path.join(out, "report.txt"), "w", encoding="utf-8").write("\n".join(rep) + "\n")
    say(f"\nDONE: {final}  ({len(full) / sr / 60:.1f} min)")
    if bad:
        say(f"   {len(bad)} lines to listen to: {', '.join(str(k) for k, _ in sorted(bad))}  (report.txt)")
