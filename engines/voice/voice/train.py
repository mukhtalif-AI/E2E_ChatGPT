"""train.py - teach Chatterbox YOUR voice: a LoRA add-on on its speech model, trained on your dataset lines.

    run.py train                 continue the newest unfinished run, else start a new one
    run.py train --new [name]    always start a new run
    run.py train --epochs 25     change the number of epochs (also when continuing)

Output: <model_dir>\\<run>\\  best.pt (lowest error on the kept-out lines = the one that is used),
last.pt (to continue after a crash / Stop), train_log.txt, done.txt when finished.
Speed on an RTX 3060: roughly 1-2 min per epoch for 30 min of voice. Close ComfyUI / other GPU work first.
"""
import json, math, os, random, re, shutil, sys, time
import numpy as np
from . import cfg
from .model import load_base, say

PROMPT_LEN = 150          # = Chatterbox's speech prompt length (6 s at 25 tokens/s)


def features(m, meta):
    """speech tokens + text tokens + speaker vector of every line (cached in training\\_features.pt)."""
    import torch, librosa
    from chatterbox.mtl_tts import punc_norm
    path = os.path.join(cfg.TRAINING, "_features.pt")
    cache = torch.load(path, weights_only=False) if os.path.exists(path) else {}
    todo = [x for x in meta if x["id"] not in cache or cache[x["id"]].get("text_str") != x["text"]
            or "f0" not in cache[x["id"]]]
    if todo:
        say(f"   preparing {len(todo)} lines (one time) ...")
        t0 = time.time()
        for k, x in enumerate(todo):
            wav, _ = librosa.load(x["wav"], sr=16000)
            with torch.inference_mode():
                sp, sl = m.s3gen.tokenizer.forward([wav])
                tt = m.tokenizer.text_to_tokens(punc_norm(x["text"]), language_id="hi")
                ve = m.ve.embeds_from_wavs([wav], sample_rate=16000)
            from .pace import measure, pitch
            rate, pause = measure(wav, 16000, len(x["text"].split()))
            f0 = pitch(wav, 16000)
            cache[x["id"]] = {"speech": sp[0, :int(sl[0])].cpu().short(), "text": tt[0].cpu().short(),
                              "ve": np.asarray(ve[0], np.float32), "text_str": x["text"], "rate": rate,
                              "pause": pause, "f0": f0}
            if (k + 1) % 50 == 0 or k + 1 == len(todo):
                say(f"     {k + 1}/{len(todo)}  ({time.time() - t0:.0f} s)")
        os.makedirs(cfg.TRAINING, exist_ok=True)
        torch.save(cache, path + ".part")
        os.replace(path + ".part", path)
    return cache


PROFILES = {"sukoon_calm": "Sukoon calm: your slow, pausing, normal-pitch lines from ALL recordings",
            "sukoon_only": "Sukoon only: just your Sukoon episodes",
            "all": "everything (all styles; the pace knob tells them apart)",
            "sukoon_flow": "Sukoon flow: your Sukoon lines + 2-4 lines in a row with your real pauses (run.py flowdata)"}


