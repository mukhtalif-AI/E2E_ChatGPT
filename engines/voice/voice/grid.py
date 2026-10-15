"""grid.py - the night's listening grid + scorecard.
main():  the same short script spoken by every combination of speech model (T3 runs) x sound generator
         (Chatterbox's own / your trained one), each as its own narration folder night_<T3>__<S3>\\ with
         narration.wav + narration_music.wav. Every combination runs in its own process (clean GPU memory).
score(): measures each against YOUR real Sukoon voice: voice likeness (voice encoder), clarity (Hindi checker
         letter errors), speaking pace, pauses, pitch -> night_scores.txt (best first) + night_best.json.
final(): full narration of the given script with the best-scored combination."""
import glob, json, os, subprocess, sys, time
import numpy as np
from . import cfg

ROOT = cfg.ROOT


def say(*a):
    print(*a, flush=True)


def t3_runs():
    want = []
    for n, d in cfg.runs():
        if n.startswith("s3_") or "_smoke" in n or not os.path.exists(os.path.join(d, "best.pt")):
            continue
        if n in ("run_20261012_2101_sukoon_calm", "run_20261012_2231_sukoon_calm") or "sukoon_only" in n:
            want.append(n)
    return want[:3]


def s3_options():
    from .model import resolve_s3gen
    b = resolve_s3gen("best")
    return ["base"] + ([os.path.basename(os.path.dirname(b))] if b else [])


def tag(t3, s3):
    if "_smoke" in t3 or "_smoke" in s3:
        return f"night_{'T3smoke' if '_smoke' in t3 else 'A_liked'}__{'S3smoke' if '_smoke' in s3 else 'S3base'}"
    t = {"run_20261012_2101_sukoon_calm": "A_liked", "run_20261012_2231_sukoon_calm": "B_new"}.get(t3, "C_sukoononly")
    return f"night_{t}__{'S3base' if s3 == 'base' else 'S3yours'}"


def _run(args, logname):
    env = dict(os.environ, SV_LOGNAME=logname, PYTHONIOENCODING="utf-8")
    say(f"\n>>> {' '.join(args)}")
    r = subprocess.run([sys.executable, "-s", "-u", os.path.join(ROOT, "run.py")] + args, env=env, cwd=ROOT)
    return r.returncode


def main(script=None, smoke=False):
    script = script or os.path.join(ROOT, "1010_2min.txt")
    if smoke:
        script = os.path.join(cfg.TMP, "smoke_script.txt")
        os.makedirs(cfg.TMP, exist_ok=True)
        open(script, "w", encoding="utf-8").write("आज की रात हम बस आराम करेंगे...\n(...)\nसब ठीक हो जाएगा।\n")
    combos = [(t, s) for t in t3_runs() for s in s3_options()]
    if smoke:                                    # test every path: liked voice + the smoke-trained ones
        sm_t3 = [n for n, d in cfg.runs() if "_smoke" in n and not n.startswith("s3_")
                 and os.path.exists(os.path.join(d, "best.pt"))]
        sm_s3 = [n for n, d in cfg.runs() if n.startswith("s3_") and "_smoke" in n
                 and os.path.exists(os.path.join(d, "best.pt"))]
        combos = [("run_20261012_2101_sukoon_calm", "base")]
        if sm_s3:
            combos.append(("run_20261012_2101_sukoon_calm", sm_s3[0]))
        if sm_t3:
            combos.append((sm_t3[0], "base"))
    say(f"   grid: {len(combos)} combinations on {os.path.basename(script)}")
    for t, s in combos:
        name = tag(t, s) + ("_smoke" if smoke else "")
        rc = _run(["speak", script, "--name", name, "--model", t, "--s3gen", s], f"grid_{name}.txt")
        say(f"   {name}: {'ok' if rc == 0 else 'FAILED (logs/grid_' + name + '.txt)'}")


