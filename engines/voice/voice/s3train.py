"""s3train.py - teach the SOUND GENERATOR (S3Gen's flow-matching decoder) your voice.
T3 decides WHAT sound-pieces come and when; S3Gen turns them into the actual sound (timbre, breath, air).
Until now S3Gen only copied a 10-s sample of you. Here its decoder is fine-tuned on your clips with
CosyVoice's own training loss (flow.compute_loss, shipped in Chatterbox): speech tokens -> your real mel.
Only the decoder (+ speaker projection) learns, very gently; the shared model files are never changed.
Output: <media>\\training\\s3_<time>_<profile>\\best.pt (decoder weights), last.pt, train_log.txt.
    run.py s3train [--epochs 8] [--lr 1e-5] [--limit 40] [--profile sukoon_calm|sukoon_only|all]"""
import json, math, os, random, time
import numpy as np
from . import cfg
from .model import load_base, say

S3SR = 24000


def prepare(m, items, feats):
    """mel (24 kHz, 50 frames/s) + x-vector per clip, cached in training\\_s3cache.pt"""
    import torch, librosa
    from chatterbox.models.s3gen.utils.mel import mel_spectrogram
    path = os.path.join(cfg.TRAINING, "_s3cache.pt")
    cache = torch.load(path, weights_only=False) if os.path.exists(path) else {}
    todo = [x for x in items if x["id"] not in cache]
    if todo:
        say(f"   preparing {len(todo)} clips for the sound generator (one time) ...")
        t0 = time.time()
        for k, x in enumerate(todo):
            w24, _ = librosa.load(x["wav"], sr=S3SR)
            w16 = librosa.resample(w24, orig_sr=S3SR, target_sr=16000)
            with torch.inference_mode():
                mel = mel_spectrogram(torch.from_numpy(w24)[None].to(m.device)).squeeze(0)      # (80, T)
                xv = m.s3gen.speaker_encoder.inference(torch.from_numpy(w16)[None].to(m.device))
            cache[x["id"]] = {"mel": mel.half().cpu(), "xv": xv.float().cpu().view(-1)}
            if (k + 1) % 200 == 0 or k + 1 == len(todo):
                say(f"     {k + 1}/{len(todo)}  ({time.time() - t0:.0f} s)")
        torch.save(cache, path + ".part")
        os.replace(path + ".part", path)
    return cache