def select(train_m, feats, profile, log):
    """which lines a training run learns from. sukoon_calm = lines that sound like your Sukoon delivery: pace
    and pauses within your Sukoon range, pitch within +-4 semitones (config pitch_range_semitones) of your Sukoon pitch (character voices out)."""
    from .pace import semitones
    if profile.startswith("series_"):                   # one series' own recordings (series.json, hub Settings)
        from . import series
        sid, d = profile[7:], series.load()
        keep = [x for x in train_m if series.of(x.get("episode", ""), d) == sid]
        nm = d["series"].get(sid, {}).get("name", sid)
        log(f"   profile {profile}: {len(keep)} lines = {sum(x['dur'] for x in keep) / 60:.0f} min of your {nm} recordings")
        if sum(x["dur"] for x in keep) < 8 * 60:
            raise SystemExit(f"only {sum(x['dur'] for x in keep) / 60:.0f} min of {nm} recordings - tick more recordings "
                             "for this series in the hub (Settings > Voice per series); at least ~10 min is needed")
        return keep
    if profile in ("sukoon_only", "sukoon_flow"):
        keep = [x for x in train_m if x.get("series") == "sukoon"]
        log(f"   profile sukoon_only: {len(keep)} lines = {sum(x['dur'] for x in keep) / 60:.0f} min of your Sukoon")
        return keep
    suk = [x for x in train_m if x.get("series") == "sukoon" and feats[x["id"]]["f0"] > 0]
    if profile != "sukoon_calm" or len(suk) < 50:
        if profile == "sukoon_calm":
            log("   (too few Sukoon lines to define the calm style - using all lines)")
        return train_m
    f0 = float(np.median([feats[x["id"]]["f0"] for x in suk]))
    r_hi = float(np.percentile([feats[x["id"]]["rate"] for x in suk], 75))
    p_lo = float(np.percentile([feats[x["id"]]["pause"] for x in suk], 25))
    keep, why = [], {"pitch": 0, "fast": 0, "no pauses": 0}
    for x in train_m:
        F = feats[x["id"]]
        if x.get("series") == "sukoon":                  # your Sukoon lines are the style itself: always kept
            keep.append(x); continue
        if F["f0"] <= 0 or abs(semitones(F["f0"], f0)) > float(cfg.C["train"].get("pitch_range_semitones", 4)):
            why["pitch"] += 1
        elif F["rate"] > r_hi * 1.05:
            why["fast"] += 1
        elif F["pause"] < p_lo * 0.8 and x.get("series") != "sukoon":
            why["no pauses"] += 1
        else:
            keep.append(x)
    mins = {}
    for x in keep:
        mins[x.get("series", "?")] = mins.get(x.get("series", "?"), 0) + x["dur"] / 60
    log(f"   profile sukoon_calm: your Sukoon pitch {f0:.0f} Hz, pace up to {r_hi:.1f} words/s, pauses from "
        f"{p_lo:.0%}")
    log(f"   chosen {len(keep)} of {len(train_m)} lines = {sum(x['dur'] for x in keep) / 60:.0f} min ("
        + ", ".join(f"{k} {v:.0f} min" for k, v in sorted(mins.items())) + f"); left out: {why['pitch']} other "
        f"pitch, {why['fast']} too fast, {why['no pauses']} without pauses")
    return keep


def pick_run(new, name):
    rs = cfg.runs()
    if not new and not name:
        for n, d in rs:
            if os.path.exists(os.path.join(d, "last.pt")) and not os.path.exists(os.path.join(d, "done.txt")):
                return n, d, True
    n = name or time.strftime("run_%Y%m%d_%H%M")
    d = os.path.join(cfg.TRAINING, n)
    return n, d, os.path.exists(os.path.join(d, "last.pt"))


