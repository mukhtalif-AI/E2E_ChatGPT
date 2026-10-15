"""
asr_hindi.py - better Hindi transcript for Sukoon narration.
 * model: vasista22/whisper-hindi-large-v2 (IIT Madras, Hindi fine-tune) via transformers on the GPU
 * lines are cut at the narrator's real pauses (audio energy), never in the middle of a thought
 * word timestamps for word-by-word typography
Runs in ComfyUI's python (torch + transformers already there):
  E:\\ComfyUI_windows_portable\\python_embeded\\python.exe -s asr_hindi.py "D:\\Audacity\\sukoon 4.1.mp3" work\\sukoon_4_1
Writes <outdir>/transcript_hi.json (+ .txt) and, if an old transcript.json exists, asr_compare.txt.
"""
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import json, os, subprocess, sys, time
import numpy as np

REPO = "vasista22/whisper-hindi-large-v2"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402
CACHE = paths.HF_CACHE
FFMPEG = paths.ffmpeg() or "ffmpeg"
SR = 16000
# openai/whisper-large-v2 cross-attention alignment heads (used for word timing)
ALIGN_HEADS = [[10, 12], [13, 17], [16, 11], [16, 12], [16, 13], [17, 15], [17, 16], [18, 4], [18, 11],
               [18, 19], [19, 11], [21, 2], [21, 3], [22, 3], [22, 9], [22, 12], [23, 5], [23, 7], [23, 13],
               [25, 5], [26, 1], [26, 12], [27, 15]]


