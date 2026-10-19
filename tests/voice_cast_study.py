"""voice_cast_study.py - case study (19 Oct 2026): can the local model tell who speaks each quoted line?

    E:\\ComfyUI_windows_portable\\python_embeded\\python.exe -s tests\\voice_cast_study.py [--runs 2] [--only 12b_think,...]

Reads the scripts in _tmp\\ghost_voice_study_20261019\\scripts, finds the quotes with voice_cast.spans, asks the model
(voice_cast.CAST_ASK, the same prompt the hub uses) and saves every raw answer in results\\<config>_<run>.json.
Scoring against labels.json: tests\\voice_cast_study.py --score (no model needed).
Configs: 12b_think (director_model, thinking on = how the hub runs it), 12b_fast (thinking off), 26b_fast.
"""
import argparse, glob, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))
STUDY = os.path.join(ROOT, "_tmp", "ghost_voice_study_20261019")
CONFIGS = {"12b_think": ("gemma4:12b-it-qat", False), "12b_fast": ("gemma4:12b-it-qat", True),
           "26b_fast": ("gemma4:26b-a4b-it-qat", True)}
GENRE = {"bhay": "bhay", "exp": "bhay", "kisse": "kisse", "rahasya": "rahasya", "romanch": "romanch"}


def ask_all(cfg, name, runs):
    import story, tools, voice_cast
    model, fast = CONFIGS[name]
    out_dir = os.path.join(STUDY, "results")
    os.makedirs(out_dir, exist_ok=True)
    for run in range(1, runs + 1):
        res = {}
        for f in sorted(glob.glob(os.path.join(STUDY, "scripts", "*.txt"))):
            text = open(f, encoding="utf-8").read()
            sp = voice_cast.spans(text)
            if not sp:
                continue
            g = GENRE.get(os.path.basename(f).split("_")[0], "bhay")
            prompt = voice_cast.CAST_ASK.format(desc=tools.GENRES.get(g, {}).get("desc", g),
                                                story=voice_cast.story_for_ask(text, sp))
            c = dict(cfg, _llm_override=model, _no_think=fast)
            t0 = time.time()
            try:
                t, _ = story.local_chat(c, prompt, 0.2, True, 16384)
                if not t.strip():
                    t, _ = story.local_chat(c, prompt, 0.2, False, 16384)
            except Exception as e:
                t = f"ERROR {e}"
            dt = round(time.time() - t0, 1)
            res[os.path.basename(f)] = {"seconds": dt, "reply": t, "quotes": [s["quote"] for s in sp]}
            print(f"{name} run {run} {os.path.basename(f)}: {dt} s", flush=True)
        json.dump(res, open(os.path.join(out_dir, f"{name}_{run}.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def label_of(labels, fname, quote):
    lab = labels.get(fname) or {}
    for k, v in lab.items():
        if k != "*" and quote.startswith(k):
            return v
    return lab.get("*", "X")


def score():
    import tools, voice_cast
    labels = json.load(open(os.path.join(STUDY, "labels.json"), encoding="utf-8"))
    files = sorted(glob.glob(os.path.join(STUDY, "results", "*.json")))
    by_cfg = {}
    for f in files:
        by_cfg.setdefault(os.path.basename(f).rsplit("_", 1)[0], []).append(json.load(open(f, encoding="utf-8")))
    rows = []
    for name, runs in sorted(by_cfg.items()):
        for mode in ("one ask", "two asks agree"):
            use = runs[:1] if mode == "one ask" else runs[:2]
            if mode == "two asks agree" and len(runs) < 2:
                continue
            tp = fn = fp = 0
            wrong, missed, secs = [], [], []
            for fname in sorted(use[0]):
                text = open(os.path.join(STUDY, "scripts", fname), encoding="utf-8").read()
                sp = voice_cast.spans(text)
                replies = []
                for r in use:
                    secs.append(r[fname]["seconds"])
                    try:
                        replies.append(voice_cast.parse_reply(tools._loads(r[fname]["reply"])))
                    except Exception:
                        replies.append({})
                chosen, rejected = voice_cast.decide(sp, replies, {"roles": {"ghost": "ghost"}, "max_lines": 99})
                picked = {c["n"] for c in chosen}
                for x in sp:
                    lab = label_of(labels, fname, x["quote"])
                    if lab == "X":
                        continue
                    if x["n"] in picked and lab == "G":
                        tp += 1
                    elif x["n"] in picked:
                        fp += 1
                        wrong.append(f"{fname} #{x['n']} ({lab}) {x['quote'][:40]}")
                    elif lab == "G":
                        fn += 1
                        why = next((r["why"] for r in rejected if r["n"] == x["n"]), "")
                        missed.append(f"{fname} #{x['n']} {x['quote'][:40]} - {why}")
            rows.append({"config": name, "mode": mode, "ghost_lines": tp + fn, "got": tp, "missed": fn,
                         "wrong_speaker": fp, "seconds_per_story": round(sum(secs) / max(1, len(secs)), 1),
                         "wrong": wrong, "missed_lines": missed})
    json.dump(rows, open(os.path.join(STUDY, "score.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for r in rows:
        print(f"{r['config']:10} {r['mode']:15} ghost {r['got']}/{r['ghost_lines']}  wrong speaker {r['wrong_speaker']}  "
              f"{r['seconds_per_story']} s/story")
        for w in r["wrong"]:
            print("     WRONG ", w)
        for m in r["missed_lines"]:
            print("     missed", m)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--only", default="")
    ap.add_argument("--score", action="store_true")
    a = ap.parse_args(argv)
    if a.score:
        return score()
    import path_config
    cfg = path_config.load_config()
    cfg.setdefault("ollama_url", (cfg.get("paths") or {}).get("ollama_url") or "http://127.0.0.1:11434")
    for name in (a.only.split(",") if a.only else CONFIGS):
        ask_all(cfg, name, a.runs)
    score()


if __name__ == "__main__":
    main(sys.argv[1:])