def main(new=False, name=None, epochs=None, profile=None, lr=None, limit=None):
    import torch
    import torch.nn.functional as F
    from chatterbox.models.t3.modules.cond_enc import T3Cond
    from . import lora
    T = dict(cfg.C["train"])
    if epochs:
        T["epochs"] = int(epochs)
    if lr:
        T["lr"] = float(lr)
    meta = cfg.read_meta()
    if not meta:
        raise SystemExit("no dataset yet - run menu 1 (build dataset) first")
    train_m = [x for x in meta if not x["holdout"]]
    val_m = [x for x in meta if x["holdout"]]
    profile = profile or T.get("profile", "sukoon_calm")
    if profile == "sukoon_flow" or profile.startswith("series_"):   # + flow clips (several lines in a row, real pauses)
        from .flowdata import read_flow_meta
        fl = read_flow_meta()
        if profile.startswith("series_"):
            from . import series
            sid, sd = profile[7:], series.load()
            fl = [x for x in fl if series.of(x.get("episode", ""), sd) == sid]
            for x in fl:
                x["series"] = sid
        if not fl and profile == "sukoon_flow":
            raise SystemExit("no flow clips yet - run: run.py flowdata")
        rv = random.Random(5)
        vf = rv.sample(fl, min(16, len(fl) // 10)) if fl else []           # a few flow clips kept out to judge the flow too
        near = {(x["episode"], int(x["id"].split("_")[-2])) for x in vf}
        tf = [x for x in fl if x not in vf and not any(e == x["episode"] and abs(int(x["id"].split("_")[-2]) - k) <= 4
                                                        for e, k in near)]
        meta = meta + fl
        train_m = train_m + tf
        val_m = val_m + vf
    run, rdir, resume = pick_run(new, name)
    if not resume and not name:
        run = f"{run}_{profile}" + (f"_lr{T['lr']:g}" if lr else "") + ("_smoke" if limit else "")
        rdir = os.path.join(cfg.TRAINING, run)
    os.makedirs(rdir, exist_ok=True)
    logf = open(os.path.join(rdir, "train_log.txt"), "a", encoding="utf-8")

    def log(s):
        say(s)
        logf.write(time.strftime("%H:%M:%S ") + s + "\n")
        logf.flush()

    minutes = round(sum(x["dur"] for x in train_m) / 60, 1)
    log(f"== {'continuing' if resume else 'new'} training run '{run}': {len(train_m)} lines ({minutes} min), "
        f"{len(val_m)} kept out")
    random.seed(T["seed"]); torch.manual_seed(T["seed"])
    m = load_base()
    feats = features(m, meta)
    train_m = select(train_m, feats, profile, log)
    if limit:                                            # smoke test: a handful of lines
        random.Random(1).shuffle(train_m); train_m = train_m[:int(limit)]; val_m = val_m[:max(4, int(limit) // 5)]
    minutes = round(sum(x["dur"] for x in train_m) / 60, 1)
    dev = m.device
    m.s3gen.to("cpu"); m.ve.to("cpu")                       # only the speech model is trained: free the GPU
    torch.cuda.empty_cache()
    t3 = m.t3
    hp = t3.hp

    spk = np.mean([feats[x["id"]]["ve"] for x in train_m], 0)
    spk = torch.tensor(spk / (np.linalg.norm(spk) + 1e-8), dtype=torch.float32)

    from . import pace as PC
    E = PC.edges([feats[x["id"]]["rate"] for x in train_m], [feats[x["id"]]["pause"] for x in train_m])
    for x in meta:
        x["pace"] = PC.klass(feats[x["id"]]["rate"], feats[x["id"]]["pause"], E)
    style_pace = {}
    for ser in sorted({x.get("series", "story") for x in train_m}):
        L = [x for x in train_m if x.get("series", "story") == ser]
        style_pace[ser] = PC.klass(float(np.median([feats[x["id"]]["rate"] for x in L])),
                                   float(np.median([feats[x["id"]]["pause"] for x in L])), E)
        log(f"   your {ser} style: {PC.describe(style_pace[ser])} (median "
            f"{np.median([feats[x['id']]['rate'] for x in L]):.1f} words/s of speech, "
            f"{np.median([feats[x['id']]['pause'] for x in L]):.0%} pauses)")

    # prompt pools: each episode's speech tokens in order -> random 6 s windows of YOU from OTHER lines
    pools = {}
    for x in train_m:
        if x.get("source") != "flow":                    # prompts come from single lines only
            pools.setdefault(x["episode"], []).append(x["id"])

    def prompt_for(x, rng):
        bare = lambda t: " ".join(re.sub(r"[,.।!?…\-\"'“”‘’]", " ", t).split())
        ids = [i for i in pools.get(x["episode"], []) if i != x["id"] and bare(feats[i]["text_str"]) not in bare(x["text"])] \
            or [i for i in feats if i != x["id"] and not i.startswith("flow_")]
        start = rng.randrange(len(ids))
        toks = []
        for i in ids[start:] + ids[:start]:
            toks.append(feats[i]["speech"].long())
            if sum(len(t) for t in toks) >= PROMPT_LEN + 25:
                break
        t = torch.cat(toks)
        if len(t) < PROMPT_LEN:
            t = torch.cat([t] * (PROMPT_LEN // max(1, len(t)) + 1))
        o = rng.randrange(0, len(t) - PROMPT_LEN + 1)
        return t[o:o + PROMPT_LEN]

    def collate(items, rng):
        B = len(items)
        texts = [torch.cat([torch.tensor([hp.start_text_token]), feats[x["id"]]["text"].long(),
                            torch.tensor([hp.stop_text_token])]) for x in items]
        sps = [torch.cat([torch.tensor([hp.start_speech_token]), feats[x["id"]]["speech"].long(),
                          torch.tensor([hp.stop_speech_token])]) for x in items]
        tl = torch.tensor([len(t) for t in texts]); sl = torch.tensor([len(s) for s in sps])
        text = torch.full((B, int(tl.max())), hp.stop_text_token, dtype=torch.long)
        speech = torch.full((B, int(sl.max())), hp.stop_speech_token, dtype=torch.long)
        for i in range(B):
            text[i, :tl[i]] = texts[i]; speech[i, :sl[i]] = sps[i]
        prompt = torch.stack([prompt_for(x, rng) for x in items])
        cond = T3Cond(speaker_emb=spk.view(1, -1).expand(B, -1).contiguous(), cond_prompt_speech_tokens=prompt,
                      emotion_adv=0.5 * torch.ones(B, 1, 1)).to(device=dev)
        cond.pace = torch.tensor([x["pace"] for x in items], device=dev)
        return cond, text.to(dev), tl.to(dev), speech.to(dev), sl.to(dev)

    def loss_of(batch):
        cond, text, tl, speech, sl = batch
        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=(dev == "cuda")):
            out = t3.forward(t3_cond=cond, text_tokens=text, text_token_lens=tl, speech_tokens=speech,
                             speech_token_lens=sl, training=True)
        logits = out.speech_logits[:, :-1].float()          # position i predicts speech token i+1
        tgt = speech[:, 1:].clone()
        tgt[torch.arange(tgt.size(1), device=dev)[None] >= (sl - 1)[:, None]] = -100
        return F.cross_entropy(logits.reshape(-1, logits.size(-1)), tgt.reshape(-1), ignore_index=-100)

    for p in t3.parameters():
        p.requires_grad_(False)
    params = lora.apply(t3, int(T["lora_rank"]), float(T["lora_alpha"]))
    pace_emb = PC.install(t3)
    pace_emb.weight.requires_grad_(True)
    params = params + [pace_emb.weight]
    t3.tfmr.config.use_cache = False
    try:
        t3.tfmr.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    except Exception as e:
        log(f"   (no gradient checkpointing: {e})")
    opt = torch.optim.AdamW(params, lr=float(T["lr"]), weight_decay=0.0)

    bs, acc = int(T["batch"]), int(T["grad_accum"])
    W = T.get("series_weight") or {}                     # e.g. {"sukoon": 3}: those lines are seen 3x per epoch
    pool = []
    for x in train_m:
        w = float(W.get(x.get("series", ""), 1)) * (float(T.get("flow_weight", 2)) if x.get("source") == "flow" else 1)
        n = int(w) + (1 if random.random() < w - int(w) else 0)
        pool += [x] * max(0, n)
    if W:
        mins = {}
        for x in pool:
            mins[x.get("series", "?")] = mins.get(x.get("series", "?"), 0) + x["dur"] / 60
        log("   what one epoch shows: " + ", ".join(f"{k} {v:.0f} min" for k, v in sorted(mins.items()))
            + f"  (series_weight {W})")
    order = sorted(pool, key=lambda x: len(feats[x["id"]]["speech"]))
    groups = [order[i:i + bs] for i in range(0, len(order), bs)]
    steps_per_epoch = math.ceil(len(groups) / acc)
    if str(T["epochs"]).lower() == "auto":
        T["epochs"] = max(4, min(15, math.ceil(2500 / steps_per_epoch)))
        log(f"   epochs: {T['epochs']} (auto, {steps_per_epoch} steps each)")
    total = steps_per_epoch * int(T["epochs"])
    warm = min(30, total // 10 + 1)
    lr_at = lambda s: float(T["lr"]) * (min(1.0, (s + 1) / warm) if s < warm else
                                        0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * (s - warm) / max(1, total - warm))))
    epoch, step, best_val, hist = 0, 0, 1e9, []
    last = os.path.join(rdir, "last.pt")
    if resume and os.path.exists(last):
        ck = torch.load(last, map_location="cpu", weights_only=False)
        t3.load_state_dict({k: v.float() for k, v in ck["lora"].items()}, strict=False)
        if ck.get("pace_emb") is not None:
            PC.install(t3, ck["pace_emb"])
        try:
            opt.load_state_dict(ck["opt"])
        except Exception:
            pass
        epoch, step, best_val, hist = ck["epoch"], ck["step"], ck.get("best_val", 1e9), ck.get("hist", [])
        log(f"   continuing after epoch {epoch} (best error so far {best_val:.3f})")

    def evaluate():
        t3.eval()
        rng = random.Random(123)
        tot, n = 0.0, 0
        with torch.no_grad():
            for i in range(0, len(val_m), bs):
                b = val_m[i:i + bs]
                tot += float(loss_of(collate(b, rng))) * len(b); n += len(b)
        t3.train()
        return tot / max(1, n)

    def save(path, ep, val):
        ck = {"lora": lora.state(t3), "rank": int(T["lora_rank"]), "alpha": float(T["lora_alpha"]),
              "spk_emb": spk, "epoch": ep, "val_loss": val, "minutes": minutes, "run": run, "step": step,
              "best_val": best_val, "hist": hist, "pace_emb": pace_emb.weight.detach().cpu().clone(),
              "pace_edges": E, "style_pace": style_pace}
        if path == last:
            ck["opt"] = opt.state_dict()
        torch.save(ck, path + ".part")
        os.replace(path + ".part", path)

    if epoch == 0 and not hist:
        v0 = evaluate()
        hist.append({"epoch": 0, "val": round(v0, 4)})
        log(f"   error on the kept-out lines BEFORE training: {v0:.3f}  (lower = closer to you)")
    t3.train()
    t_start, steps_done = time.time(), 0
    for ep in range(epoch + 1, int(T["epochs"]) + 1):
        rng = random.Random(T["seed"] * 1000 + ep)
        g = groups[:]
        rng.shuffle(g)
        run_loss, n_loss = 0.0, 0
        for k in range(0, len(g), acc):
            opt.zero_grad(set_to_none=True)
            for b in g[k:k + acc]:
                loss = loss_of(collate(b, rng)) / len(g[k:k + acc])
                loss.backward()
                run_loss += float(loss.detach()) * len(g[k:k + acc]); n_loss += 1
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            for pg in opt.param_groups:
                pg["lr"] = lr_at(step)
            opt.step()
            step += 1; steps_done += 1
            if steps_done % 10 == 0:
                rate = (time.time() - t_start) / steps_done
                left = (total - step) * rate
                say(f"   epoch {ep}/{T['epochs']}  step {step}/{total}  error {run_loss / max(1, n_loss):.3f}  "
                    f"{rate:.1f} s/step  ~{left / 60:.0f} min left")
        val = evaluate()
        hist.append({"epoch": ep, "train": round(run_loss / max(1, n_loss), 4), "val": round(val, 4)})
        better = val < best_val
        if better:
            best_val = val
            save(os.path.join(rdir, "best.pt"), ep, val)
        save(last, ep, val)
        log(f"   epoch {ep}: training error {run_loss / max(1, n_loss):.3f}, kept-out lines {val:.3f}"
            + ("  <- best so far, saved" if better else ""))
        json.dump(hist, open(os.path.join(rdir, "history.json"), "w"), indent=1)
        best_ep_so_far = min((h for h in hist if h["epoch"] > 0), key=lambda h: h["val"])["epoch"]
        if ep - best_ep_so_far >= int(T.get("patience", 2)):
            log(f"   no improvement for {ep - best_ep_so_far} epochs -> stopping (best = epoch {best_ep_so_far})")
            break
    best_ep = min((h for h in hist if h["epoch"] > 0), key=lambda h: h["val"], default=None)
    open(os.path.join(rdir, "done.txt"), "w", encoding="utf-8").write(
        f"finished {time.strftime('%Y-%m-%d %H:%M')}; best epoch {best_ep and best_ep['epoch']} "
        f"(kept-out error {best_val:.3f}, before training {hist[0]['val']})\n")
    log(f"\nDONE. best epoch {best_ep and best_ep['epoch']}: kept-out error {hist[0]['val']:.3f} -> {best_val:.3f}")
    log(f"   {os.path.join(rdir, 'best.pt')}\n   next: menu 3 (A/B test) to LISTEN.")
    backup_root = cfg.MODEL_BACKUP_DIR
    if backup_root:
        try:
            backup_run = os.path.join(backup_root, run)
            os.makedirs(backup_run, exist_ok=True)
            for filename in ("best.pt", "last.pt", "done.txt", "history.json"):
                src = os.path.join(rdir, filename)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(backup_run, filename))
            log(f"   checkpoint copy: {backup_run}")
        except OSError as exc:
            log(f"   WARNING: model trained, but backup copy failed: {exc}")