def main(epochs=8, lr=1e-5, limit=None, profile="sukoon_calm", batch=8, patience=2):
    import torch
    from . import train as T3
    meta = cfg.read_meta()
    tr = [x for x in meta if not x["holdout"]]
    va = [x for x in meta if x["holdout"]]
    run = time.strftime("s3_%Y%m%d_%H%M_") + profile + ("_smoke" if limit else "")
    rdir = os.path.join(cfg.TRAINING, run)
    os.makedirs(rdir, exist_ok=True)
    logf = open(os.path.join(rdir, "train_log.txt"), "a", encoding="utf-8")

    def log(s):
        say(s); logf.write(time.strftime("%H:%M:%S ") + s + "\n"); logf.flush()

    fpath = os.path.join(cfg.TRAINING, "_features.pt")
    feats = torch.load(fpath, weights_only=False)            # T3 features: speech tokens + pace/pitch measures
    tr = [x for x in tr if x["id"] in feats and "f0" in feats[x["id"]]]
    tr = T3.select(tr, feats, profile if profile != "sukoon_only" else "all", log)
    if profile == "sukoon_only":
        tr = [x for x in tr if x.get("series") == "sukoon"]
    if limit:
        random.Random(1).shuffle(tr); tr = tr[:int(limit)]; va = va[:max(4, int(limit) // 5)]
    log(f"== sound generator training '{run}': {len(tr)} lines ({sum(x['dur'] for x in tr) / 60:.0f} min), "
        f"{len(va)} kept out, lr {lr}")
    m = load_base()
    dev = m.device
    m.t3.to("cpu"); m.ve.to("cpu"); torch.cuda.empty_cache()
    cache = prepare(m, tr + va, feats)
    flow = m.s3gen.flow
    for p in m.s3gen.parameters():
        p.requires_grad_(False)
    params = list(flow.decoder.estimator.parameters()) + list(flow.spk_embed_affine_layer.parameters())
    for p in params:
        p.requires_grad_(True)
    log(f"   learning {sum(p.numel() for p in params) / 1e6:.0f} M parameters of the decoder")
    opt = torch.optim.AdamW(params, lr=lr, weight_decay=0.0)

    def make(items):
        toks, mels, xvs = [], [], []
        for x in items:
            tk = feats[x["id"]]["speech"].long()
            mel = cache[x["id"]]["mel"].float()
            L = min(len(tk), mel.shape[1] // 2)
            toks.append(tk[:L]); mels.append(mel[:, :2 * L]); xvs.append(cache[x["id"]]["xv"])
        B, Tm = len(items), max(len(t) for t in toks)
        tok = torch.zeros(B, Tm, dtype=torch.long); feat = torch.zeros(B, 80, 2 * Tm)
        for i in range(B):
            tok[i, :len(toks[i])] = toks[i]; feat[i, :, :mels[i].shape[1]] = mels[i]
        return {"speech_token": tok, "speech_token_len": torch.tensor([len(t) for t in toks]),
                "speech_feat": feat, "speech_feat_len": torch.tensor([m_.shape[1] for m_ in mels]),
                "embedding": torch.stack(xvs)}

    order = sorted(tr, key=lambda x: len(feats[x["id"]]["speech"]))
    groups = [order[i:i + batch] for i in range(0, len(order), batch)]
    groups = [g for g in groups if len(g) > 1]
    total = len(groups) * epochs

    def evaluate():
        flow.eval()
        st = torch.random.get_rng_state(); cst = torch.cuda.get_rng_state() if torch.cuda.is_available() else None
        rs = random.getstate()
        torch.manual_seed(1234); random.seed(1234)
        tot = n = 0
        with torch.no_grad():
            for i in range(0, len(va), batch):
                b = va[i:i + batch]
                if len(b) < 2:
                    continue
                tot += float(flow.compute_loss(make(b), dev)["loss"]) * len(b); n += len(b)
        torch.random.set_rng_state(st); random.setstate(rs)
        if cst is not None:
            torch.cuda.set_rng_state(cst)
        flow.train()
        return tot / max(1, n)

    def save(path, ep, val):
        sd = {k: v.detach().half().cpu() for k, v in flow.state_dict().items()
              if k.startswith("decoder.estimator.") or k.startswith("spk_embed_affine_layer.")}
        torch.save({"flow": sd, "epoch": ep, "val_loss": val, "run": run, "profile": profile}, path + ".part")
        os.replace(path + ".part", path)

    v0 = evaluate()
    log(f"   kept-out error BEFORE: {v0:.4f}")
    best, best_ep, hist, step, t0 = v0, 0, [{"epoch": 0, "val": v0}], 0, time.time()
    flow.train()
    for ep in range(1, epochs + 1):
        rng = random.Random(ep); g = groups[:]; rng.shuffle(g)
        rl = nl = 0
        for b in g:
            loss = flow.compute_loss(make(b), dev)["loss"]
            opt.zero_grad(set_to_none=True); loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 1.0); opt.step()
            rl += float(loss.detach()); nl += 1; step += 1
            if step % 25 == 0:
                rate = (time.time() - t0) / step
                say(f"   epoch {ep}/{epochs}  step {step}/{total}  error {rl / nl:.4f}  {rate:.2f} s/step  "
                    f"~{(total - step) * rate / 60:.0f} min left")
        val = evaluate()
        hist.append({"epoch": ep, "train": rl / max(1, nl), "val": val})
        json.dump(hist, open(os.path.join(rdir, "history.json"), "w"), indent=1)
        save(os.path.join(rdir, "last.pt"), ep, val)
        if val < best:
            best, best_ep = val, ep
            save(os.path.join(rdir, "best.pt"), ep, val)
        log(f"   epoch {ep}: training {rl / max(1, nl):.4f}, kept-out {val:.4f}" + ("  <- best, saved" if best_ep == ep else ""))
        if ep - best_ep >= patience:
            log(f"   no improvement for {patience} epochs -> stop"); break
    open(os.path.join(rdir, "done.txt"), "w").write(f"best epoch {best_ep}: {v0:.4f} -> {best:.4f}\n")
    log(f"DONE sound generator: best epoch {best_ep}, kept-out {v0:.4f} -> {best:.4f}")
    return rdir if best_ep else None