def score(dirpat=None):
    import torch, soundfile as sf
    from .model import load_base
    from .pace import measure, pitch
    folders = sorted(glob.glob(os.path.join(cfg.NARRATIONS, dirpat or "night_*")))
    folders = [f for f in folders if os.path.exists(os.path.join(f, "_lines.json"))]
    if not folders:
        raise SystemExit("nothing to score")
    m = load_base()
    m.t3.to("cpu"); m.s3gen.to("cpu"); torch.cuda.empty_cache()
    meta = [x for x in cfg.read_meta() if x.get("series") == "sukoon" and x["source"] == "script" and not x["holdout"]]
    meta = [meta[k] for k in np.linspace(0, len(meta) - 1, min(60, len(meta))).astype(int)]
    def emb(wavs):
        e = np.asarray(m.ve.embeds_from_wavs(wavs, sample_rate=16000), np.float32)
        return e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-8)
    import librosa
    real = [librosa.load(x["wav"], sr=16000)[0] for x in meta]
    me = emb(real).mean(0); me /= np.linalg.norm(me)
    you = {"rate": np.median([measure(a, 16000, len(x["text"].split()))[0] for a, x in zip(real, meta)]),
           "f0": np.median([f for f in (pitch(a, 16000) for a in real) if f > 0])}
    rows = []
    for d in folders:
        info = json.load(open(os.path.join(d, "_lines.json"), encoding="utf-8"))
        wavs, rates, f0s, les = [], [], [], []
        for k, v in info.items():
            p = os.path.join(d, "lines", f"{int(k):04d}.wav")
            if not os.path.exists(p):
                continue
            a, sr = sf.read(p, dtype="float32")
            a16 = librosa.resample(a, orig_sr=sr, target_sr=16000)
            wavs.append(a16)
            if len(v["text"].split()) >= 4:
                rates.append(measure(a16, 16000, len(v["text"].split()))[0])
            f = pitch(a16, 16000)
            if f > 0:
                f0s.append(f)
            if v.get("letter_error") is not None:
                les.append(v["letter_error"])
        sim = float(np.mean(emb(wavs) @ me)) if wavs else 0
        rate, f0 = float(np.median(rates)) if rates else 0, float(np.median(f0s)) if f0s else 0
        le = float(np.mean(les)) if les else 0
        pace_off = abs(np.log(max(rate, .1) / you["rate"]))
        pitch_off = abs(12 * np.log2(max(f0, 1) / you["f0"]))
        sc = 10 * sim - 10 * le - 4 * pace_off - 0.3 * pitch_off
        rows.append((sc, os.path.basename(d), sim, le, rate, f0, pitch_off))
    rows.sort(reverse=True)
    lines = [f"YOUR Sukoon voice: pace {you['rate']:.2f} words/s, pitch {you['f0']:.0f} Hz", "",
             "score  combination                          likeness  letters-wrong  pace   pitch (off by)"]
    for sc, n, sim, le, rate, f0, po in rows:
        lines.append(f"{sc:6.2f}  {n:36s} {sim:.3f}    {le:6.1%}       {rate:.2f}   {f0:.0f} Hz ({po:.1f} st)")
    out = os.path.join(cfg.NARRATIONS, "night_scores.txt")
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    json.dump({"best": rows[0][1]}, open(os.path.join(cfg.NARRATIONS, "night_best.json"), "w"))
    say("\n".join(lines))


def final(script):
    b = json.load(open(os.path.join(cfg.NARRATIONS, "night_best.json")))["best"]
    t3 = "run_20261012_2101_sukoon_calm" if "A_liked" in b else "run_20261012_2231_sukoon_calm" if "B_new" in b \
        else next((r for r in t3_runs() if "sukoon_only" in r), t3_runs()[0])
    s3 = "base" if "S3base" in b else s3_options()[-1]
    _run(["speak", script, "--name", "night_FULL_" + b.replace("night_", ""), "--model", t3, "--s3gen", s3],
         "grid_final.txt")