import re
# what Whisper-family models "hear" in silence / breaths / music (All-India-Radio training data)
HALLU = re.compile(r"समाचार|बुलेटिन|आकाशवाणी|प्रादेशिक|नमस्कार|सब्सक्राइब|प्रधानमंत्री|मुख्यमंत्री")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def load_audio(path):
    ff = FFMPEG if os.path.exists(FFMPEG) else "ffmpeg"
    raw = subprocess.run([ff, "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).copy()


# ------------------------------------------------------------------ pause-based segmentation
def speech_runs(x, hop=0.02):
    n = int(SR * hop)
    f = x[: len(x) // n * n].reshape(-1, n)
    db = 10 * np.log10((f.astype(np.float64) ** 2).mean(1) + 1e-10)
    k = 5                                                     # 100 ms smoothing
    db = np.convolve(db, np.ones(k) / k, mode="same")
    floor, top = np.percentile(db, 15), np.percentile(db, 97)
    thr = max(floor + 0.40 * (top - floor), top - 28.0)       # breaths / room noise are not speech
    on = db > thr
    runs, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            runs.append([i * hop, j * hop])
            i = j
        else:
            i += 1
    merged = []
    for r in runs:                                            # join tiny gaps inside words
        if merged and r[0] - merged[-1][1] < 0.18:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [r for r in merged if r[1] - r[0] > 0.12], float(thr)


def chunks(runs, total, join_gap=0.7, max_len=10.0):
    """Group speech runs into chunks for the model (context) - boundaries only at pauses >= join_gap."""
    out = []
    for s, e in runs:
        if out and s - out[-1][1] < join_gap and e - out[-1][0] < max_len:
            out[-1][1] = e
        else:
            out.append([s, e])
    return [[max(0, s - 0.25), min(total, e + 0.3)] for s, e in out]


# ------------------------------------------------------------------ model
def load_model():
    import torch, warnings
    warnings.filterwarnings("ignore")
    try:
        from transformers.utils import logging as tlog
        tlog.set_verbosity_error()
    except Exception:
        pass
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
    print("loading the Hindi speech model ...", flush=True)
    path = snapshot_download(REPO, cache_dir=CACHE,
                             allow_patterns=["*.json", "*.txt", "pytorch_model.bin"])
    pass
    dt = torch.float16 if torch.cuda.is_available() else torch.float32
    try:
        model = AutoModelForSpeechSeq2Seq.from_pretrained(path, dtype=dt, low_cpu_mem_usage=True, attn_implementation="eager")
    except TypeError:
        model = AutoModelForSpeechSeq2Seq.from_pretrained(path, torch_dtype=dt, low_cpu_mem_usage=True, attn_implementation="eager")
    proc = AutoProcessor.from_pretrained(path)
    tok = proc.tokenizer
    gc = model.generation_config
    tid = tok.convert_tokens_to_ids
    if not getattr(gc, "lang_to_id", None):
        gc.lang_to_id = {"<|hi|>": tid("<|hi|>"), "<|en|>": tid("<|en|>")}
    if not getattr(gc, "task_to_id", None):
        gc.task_to_id = {"transcribe": tid("<|transcribe|>"), "translate": tid("<|translate|>")}
    gc.is_multilingual = True
    if getattr(gc, "no_timestamps_token_id", None) is None:
        gc.no_timestamps_token_id = tid("<|notimestamps|>")
    if not getattr(gc, "alignment_heads", None):
        gc.alignment_heads = ALIGN_HEADS
    gc.forced_decoder_ids = None
    model.config.forced_decoder_ids = None
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(dev).eval()
    print(f"model ready on {'the GPU' if dev == 'cuda' else 'the CPU (slow)'}", flush=True)
    return model, proc, dev, dt


def transcribe_many(model, proc, dev, dt, audios, word_ok):
    """Several phrases in ONE GPU call (batched beam search), then each word lined up to the voice."""
    import torch
    feats = proc.feature_extractor(audios, sampling_rate=SR, return_tensors="pt").input_features.to(dev, dt)
    with torch.inference_mode():
        seqs = model.generate(feats, language="hi", task="transcribe", num_beams=5, max_new_tokens=220)
    tok = proc.tokenizer
    special = set(tok.all_special_ids)
    res = []
    for b, audio in enumerate(audios):
        text_ids = [t for t in seqs[b].tolist() if t not in special]
        if not text_ids:
            res.append([]); continue
        if word_ok[0]:
            try:
                res.append(align_words(model, tok, feats[b:b + 1], text_ids, len(audio) / SR)); continue
            except Exception as ex:
                log("word alignment off:", repr(ex)[:300])
                word_ok[0] = False
                torch.cuda.empty_cache()
        text = tok.decode(text_ids).strip()
        res.append([{"w": w, "start": None, "end": None} for w in text.split()])
    return res


def transcribe_chunk(model, proc, dev, dt, audio, word_ok):
    return transcribe_many(model, proc, dev, dt, [audio], word_ok)[0]


def _dtw(x):
    """Monotonic alignment path through cost matrix x (tokens x frames), as in OpenAI Whisper."""
    N, M = x.shape
    cost = np.full((N + 1, M + 1), np.inf, np.float64); cost[0, 0] = 0
    trace = np.full((N + 1, M + 1), -1, np.int8)
    for j in range(1, M + 1):
        cj, cp = cost[:, j], cost[:, j - 1]
        for i in range(1, N + 1):
            c0, c1, c2 = cp[i - 1], cj[i - 1], cp[i]
            if c0 <= c1 and c0 <= c2:
                cj[i] = x[i - 1, j - 1] + c0; trace[i, j] = 0
            elif c1 <= c2:
                cj[i] = x[i - 1, j - 1] + c1; trace[i, j] = 1
            else:
                cj[i] = x[i - 1, j - 1] + c2; trace[i, j] = 2
    trace[0, :] = 2; trace[:, 0] = 1
    i, j, ti, tj = N, M, [], []
    while i > 0 or j > 0:
        ti.append(i - 1); tj.append(j - 1)
        t = trace[i, j]
        if t == 0:
            i -= 1; j -= 1
        elif t == 1:
            i -= 1
        else:
            j -= 1
    return np.array(ti[::-1]), np.array(tj[::-1])


def align_words(model, tok, feats, text_ids, dur):
    import torch
    tid = tok.convert_tokens_to_ids
    sot = [tid("<|startoftranscript|>"), tid("<|hi|>"), tid("<|transcribe|>")]
    ids = sot + [tid("<|notimestamps|>")] + text_ids + [tid("<|endoftext|>")]
    dec = torch.tensor([ids], device=feats.device)
    with torch.inference_mode():
        out = model(input_features=feats, decoder_input_ids=dec, output_attentions=True)
    # confidence: probability the model gave each text token (logits at j predict ids[j+1])
    lp = torch.log_softmax(out.logits[0].float(), -1)
    pos = torch.arange(len(sot), len(sot) + len(text_ids), device=lp.device)
    tok_p = lp[pos, torch.tensor(text_ids, device=lp.device)].exp().cpu().numpy()
    heads = model.generation_config.alignment_heads or ALIGN_HEADS
    n_frames = int(min(1500, np.ceil(dur * 50)))
    w = torch.stack([out.cross_attentions[l][0, h] for l, h in heads]).float()[:, :, :n_frames]  # [heads, T, F]
    w = w.cpu().numpy()
    w = (w - w.mean(-2, keepdims=True)) / (w.std(-2, keepdims=True) + 1e-6)
    if w.shape[-1] >= 7:                                       # median filter along time (width 7)
        pad = np.pad(w, ((0, 0), (0, 0), (3, 3)), mode="reflect")
        w = np.median(np.lib.stride_tricks.sliding_window_view(pad, 7, axis=-1), axis=-1)
    mat = w.mean(0)[len(sot):-1]                              # rows: prediction of text token k (+ eot)
    ti, tj = _dtw(-mat.astype(np.float64))
    jumps = np.pad(np.diff(ti), (1, 0), constant_values=1).astype(bool)
    jt = tj[jumps] / 50.0                                     # start time of each token (+ eot)
    groups, cur = [], []
    for k, t in enumerate(text_ids):
        piece = tok.convert_ids_to_tokens(t)
        if cur and (piece.startswith("\u0120") or piece.startswith(" ")):
            groups.append(cur); cur = []
        cur.append(k)
    if cur:
        groups.append(cur)
    res = []
    for g in groups:
        word = tok.decode([text_ids[k] for k in g]).strip()
        if not word:
            continue
        st = float(jt[min(g[0], len(jt) - 1)])
        en = float(jt[min(g[-1] + 1, len(jt) - 1)])
        res.append({"w": word, "start": round(st, 2), "end": round(max(en, st + 0.08), 2),
                    "p": round(float(min(tok_p[k] for k in g)), 3)})
    return res


def fill_times(words, s, e, runs):
    """Fallback word timing: spread words over the speech inside [s, e] in proportion to length."""
    sp = [(max(a, s), min(b, e)) for a, b in runs if b > s and a < e] or [(s, e)]
    tot = sum(b - a for a, b in sp)
    L = np.array([len(w["w"]) + 1.5 for w in words], float)
    pos = np.concatenate([[0], np.cumsum(L)]) / L.sum() * tot

    def at(p):
        for a, b in sp:
            if p <= b - a:
                return a + p
            p -= b - a
        return sp[-1][1]
    for i, w in enumerate(words):
        w["start"], w["end"] = round(at(pos[i] + 1e-3), 2), round(at(pos[i + 1] - 1e-3), 2)


# ------------------------------------------------------------------ lines from words + pauses
def to_lines(words, runs, gap=0.9, max_words=16):
    """A new line starts where the narrator actually pauses (>= gap s of silence)."""
    pauses = [(runs[i][1], runs[i + 1][0]) for i in range(len(runs) - 1) if runs[i + 1][0] - runs[i][1] >= gap]
    lines, cur = [], []
    for w in words:
        if cur:
            brk = any(cur[-1]["end"] - 0.15 <= p0 and w["start"] + 0.15 >= p1 for p0, p1 in pauses) \
                  or (w["start"] - cur[-1]["end"] >= gap) or len(cur) >= max_words
            if brk:
                lines.append(cur); cur = []
        w["pause_before"] = round(w["start"] - cur[-1]["end"], 2) if cur else None   # breath inside a line
        cur.append(w)
    if cur:
        lines.append(cur)
    return [{"start": l[0]["start"], "end": l[-1]["end"], "text": " ".join(x["w"] for x in l), "words": l}
            for l in lines]


def main():
    src, outdir = sys.argv[1], sys.argv[2]
    t_lim = float(sys.argv[3]) if len(sys.argv) > 3 else None
    os.makedirs(outdir, exist_ok=True)
    x = load_audio(src)
    if t_lim:
        x = x[: int(t_lim * SR)]
    total = len(x) / SR
    runs, thr = speech_runs(x)
    ch = chunks(runs, total)
    print(f"{total / 60:.1f} min of audio, {sum(e - s for s, e in runs) / 60:.1f} min of speech in {len(ch)} phrases", flush=True)
    model, proc, dev, dt = load_model()
    words, word_ok = [], [True]
    t0 = time.time()
    def speech_in(s, e):
        return sum(max(0.0, min(b, e) - max(a, s)) for a, b in runs)

    def run_span(s, e, depth=0, pre=None):
        """Transcribe [s,e]; if the model skipped speech (too few words), split at the widest pause and retry."""
        ws = pre if pre is not None else transcribe_chunk(model, proc, dev, dt, x[int(s * SR):int(e * SR)], word_ok)
        if ws and ws[0]["start"] is not None:
            for w in ws:
                w["start"] = round(s + w["start"], 2); w["end"] = round(min(e, s + w["end"]), 2)
        else:
            fill_times(ws, s, e, runs)
        if any(HALLU.search(w["w"]) for w in ws):
            return [w for w in ws if not HALLU.search(w["w"])][:0]   # model imagined a news bulletin: no speech here
        sp = speech_in(s, e)
        if depth < 3 and sp > 2.5 and len(ws) < 1.1 * sp:          # narrator speaks ~1.7 words/s
            inner = [r for r in runs if r[0] > s + 0.3 and r[1] < e - 0.3]
            gaps = [(inner[i + 1][0] - inner[i][1], (inner[i][1] + inner[i + 1][0]) / 2) for i in range(len(inner) - 1)]
            mid = max(gaps)[1] if gaps else (s + e) / 2
            alt = run_span(s, mid + 0.1, depth + 1) + run_span(mid - 0.1, e, depth + 1)
            if len(alt) > len(ws):
                pass
                return alt
        return ws

    def save_atomic(obj, path):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)

    B = 8                                                       # phrases per GPU call
    ck = os.path.join(outdir, "_transcript_progress.json")      # crash-safe: resume where we stopped
    start_k = 0
    if os.path.exists(ck):
        try:
            c = json.load(open(ck, encoding="utf-8"))
            if c.get("n_chunks") == len(ch) and c.get("source") == os.path.basename(src):
                words, start_k = c["words"], c["done"]
                print(f"resuming after a stop: {100 * start_k // len(ch)}% was already done", flush=True)
        except Exception:
            pass
    for k0 in range(start_k, len(ch), B):
        grp = ch[k0:k0 + B]
        try:
            pres = transcribe_many(model, proc, dev, dt, [x[int(s * SR):int(e * SR)] for s, e in grp], word_ok)
        except Exception as ex:                                 # e.g. out of GPU memory -> one at a time
            log("batch failed, going one by one:", repr(ex)[:200])
            __import__("torch").cuda.empty_cache()
            pres = [None] * len(grp)
        for (s, e), pre in zip(grp, pres):
            words += run_span(s, e, pre=pre)
        done = k0 + len(grp)
        save_atomic({"n_chunks": len(ch), "source": os.path.basename(src), "done": done, "words": words}, ck)
        el = time.time() - t0
        print(f"{100 * done // len(ch):3d}%  ~{el / max(1, done - start_k) * (len(ch) - done) / 60:.0f} min left   ..."
              f"{' '.join(w['w'] for w in words[-8:])[:70]}", flush=True)
    print(f"understood {len(words)} words in {(time.time() - t0) / 60:.1f} min", flush=True)
    lines = to_lines(words, runs)
    res = {"model": REPO, "source": src, "duration": total, "word_timing": "model" if word_ok[0] else "estimated",
           "segments": lines}
    jp = os.path.join(outdir, "transcript_hi.json")
    save_atomic(res, jp)
    try:
        os.remove(ck)                                           # finished: progress file no longer needed
    except OSError:
        pass
    with open(os.path.join(outdir, "transcript_hi.txt"), "w", encoding="utf-8") as f:
        for l in lines:
            f.write(f"{l['start']:7.2f}-{l['end']:7.2f}  {l['text']}\n")
    old = os.path.join(outdir, "transcript.json")
    if os.path.exists(old):
        o = json.load(open(old, encoding="utf-8"))
        with open(os.path.join(outdir, "asr_compare.txt"), "w", encoding="utf-8") as f:
            for l in lines:
                ov = [s["text"] for s in o if s["end"] > l["start"] - 0.3 and s["start"] < l["end"] + 0.3]
                f.write(f"[{l['start']:7.2f}] NEW: {l['text']}\n          OLD: {' | '.join(ov)}\n\n")
    print(f"transcript saved ({len(lines)} lines)", flush=True)


if __name__ == "__main__":
    main()
