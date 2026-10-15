# ========================================== 
# File: abtest.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\abtest.py 
# ========================================== 
"""abtest.py - does it sound like YOU? The kept-out lines (never trained on) are spoken by
  A = plain Chatterbox clone of your voice prompt (what Kisse used)   B = your trained voice
  C = B + match to you (pitch / pace, if praat is installed)
and compared with your real recording of the same line.
Output <media>\\tests\\<time>\\  compare.wav (each line: YOU -> A -> B), report.txt (numbers), lines\\"""
import json, os, subprocess, time
import numpy as np
from . import cfg
from .model import load_base, resolve_checkpoint, resolve_reference, use_checkpoint, set_voice, speak_sentence, \
    words_per_second, say


def load24(path):
    import librosa
    return librosa.load(path, sr=24000)[0].astype(np.float32)


def level(a, rms=0.08):
    r = float(np.sqrt(np.mean(a ** 2))) if len(a) else 0
    return a * min(rms / r, 0.95 / max(1e-6, float(np.abs(a).max()))) if r > 1e-5 else a


def main(model=None, n=None):
    import soundfile as sf, torch
    from . import asr
    S = dict(cfg.C["speak"])
    meta = [x for x in cfg.read_meta() if x["holdout"]]
    if not meta:
        raise SystemExit("no dataset yet - run menu 1 first")
    meta = meta[:int(n)] if n else meta
    ck_path = resolve_checkpoint(model or "best")
    out = os.path.join(cfg.TESTS, time.strftime("%Y%m%d_%H%M"))
    os.makedirs(os.path.join(out, "lines"), exist_ok=True)
    m = load_base()
    ref = resolve_reference()
    wps = words_per_second()
    S["max_retakes"] = 0                                 # fair test: first take only
    versions = [("A_clone", None)] + ([("B_trained", ck_path)] if ck_path else [])
    if not ck_path:
        say("   no trained voice yet - testing only the plain clone (A)")
    res = {x["id"]: {"text": x["text"], "real": x["wav"]} for x in meta}
    base_S = dict(S)
    for tag, ckp in versions:
        ck = use_checkpoint(m, ckp) if ckp else None
        S = dict(base_S)
        if ck:                                           # trained voice: pauses allowed (brakes.py)
            from .model import patient_brakes
            patient_brakes(S)
        else:
            S["repetition_penalty"] = 2.0                # plain clone = exactly what Chatterbox / Kisse used
        set_voice(m, ref, ck, float(S["exaggeration"]))
        say(f"\n   {tag}:")
        for k, x in enumerate(meta):
            a, info = speak_sentence(m, x["text"], 11 + k, S, wps)
            p = os.path.join(out, "lines", f"{k + 1:02d}_{tag}.wav")
            sf.write(p, a, m.sr, subtype="PCM_16")
            res[x["id"]][tag] = dict(info, wav=p)
            say(f"     {k + 1}/{len(meta)}  {info['dur']} s  letters wrong {info.get('letter_error')}")
    from . import match
    if ck_path and match.available() and cfg.C["speak"].get("match", True):
        versions.append(("C_matched", "match"))
        say("\n   C_matched = B + match to you (pitch / pace):")
        for k, x in enumerate(meta):
            a, sr_ = sf.read(res[x["id"]]["B_trained"]["wav"], dtype="float32")
            y, mi = match.apply(a, sr_, x["text"], "sukoon")
            p = os.path.join(out, "lines", f"{k + 1:02d}_C_matched.wav")
            sf.write(p, y, sr_, subtype="PCM_16")
            res[x["id"]]["C_matched"] = dict(res[x["id"]]["B_trained"], wav=p, dur=round(len(y) / sr_, 2), **mi)
            say(f"     {k + 1}/{len(meta)}  {mi}")
    # speaker likeness: voice-encoder similarity of every take to your REAL line
    ve = m.ve
    emb = lambda a: np.asarray(ve.embeds_from_wavs([np.interp(np.arange(0, len(a), 1.5), np.arange(len(a)), a)
                                                    .astype(np.float32)], sample_rate=16000)[0])
    rows, parts, gap = [], [], lambda s: np.zeros(int(24000 * s), np.float32)
    sums = {t: {"like": [], "le": [], "len": [], "sil": []} for t, _ in versions}

    def silence(a):
        h = 480
        f = a[: len(a) // h * h].reshape(-1, h)
        db = 10 * np.log10((f ** 2).mean(1) + 1e-10)
        return float((db < db.max() - 35).mean()) if len(db) else 0.0
    you_sil = []
    for k, x in enumerate(meta):
        r = res[x["id"]]
        real = load24(x["wav"])
        e_real = emb(real)
        real_dur = len(real) / 24000
        you_sil.append(silence(real))
        parts += [level(real), gap(0.9)]
        line = [f"{k + 1}. {x['text']}", f"   YOU   {real_dur:.1f} s"]
        for tag, _ in versions:
            a = load24(r[tag]["wav"])
            like = float(np.dot(e_real, emb(a)) / (np.linalg.norm(e_real) * np.linalg.norm(emb(a)) + 1e-9))
            sums[tag]["like"].append(like)
            if r[tag].get("letter_error") is not None:
                sums[tag]["le"].append(r[tag]["letter_error"])
            sums[tag]["len"].append(len(a) / 24000 / real_dur)
            sums[tag]["sil"].append(silence(a))
            line.append(f"   {tag:9s} {len(a) / 24000:.1f} s   sounds-like-you {like:.3f}   "
                        f"letters wrong {r[tag].get('letter_error')}")
            parts += [level(a), gap(0.9)]
        parts.append(gap(1.3))
        rows += line
    sf.write(os.path.join(out, "compare.wav"), np.concatenate(parts), 24000, subtype="PCM_16")
    head = ["A/B TEST - each line in compare.wav: YOU -> " + " -> ".join(t for t, _ in versions), "",
            f"voice prompt: {ref}", f"trained voice: {ck_path or '-'}", "",
            "AVERAGES   (sounds-like-you: 1.0 = identical voice;  length: 1.0 = your pace;  pauses = share of silence)",
            f"  YOU       pauses {np.mean(you_sil):.0%}"]
    for tag, _ in versions:
        s = sums[tag]
        head.append(f"  {tag:9s} sounds-like-you {np.mean(s['like']):.3f}   letters wrong "
                    f"{(np.mean(s['le']) if s['le'] else float('nan')):.1%}   length vs you {np.mean(s['len']):.2f}   "
                    f"pauses {np.mean(s['sil']):.0%}")
    open(os.path.join(out, "report.txt"), "w", encoding="utf-8").write("\n".join(head + [""] + rows) + "\n")
    say("\n" + "\n".join(head))
    say(f"\nLISTEN: {os.path.join(out, 'compare.wav')}")
 
 
# ========================================== 
# File: asr.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\asr.py 
# ========================================== 
"""asr.py - "did the voice really say the words?" Hindi Whisper (IIT Madras fine-tune, shared model in hf_models)
hears every take; the letter error rate decides retakes. (Minimal copy of the Studio's asr_hindi model loading.)"""
import difflib, re
import numpy as np
from . import cfg

_M = {}


def _load():
    import torch, warnings
    warnings.filterwarnings("ignore")
    try:
        from transformers.utils import logging as tlog
        tlog.set_verbosity_error(); tlog.disable_progress_bar()       # keep the window + log readable
    except Exception:
        pass
    from huggingface_hub import snapshot_download
    from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
    print("      loading the Hindi checker (Whisper) ...", flush=True)
    path = snapshot_download(cfg.C["asr_model"], cache_dir=cfg.C["hf_models"],
                             allow_patterns=["*.json", "*.txt", "pytorch_model.bin"])
    dt = torch.float16 if torch.cuda.is_available() else torch.float32
    try:
        model = AutoModelForSpeechSeq2Seq.from_pretrained(path, dtype=dt, low_cpu_mem_usage=True)
    except TypeError:
        model = AutoModelForSpeechSeq2Seq.from_pretrained(path, torch_dtype=dt, low_cpu_mem_usage=True)
    proc = AutoProcessor.from_pretrained(path)
    gc, tid = model.generation_config, proc.tokenizer.convert_tokens_to_ids
    if not getattr(gc, "lang_to_id", None):
        gc.lang_to_id = {"<|hi|>": tid("<|hi|>"), "<|en|>": tid("<|en|>")}
    if not getattr(gc, "task_to_id", None):
        gc.task_to_id = {"transcribe": tid("<|transcribe|>"), "translate": tid("<|translate|>")}
    gc.is_multilingual = True
    if getattr(gc, "no_timestamps_token_id", None) is None:
        gc.no_timestamps_token_id = tid("<|notimestamps|>")
    gc.forced_decoder_ids = None
    model.config.forced_decoder_ids = None
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(dev).eval()
    return model, proc, dev, dt


SAME = {"यह": "ये", "वह": "वो", "नही": "नहीं", "हां": "हाँ", "मै": "मैं", "हैं": "है", "थीं": "थी",
        "किसी": "किसी", "ज्यादा": "ज़्यादा"}                      # spellings that sound the same


NUM = {0: "शून्य", 1: "एक", 2: "दो", 3: "तीन", 4: "चार", 5: "पांच", 6: "छह", 7: "सात", 8: "आठ", 9: "नौ", 10: "दस",
       11: "ग्यारह", 12: "बारह", 13: "तेरह", 14: "चौदह", 15: "पंद्रह", 16: "सोलह", 17: "सत्रह", 18: "अठारह",
       19: "उन्नीस", 20: "बीस", 30: "तीस", 40: "चालीस", 50: "पचास", 100: "सौ"}
SAME.update({"छः": "छह", "छे": "छह", "पाँच": "पांच"})


def _digits(t):
    """the checker writes counted numbers as digits (4 5 6) - the script has words (चार... पांच... छह...)"""
    t = t.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    return re.sub(r"\d+", lambda m: f" {NUM.get(int(m.group()), m.group())} ", t)


def _words(t):
    out = []
    for w in re.split(r"\s+", _digits(t)):
        w = re.sub("[^ऀ-ॿ]", "", w)
        if not w or w in ("।", "॥"):
            continue
        w = SAME.get(w, w)
        out.append(w.replace("़", "").replace("ँ", "ं"))
    return out


def norm(t):
    return "".join(_words(t))


def hear(a, sr):
    """what the checker hears in a take (text), or None when the checker can't run."""
    if _M.get("off"):
        return None
    try:
        import torch
        if "m" not in _M:
            _M["m"] = _load()
        model, proc, dev, dt = _M["m"]
        x = a if sr == 16000 else np.interp(np.arange(0, len(a), sr / 16000), np.arange(len(a)), a).astype(np.float32)
        feats = proc.feature_extractor([x], sampling_rate=16000, return_tensors="pt").input_features.to(dev, dt)
        try:
            return fast_decode(model, proc, feats, [len(x) / 16000])[0][0]
        except Exception as e:
            print(f"      (fast decoding failed: {repr(e)[:120]} - using the slow one)", flush=True)
            with torch.inference_mode():
                seq = model.generate(feats, language="hi", task="transcribe", num_beams=2, max_new_tokens=200)
            return proc.tokenizer.decode(seq[0], skip_special_tokens=True)
    except Exception as e:
        print(f"      (checking takes by ear is off: {e})", flush=True)
        _M["off"] = True
        return None


LAST = {}


def missed_words(text):
    """script words the checker did not hear in the last take (Hindi words of 2+ letters)"""
    heard = LAST.get("heard")
    if heard is None:
        return None
    got = _words(heard)
    out = []
    for w in re.findall(r"[\u0900-\u097F]{2,}", text):
        ww = _words(w)
        if not ww:
            continue
        r = max([difflib.SequenceMatcher(None, ww[0], g).ratio() for g in got] or [0])
        if r < 0.55:
            out.append(w)
    return out


def letter_error(a, sr, text):
    heard = hear(a, sr)
    LAST["heard"] = heard
    if heard is None:
        return None
    want, got = _words(text), _words(heard)
    latin = len(re.findall(r"[A-Za-z]{2,}", heard))
    if latin and want:
        # English words said right come back in English letters: leave out as many script words as the checker
        # wrote in English - the ones it matched worst
        sim = [max([difflib.SequenceMatcher(None, w, g).ratio() for g in got] or [0]) for w in want]
        drop = set(sorted(range(len(want)), key=lambda i: sim[i])[:latin])
        want = [w for i, w in enumerate(want) if i not in drop]
    return 1 - difflib.SequenceMatcher(None, "".join(want), "".join(got)).ratio()


def unload():
    if "m" in _M:
        del _M["m"]
        import torch
        torch.cuda.empty_cache()


def fast_decode(model, proc, feats, durs=None, max_new=160):
    """Greedy decoding written out by hand: same model, same choices as generate(num_beams=1), but without the
    library's per-step overhead that grows with batch size (measured: batch 16 = 40 s with generate()).
    Returns [(text, mean log-probability of its tokens)]."""
    import torch
    from transformers.modeling_outputs import BaseModelOutput
    tok = proc.tokenizer
    cid = tok.convert_tokens_to_ids
    eot = cid("<|endoftext|>")
    prefix = [cid("<|startoftranscript|>"), cid("<|hi|>"), cid("<|transcribe|>"), cid("<|notimestamps|>")]
    B, dev = feats.shape[0], feats.device
    V = model.config.vocab_size
    gc = model.generation_config
    mask = torch.zeros(V, dtype=torch.bool, device=dev)
    mask[eot + 1:] = True                                        # language / task / timestamp tokens
    for t in (getattr(gc, "suppress_tokens", None) or []):
        if 0 <= t < V:
            mask[t] = True
    first = mask.clone()
    for t in (getattr(gc, "begin_suppress_tokens", None) or [eot]):
        if 0 <= t < V:
            first[t] = True
    limit = [min(max_new, int(d * 30) + 24) if durs is not None else max_new for d in (durs or [0] * B)]
    toks, lps = [[] for _ in range(B)], [[] for _ in range(B)]
    done = [False] * B
    with torch.inference_mode():
        enc = BaseModelOutput(last_hidden_state=model.model.encoder(feats).last_hidden_state)
        ids = torch.tensor([prefix] * B, device=dev)
        out = model(encoder_outputs=enc, decoder_input_ids=ids, use_cache=True)
        for step in range(max(limit)):
            logits = out.logits[:, -1, :].float()
            logits[:, first if step == 0 else mask] = -float("inf")
            logp = torch.log_softmax(logits, -1)
            nxt = logits.argmax(-1)
            lp = logp.gather(1, nxt[:, None])[:, 0]
            nl, ll = nxt.tolist(), lp.tolist()
            for b in range(B):
                if done[b]:
                    continue
                if nl[b] == eot or len(toks[b]) >= limit[b]:
                    done[b] = True
                else:
                    toks[b].append(nl[b]); lps[b].append(ll[b])
            if all(done):
                break
            nin = torch.tensor([eot if done[b] else nl[b] for b in range(B)], device=dev)[:, None]
            out = model(encoder_outputs=enc, decoder_input_ids=nin, past_key_values=out.past_key_values,
                        use_cache=True)
    return [(" ".join(tok.decode(t, skip_special_tokens=True).split()), float(np.mean(l)) if l else -9.0)
            for t, l in zip(toks, lps)]
 
 
# ========================================== 
# File: aup3.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\aup3.py 
# ========================================== 
"""aup3.py - your narration track out of an Audacity .aup3 project at FULL quality, READ-ONLY.
(Copied + adapted from MukhtalifStudio\\engine\\aup3_voice.py - this project does not import from the Studio.)
The project is opened with SQLite's immutable read-only mode, so it is never modified."""
import os, re, sqlite3
import numpy as np
from .aup3_doc import decode, tracks


def open_ro(path):
    uri = "file:" + os.path.abspath(path).replace("\\", "/")
    con = sqlite3.connect(uri + "?mode=ro&immutable=1", uri=True)
    try:
        con.execute("select count(*) from project").fetchone()
        return con
    except sqlite3.DatabaseError:
        con.close()
        return sqlite3.connect(uri + "?mode=ro", uri=True)


def track_audio(con, tr):
    """float32 samples of one track on the project timeline (index 0 = time 0)."""
    rate = tr["rate"]
    pieces, end = [], 0
    for c in tr["clips"]:
        n = c["numsamples"] or 0
        buf = np.zeros(n, np.float32)
        bl = c["blocks"]
        for k, (start, bid) in enumerate(bl):
            ln = (bl[k + 1][0] if k + 1 < len(bl) else n) - start
            if bid is None or bid <= 0:
                continue
            row = con.execute("select sampleformat, samples from sampleblocks where blockid=?", (bid,)).fetchone()
            if not row:
                continue
            fmt, blob = row
            if (fmt & 0xFFFF) == 0x000F:
                s = np.frombuffer(blob, "<f4")
            elif (fmt >> 16) == 4:
                s = np.frombuffer(blob, "<i4").astype(np.float32) / 8388608.0
            else:
                s = np.frombuffer(blob, "<i2").astype(np.float32) / 32768.0
            m = min(ln, len(s), n - start)
            buf[start:start + m] = s[:m]
        tl = int(round((c.get("trimLeft") or 0) * rate))
        tr_ = int(round((c.get("trimRight") or 0) * rate))
        buf = buf[tl:n - tr_ if tr_ else n]
        off = int(round(((c.get("offset") or 0) + (c.get("trimLeft") or 0)) * rate))
        pieces.append((off, buf))
        end = max(end, off + len(buf))
    out = np.zeros(max(0, end), np.float32)
    for off, buf in pieces:
        a = max(0, off); b0 = a - off
        out[a:a + len(buf) - b0] += buf[b0:]
    return out * float(tr.get("gain") or 1.0)


def speechiness(x, rate):
    n = int(rate * 0.05)
    f = x[: len(x) // n * n].reshape(-1, n)
    db = 10 * np.log10((f.astype(np.float64) ** 2).mean(1) + 1e-10)
    if len(db) < 20:
        return 0.0
    lo, hi = np.percentile(db, 10), np.percentile(db, 90)
    return float(((db < lo + 0.3 * (hi - lo)).mean()) * min(1.0, (hi - lo) / 30))


def voice_track(path):
    """(samples float32, rate, track name) of the narration track."""
    con = open_ro(path)
    try:
        d, doc = con.execute("select dict, doc from project").fetchone()
        trs = tracks(decode(bytes(d), bytes(doc)))
        musicish = re.compile(r" - |music|bgm|ambient|ambience|piano|rain|\.mp3|\.wav|instrumental", re.I)
        voiceish = re.compile(r"audio|voice|vocal|narr|record|mic|vo\b|आवाज़?", re.I)
        cands = []
        for i, t in enumerate(trs):
            if t["mute"] or not t["clips"]:
                continue
            mono = not t.get("linked") and not (i > 0 and trs[i - 1].get("linked"))
            score = (2 if mono else 0) + (2 if voiceish.search(t["name"] or "") else 0) - \
                    (3 if musicish.search(t["name"] or "") else 0)
            cands.append((score, i))
        cands.sort(reverse=True)
        best, best_s = None, -1e9
        for score, i in cands[:3]:
            x = track_audio(con, trs[i])
            total = score + 4 * speechiness(x[: int(trs[i]["rate"] * 300)], trs[i]["rate"])
            if total > best_s:
                best, best_s = (x, int(trs[i]["rate"]), trs[i]["name"]), total
        return best
    finally:
        con.close()
 
 
# ========================================== 
# File: aup3_doc.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\aup3_doc.py 
# ========================================== 
"""aup3_doc.py - decode the binary project document inside an Audacity 3 .aup3 file (ProjectSerializer format)
into a simple tree, and list wave tracks -> clips -> sample blocks. Pure Python, read-only."""
import struct

FT = ["CharSize", "StartTag", "EndTag", "String", "Int", "Bool", "Long", "LongLong", "SizeT",
      "Float", "Double", "Data", "Raw", "Push", "Pop", "Name"]


def decode(dict_blob, doc_blob):
    names, cs = {}, 2

    def rd_str(b, i, n):
        raw = b[i:i + n]
        return raw.decode("utf-16-le" if cs == 2 else "utf-32-le" if cs == 4 else "utf-8", "replace")

    # dictionary of names
    b, i = dict_blob, 0
    while i < len(b):
        t = b[i]; i += 1
        if t == 0:
            cs = b[i]; i += 1
        elif t == 15:
            nid, ln = struct.unpack_from("<HH", b, i); i += 4
            names[nid] = rd_str(b, i, ln); i += ln
        else:
            raise ValueError(f"dict: unexpected type {t} at {i}")
    # document
    root = {"tag": "#root", "attrs": {}, "children": []}
    stack = [root]
    b, i = doc_blob, 0
    while i < len(b):
        t = b[i]; i += 1
        if t == 0:
            cs = b[i]; i += 1
        elif t == 1:
            nid, = struct.unpack_from("<H", b, i); i += 2
            node = {"tag": names.get(nid, f"?{nid}"), "attrs": {}, "children": []}
            stack[-1]["children"].append(node); stack.append(node)
        elif t == 2:
            i += 2; stack.pop()
        elif t == 3:
            nid, ln = struct.unpack_from("<Hi", b, i); i += 6
            stack[-1]["attrs"][names.get(nid, nid)] = rd_str(b, i, ln); i += ln
        elif t in (4, 6):
            nid, v = struct.unpack_from("<Hi", b, i); i += 6
            stack[-1]["attrs"][names.get(nid, nid)] = v
        elif t == 5:
            nid, v = struct.unpack_from("<HB", b, i); i += 3
            stack[-1]["attrs"][names.get(nid, nid)] = bool(v)
        elif t == 7:
            nid, v = struct.unpack_from("<Hq", b, i); i += 10
            stack[-1]["attrs"][names.get(nid, nid)] = v
        elif t == 8:
            nid, v = struct.unpack_from("<HI", b, i); i += 6
            stack[-1]["attrs"][names.get(nid, nid)] = v
        elif t == 9:
            nid, v, d = struct.unpack_from("<Hfi", b, i); i += 10
            stack[-1]["attrs"][names.get(nid, nid)] = v
        elif t == 10:
            nid, v, d = struct.unpack_from("<Hdi", b, i); i += 14
            stack[-1]["attrs"][names.get(nid, nid)] = v
        elif t in (11, 12):
            ln, = struct.unpack_from("<i", b, i); i += 4 + ln
        elif t in (13, 14):
            pass
        elif t == 15:
            nid, ln = struct.unpack_from("<HH", b, i); i += 4
            names[nid] = rd_str(b, i, ln); i += ln
        else:
            raise ValueError(f"doc: unknown field type {t} at offset {i - 1}")
    return root


def walk(node, tag):
    if node["tag"] == tag:
        yield node
    for c in node["children"]:
        yield from walk(c, tag)


def tracks(root):
    """[{name, rate, gain, pan, mute, solo, channel, clips:[{offset, trim_left, blocks:[(start, blockid)], numsamples}]}]"""
    out = []
    for wt in walk(root, "wavetrack"):
        a = wt["attrs"]
        tr = {"name": a.get("name"), "rate": a.get("rate"), "gain": a.get("gain", 1.0), "pan": a.get("pan", 0.0),
              "mute": a.get("mute", False), "solo": a.get("solo", False), "channel": a.get("channel"),
              "linked": a.get("linked"), "clips": []}
        for wc in walk(wt, "waveclip"):
            ca = wc["attrs"]
            for sq in walk(wc, "sequence"):
                blocks = [(bl["attrs"].get("start"), bl["attrs"].get("blockid")) for bl in walk(sq, "waveblock")]
                tr["clips"].append({"offset": ca.get("offset", 0.0), "trimLeft": ca.get("trimLeft", 0.0),
                                    "trimRight": ca.get("trimRight", 0.0), "rate": ca.get("rate"),
                                    "numsamples": sq["attrs"].get("numsamples"), "blocks": blocks})
        out.append(tr)
    return out


if __name__ == "__main__":
    import sys, json
    d = open(sys.argv[1], "rb").read(); doc = open(sys.argv[2], "rb").read()
    root = decode(d, doc)
    for t in tracks(root):
        print({k: v for k, v in t.items() if k != "clips"})
        for c in t["clips"]:
            print("   clip", {k: v for k, v in c.items() if k != "blocks"}, "blocks:", len(c["blocks"]),
                  c["blocks"][:2])
 
 
# ========================================== 
# File: brakes.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\brakes.py 
# ========================================== 
"""brakes.py - Chatterbox's safety brakes (alignment_stream_analyzer.py, MIT, Resemble AI), re-written with
settings that suit slow narration (used for the trained voice):
  - a pause = the silence sound-piece repeated: Chatterbox ends the line after 2 repeats -> here after
    max_same_token (25 = 1 s)
  - the last word may be held / trail off: Chatterbox cuts after 0.2 s -> here after tail_seconds (0.8 s)
  - the other checks (false start, repeating earlier words after the end) are kept as they were."""
import logging

_log = logging.getLogger("sukoonvoice.brakes")
SET = {"max_same": 25, "tail_frames": 20}


def install(max_same=25, tail_seconds=0.8):
    SET["max_same"] = int(max_same)
    SET["tail_frames"] = max(5, int(round(float(tail_seconds) * 25)))
    import chatterbox.models.t3.t3 as t3mod
    from chatterbox.models.t3.inference.alignment_stream_analyzer import AlignmentStreamAnalyzer as Base
    if getattr(t3mod.AlignmentStreamAnalyzer, "_sukoon", False):
        return

    class Patient(Base):
        _sukoon = True

        def step(self, logits, next_token=None):
            import torch
            aligned_attn = torch.stack(self.last_aligned_attns).mean(dim=0)
            i, j = self.text_tokens_slice
            if self.curr_frame_pos == 0:
                A_chunk = aligned_attn[j:, i:j].clone().cpu()
            else:
                A_chunk = aligned_attn[:, i:j].clone().cpu()
            A_chunk[:, self.curr_frame_pos + 1:] = 0
            self.alignment = torch.cat((self.alignment, A_chunk), dim=0)
            A = self.alignment
            T, S = A.shape
            cur = A_chunk[-1].argmax()
            if -4 < cur - self.text_position < 7:
                self.text_position = cur
            false_start = (not self.started) and (A[-2:, -2:].max() > 0.1 or A[:, :4].max() < 0.5)
            self.started = not false_start
            if self.started and self.started_at is None:
                self.started_at = T
            self.complete = self.complete or self.text_position >= S - 3
            if self.complete and self.completed_at is None:
                self.completed_at = T
            long_tail = self.complete and (A[self.completed_at:, -3:].sum(dim=0).max() >= SET["tail_frames"])
            alignment_repetition = self.complete and (A[self.completed_at:, :-5].max(dim=1).values.sum() > 5)
            tid = None
            if next_token is not None:
                tid = next_token.view(-1)[0].item() if isinstance(next_token, torch.Tensor) else int(next_token)
            run = getattr(self, "_run", [None, 0])
            run = [tid, run[1] + 1] if tid is not None and tid == run[0] else [tid, 1]
            self._run = run
            stuck = tid is not None and run[1] >= SET["max_same"]
            if cur < S - 3 and S > 5:
                logits[..., self.eos_idx] = -2 ** 15                    # not finished the text yet: no ending
            if long_tail or alignment_repetition or stuck:
                _log.info(f"ending line: tail={bool(long_tail)} repeat={bool(alignment_repetition)} stuck={stuck}")
                logits = -(2 ** 15) * torch.ones_like(logits)
                logits[..., self.eos_idx] = 2 ** 15
            self.curr_frame_pos += 1
            return logits

    t3mod.AlignmentStreamAnalyzer = Patient
 
 
# ========================================== 
# File: breaths.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\breaths.py 
# ========================================== 
"""breaths.py - YOUR real inhales, taken from your Sukoon recordings, placed in some of the longer pauses of
a narration so it breathes like you (not a sound effect).

build():  scans the Sukoon .aup3 projects (read-only) for inhales inside pauses: soft noise without pitch,
          0.2-1.0 s, not touching any word (>= 0.1 s gap both sides). Each is cut tightly with 30 ms fades.
          -> <media>\\breaths\\sukoon\\NN_<episode>.wav + index.json, and _preview.wav = all of them with
          their numbers spoken as beeps-free gaps (listen once, DELETE the .wav files that are not good).
place():  while joining a narration: in pauses >= breath_min_pause, with probability by "breaths" setting
          (light / normal), never two pauses in a row, never the same breath twice in a row; ends 0.15-0.35 s
          before the next phrase; loudness = how loud that breath was next to your speech in its episode."""
import glob, json, os, random, re
import numpy as np
from . import cfg

BDIR = os.path.join(cfg.MEDIA, "breaths", "sukoon")


def say(*a):
    print(*a, flush=True)


def _projects():
    out = []
    for folder in (cfg.C.get("recordings") or {}).get("folders", []) + [cfg.C.get("episodes_dir") or ""]:
        if os.path.isdir(folder):
            out += [f for f in glob.glob(os.path.join(folder, "**", "*.aup3"), recursive=True)
                    if "sukoon" in os.path.basename(f).lower()]
    seen, res = set(), []
    for f in out:                                      # same project in two folders -> once
        k = re.sub(r"[^0-9a-z]", "", os.path.splitext(os.path.basename(f))[0].lower())
        if k not in seen:
            seen.add(k); res.append(f)
    ex = [e.lower() for e in (cfg.C.get("recordings") or {}).get("exclude", [])]
    import fnmatch
    return [f for f in sorted(res) if not any(fnmatch.fnmatch(os.path.basename(f).lower(), e) for e in ex)]


def detect(x16, runs):
    """breath events [(start_s, end_s, level_vs_speech_dB)] in the pauses between speech runs."""
    h = 320
    n = len(x16) // h
    f = x16[: n * h].reshape(n, h)
    db = 10 * np.log10((f.astype(np.float64) ** 2).mean(1) + 1e-12)
    floor, top = np.percentile(db, 10), np.percentile(db, 97)
    F = np.abs(np.fft.rfft(f * np.hanning(h), axis=1)) ** 2
    freqs = np.fft.rfftfreq(h, 1 / 16000)
    cen = (F * freqs).sum(1) / (F.sum(1) + 1e-12)
    ac = np.fft.irfft(F, axis=1)[:, :h]
    voic = ac[:, 16000 // 300:16000 // 70].max(1) / (ac[:, 0] + 1e-12)
    ev = []
    for (a0, a1), (b0, b1) in zip(runs, runs[1:]):
        if b0 - a1 < 0.6:
            continue
        lo, hi = a1 + 0.12, b0 - 0.12                  # never near a word
        i0, i1 = int(lo / 0.02), int(hi / 0.02)
        if i1 - i0 < 10:
            continue
        on = (db[i0:i1] > floor + 3) & (db[i0:i1] < top - 15) & (voic[i0:i1] < 0.5) & (cen[i0:i1] > 700)
        best, s = (0, 0, 0), None
        for k, v in enumerate(list(on) + [False]):
            if v and s is None:
                s = k
            if not v and s is not None:
                if k - s > best[0]:
                    best = (k - s, s, k)
                s = None
        L, s0, s1 = best
        if not 0.2 <= L * 0.02 <= 1.0:
            continue
        if s0 == 0 or s1 >= len(on):                   # touches the edge of the safe zone -> likely a word tail
            continue
        st, en = (i0 + s0) * 0.02, (i0 + s1) * 0.02
        ev.append((st, en, float(db[i0 + s0:i0 + s1].mean() - top)))
    return ev


def build():
    from .aup3 import voice_track
    from .recordings import to16, speech_runs
    import soundfile as sf, soxr
    os.makedirs(BDIR, exist_ok=True)
    projects = _projects()
    if not projects:
        raise SystemExit("no Sukoon .aup3 projects found in recordings folders / episodes_dir")
    index, n = [], 0
    for f in projects:
        name = os.path.splitext(os.path.basename(f))[0]
        try:
            x, r, t = voice_track(f)
        except Exception as e:
            say(f"   {name}: cannot read ({e})"); continue
        x16 = to16(x, r)
        ev = detect(x16, speech_runs(x16))
        say(f"   {name}: {len(ev)} breaths")
        for st, en, lvl in ev:
            n += 1
            a = x[int((st - 0.03) * r):int((en + 0.03) * r)].astype(np.float32)
            y = soxr.resample(a, r, 24000) if r != 24000 else a
            fd = int(0.03 * 24000)
            if len(y) > 3 * fd:
                y[:fd] *= np.linspace(0, 1, fd); y[-fd:] *= np.linspace(1, 0, fd)
            fn = f"{n:03d}_{re.sub(r'[^0-9A-Za-z]+', '_', name)}.wav"
            sf.write(os.path.join(BDIR, fn), y, 24000, subtype="PCM_16")
            index.append({"file": fn, "episode": name, "at": round(st, 2), "dur": round(len(y) / 24000, 2),
                          "level_db": round(lvl, 1)})
    json.dump(index, open(os.path.join(BDIR, "index.json"), "w", encoding="utf-8"), indent=1)
    parts = []                                         # preview: each breath, gain up so you can hear it
    for it in index:
        y, _ = sf.read(os.path.join(BDIR, it["file"]), dtype="float32")
        pk = float(np.abs(y).max()) or 1
        parts += [y / pk * 0.5, np.zeros(int(0.9 * 24000), np.float32)]
    if parts:
        sf.write(os.path.join(BDIR, "_preview.wav"), np.concatenate(parts), 24000, subtype="PCM_16")
    open(os.path.join(BDIR, "_preview_order.txt"), "w", encoding="utf-8").write(
        "in _preview.wav each breath is played louder than real, then 0.9 s silence - in this order:\n" +
        "\n".join(f"{k + 1:3d}. {it['file']}  ({it['dur']} s)" for k, it in enumerate(index)) +
        "\n\nDelete the .wav files that are not clean inhales; the rest are used.\n")
    say(f"\nBREATH LIBRARY: {len(index)} breaths in {BDIR}\n  listen: _preview.wav (order in _preview_order.txt),"
        f" delete the bad .wav files - only the remaining ones are used")


def library():
    p = os.path.join(BDIR, "index.json")
    if not os.path.exists(p):
        return []
    import soundfile as sf
    out = []
    for it in json.load(open(p, encoding="utf-8")):
        f = os.path.join(BDIR, it["file"])
        if os.path.exists(f):                          # deleted by you = not used
            it = dict(it, audio=sf.read(f, dtype="float32")[0])
            out.append(it)
    return out


def place(pieces_plan, sr, setting="light", seed=1, S=None):
    """pieces_plan: [(phrase_audio, pause_after_s)] -> list of audio pieces with breaths in some pauses.
    Returns (pieces, count)."""
    S = S or cfg.C["speak"]
    lib = library() if setting not in ("off", "", None, False) else []
    if not lib:
        out = []
        for a, p in pieces_plan:
            out += [a, np.zeros(int(p * sr), np.float32)]
        return out, 0
    rng = random.Random(seed)
    prob = {"light": 0.3, "normal": 0.55, "more": 0.8}.get(str(setting), 0.3)
    min_pause = float(S.get("breath_min_pause", 1.0))
    sp = [float(np.sqrt(np.mean(a ** 2))) for a, _ in pieces_plan if len(a)]
    speech_rms = float(np.median(sp)) if sp else 0.05
    out, last_used, last_breath, count = [], -9, None, 0
    for k, (a, p) in enumerate(pieces_plan):
        out.append(a)
        gap = np.zeros(int(p * sr), np.float32)
        if p >= min_pause and k - last_used > 1 and k + 1 < len(pieces_plan) and \
                rng.random() < prob * (1.4 if p >= float(S.get("pause_paragraph", 2.2)) else 1.0):
            cands = [b for b in lib if b["file"] != last_breath and b["dur"] < p - 0.3] or []
            if cands:
                b = rng.choice(cands)
                y = b["audio"].copy()
                rms = float(np.sqrt(np.mean(y ** 2))) or 1e-4
                target = speech_rms * 10 ** (max(-40.0, min(-12.0, b["level_db"] + 6)) / 20)
                y = y * (target / rms)
                end = len(gap) - int(rng.uniform(0.15, 0.35) * sr)
                st = max(0, end - len(y))
                gap[st:st + len(y)] += y[: len(gap) - st]
                last_used, last_breath, count = k, b["file"], count + 1
        out.append(gap)
    return out, count
 
 
# ========================================== 
# File: cfg.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\cfg.py 
# ========================================== 
"""HubGPT Voice settings. Paths come from the HubGPT root config.json; SV_* env vars override them."""
import json, os, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(ROOT, "config.json")
LIBS = os.path.join(ROOT, "libs", "cb")            # Chatterbox + its small libraries (never a 2nd PyTorch)
CACHE = os.path.join(ROOT, ".cache")               # pip / torch caches stay on E:, never C:


def load():
    with open(CONFIG_FILE, encoding="utf-8-sig") as f:
        c = json.load(f)
    hub_root = os.path.dirname(os.path.dirname(ROOT))
    with open(os.path.join(hub_root, "config.json"), encoding="utf-8-sig") as f:
        hub = json.load(f)
    paths = hub.get("paths") or {}
    layout = hub.get("layout") or {}
    c.update({
        "media_dir": paths.get("voice_data_root", c.get("media_dir", "")),
        "model_dir": paths.get("voice_models", os.path.join(ROOT, "models")),
        "model_backup_dir": paths.get("voice_model_backup", c.get("model_backup_dir", "")),
        "hf_models": paths.get("hf_models", c.get("hf_models", "")),
        "shared_pythons": [paths.get("gpu_python", "")],
        "ffmpeg": paths.get("ffmpeg", c.get("ffmpeg", "")),
        "episodes_dir": paths.get("episodes_root", ""),
        "legacy_episodes_dir": paths.get("voice_legacy_episodes_root", ""),
        "import_transcripts_from": (paths.get("voice_import_roots") or [""])[0],
        "legacy_import_transcripts_from": (paths.get("voice_import_roots") or ["", ""])[1],
    })
    if c.get("model_dir") and not os.path.isabs(c["model_dir"]):
        c["model_dir"] = os.path.abspath(os.path.join(hub_root, c["model_dir"]))
    c.setdefault("recordings", {})["folders"] = paths.get("voice_recording_roots", c.get("recordings", {}).get("folders", []))
    c.setdefault("speak", {})["music"] = paths.get("voice_music_default", c.get("speak", {}).get("music", ""))
    c["_hub_layout"] = layout
    c["_hub_paths"] = paths
    for k, e in (("media_dir", "SV_MEDIA"), ("hf_models", "SV_HF_MODELS"), ("ffmpeg", "SV_FFMPEG"),
                 ("model_dir", "SV_MODEL_DIR"), ("model_backup_dir", "SV_MODEL_BACKUP_DIR"),
                 ("episodes_dir", "SV_EPISODES"), ("import_transcripts_from", "SV_IMPORT")):
        if os.environ.get(e):
            c[k] = os.environ[e]
    return c


C = load()
LAYOUT = C.get("_hub_layout", {})
MEDIA = C["media_dir"]
DATASET = os.path.join(MEDIA, LAYOUT.get("voice_dataset", "dataset"))
CLIPS = os.path.join(DATASET, LAYOUT.get("voice_clips", "clips"))
REFS = os.path.join(DATASET, LAYOUT.get("voice_references", "refs"))
META = os.path.join(DATASET, "metadata.jsonl")
MODEL_DIR = C.get("model_dir") or os.path.join(ROOT, "models")
if not os.path.isabs(MODEL_DIR):
    MODEL_DIR = os.path.abspath(os.path.join(ROOT, MODEL_DIR))
MODEL_BACKUP_DIR = C.get("model_backup_dir") or ""
TRAINING = MODEL_DIR
TESTS = os.path.join(MEDIA, LAYOUT.get("voice_tests", "tests"))
PROJECTS = os.path.join(DATASET, LAYOUT.get("voice_projects", "projects"))
NARRATIONS = os.environ.get("SV_NARRATIONS") or C["_hub_paths"].get("narrations_root", os.path.join(MEDIA, LAYOUT.get("narrations", "narrations")))
TMP = os.path.join(MEDIA, LAYOUT.get("temporary", "_tmp"))


def ffmpeg():
    for c in (C.get("ffmpeg"), shutil.which("ffmpeg")):
        if c and os.path.exists(c):
            return c
    raise SystemExit("ffmpeg not found - set \"ffmpeg\" in config.json")


def read_meta():
    if not os.path.exists(META):
        return []
    with open(META, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def runs():
    """training runs, newest first: [(name, folder)]"""
    if not os.path.isdir(TRAINING):
        return []
    r = [(n, os.path.join(TRAINING, n)) for n in os.listdir(TRAINING)
         if os.path.isdir(os.path.join(TRAINING, n)) and not n.startswith("_")]
    return sorted(r, key=lambda x: os.path.getmtime(x[1]), reverse=True)


def best_checkpoint():
    for n, d in runs():
        if n.startswith("s3_") or "_smoke" in n or "_series_" in n:   # a series' own voice is only used when asked
            continue
        p = os.path.join(d, "best.pt")
        if os.path.exists(p):
            return p
    return None
 
 
# ========================================== 
# File: dataset.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\dataset.py 
# ========================================== 
"""dataset.py - build the training set: your Sukoon lines as short clips + their exact text.

Source of line timings: the configured episode tree's _work folders:
transcript_script.json = lines matched to your script (best), else transcript_hi.json = speech recogniser
lines, kept only when it was sure). The audio is taken at FULL quality from your .aup3 in episodes_dir when it
is found and lines up; otherwise from that folder's 16 kHz voice.wav.
Everything is copied into  <media>\\dataset\\  (clips\\, refs\\, metadata.jsonl) - training never reads the
source folders again. Safe to re-run: existing clips are kept, new episodes are added.
"""
import glob, hashlib, json, os, re, subprocess, wave
import numpy as np
from . import cfg
from .aup3 import voice_track

SR_OUT = 24000


def say(*a):
    print(*a, flush=True)


def norm_name(s):
    return re.sub(r"[^0-9a-z]", "", os.path.splitext(os.path.basename(s))[0].lower())


def read_wav16(path):
    with wave.open(path) as w:
        n, rate, ch, sw = w.getnframes(), w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    return x, rate


def signature(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(size // 2)
        return f"{size}-{hashlib.md5(f.read(65536)).hexdigest()}"


def write_clip(path, x, rate):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    r = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    if r > 1e-5:                                              # same loudness for every clip (about -20 dBFS)
        x = x * min(0.1 / r, 0.95 / max(1e-6, float(np.abs(x).max())))
    try:                                                      # fast path: in this process (no ffmpeg start-up)
        import soundfile as sf, soxr
        y = soxr.resample(np.clip(x, -1, 1).astype(np.float32), rate, SR_OUT, quality="HQ") if rate != SR_OUT else x
        sf.write(path + ".part.wav", np.clip(y, -1, 1), SR_OUT, subtype="PCM_16")
        os.replace(path + ".part.wav", path)
        return
    except ImportError:
        pass
    p = subprocess.run([cfg.ffmpeg(), "-v", "error", "-y", "-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "-",
                        "-ar", str(SR_OUT), "-ac", "1", "-c:a", "pcm_s16le", path + ".part.wav"],
                       input=np.clip(x, -1, 1).astype("<f4").tobytes())
    if p.returncode:
        raise SystemExit(f"ffmpeg could not write {path}")
    os.replace(path + ".part.wav", path)


def find_aup3s():
    out = {}
    roots = (cfg.C.get("episodes_dir") or "", cfg.C.get("legacy_episodes_dir") or r"D:\Mukhtalif\Episodes")
    for root in roots:
        if os.path.isdir(root):
            for dp, dn, fn in os.walk(root):
                for f in fn:
                    if f.lower().endswith(".aup3"):
                        out[norm_name(f)] = os.path.join(dp, f)
                        out.setdefault(norm_name(os.path.basename(dp)), os.path.join(dp, f))
    return out


def sources():
    roots = (cfg.C.get("import_transcripts_from") or "", cfg.C.get("legacy_import_transcripts_from") or "")
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        voice_files = glob.glob(os.path.join(root, "**", "voice.wav"), recursive=True)
        for v in voice_files:
            d = os.path.dirname(v)
            name = os.path.basename(os.path.dirname(d)) if os.path.basename(d).lower() == "_work" else os.path.basename(d)
            for tf, kind in (("transcript_script.json", "script"), ("transcript_hi.json", "asr")):
                if os.path.exists(os.path.join(d, tf)):
                    found.append({"name": name, "dir": d, "voice": v, "transcript": os.path.join(d, tf), "kind": kind,
                                  "sig": signature(v)})
                    break
    best = {}                                                 # same recording in two folders: keep the script one
    for s in found:
        b = best.get(s["sig"])
        if b is None or (b["kind"] != "script" and s["kind"] == "script"):
            best[s["sig"]] = s
    return list(best.values())


def good_lines(src):
    D = cfg.C["dataset"]
    j = json.load(open(src["transcript"], encoding="utf-8"))
    segs = j["segments"] if isinstance(j, dict) else j
    segs = [s for s in segs if (s.get("text") or "").strip() and s.get("end") is not None]
    keep = []
    for i, s in enumerate(segs):
        dur = s["end"] - s["start"]
        if not D["min_sec"] <= dur <= D["max_sec"]:
            continue
        if not re.search("[ऀ-ॿ]", s["text"]):
            continue
        source = "script" if src["kind"] == "script" and s.get("source") in (None, "script") else "asr"
        if source == "asr":
            ps = [w.get("p") for w in s.get("words") or [] if w.get("p") is not None]
            if not ps or np.mean(ps) < D["asr_min_avg_p"] or min(ps) < D["asr_min_word_p"]:
                continue
        prev_end = segs[i - 1]["end"] if i else 0.0
        next_start = segs[i + 1]["start"] if i + 1 < len(segs) else s["end"] + 1.0
        a = max(s["start"] - 0.12, (prev_end + s["start"]) / 2)
        b = min(s["end"] + 0.25, (s["end"] + next_start) / 2)
        keep.append({"start": round(a, 3), "end": round(b, 3), "text": " ".join(s["text"].split()), "source": source,
                     "speech": (s["start"], s["end"])})
    return keep


def pick_holdout(meta, n):
    cand = [m for m in meta if m["source"] == "script" and 6 <= len(m["text"].split()) <= 22]
    if len(cand) < n:
        cand = [m for m in meta if 6 <= len(m["text"].split()) <= 22]
    if not cand:
        return
    for k in np.linspace(0, len(cand) - 1, min(n, len(cand))).astype(int):
        cand[k]["holdout"] = True


def pick_reference(x, rate, lines, ep, used_ids):
    """8-10.5 s of steady, continuous speech (consecutive lines, short gaps) = the voice prompt."""
    best, best_score = None, -1
    for i in range(len(lines)):
        if lines[i]["id"] in used_ids:
            continue
        s0 = lines[i]["speech"][0]
        speech, j = 0.0, i
        while j < len(lines) and lines[j]["id"] not in used_ids:
            if j > i and lines[j]["speech"][0] - lines[j - 1]["speech"][1] > 0.9:
                break
            speech += lines[j]["speech"][1] - lines[j]["speech"][0]
            span = lines[j]["speech"][1] - s0
            if 8.0 <= span <= 10.5:
                score = speech / span + (0.2 if lines[i]["source"] == "script" else 0)
                if score > best_score:
                    best, best_score = (s0, lines[j]["speech"][1], " ".join(l["text"] for l in lines[i:j + 1])), score
            if span > 10.5:
                break
            j += 1
    if not best:
        return None
    a, b, text = best
    path = os.path.join(cfg.REFS, f"{ep}.wav")
    write_clip(path, x[int((a - 0.08) * rate):int((b + 0.2) * rate)], rate)
    open(path[:-4] + ".txt", "w", encoding="utf-8").write(text + "\n")
    return {"episode": ep, "wav": path, "text": text, "score": round(best_score, 3), "dur": round(b - a, 2)}


def my_voice(m, meta):
    """your voice vector = average voice-encoder embedding of your script-matched lines (or refs\\reference.wav)."""
    import librosa
    pick = [x for x in meta if x["source"] == "script"] or meta
    pick = [pick[k] for k in np.linspace(0, len(pick) - 1, min(80, len(pick))).astype(int)] if pick else []
    wavs = [librosa.load(x["wav"], sr=16000)[0] for x in pick]
    if not wavs:
        r = os.path.join(cfg.REFS, "reference.wav")
        sample = cfg.C.get("my_voice_sample") or r
        if not os.path.exists(sample):
            raise SystemExit("need a sample of your voice: set \"my_voice_sample\" in config.json to a clean .wav of you")
        wavs = [librosa.load(sample, sr=16000)[0]]
    e = np.asarray(m.ve.embeds_from_wavs(wavs, sample_rate=16000), np.float32)
    e = e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-8)
    v = e.mean(0)
    return v / np.linalg.norm(v)


def main(rebuild=False, skip_recordings=False):
    srcs = sources()
    if not srcs:
        say("no episodes with line timings found in import_transcripts_from = "
            f"{cfg.C.get('import_transcripts_from')!r}")
    use_rec = bool((cfg.C.get("recordings") or {}).get("folders")) and not skip_recordings
    if use_rec:                         # episodes WITHOUT a script: the recordings step does them better (whole
        from . import recordings        # project, its own checks) - only script-matched episodes are imported here
        rec = {norm_name(f) for f in recordings.files()}
        srcs = [s_ for s_ in srcs if s_["kind"] == "script" or norm_name(s_["name"]) not in rec]
    aups = find_aup3s()
    old = {m["id"]: m for m in cfg.read_meta()}
    meta, refs = [], []
    say(f"{len(srcs)} episodes with line timings")
    for src in srcs:
        ep = norm_name(src["name"])
        lines = good_lines(src)
        say(f"\n== {src['name']}  ({'matched to your script' if src['kind'] == 'script' else 'speech recogniser'}): "
            f"{len(lines)} usable lines, {sum(l['end'] - l['start'] for l in lines) / 60:.1f} min")
        if not lines:
            continue
        for k, l in enumerate(lines):
            l["id"] = f"{ep}_{k:04d}"
        todo = rebuild or any(not os.path.exists(os.path.join(cfg.CLIPS, l["id"] + ".wav")) for l in lines) or \
            not os.path.exists(os.path.join(cfg.REFS, ep + ".wav"))
        x = rate = None
        if todo:
            v16, r16 = read_wav16(src["voice"])
            x, rate, how = v16, r16, "16 kHz voice.wav"
            ap = aups.get(ep)
            if ap:
                try:
                    say(f"   reading full-quality voice from {os.path.basename(ap)} ...")
                    nx, nr, tname = voice_track(ap)
                    if abs(len(nx) / nr - len(v16) / r16) < 1.0:
                        x, rate, how = nx, nr, f"{os.path.basename(ap)} track '{tname}' {nr} Hz"
                    else:
                        say(f"   (the .aup3 is {len(nx) / nr:.0f} s long, the timings are for {len(v16) / r16:.0f} s"
                            " - using voice.wav)")
                except Exception as e:
                    say(f"   (could not read the .aup3: {e} - using voice.wav)")
            say(f"   audio: {how}")
        for k, l in enumerate(lines):
            p = os.path.join(cfg.CLIPS, l["id"] + ".wav")
            if rebuild or not os.path.exists(p):
                write_clip(p, x[int(l["start"] * rate):int(l["end"] * rate)], rate)
            m = {"id": l["id"], "wav": p, "text": l["text"], "dur": round(l["end"] - l["start"], 3),
                 "episode": src["name"], "series": "sukoon" if "sukoon" in src["name"].lower() else "story",
                 "source": l["source"], "holdout": bool(old.get(l["id"], {}).get("holdout"))}
            meta.append(m)
            if (k + 1) % 50 == 0:
                say(f"   clips {k + 1}/{len(lines)}")
        if x is not None:
            r = pick_reference(x, rate, lines, ep, set())
            if r:
                refs.append(r)
                say(f"   voice prompt: {r['dur']} s  \"{r['text'][:70]}...\"")
    if use_rec:
        from .model import load_base
        m = load_base()
        me = my_voice(m, meta)
        rmeta, rrefs = recordings.main(m, {norm_name(s_["name"]) for s_ in srcs}, me)
        for x_ in rmeta:
            x_["holdout"] = bool(old.get(x_["id"], {}).get("holdout"))
        meta += rmeta
        refs += rrefs
    if not meta:
        raise SystemExit("no usable lines found - check import_transcripts_from / recordings in config.json")
    want = int(cfg.C["dataset"]["holdout"])
    have = sum(1 for m in meta if m["holdout"])
    if have < want:                                   # top up (earlier kept-out lines stay kept out)
        rest = [m for m in meta if not m["holdout"]]
        pick_holdout(rest, want - have)
    os.makedirs(cfg.DATASET, exist_ok=True)
    with open(cfg.META + ".part", "w", encoding="utf-8") as f:
        for m in meta:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    os.replace(cfg.META + ".part", cfg.META)
    rj = os.path.join(cfg.REFS, "refs.json")
    allrefs = {r["episode"]: r for r in (json.load(open(rj, encoding="utf-8")) if os.path.exists(rj) else [])}
    allrefs.update({r["episode"]: r for r in refs})
    if allrefs:
        os.makedirs(cfg.REFS, exist_ok=True)
        json.dump(list(allrefs.values()), open(rj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        top = max(allrefs.values(), key=lambda r: r["score"])
        main_ref = os.path.join(cfg.REFS, "reference.wav")
        if not os.path.exists(main_ref) or rebuild:
            import shutil
            shutil.copyfile(top["wav"], main_ref)
            shutil.copyfile(top["wav"][:-4] + ".txt", main_ref[:-4] + ".txt")
    tr = [m for m in meta if not m["holdout"]]
    for ser in sorted({m.get("series", "?") for m in meta}):
        say(f"  {ser:7s}: {sum(m['dur'] for m in meta if m.get('series') == ser) / 60:.1f} min")
    say(f"\nDATASET: {len(meta)} lines = {sum(m['dur'] for m in meta) / 60:.1f} min of your voice "
        f"({sum(m['dur'] for m in meta if m['source'] == 'script') / 60:.1f} min script-matched)")
    say(f"  training: {len(tr)} lines, {sum(m['dur'] for m in tr) / 60:.1f} min   |   kept out for the A/B test: "
        f"{len(meta) - len(tr)} lines")
    say(f"  folder: {cfg.DATASET}")
    say("  voice prompt for speaking: dataset\\refs\\reference.wav  (any refs\\<episode>.wav can be set in config)")
 
 
# ========================================== 
# File: flowdata.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\flowdata.py 
# ========================================== 
"""flowdata.py - 'flow clips': 2-4 of your Sukoon lines in a row, cut WITH the real pauses, breaths and
continuing tone between them.

Why: the normal dataset has one phrase per clip, so the model learned that every phrase ENDS (falling,
'instruction' tone, then stop). Flow clips teach it how you carry on from one phrase into the next.

    run.py flowdata          -> <media>\\dataset\\flow_clips\\*.wav + dataset\\metadata_flow.jsonl
Kept-out (holdout) lines are never used. Only reads the Studio transcripts + voice.wav (16 kHz is enough: the
speech model learns from 16 kHz speech tokens).
"""
import json, os, random, re, wave
import numpy as np
from . import cfg
from .dataset import sources, good_lines, norm_name, read_wav16

MAX_SPAN = 15.5       # seconds per flow clip
MAX_GAP = 3.0         # a longer silence ends the flow
SHORT_GAP = 0.45      # shorter gap = comma, longer = '...'


def say(*a):
    print(*a, flush=True)


def flow_meta_path():
    return os.path.join(os.path.dirname(cfg.META), "metadata_flow.jsonl")


def read_flow_meta():
    p = flow_meta_path()
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def join_text(parts, gaps):
    """same joining as speak.py's flow mode: short gap = comma, longer = '...'"""
    out = parts[0].strip().rstrip(" ,।.")
    for t, g in zip(parts[1:], gaps):
        out += (", " if g < SHORT_GAP else "... ") + t.strip().rstrip(" ,।.")
    return out + "."


def write16(path, x, rate):
    if rate != 16000:
        n = int(len(x) * 16000 / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
    r = float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0
    if r > 1e-5:
        x = x * min(0.1 / r, 0.95 / max(1e-6, float(np.abs(x).max())))
    y = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(path + ".part", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(y.tobytes())
    os.replace(path + ".part", path)


def main(series="sukoon", dry=False):
    meta = {m["id"]: m for m in cfg.read_meta()}
    out_dir = os.path.join(os.path.dirname(cfg.META), "flow_clips")
    if not dry:
        os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(11)
    rows = []
    D = cfg.C["dataset"]
    for src in sources():
        if series not in src["name"].lower():
            continue
        ep = norm_name(src["name"])
        good = good_lines(src)
        hold = set()
        for k, l in enumerate(good):
            m = meta.get(f"{ep}_{k:04d}")
            if m is None or m.get("holdout") or m.get("text") != l["text"]:
                hold.add(round(l["speech"][0], 2))     # kept-out (or unknown) line: never inside a flow clip
        j = json.load(open(src["transcript"], encoding="utf-8"))
        segs = j["segments"] if isinstance(j, dict) else j
        segs = [s_ for s_ in segs if (s_.get("text") or "").strip() and s_.get("end") is not None]

        def usable(s_):
            if round(s_["start"], 2) in hold or not re.search("[\u0900-\u097F]", s_["text"]):
                return False
            if src["kind"] == "script" and s_.get("source") in (None, "script"):
                return True
            ps = [w.get("p") for w in s_.get("words") or [] if w.get("p") is not None]
            return bool(ps) and np.mean(ps) >= D["asr_min_avg_p"] and min(ps) >= D["asr_min_word_p"]

        if len(segs) < 2:
            continue
        x, rate = (None, None) if dry else read_wav16(src["voice"])
        made = 0
        for k in range(len(segs) - 1):
            n_want = rng.choice([2, 3, 3, 4, 4])
            if not usable(segs[k]):
                continue
            grp = [segs[k]]
            for jx in range(k + 1, min(len(segs), k + n_want)):
                gap = segs[jx]["start"] - grp[-1]["end"]
                if not usable(segs[jx]) or gap > MAX_GAP or gap < 0 or segs[jx]["end"] - grp[0]["start"] > MAX_SPAN:
                    break
                grp.append(segs[jx])
            if len(grp) < 2:
                continue
            gaps = [b_["start"] - a_["end"] for a_, b_ in zip(grp, grp[1:])]
            text = join_text([" ".join(g["text"].split()) for g in grp], gaps)
            prev_end = segs[k - 1]["end"] if k else 0.0
            nxt = segs[k + len(grp)]["start"] if k + len(grp) < len(segs) else grp[-1]["end"] + 1.0
            a = max(grp[0]["start"] - 0.12, (prev_end + grp[0]["start"]) / 2)
            b = min(grp[-1]["end"] + 0.25, (grp[-1]["end"] + nxt) / 2)
            fid = f"flow_{ep}_{k:04d}_{len(grp)}"
            p = os.path.join(out_dir, fid + ".wav")
            if not dry and not os.path.exists(p):
                write16(p, x[int(a * rate):int(b * rate)], rate)
            rows.append({"id": fid, "wav": p, "text": text, "dur": round(b - a, 3), "episode": src["name"],
                         "series": series, "source": "flow", "holdout": False, "lines": len(grp)})
            made += 1
        say(f"   {src['name']}: {made} flow clips")
    say(f"DONE flow clips: {len(rows)} clips, {sum(r['dur'] for r in rows) / 60:.0f} min")
    if dry:
        return rows
    with open(flow_meta_path() + ".part", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(flow_meta_path() + ".part", flow_meta_path())
    say(f"   -> {flow_meta_path()}")
    return rows
 
 
# ========================================== 
# File: grid.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\grid.py 
# ========================================== 
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
 
 
# ========================================== 
# File: install.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\install.py 
# ========================================== 
"""install.py - makes SukoonVoice ready on this PC (run by setup.bat). Asks before every download.
Chatterbox's libraries go into libs\\cb one by one (--no-deps) so the shared Python's PyTorch / numpy are never
replaced (same method Kisse used - copied, not imported)."""
import os, re, subprocess, sys
from . import cfg

PY = sys.executable
T = cfg.LIBS
MAP = {"perth": "resemble-perth", "sklearn": "scikit-learn", "yaml": "pyyaml", "spacy_pkuseg": "spacy-pkuseg",
       "antlr4": "antlr4-python3-runtime", "lazy_loader": "lazy-loader", "cv2": "opencv-python-headless",
       "google": "protobuf", "pkg_resources": "setuptools", "attr": "attrs", "dateutil": "python-dateutil",
       "PIL": "pillow", "jwt": "pyjwt", "importlib_metadata": "importlib-metadata", "huggingface_hub": "huggingface-hub",
       "_soundfile_data": "soundfile"}
NEVER = {"torch", "torchaudio", "torchvision", "numpy"}
FIRST = ["chatterbox-tts", "soundfile", "librosa", "s3tokenizer", "resemble-perth", "conformer", "diffusers==0.29.0",
         "omegaconf", "antlr4-python3-runtime==4.9.3", "pykakasi", "audioread", "soxr", "lazy-loader", "pooch",
         "numba", "llvmlite", "msgpack", "decorator", "joblib", "scikit-learn", "threadpoolctl", "einops", "pyloudnorm"]


def ask(q, default=True):
    try:
        a = input(f"  {q} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    except EOFError:
        return default
    return default if not a else a.startswith("y")


def pip(*pk):
    print("  $ pip install", " ".join(pk), flush=True)
    return subprocess.run([PY, "-s", "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                           "--no-deps", "--target", T, "--upgrade", *pk]).returncode


def try_import(code):
    r = subprocess.run([PY, "-s", "-c", f"import sys; sys.path.insert(0, r'{T}'); {code}"], capture_output=True,
                       text=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return r.returncode, r.stdout + r.stderr


def cached(repo, hub_layout=True):
    base = os.path.join(cfg.C["hf_models"], "hub") if hub_layout else cfg.C["hf_models"]
    d = os.path.join(base, "models--" + repo.replace("/", "--"), "snapshots")
    return os.path.isdir(d) and any(os.scandir(d))


def main():
    print("\n== 1/4  Python + graphics card", flush=True)
    rc, out = try_import("import torch, numpy; print('torch', torch.__version__, '| CUDA', torch.cuda.is_available(),"
                         " '|', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no GPU')")
    print("  " + out.strip())
    if rc:
        raise SystemExit("this Python has no PyTorch - run setup.bat again and choose 2 (own Python)")
    print("\n== 2/4  ffmpeg")
    try:
        print("  ok:", cfg.ffmpeg())
    except SystemExit as e:
        print("  !!", e)
    print("\n== 3/4  Chatterbox libraries ->", T)
    rc, _ = try_import("import chatterbox.mtl_tts")
    if rc:
        if not ask("install Chatterbox + its small libraries into libs\\cb (~300 MB, nothing outside this folder)?"):
            raise SystemExit("stopped")
        os.makedirs(T, exist_ok=True)
        pip(*FIRST)
        for i in range(30):
            rc, out = try_import("import chatterbox.mtl_tts")
            if rc == 0:
                break
            mm = re.search(r"No module named '([\w\.]+)'", out)
            if not mm:
                print(out[-3000:])
                raise SystemExit("import failed (see above) - send logs\\last_run.txt")
            mod = mm.group(1).split(".")[0]
            if mod in NEVER:
                raise SystemExit(f"needs {mod} - not installing a second copy over the shared Python's")
            pk = MAP.get(mod, mod.replace("_", "-"))
            print(f"  missing {mod} -> {pk}")
            if pip(pk):
                raise SystemExit(f"could not install {pk}")
    rc, out = try_import("import chatterbox.mtl_tts, transformers, librosa; print('chatterbox OK | transformers',"
                         " transformers.__version__)")
    print("  " + out.strip().splitlines()[-1])
    rc, _ = try_import("import parselmouth")
    if rc:
        if ask("install praat-parselmouth (~15 MB, into libs\\cb) for 'match to you' - your pitch and pace?"):
            pip("praat-parselmouth")
    rc, out = try_import("import parselmouth; print('praat (pitch / pace matching) OK', parselmouth.VERSION)")
    print("  " + (out.strip().splitlines()[-1] if out.strip() else "praat not installed - matching is off"))
    print("\n== 4/4  models (shared folder", cfg.C["hf_models"] + ")")
    have_cb = cached("ResembleAI/chatterbox")
    have_asr = cached(cfg.C["asr_model"], hub_layout=False)
    print(f"  Chatterbox Multilingual (~3 GB): {'already there' if have_cb else 'not downloaded'}")
    print(f"  Hindi checker {cfg.C['asr_model']} (~3 GB): {'already there' if have_asr else 'not downloaded'}")
    want_cb = have_cb or ask("download Chatterbox Multilingual now (~3 GB)?")
    want_asr = have_asr or ask("download the Hindi checker now (~3 GB; without it takes are not checked by ear)?")
    import importlib                                   # libs\cb did not exist when this Python started:
    sys.path_importer_cache.pop(T, None)               # forget that, so its new libraries are found now
    if T not in sys.path:
        sys.path.insert(0, T)
    importlib.invalidate_caches()
    if want_asr and not have_asr:
        from huggingface_hub import snapshot_download
        snapshot_download(cfg.C["asr_model"], cache_dir=cfg.C["hf_models"],
                          allow_patterns=["*.json", "*.txt", "pytorch_model.bin"])
    if want_cb:
        print("\n  test: one Hindi sentence ...", flush=True)
        import numpy as np, soundfile as sf
        from .model import load_base
        m = load_base()
        import torch
        with torch.inference_mode():
            w = m.generate("आज की रात हम बस आराम करेंगे.", language_id="hi")
        os.makedirs(cfg.TMP, exist_ok=True)
        p = os.path.join(cfg.TMP, "setup_test.wav")
        sf.write(p, w.squeeze(0).cpu().numpy(), m.sr)
        print(f"  ok - {p}")
    print("\nSETUP DONE. Next: sukoon_voice.bat -> 1 (build dataset) -> 2 (train) -> 3 (A/B test)")
 
 
# ========================================== 
# File: lora.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\lora.py 
# ========================================== 
"""lora.py - a small trainable add-on (LoRA) on the attention + MLP layers of Chatterbox's speech model (T3).
The shared model files are never changed: the add-on is saved separately (~45 MB) and merged in memory."""
import math
import torch
from torch import nn
import torch.nn.functional as F

TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")


class LoRALinear(nn.Module):
    def __init__(self, base: nn.Linear, r: int, alpha: float):
        super().__init__()
        self.base, self.r, self.scale = base, r, alpha / r
        dev = base.weight.device
        self.lora_A = nn.Parameter(torch.empty(r, base.in_features, device=dev, dtype=torch.float32))
        self.lora_B = nn.Parameter(torch.zeros(base.out_features, r, device=dev, dtype=torch.float32))
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))

    def forward(self, x):
        return self.base(x) + F.linear(F.linear(x, self.lora_A.to(x.dtype)), self.lora_B.to(x.dtype)) * self.scale


def apply(t3, r, alpha):
    """wrap every target Linear inside t3.tfmr; returns the new trainable parameters."""
    params = []
    for name, mod in list(t3.tfmr.named_modules()):
        for child_name, child in list(mod.named_children()):
            if child_name in TARGETS and isinstance(child, nn.Linear):
                w = LoRALinear(child, r, alpha)
                setattr(mod, child_name, w)
                params += [w.lora_A, w.lora_B]
    return params


def state(t3):
    return {k: v.detach().to("cpu", torch.float16) for k, v in t3.state_dict().items() if ".lora_" in k}


def load_into(t3, ckpt):
    """apply the add-on from a checkpoint dict and merge it into the weights (fast speaking afterwards)."""
    apply(t3, ckpt["rank"], ckpt["alpha"])
    missing = t3.load_state_dict({k: v.float() for k, v in ckpt["lora"].items()}, strict=False)
    bad = [k for k in missing.unexpected_keys]
    if bad:
        raise SystemExit(f"checkpoint does not fit this model ({len(bad)} unknown weights)")
    merge(t3)


@torch.no_grad()
def merge(t3):
    for name, mod in list(t3.tfmr.named_modules()):
        for child_name, child in list(mod.named_children()):
            if isinstance(child, LoRALinear):
                b = child.base
                b.weight += ((child.lora_B @ child.lora_A) * child.scale).to(device=b.weight.device, dtype=b.weight.dtype)
                setattr(mod, child_name, b)
 
 
# ========================================== 
# File: match.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\match.py 
# ========================================== 
"""match.py - "match to you": brings each generated phrase to YOUR measured pitch and pace, with Praat's
PSOLA (pitch moved, voice colour / formants kept; time stretched without chipmunk effects).
Targets are measured once from your Sukoon lines in the dataset -> dataset\\voice_targets.json.
Small corrections only (limits in config: match_max_semitones, match_max_stretch) - beyond that PSOLA
starts to sound processed. Needs praat-parselmouth (setup.bat installs it into libs\\cb, asks first)."""
import json, os
import numpy as np
from . import cfg

_T = {}


def available():
    try:
        import parselmouth  # noqa: F401
        return True
    except Exception:
        return False


def _f0(snd):
    from parselmouth.praat import call
    p = snd.to_pitch(time_step=0.01, pitch_floor=65, pitch_ceiling=300)
    f = p.selected_array["frequency"]
    f = f[f > 0]
    return float(np.median(f)) if len(f) >= 5 else 0.0


def _rate(a, sr, words):
    from .pace import measure
    return measure(a, sr, words)[0]


def targets(style="sukoon"):
    """your median pitch + speech pace per style, measured once from the dataset."""
    if style in _T:
        return _T[style]
    p = os.path.join(cfg.DATASET, "voice_targets.json")
    allt = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    if style not in allt:
        import parselmouth, soundfile as sf
        meta = [m for m in cfg.read_meta() if m.get("series", "story") == style and not m["holdout"]]
        meta = [meta[k] for k in np.linspace(0, len(meta) - 1, min(200, len(meta))).astype(int)]
        f0s, rates = [], []
        for m in meta:
            a, sr = sf.read(m["wav"], dtype="float32")
            f = _f0(parselmouth.Sound(a, sr))
            if f > 0:
                f0s.append(f)
            rates.append(_rate(a, sr, len(m["text"].split())))
        allt[style] = {"f0": float(np.median(f0s)), "rate": float(np.median(rates)), "lines": len(meta)}
        json.dump(allt, open(p, "w", encoding="utf-8"), indent=1)
        print(f"      your {style} voice: pitch {allt[style]['f0']:.0f} Hz, {allt[style]['rate']:.1f} words/s "
              f"(measured on {len(meta)} lines)", flush=True)
    _T[style] = allt[style]
    return _T[style]


def apply(a, sr, text, style="sukoon", S=None):
    """returns (audio, info). Never raises - on any problem the phrase is returned unchanged."""
    S = S or cfg.C["speak"]
    try:
        import parselmouth
        from parselmouth.praat import call
        T = targets(style)
        snd = parselmouth.Sound(a.astype(np.float64), sr)
        f = _f0(snd)
        info = {}
        ms = float(S.get("match_max_semitones", 4))
        pf = 1.0
        if f > 0 and S.get("match_pitch", True):
            st = float(np.clip(12 * np.log2(T["f0"] / f), -ms, ms))
            pf = 2 ** (st / 12)
            info["pitch_semitones"] = round(st, 2)
        df = 1.0
        if S.get("match_pace", True):
            r = _rate(a, sr, len(text.split()))
            mx = float(S.get("match_max_stretch", 0.2))
            if r > 0:
                df = float(np.clip(r / T["rate"], 1 - mx, 1 + mx))       # >1 = slower (longer)
                info["stretch"] = round(df, 3)
        if abs(pf - 1) < 0.01 and abs(df - 1) < 0.02:
            return a, info
        man = call(snd, "To Manipulation", 0.01, 65, 300)
        if abs(pf - 1) >= 0.01:
            pt = call(man, "Extract pitch tier")
            call(pt, "Multiply frequencies", snd.xmin, snd.xmax, pf)
            call([pt, man], "Replace pitch tier")
        if abs(df - 1) >= 0.02:
            dt = call("Create DurationTier", "dur", snd.xmin, snd.xmax)
            call(dt, "Add point", snd.xmin, df)
            call([dt, man], "Replace duration tier")
        out = call(man, "Get resynthesis (overlap-add)")
        y = out.values[0].astype(np.float32)
        peak = float(np.abs(y).max()) or 1.0
        if peak > 0.99:
            y = y / peak * 0.95
        return y, info
    except Exception as e:
        return a, {"match_error": repr(e)[:120]}
 
 
# ========================================== 
# File: model.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\model.py 
# ========================================== 
"""model.py - loading Chatterbox Multilingual (shared model in hf_models) + your trained add-on."""
import json, os, re, time
import numpy as np
from . import cfg


def say(*a):
    print(*a, flush=True)


def load_base():
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    if dev != "cuda":
        say("   !! no graphics card found by PyTorch - this will be VERY slow")
    t0 = time.time()
    say(f"   loading Chatterbox Multilingual on {dev} (first time downloads ~3 GB into {cfg.C['hf_models']}) ...")
    m = ChatterboxMultilingualTTS.from_pretrained(device=dev)
    say(f"   loaded in {time.time() - t0:.0f} s")
    return m


def resolve_checkpoint(choice):
    """'best' -> newest run's best.pt | 'base' -> None | a path."""
    choice = (choice or "best").strip()
    if choice.lower() == "base":
        return None
    if choice.lower() == "best":
        return cfg.best_checkpoint()
    if choice.lower().startswith("latest:"):                       # newest finished run of that profile
        tag = choice.split(":", 1)[1]
        for _, d in cfg.runs():
            n = os.path.basename(d)
            if tag in n and "_smoke" not in n and not n.startswith("s3_") and os.path.exists(os.path.join(d, "best.pt")):
                return os.path.join(d, "best.pt")
        raise SystemExit(f"no finished training run with '{tag}' in its name")
    if os.path.exists(choice):
        return choice
    d = os.path.join(cfg.TRAINING, choice, "best.pt")              # a run name
    if os.path.exists(d):
        return d
    raise SystemExit(f"checkpoint not found: {choice}")


def resolve_s3gen(choice):
    """'base'/None -> no change | 'best' -> newest s3_* run's best.pt | run name | path"""
    if not choice or str(choice).lower() == "base":
        return None
    if str(choice).lower() == "best":
        rs = [d for _, d in cfg.runs() if os.path.basename(d).startswith("s3_") and "_smoke" not in d
              and os.path.exists(os.path.join(d, "best.pt"))]
        return os.path.join(rs[0], "best.pt") if rs else None
    if os.path.exists(choice):
        return choice
    p = os.path.join(cfg.TRAINING, choice, "best.pt")
    if os.path.exists(p):
        return p
    raise SystemExit(f"sound generator checkpoint not found: {choice}")


def use_s3gen(m, path):
    import torch
    ck = torch.load(path, map_location="cpu", weights_only=False)
    sd = {k: v.float() for k, v in ck["flow"].items()}
    missing = m.s3gen.flow.load_state_dict(sd, strict=False)
    if missing.unexpected_keys:
        raise SystemExit("sound generator checkpoint does not fit")
    say(f"   your trained sound generator: {os.path.relpath(path, cfg.TRAINING)} (epoch {ck.get('epoch')})")
    return ck


def resolve_reference(choice=None):
    choice = choice or cfg.C["speak"].get("reference") or "auto"
    if choice == "auto":
        p = os.path.join(cfg.REFS, "reference.wav")
        if not os.path.exists(p):
            raise SystemExit("no voice prompt yet - run 'build dataset' first (menu 1)")
        return p
    if os.path.exists(choice):
        return choice
    exact = os.path.join(cfg.REFS, choice if choice.lower().endswith(".wav") else choice + ".wav")
    if os.path.exists(exact):
        return exact
    import re as _re
    key = _re.sub(r"[^0-9a-z]", "", os.path.splitext(os.path.basename(choice))[0].lower())
    p = os.path.join(cfg.REFS, key + ".wav")
    if os.path.exists(p):
        return p
    have = sorted(f[:-4] for f in os.listdir(cfg.REFS) if f.endswith(".wav")) if os.path.isdir(cfg.REFS) else []
    raise SystemExit(f"style / reference not found: {choice}  (available: {', '.join(have)})")


def use_checkpoint(m, path):
    """merge a trained add-on into the loaded model; returns the checkpoint info."""
    import torch
    from . import lora
    ck = torch.load(path, map_location="cpu", weights_only=False)
    lora.load_into(m.t3, ck)
    if ck.get("pace_emb") is not None:
        from . import pace
        pace.install(m.t3, ck["pace_emb"])
    m.t3.eval()
    say(f"   your trained voice: {os.path.relpath(path, cfg.TRAINING)}  (epoch {ck.get('epoch')}, "
        f"trained on {ck.get('minutes', '?')} min)")
    return ck


def patient_brakes(S):
    """let pauses happen (see brakes.py) - used for the trained voice"""
    from . import brakes
    brakes.install(S.get("max_same_token", 25), S.get("tail_seconds", 0.8))


def pace_class(ck, style="sukoon", setting="auto"):
    """the pace class to ask for: your style's own (auto), or 'slower' / 'faster' / 'more pauses' etc."""
    from . import pace
    if not ck or ck.get("style_pace") is None:
        return None
    sp = ck["style_pace"]
    k = sp.get(style) or sp.get("sukoon") or next(iter(sp.values()))
    s = str(setting or "auto").lower()
    k = pace.shift(k, faster=(s.count("faster") - s.count("slower")),
                   more_pauses=(s.count("more pauses") - s.count("fewer pauses")))
    return k


def set_voice(m, reference, ck=None, exaggeration=0.5, style="sukoon", pace_setting="auto"):
    """voice prompt from the reference clip; with a trained add-on, the speaker identity = the average of ALL
    your training lines (steadier than one clip) and the pace = your style's own (pace.py)."""
    import torch
    m.prepare_conditionals(reference, exaggeration=exaggeration)
    if ck is not None and ck.get("spk_emb") is not None:
        m.conds.t3.speaker_emb = ck["spk_emb"].to(m.device, torch.float32).view(1, -1)
    k = pace_class(ck, style, pace_setting)
    m._pace = k
    if k is not None:
        from . import pace
        m.conds.t3.pace = torch.tensor([k], device=m.device)
        say(f"   pace: {pace.describe(k)}")


SAY_AS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "say_as.json")
_SAY = {}


def say_as():
    """words the voice should read with a different spelling (captions keep the real one): say_as.json"""
    try:
        mt = os.path.getmtime(SAY_AS_FILE)
    except OSError:
        return {}
    if _SAY.get("mt") != mt:
        try:
            d = json.load(open(SAY_AS_FILE, encoding="utf-8-sig"))
            _SAY["map"] = {k: v for k, v in (d.get("words") or {}).items() if k and v and not k.startswith("_")}
        except Exception:
            _SAY["map"] = {}
        _SAY["mt"] = mt
    return _SAY["map"]


def for_model(s):
    for k, v in say_as().items():                        # whole words only
        s = re.sub(rf"(?<![\u0900-\u097F]){re.escape(k)}(?![\u0900-\u097F])", v, s)
    s = s.replace("।", ".").replace("॥", ".").replace("‘", "").replace("’", "").replace('"', "")
    s = s.replace("“", "").replace("”", "")
    s = re.sub(r"^\.\.\.\s*", "", s)
    return " ".join(s.split()).strip()


def trim(a, sr, rel=0.012, keep=0.06, keep_end=0.25, fade_in=0.012):
    peak = float(np.abs(a).max()) if len(a) else 0
    if peak <= 0:
        return a
    idx = np.where(np.abs(a) > peak * rel)[0]
    k = int(keep * sr)
    a = a[max(0, idx[0] - k):idx[-1] + int(keep_end * sr)].copy()     # soft endings / trailing breath kept
    f, g = min(len(a) // 4, int(fade_in * sr)), min(len(a) // 4, int(0.08 * sr))
    if f > 1:
        a[:f] *= np.linspace(0, 1, f)
    if g > 1:
        a[-g:] *= np.linspace(1, 0, g) ** 2                            # gentle fade, no click
    return a


def stretch_pauses(a, sr, min_pause=0.6, found=0.22):
    """inside one breath-group: a quiet gap of at least `found` s that is shorter than `min_pause` is made
    longer with its OWN quiet sound (room tone + breath tail), crossfaded - the speech itself is untouched."""
    hop = int(0.01 * sr)
    if len(a) < hop * 10:
        return a
    n = len(a) // hop
    e = np.sqrt(np.mean(a[:n * hop].reshape(n, hop) ** 2, 1) + 1e-12)
    thr = max(1e-5, np.percentile(e, 95) * 0.03)          # ~ -30 dB below the loud parts
    q = e < thr
    runs, i = [], 0
    while i < n:
        if q[i]:
            j = i
            while j < n and q[j]:
                j += 1
            if i > 5 and j < n - 5 and (j - i) * 0.01 >= found:      # inner gaps only
                runs.append((i * hop, j * hop))
            i = j
        else:
            i += 1
    if not runs:
        return a
    out, last = [], 0
    for s0, s1 in runs:
        gap = (s1 - s0) / sr
        out.append(a[last:s1])
        need = int((min_pause - gap) * sr)
        if need > 0:
            fill = a[s0:s1]
            k = int(np.ceil(need / max(1, len(fill)))) + 1
            fill = np.tile(fill, k)[:need].copy()
            f = min(len(fill) // 4, int(0.03 * sr))
            if f > 1:
                fill[:f] *= np.linspace(0, 1, f); fill[-f:] *= np.linspace(1, 0, f)
            out.append(fill)
        last = s1
    out.append(a[last:])
    return np.concatenate(out).astype(np.float32)


def words_per_second():
    meta = cfg.read_meta()
    w = sum(len(m["text"].split()) for m in meta)
    d = sum(max(0.5, m["dur"] - 0.37) for m in meta)
    return w / d if d > 30 else 2.3


def repeat_score(a, sr):
    """how strongly a 60-250 ms piece of sound is repeated right after itself (a 'stutter' glitch).
    Real speech of yours: almost always < 0.85; a glitch: ~0.9+."""
    a = np.asarray(a, np.float32)
    h, w, nb = int(0.01 * sr), int(0.025 * sr), 40
    n = 1 + (len(a) - w) // h if len(a) > w else 0
    if n < 40:
        return 0.0
    idx = np.arange(w)[None] + h * np.arange(n)[:, None]
    F = np.abs(np.fft.rfft(a[idx] * np.hanning(w).astype(np.float32), axis=1)) ** 2
    fr = np.fft.rfftfreq(w, 1 / sr)
    mel = lambda f: 2595 * np.log10(1 + f / 700)
    hz = 700 * (10 ** (np.linspace(mel(60), mel(min(7600, sr / 2 - 1)), nb + 2) / 2595) - 1)
    B = np.zeros((nb, len(fr)), np.float32)
    for i in range(nb):
        l, c, r = hz[i], hz[i + 1], hz[i + 2]
        B[i] = np.clip(np.minimum((fr - l) / (c - l), (r - fr) / (r - c)), 0, None)
    M = np.log(F @ B.T + 1e-8)
    e = M.max(1)
    sp = e > np.percentile(e, 90) - 6
    Z = M - M.mean(1, keepdims=True)
    Z /= np.linalg.norm(Z, axis=1, keepdims=True) + 1e-8
    best = 0.0
    for L in range(6, 26):
        s_ = (Z[:-L] * Z[L:]).sum(1)
        hh = max(2, L // 2)
        s2 = (Z[:-hh] * Z[hh:]).sum(1)[:len(s_)]
        g = sp[:len(s_)] & sp[L:]
        k = np.convolve(np.where(g, s_ - s2, -1.0), np.ones(L) / L, "valid")
        if len(k):
            best = max(best, float(k.max()))
    return best


def _likeness(m, a):
    """how much a take sounds like him: cosine of its voice fingerprint vs the trained one (None if unavailable)."""
    try:
        import librosa, torch
        ref = getattr(m.conds.t3, "speaker_emb", None)
        if ref is None:
            return None
        w = librosa.resample(np.asarray(a, np.float32), orig_sr=m.sr, target_sr=16000)
        e = np.asarray(m.ve.embeds_from_wavs([w], sample_rate=16000), np.float32).reshape(-1)
        r = ref.detach().float().cpu().numpy().reshape(-1)
        if e.shape != r.shape:
            return None
        return float(e @ r / (np.linalg.norm(e) * np.linalg.norm(r) + 1e-9))
    except Exception:
        return None


def speak_sentence(m, text, seed, S, wps, label=""):
    """one sentence, best of several takes: every take is scored on wrong letters (Hindi checker), length,
    pitch closeness to his voice and voice likeness; the best one is kept. Returns (audio, info)."""
    import torch
    from . import asr
    from .pace import pitch as f0_of, semitones
    want = max(0.8, len(text.split()) / wps) + 0.7 * text.count("...") + 0.2 * text.count(",")
    best, best_err, info = None, 1e9, {}
    missed_all = None
    min_takes = max(1, int(S.get("takes", 1)))
    tries = max(min_takes, 1 + int(S.get("max_retakes", 3)))
    f_target = float(S.get("target_pitch_hz", 0) or 0)
    ok = False
    for attempt in range(tries):
        torch.manual_seed(seed * 1009 + attempt * 7919)
        if getattr(m, "_pace", None) is not None and getattr(m.conds.t3, "pace", None) is None:
            m.conds.t3.pace = torch.tensor([m._pace], device=m.device)    # generate() rebuilt the conditioning
        with torch.inference_mode():
            wav = m.generate(for_model(text), language_id="hi", exaggeration=float(S["exaggeration"]),
                             cfg_weight=float(S["cfg"]), temperature=float(S["temperature"]),
                             repetition_penalty=float(S.get("repetition_penalty", 2.0)))
        lk_ = float(S.get("lead_keep", 0.06))          # >0.06: the in-breath before a phrase is kept whole and
        a = trim(wav.squeeze(0).float().cpu().numpy().astype(np.float32), m.sr,   # faded in (not clipped = gasp)
                 keep=lk_, fade_in=max(0.012, 0.6 * lk_) if lk_ > 0.06 else 0.012)
        if S.get("flow") and float(S.get("flow_min_pause", 0) or 0) > 0 and ("..." in text or "," in text):
            a = stretch_pauses(a, m.sr, float(S["flow_min_pause"]))
        d = len(a) / m.sr
        le = asr.letter_error(a, m.sr, text)
        _mw = asr.missed_words(text)                    # words missed in EVERY take = words this voice stumbles on
        if _mw is not None:
            missed_all = set(_mw) if attempt == 0 or missed_all is None else missed_all & set(_mw)
        err = (le if le is not None else 0.0) + 0.3 * abs(np.log(max(d, 0.1) / want))
        st, lk = None, None
        if min_takes > 1:
            if f_target > 0:
                f = f0_of(a, m.sr)
                if f > 0:
                    st = abs(semitones(f, f_target))
                    err += 0.02 * st
            lk = _likeness(m, a)
            if lk is not None:
                err += 1.0 * (1.0 - lk)
        rp = repeat_score(a, m.sr)                      # stutter / repeated-chunk glitch
        err += 2.0 * max(0.0, rp - 0.7)
        short = len(text.split()) <= 3                  # "सो जाओ...", "गुड नाईट": the checker can't judge 1 s of voice
        if short and le is not None:
            err -= le * 0.8
        take_ok = (short or le is None or le <= float(S.get("max_letter_error", 0.12))) and 0.55 < d / want < 1.9 \
            and rp < float(S.get("max_repeat", 0.85))
        if err < best_err:
            best, best_err, ok = a, err, take_ok
            info = {"letter_error": None if le is None else round(le, 3), "dur": round(d, 2),
                    "want": round(want, 2), "pick": attempt + 1,
                    "pitch_off_st": None if st is None else round(st, 2),
                    "likeness": None if lk is None else round(lk, 3), "repeat": round(rp, 3)}
        if ok and attempt + 1 >= min_takes:
            break
        # smart takes: a first take that is clearly excellent is kept at once (no need to make 2 more and compare)
        if attempt == 0 and take_ok and S.get("smart_takes", True) and (le is not None and le <= 0.05) \
                and rp < 0.5 and 0.6 < d / want < 1.5 and (lk is None or lk >= 0.85):
            info["smart"] = True
            break
        # good after two: both takes fine and the better one clearly clean -> the third take rarely wins
        # (measured on "A Letter to Myself": 33 of 68 lines needed 3 takes although take 1 or 2 was fine)
        if attempt == 1 and ok and S.get("smart_takes", True) and info.get("letter_error") is not None \
                and info["letter_error"] <= 0.06 and info.get("repeat", 1) < 0.6:
            info["smart"] = True
            break
        if short and ok and attempt == 0:
            break
        if not take_ok and attempt + 1 < tries and attempt + 1 >= min_takes:
            say(f"      retake {label}({d:.1f} s for {len(text.split())} words"
                + (f", {le:.0%} letters heard wrong" if le is not None else "") + ")")
    info["takes"] = attempt + 1
    info["ok"] = ok
    if missed_all and not ok:
        info["missed"] = sorted(missed_all)
    return best, info
 
 
# ========================================== 
# File: music.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\music.py 
# ========================================== 
"""music.py - your background music under a narration, balanced like your Sukoon episodes.
Measured from your projects: music sits ~6-9 dB under your voice -> default 8 dB under while you speak,
rising gently (+3 dB) in pauses, faded in/out, looped with a crossfade if shorter than the narration.
Track: config speak.music (path, or a folder -> one track picked from it). Output: narration_music.wav
(48 kHz stereo, -16 LUFS). Only ffmpeg + numpy (no extra installs)."""
import glob, os, random, subprocess, wave
import numpy as np
from . import cfg

SR = 48000


def _ff():
    return cfg.ffmpeg()


def decode(path, ch=2):
    raw = subprocess.run([_ff(), "-v", "error", "-i", path, "-ac", str(ch), "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, ch).copy()


def pick_track(setting=None, seed=0):
    s = setting or cfg.C["speak"].get("music") or ""
    if os.path.isfile(s):
        return s
    if os.path.isdir(s):
        fs = sorted(glob.glob(os.path.join(s, "*.mp3")) + glob.glob(os.path.join(s, "*.wav")))
        if fs:
            return random.Random(seed).choice(fs)
    raise SystemExit(f"music not found: {s!r} (config speak.music)")


def _loop(m, n, xf=SR * 3):
    if len(m) >= n:
        return m[:n]
    out = m.copy()
    while len(out) < n:
        k = min(xf, len(out), len(m))
        ramp = np.linspace(0, 1, k)[:, None]
        out[-k:] = out[-k:] * (1 - ramp) + m[:k] * ramp
        out = np.concatenate([out, m[k:]])
    return out[:n]


def mix(narration, out=None, track=None, under_db=None, duck_db=None, lufs=None):
    S = cfg.C["speak"]
    under_db = float(S.get("music_under_db", 8) if under_db is None else under_db)
    duck_db = float(S.get("music_pause_lift_db", 3) if duck_db is None else duck_db)
    lufs = float(S.get("music_lufs", -16) if lufs is None else lufs)
    track = track or pick_track(seed=len(narration))
    v = decode(narration, 1)[:, 0]
    tail = int(SR * 4)
    n = len(v) + tail
    m = _loop(decode(track, 2), n)
    h = int(SR * 0.05)                                   # 50 ms frames
    fr = np.pad(v, (0, (-len(v)) % h)).reshape(-1, h)
    db = 10 * np.log10((fr.astype(np.float64) ** 2).mean(1) + 1e-12)
    speaking = db > db.max() - 40
    v_lvl = 10 * np.log10((fr[speaking] ** 2).mean() + 1e-12)
    mf = np.pad(m.mean(1), (0, (-n) % h)).reshape(-1, h)
    m_lvl = 10 * np.log10((mf ** 2).mean() + 1e-12)
    base = 10 ** ((v_lvl - under_db - m_lvl) / 20)
    # ducking curve: speech (held 0.4 s) -> base, pause -> base + duck_db; smoothed
    hold = np.convolve(speaking.astype(float), np.ones(8), "same") > 0
    target = np.where(hold, 0.0, duck_db)
    target = np.concatenate([target, np.full(int(np.ceil(tail / h)) + 1, duck_db)])
    sm = np.zeros_like(target)
    for i in range(len(target)):
        a = 0.35 if target[i] < (sm[i - 1] if i else 0) else 0.06   # fall fast before speech, rise slowly
        sm[i] = (sm[i - 1] if i else target[i]) + a * (target[i] - (sm[i - 1] if i else target[i]))
    g = np.repeat(10 ** (sm / 20), h)[:n] * base
    fi, fo = int(SR * 3), int(SR * 5)
    env = np.ones(n); env[:fi] = np.linspace(0, 1, fi); env[-fo:] = np.linspace(1, 0, fo)
    y = m * (g * env)[:, None]
    y[: len(v)] += v[:, None]
    out = out or os.path.join(os.path.dirname(narration), "narration_music.wav")
    r = subprocess.run([_ff(), "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                        "-af", f"loudnorm=I={lufs}:TP=-1.5:LRA=11", "-ar", str(SR), "-c:a", "pcm_s16le", out + ".part.wav"],
                       input=y.astype(np.float32).tobytes())
    if r.returncode:
        raise SystemExit("ffmpeg could not write the music mix")
    os.replace(out + ".part.wav", out)
    print(f"   music: {os.path.basename(track)}  ({under_db:.0f} dB under your voice, +{duck_db:.0f} dB in pauses) -> {out}",
          flush=True)
    return out
 
 
# ========================================== 
# File: pace.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\pace.py 
# ========================================== 
"""pace.py - pace control: every training line is measured (speaking speed + how much it pauses) and gets a
pace class; the model learns one small vector per class, added to its conditioning next to your voice and
emotion. When speaking we ask for the class of YOUR style (median of your Sukoon lines, or of the stories)
- or slower / faster. 5 speed levels x 3 pause levels = 15 classes (class 0 = no pace given)."""
import numpy as np

N_CLASSES = 16


def measure(wav, sr, words):
    """(words per second of actual speech, share of pauses inside the line)"""
    a = np.asarray(wav, np.float32)
    if not len(a) or float(np.abs(a).max()) <= 0:
        return 0.0, 0.0
    idx = np.where(np.abs(a) > np.abs(a).max() * 0.03)[0]
    a = a[idx[0]:idx[-1] + 1]
    h = int(sr * 0.02)
    f = a[: len(a) // h * h].reshape(-1, h)
    if not len(f):
        return 0.0, 0.0
    db = 10 * np.log10((f.astype(np.float64) ** 2).mean(1) + 1e-10)
    voiced = db >= db.max() - 35
    return words / max(0.3, voiced.sum() * 0.02), float(1 - voiced.mean())


def edges(rates, pauses):
    return {"rate": [float(np.percentile(rates, q)) for q in (20, 40, 60, 80)],
            "pause": [float(np.percentile(pauses, q)) for q in (33, 66)]}


def klass(rate, pause, E):
    p = int(np.searchsorted(E["rate"], rate))       # 0 slow .. 4 fast
    q = int(np.searchsorted(E["pause"], pause))     # 0 few pauses .. 2 many
    return 1 + p * 3 + q


def describe(k):
    if not k:
        return "none"
    p, q = divmod(k - 1, 3)
    return f"speed {['very slow', 'slow', 'medium', 'fast', 'very fast'][p]}, {['few', 'some', 'many'][q]} pauses"


def shift(k, faster=0, more_pauses=0):
    p, q = divmod(k - 1, 3)
    p = min(4, max(0, p + faster)); q = min(2, max(0, q + more_pauses))
    return 1 + p * 3 + q


def install(t3, emb=None):
    """add the pace vector to T3's conditioning (an extra conditioning token when cond.pace is set)."""
    import torch
    from torch import nn
    if getattr(t3, "_pace_emb", None) is None:
        e = nn.Embedding(N_CLASSES, t3.cfg.hidden_size).to(t3.device)
        nn.init.normal_(e.weight, std=0.02)
        with torch.no_grad():
            e.weight[0].zero_()
        t3._pace_emb = e
        orig = t3.cond_enc.forward

        def fwd(cond):
            out = orig(cond)
            k = getattr(cond, "pace", None)
            if k is None:
                return out
            k = torch.as_tensor(k, device=out.device).long().view(-1)
            tok = t3._pace_emb(k).to(out.dtype)[:, None, :]
            if tok.size(0) != out.size(0):
                tok = tok.expand(out.size(0), -1, -1)
            return torch.cat([out, tok], dim=1)
        t3.cond_enc.forward = fwd
    if emb is not None:
        t3._pace_emb.load_state_dict({"weight": emb.float()})
    return t3._pace_emb


def pitch(wav, sr):
    """median voice pitch (Hz) of a line - vectorised autocorrelation on 40 ms frames (0 if unvoiced)."""
    a = np.asarray(wav, np.float32)
    n, h = int(sr * 0.04), int(sr * 0.01)
    if len(a) < n * 2:
        return 0.0
    idx = np.arange(0, len(a) - n, h)[:, None] + np.arange(n)[None]
    fr = a[idx] * np.hanning(n).astype(np.float32)
    e = (fr ** 2).mean(1)
    fr = fr[e > max(1e-6, np.percentile(e, 50) * 0.5)]
    if not len(fr):
        return 0.0
    F = np.fft.rfft(fr, 2 * n)
    ac = np.fft.irfft(np.abs(F) ** 2)[:, :n]
    lo, hi = int(sr / 350), int(sr / 65)
    k = lo + ac[:, lo:hi].argmax(1)
    good = ac[np.arange(len(ac)), k] > 0.4 * ac[:, 0]
    return float(np.median(sr / k[good])) if good.sum() >= 5 else 0.0


def semitones(f, ref):
    return 12 * np.log2(max(f, 1) / max(ref, 1))
 
 
# ========================================== 
# File: prompts.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\prompts.py 
# ========================================== 
"""prompts.py - which sample of your voice gives the best tone (e.g. your faint smile)?
The trained voice speaks the SAME phrases once per voice prompt: every Sukoon sample in dataset\\refs plus any
clip you put into <media>\\my_prompts\\ (8-10 s of clean voice, cut in Audacity). One file per prompt +
all_prompts.wav (all in a row, order in all_prompts.txt). Pick one -> config speak.reference = its name."""
import glob, os, time
import numpy as np
from . import cfg
from .model import load_base, resolve_checkpoint, use_checkpoint, set_voice, speak_sentence, words_per_second, \
    patient_brakes, say

PHRASES = ["आज की रात हम किसी थकान के बारे में बात नहीं करेंगे",
           "बस अपनी आँखें बंद कीजिए... और धीरे से एक गहरी साँस लीजिए",
           "सब ठीक हो जाएगा... आप यहाँ बिल्कुल महफूज़ हैं"]
MY = os.path.join(cfg.MEDIA, "my_prompts")


def candidates():
    os.makedirs(MY, exist_ok=True)
    c = [p for p in sorted(glob.glob(os.path.join(cfg.REFS, "*.wav")))
         if "sukoon" in os.path.basename(p).lower()]
    c += sorted(glob.glob(os.path.join(MY, "*.wav")))
    return c


def main(only_mine=False):
    import soundfile as sf
    S = dict(cfg.C["speak"])
    S["max_retakes"] = 1
    refs = candidates()
    if only_mine:
        refs = [r for r in refs if r.startswith(MY)]
    if not refs:
        raise SystemExit(f"no voice prompts found (dataset\\refs or {MY})")
    out = os.path.join(cfg.TESTS, time.strftime("prompts_%Y%m%d_%H%M"))
    os.makedirs(out, exist_ok=True)
    m = load_base()
    ckp = resolve_checkpoint(S.get("model", "best"))
    ck = use_checkpoint(m, ckp) if ckp else None
    if ck:
        patient_brakes(S)
    wps = words_per_second()
    allp, order = [], []
    say(f"   {len(refs)} voice prompts x {len(PHRASES)} phrases (~{len(refs) * 20 // 60 + 1} min)")
    for k, ref in enumerate(refs, 1):
        name = os.path.splitext(os.path.basename(ref))[0] + ("  (yours)" if ref.startswith(MY) else "")
        set_voice(m, ref, ck, float(S["exaggeration"]), style="sukoon", pace_setting=S.get("pace", "auto"))
        parts = []
        for j, t in enumerate(PHRASES):
            a, _ = speak_sentence(m, t, 21 + j, S, wps)
            parts += [a, np.zeros(int(1.0 * m.sr), np.float32)]
        y = np.concatenate(parts)
        y = y / (float(np.abs(y).max()) or 1) * 0.89
        sf.write(os.path.join(out, f"{k:02d}_{os.path.splitext(os.path.basename(ref))[0]}.wav"), y, m.sr,
                 subtype="PCM_16")
        allp += [y, np.zeros(int(2.0 * m.sr), np.float32)]
        order.append(f"{k:2d}. {name}   ({ref})")
        say(f"   {k}/{len(refs)}  {name}")
    sf.write(os.path.join(out, "all_prompts.wav"), np.concatenate(allp), m.sr, subtype="PCM_16")
    try:                                                   # which prompt made the trained voice smile most?
        from . import smile
        rank = smile.rank_files(sorted(glob.glob(os.path.join(out, "[0-9][0-9]_*.wav"))))
        open(os.path.join(out, "smile_rank.txt"), "w", encoding="utf-8").write(rank)
        say("\n" + rank)
    except Exception as e:
        say(f"   (smile ranking skipped: {e})")
    open(os.path.join(out, "all_prompts.txt"), "w", encoding="utf-8").write(
        "all_prompts.wav plays these in order (2 s silence between):\n" + "\n".join(order) +
        "\n\nLike one? set it in config.json -> speak -> \"reference\": \"<the name, e.g. sukoon50>\" "
        "or the full path of your own clip.\n")
    say(f"\nLISTEN: {os.path.join(out, 'all_prompts.wav')}  (order: all_prompts.txt)")
 
 
# ========================================== 
# File: recordings.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\recordings.py 
# ========================================== 
"""recordings.py - your UNSCRIPTED Audacity projects -> training lines (runs on the graphics card).

For every .aup3 in config "recordings.folders" (minus "exclude", minus episodes already imported with a script):
  1 the track that sounds most like YOU is used (voice encoder vs your voice; dry tracks preferred over
    "Effect"/"Mix"/"SFX" ones) - read-only, the project is never changed
  2 cut at your pauses into 1.5-14 s lines (pauses under 1 s stay inside a line, as you speak)
  3 each line transcribed by the Hindi checker (Whisper); kept only when it was sure, the text has a sane
    length for the time and no known hallucination words
  4 each line compared with your voice - music / SFX / another person's voice is dropped
Result per project: <media>\\dataset\\projects\\<name>.jsonl (+ clips). Crash-safe: finished projects are skipped.
"""
import fnmatch, glob, json, os, re, time
import numpy as np
from . import cfg
from .aup3 import open_ro, track_audio, speechiness
from .aup3_doc import decode, tracks
from .dataset import write_clip, norm_name, say, pick_reference

PROJ = cfg.PROJECTS
HALLU = re.compile(r"समाचार|बुलेटिन|आकाशवाणी|प्रादेशिक|सब्सक्राइब|प्रधानमंत्री|मुख्यमंत्री|धन्यवाद देखने")
SR16 = 16000


def to16(x, rate):
    if rate == SR16:
        return x
    n = int(len(x) * SR16 / rate)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def speech_runs(x, hop=0.02):
    """(copied from the Studio's asr_hindi) speech = louder than the room / breaths."""
    n = int(SR16 * hop)
    f = x[: len(x) // n * n].reshape(-1, n)
    db = 10 * np.log10((f.astype(np.float64) ** 2).mean(1) + 1e-10)
    db = np.convolve(db, np.ones(5) / 5, mode="same")
    floor, top = np.percentile(db, 15), np.percentile(db, 97)
    on = db > max(floor + 0.40 * (top - floor), top - 28.0)
    runs, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            runs.append([i * hop, j * hop]); i = j
        else:
            i += 1
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] < 0.18:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [r for r in merged if r[1] - r[0] > 0.12]


def lines_from_runs(runs, join_gap=1.0, max_len=14.0, min_len=1.5):
    out = []
    for s, e in runs:
        if out and s - out[-1][1] < join_gap and e - out[-1][0] <= max_len:
            out[-1][1] = e
        else:
            out.append([s, e])
    return [o for o in out if min_len <= o[1] - o[0] <= max_len]


def files():
    R = cfg.C.get("recordings") or {}
    ex = [e.lower() for e in R.get("exclude", [])]
    out = []
    for folder in R.get("folders", []):
        for f in sorted(glob.glob(os.path.join(folder, "*.aup3"))):
            n = os.path.basename(f).lower()
            if any(fnmatch.fnmatch(n, e) or fnmatch.fnmatch(os.path.splitext(n)[0], e) for e in ex):
                continue
            out.append(f)
    return out


class Ear:
    """voice encoder (who is speaking) + Hindi Whisper with confidence (what was said)."""

    def __init__(self, m):
        from . import asr
        self.m = m
        self.ve = m.ve
        if "m" not in asr._M:
            asr._M["m"] = asr._load()
        self.wm, self.proc, self.dev, self.dt = asr._M["m"]
        self.t_ve = self.t_wh = 0.0
        import torch
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            say(f"      Whisper on {next(self.wm.parameters()).device} ({self.dt}); graphics card memory: "
                f"{(total - free) / 2**30:.1f} of {total / 2**30:.1f} GB in use (all programs)")
        else:
            say("      !! Whisper runs on the PROCESSOR (no CUDA) - very slow")

    def emb(self, wavs16):
        e = np.asarray(self.ve.embeds_from_wavs(wavs16, sample_rate=SR16), np.float32)
        return e / (np.linalg.norm(e, axis=1, keepdims=True) + 1e-8)

    def transcribe(self, wavs16):
        import torch
        feats = self.proc.feature_extractor(wavs16, sampling_rate=SR16, return_tensors="pt").input_features
        feats = feats.to(self.dev, self.dt)
        tok = self.proc.tokenizer
        special = set(tok.all_special_ids)
        if not getattr(self, "no_fast", False):
            from . import asr
            try:
                return asr.fast_decode(self.wm, self.proc, feats, [len(x) / SR16 for x in wavs16])
            except Exception as e:
                say(f"   (fast decoding failed: {repr(e)[:160]} - using the slow one)")
                self.no_fast = True
        if not getattr(self, "no_scores", False):
            try:
                with torch.inference_mode():
                    out = self.wm.generate(feats, language="hi", task="transcribe", num_beams=1, do_sample=False,
                                           max_new_tokens=160, return_dict_in_generate=True, output_scores=True)
                    sc = self.wm.compute_transition_scores(out.sequences, out.scores, normalize_logits=True)
            except Exception as e:
                say(f"   (word confidence not available: {repr(e)[:120]} - using the other checks only)")
                self.no_scores = True
        if getattr(self, "no_scores", False):
            with torch.inference_mode():
                seqs = self.wm.generate(feats, language="hi", task="transcribe", num_beams=2, max_new_tokens=160)
            return [(" ".join(tok.decode([t for t in q.tolist() if t not in special]).split()), 0.0) for q in seqs]
        res = []
        seqs = out.sequences[:, -sc.shape[1]:]
        for b in range(len(wavs16)):
            ids = seqs[b].tolist()
            lp = [float(sc[b, k]) for k, t in enumerate(ids) if t not in special and np.isfinite(float(sc[b, k]))]
            text = tok.decode([t for t in ids if t not in special]).strip()
            res.append((" ".join(text.split()), float(np.mean(lp)) if lp else -9.0))
        return res


def pick_track(path, ear, me):
    con = open_ro(path)
    try:
        d, doc = con.execute("select dict, doc from project").fetchone()
        trs = tracks(decode(bytes(d), bytes(doc)))
        best, best_s, notes = None, -9, []
        for i, t in enumerate(trs):
            if t["mute"] or not t["clips"]:
                continue
            x = track_audio(con, t)
            rate = int(t["rate"])
            if len(x) < rate * 20 or speechiness(x[: rate * 300], rate) < 0.05:
                continue
            x16 = to16(x, rate)
            runs = lines_from_runs(speech_runs(x16))
            if len(runs) < 5:
                continue
            pick = [runs[k] for k in np.linspace(0, len(runs) - 1, min(12, len(runs))).astype(int)]
            e = ear.emb([x16[int(a * SR16):int(b * SR16)] for a, b in pick])
            sim = float(np.median(e @ me))
            pen = 0.04 if re.search(r"effect|mix|sfx|reverb|echo", t["name"] or "", re.I) else 0.0
            notes.append(f"'{t['name']}' {sim:.2f}")
            if sim - pen > best_s:
                best, best_s = (x, rate, t["name"], sim), sim - pen
            del x16
        return best, notes
    finally:
        con.close()


def process(path, ear, me, R):
    name = os.path.splitext(os.path.basename(path))[0]
    ep = norm_name(os.path.basename(path))       # from the FILE name: "Sukoon 2.1" and "Sukoon 2.2" must differ
    t0 = time.time()
    best, notes = pick_track(path, ear, me)
    if not best:
        say(f"   no voice track found ({', '.join(notes) or 'nothing speech-like'})")
        return [], None
    x, rate, tname, sim = best
    say(f"   track '{tname}' (sounds like you: {sim:.2f}; all: {', '.join(notes)})")
    if sim < float(R["speaker_min_similarity"]) - 0.1:
        say("   -> not your voice, skipped")
        return [], None
    x16 = to16(x, rate)
    spans = lines_from_runs(speech_runs(x16))
    total = len(x16) / SR16
    lines, dropped = [], {"voice": 0, "unsure": 0, "text": 0}
    B = 16
    t_pick = time.time() - t0
    t_asr = 0.0
    for k in range(0, len(spans), B):
        part = spans[k:k + B]
        cuts = [[max(0.0, a - 0.12), min(total, b + 0.25)] for a, b in part]
        w16 = [x16[int(a * SR16):int(b * SR16)] for a, b in cuts]
        ta = time.time()
        sims = ear.emb(w16) @ me
        tb = time.time()
        heard = ear.transcribe(w16)
        t_asr += time.time() - ta
        ear.t_ve += tb - ta; ear.t_wh += time.time() - tb
        say(f"   listening {min(k + B, len(spans))}/{len(spans)} lines   ({time.time() - t0:.0f} s, kept so far "
            f"{len(lines)})")
        for (a, b), (sa, sb), s, (text, lp) in zip(cuts, part, sims, heard):
            letters = len(re.sub("[^ऀ-ॿ]", "", text))
            cps = letters / max(0.3, sb - sa)
            words = text.split()
            rep = any(words[i] == words[i + 1] == words[i + 2] for i in range(len(words) - 2))
            if s < float(R["speaker_min_similarity"]):
                dropped["voice"] += 1
            elif lp < float(R["asr_min_logprob"]):
                dropped["unsure"] += 1
            elif not (5 <= cps <= 22) or HALLU.search(text) or rep or len(words) < 2 or \
                    re.search("[a-zA-Z]", text):
                dropped["text"] += 1
            else:
                lines.append({"start": round(a, 3), "end": round(b, 3), "text": text, "source": "asr",
                              "speech": (sa, sb), "sim": round(float(s), 3), "lp": round(lp, 3)})
    tw = time.time()
    for i, l in enumerate(lines):
        l["id"] = f"{ep}_{i:04d}"
        write_clip(os.path.join(cfg.CLIPS, l["id"] + ".wav"), x[int(l["start"] * rate):int(l["end"] * rate)], rate)
    ref = pick_reference(x, rate, lines, ep, set()) if lines else None
    series = "sukoon" if "sukoon" in name.lower() else "story"
    meta = [{"id": l["id"], "wav": os.path.join(cfg.CLIPS, l["id"] + ".wav"), "text": l["text"],
             "dur": round(l["end"] - l["start"], 3), "episode": name, "series": series, "source": "asr",
             "holdout": False, "sim": l["sim"], "lp": l["lp"]} for l in lines]
    say(f"   kept {len(meta)} of {len(spans)} lines = {sum(m['dur'] for m in meta) / 60:.1f} min   (dropped: "
        f"{dropped['voice']} not you, {dropped['unsure']} unclear words, {dropped['text']} odd text)  "
        f"[{time.time() - t0:.0f} s: read+track {t_pick:.0f}, listen {t_asr:.0f} (voice check {ear.t_ve:.0f}, Whisper "
        f"{ear.t_wh:.0f}), save {time.time() - tw:.0f}]")
    ear.t_ve = ear.t_wh = 0.0
    return meta, ref


def main(m, have_names, me):
    """m = loaded Chatterbox (for its voice encoder); have_names = episodes already imported with timings;
    me = your voice vector. Returns (all project lines, new refs)."""
    R = dict({"speaker_min_similarity": 0.72, "asr_min_logprob": -0.45}, **(cfg.C.get("recordings") or {}))
    fs = [f for f in files() if norm_name(f) not in have_names]
    os.makedirs(PROJ, exist_ok=True)
    todo = [f for f in fs if not os.path.exists(os.path.join(PROJ, norm_name(f) + ".jsonl"))]
    say(f"\n== your other recordings: {len(fs)} projects ({len(fs) - len(todo)} already done, {len(todo)} to do)")
    if todo:                              # only the voice encoder is needed here: the rest of Chatterbox
        import torch                      # leaves the graphics card so Whisper has room to run fast
        m.t3.to("cpu"); m.s3gen.to("cpu")
        torch.cuda.empty_cache()
    ear = Ear(m) if todo else None
    refs = []
    t0 = time.time()
    for k, f in enumerate(todo):
        say(f"\n[{k + 1}/{len(todo)}] {os.path.basename(f)}"
            + (f"   (~{(time.time() - t0) / k * (len(todo) - k) / 60:.0f} min left)" if k else ""))
        try:
            meta, ref = process(f, ear, me, R)
        except Exception as e:
            say(f"   !! could not read it: {repr(e)[:200]} - skipped")
            meta, ref = [], None
        if ref:
            refs.append(ref)
        p = os.path.join(PROJ, norm_name(f) + ".jsonl")
        with open(p + ".part", "w", encoding="utf-8") as fh:
            for x in meta:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
        os.replace(p + ".part", p)
    allm = []
    for f in fs:
        p = os.path.join(PROJ, norm_name(f) + ".jsonl")
        if os.path.exists(p):
            allm += [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    return allm, refs
 
 
# ========================================== 
# File: roomtone.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\roomtone.py 
# ========================================== 
"""roomtone.py - the quiet 'air' of your real room under the whole narration, so pauses are not dead digital
silence (a real room never is).

ONE clean, continuous stretch of room tone is taken from a real pause in your Sukoon recording (no breath, no
words, steady), its end is crossfaded into its start so it loops without a seam, and that loop is laid end to
end under the narration at your real level. (One clean piece looped = no stitching artifacts.)
Built once -> dataset\\refs\\room_tone_loop.wav + room_tone_loop.json"""
import json, os, wave
import numpy as np
from . import cfg

PATH = os.path.join(cfg.REFS, "room_tone_loop.wav")
INFO = os.path.join(cfg.REFS, "room_tone_loop.json")


def _read(p):
    with wave.open(p) as w:
        n, sr, ch = w.getnframes(), w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(n), "<i2").astype(np.float32) / 32768.0
    return (x.reshape(-1, ch).mean(1) if ch > 1 else x), sr


def _write(p, x, sr):
    y = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(p + ".part", "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(y.tobytes())
    os.replace(p + ".part", p)


def _rms(x):
    return float(np.sqrt(np.mean(x ** 2) + 1e-12))


def build(min_len=None, xfade=0.35):
    """best real pause of your Sukoon recordings -> seamless loop."""
    if min_len is None:                                         # longest clean loop we can find (fewer repeats)
        for ml in (4.0, 3.0, 2.2, 1.6):
            i = build(ml, xfade)
            if i:
                return i
        return None
    from .dataset import sources
    best = None
    for src in sources():
        if "sukoon" not in src["name"].lower():
            continue
        try:
            x, sr = _read(src["voice"])
            j = json.load(open(src["transcript"], encoding="utf-8"))
        except Exception:
            continue
        segs = j["segments"] if isinstance(j, dict) else j
        segs = sorted([s for s in segs if s.get("end") is not None], key=lambda s: s["start"])
        sp = np.concatenate([x[int(s["start"] * sr):int(s["end"] * sr)] for s in segs[:200]]) if segs else x
        speech = _rms(sp)
        for a, b in zip(segs, segs[1:]):
            g0, g1 = a["end"] + 0.35, b["start"] - 0.25           # stay clear of word tails / next breath-in
            if g1 - g0 < min_len:
                continue
            q = x[int(g0 * sr):int(g1 * sr)]
            hop = int(0.02 * sr)
            n = len(q) // hop
            e = np.sqrt((q[:n * hop].reshape(n, hop) ** 2).mean(1) + 1e-12)
            if e.max() > 4 * np.median(e) or np.median(e) < 1e-5:  # a breath/click inside, or edited-out silence
                continue
            if 20 * np.log10(_rms(q) / speech) > -42:                # music / untranscribed words, not room air
                continue
            steady = float(np.std(20 * np.log10(e)))               # lower = steadier hiss
            score = steady + 0.3 * max(0.0, 3.0 - (g1 - g0))         # prefer long, steady stretches
            if best is None or score < best[0]:
                best = (score, q.copy(), sr, 20 * np.log10(_rms(q) / speech), src["name"], round(g0, 2))
    if best is None:
        return None
    _, q, sr, rel, name, at = best
    q = q - np.mean(q)
    c = int(xfade * sr)
    if len(q) < 3 * c:
        return None
    t = np.linspace(0, np.pi / 2, c)
    loop = q[:len(q) - c].copy()
    loop[:c] = q[:c] * np.sin(t) + q[len(q) - c:] * np.cos(t)  # equal-power: end flows into start
    os.makedirs(cfg.REFS, exist_ok=True)
    _write(PATH, loop / max(1e-6, np.abs(loop).max()) * 0.5, sr)
    info = {"rel_db": round(float(rel), 1), "sr": sr, "from": name, "at_s": at, "len_s": round(len(loop) / sr, 2)}
    json.dump(info, open(INFO, "w"), indent=1)
    return info


def lay_under(full, sr, extra_db=0.0):
    """the loop, end to end, under `full` (float32 mono) at your recordings' real level vs the speech."""
    if not os.path.exists(PATH) or not os.path.exists(INFO):
        if not build():
            return full, None
    info = json.load(open(INFO))
    t, tsr = _read(PATH)
    if tsr != sr:
        n = int(len(t) * sr / tsr)
        t = np.interp(np.linspace(0, len(t) - 1, n), np.arange(len(t)), t).astype(np.float32)
    hop = int(0.02 * sr)
    n = len(full) // hop
    e = np.sqrt((full[:n * hop].reshape(n, hop) ** 2).mean(1) + 1e-12)
    voiced = full[:n * hop].reshape(n, hop)[e > np.percentile(e, 90) * 0.1]
    if not len(voiced):
        return full, None
    want = _rms(voiced) * 10 ** ((info["rel_db"] + extra_db) / 20)
    bed = np.tile(t, len(full) // len(t) + 1)[:len(full)] * (want / _rms(t))
    return (full + bed).astype(np.float32), info["rel_db"] + extra_db
 
 
# ========================================== 
# File: s3train.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\s3train.py 
# ========================================== 
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
 
 
# ========================================== 
# File: series.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\series.py 
# ========================================== 
"""series.py - which of your recordings belong to which series, so a voice can be trained per series
(सुकून calm, किस्से emotional, रहस्य thriller, भय horror, रोमांच, करीब ASMR, ज्ञान informational ...).

D:\\SukoonVoice\\series.json = {"series": {id: {"name": "किस्से", "about": "emotional stories"}},
                                "episodes": {"<recording name>": "<series id>"}}
Made on first use: every recording with "sukoon" in its name is सुकून; the rest waits for you (hub Settings).
Training with profile "series_<id>" learns only from that series' recordings (+ their flow clips).
"""
import json, os
from . import cfg

PATH = os.path.join(cfg.MEDIA, "series.json")
DEFAULT = {
    "sukoon": {"name": "सुकून", "about": "calm, sleep, counselling"},
    "kisse": {"name": "किस्से", "about": "emotional stories"},
    "rahasya": {"name": "रहस्य", "about": "thriller, mystery"},
    "bhay": {"name": "भय", "about": "horror"},
    "romanch": {"name": "रोमांच", "about": "romance / sensual"},
    "kareeb": {"name": "करीब", "about": "ASMR, close and soft"},
    "gyaan": {"name": "ज्ञान", "about": "informational"},
}


def load():
    d = {}
    if os.path.exists(PATH):
        try:
            d = json.load(open(PATH, encoding="utf-8"))
        except Exception:
            d = {}
    d.setdefault("series", {})
    for k, v in DEFAULT.items():
        d["series"].setdefault(k, v)
    d.setdefault("episodes", {})
    return d


def save(d):
    tmp = PATH + ".part"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


def _norm(s):
    import re
    return re.sub(r"[^0-9a-z]", "", os.path.splitext(os.path.basename(str(s)))[0].lower())


def of(episode, d=None):
    """the series of a recording (names compared without spaces / case: 'Burari Kand' = 'burarikand')"""
    d = d or load()
    e = d.get("_n") or {_norm(k): v for k, v in d["episodes"].items()}
    d["_n"] = e
    s = e.get(_norm(episode))
    if s:
        return s
    return "sukoon" if "sukoon" in (episode or "").lower() else ""
 
 
# ========================================== 
# File: smile.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\smile.py 
# ========================================== 
"""smile.py - find the stretches where you sound like you are faintly smiling.
A smile spreads the lips -> the mouth's resonances (formants F2, F3) move UP, the tone gets a bit brighter, the
pitch a little livelier. Every Sukoon clip of 6-12 s is measured with Praat and compared with YOUR average
(z-scores): score mainly = how much F2/F3 RISE in the last 1.5 s (your smile comes at phrase endings) + overall F2/F3. Over 6-12 s the vowels average out, so the
score points at the tone, not the words. It is a pointer - your ears decide (menu 7 tests the winners).
Output: <media>\\my_prompts\\smile_01..08.wav  +  smile_scores.txt"""
import os, shutil
import numpy as np
from . import cfg


def say(*a):
    print(*a, flush=True)


def measure_end(snd, voiced, fm):
    """F2/F3 in the last 1.5 s of speech minus the rest = the smile at the END of a phrase"""
    if len(voiced) < 60:
        return np.nan, np.nan
    t_end = voiced[-1]
    tail = [t for t in voiced if t >= t_end - 1.5]
    head = [t for t in voiced if t < t_end - 1.5]
    def med(ts, n, lo, hi):
        v = [fm.get_value_at_time(n, t) for t in ts]
        v = [x for x in v if x == x and lo < x < hi]
        return float(np.median(v)) if len(v) >= 10 else np.nan
    return (med(tail, 2, 700, 2800) - med(head, 2, 700, 2800), med(tail, 3, 1800, 3800) - med(head, 3, 1800, 3800))


def measure(path):
    import parselmouth
    snd = parselmouth.Sound(path)
    pitch = snd.to_pitch(time_step=0.01, pitch_floor=65, pitch_ceiling=300)
    f0 = pitch.selected_array["frequency"]
    ts = pitch.xs()
    voiced = [t for t, f in zip(ts, f0) if f > 0]
    if len(voiced) < 50:
        return None
    fm = snd.to_formant_burg(time_step=0.01, max_number_of_formants=5, maximum_formant=5000)
    f2 = [fm.get_value_at_time(2, t) for t in voiced]
    f3 = [fm.get_value_at_time(3, t) for t in voiced]
    f2 = [v for v in f2 if v == v and 700 < v < 2800]
    f3 = [v for v in f3 if v == v and 1800 < v < 3800]
    a = snd.values[0]
    sp = np.abs(np.fft.rfft(a * np.hanning(len(a)))) ** 2
    fr = np.fft.rfftfreq(len(a), 1 / snd.sampling_frequency)
    band = (fr > 80) & (fr < 8000)
    cen = float((sp[band] * fr[band]).sum() / (sp[band].sum() + 1e-12))
    f0v = f0[f0 > 0]
    e2, e3 = measure_end(snd, voiced, fm)
    return {"end2": e2, "end3": e3, "end_t": voiced[-1], "dur": snd.duration,
            "f2": float(np.median(f2)) if f2 else np.nan, "f3": float(np.median(f3)) if f3 else np.nan,
            "bright": cen, "f0": float(np.median(f0v)), "f0range": float(np.percentile(f0v, 90) - np.percentile(f0v, 10))}


def main(top=8):
    try:
        import parselmouth  # noqa: F401
    except Exception:
        raise SystemExit("needs Praat (praat-parselmouth) - run setup.bat and answer Y")
    meta = [m for m in cfg.read_meta() if m.get("series") == "sukoon" and 6 <= m["dur"] <= 12]
    if not meta:
        raise SystemExit("no Sukoon clips of 6-12 s in the dataset (menu 1 first)")
    say(f"   measuring {len(meta)} Sukoon clips (formants, brightness, pitch) ...")
    rows = []
    for k, m in enumerate(meta):
        try:
            r = measure(m["wav"])
        except Exception:
            r = None
        if r and all(r[k] == r[k] for k in ("f2", "f3", "end2", "end3")):
            rows.append((m, r))
        if (k + 1) % 50 == 0:
            say(f"     {k + 1}/{len(meta)}")
    if len(rows) < top:
        raise SystemExit("too few measurable clips")
    keys = ["f2", "f3", "bright", "f0", "end2", "end3"]
    M = {k: np.array([r[k] for _, r in rows]) for k in keys}
    z = {k: (M[k] - np.median(M[k])) / (np.std(M[k]) + 1e-9) for k in keys}
    # your smile shows at the END of a phrase: the rise of F2/F3 at the end counts most
    score = 1.5 * z["end2"] + 1.5 * z["end3"] + 0.5 * z["f2"] + 0.5 * z["f3"] + 0.25 * z["bright"]
    order = np.argsort(-score)
    out = os.path.join(cfg.MEDIA, "my_prompts")
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):                         # old smile picks are replaced (your own clips stay)
        if f.startswith("smile_") and f.endswith(".wav"):
            os.replace(os.path.join(out, f), os.path.join(cfg.TMP, "old_" + f))
    lines = ["smile score = how much your resonances RISE at the end of the phrase (F2, F3 in the last 1.5 s), plus overall F2/F3 + brightness, vs YOUR average; files = the last 6.5 s of each clip",
             f"your averages: F2 {np.median(M['f2']):.0f} Hz, F3 {np.median(M['f3']):.0f} Hz, pitch {np.median(M['f0']):.0f} Hz",
             ""]
    for rank, i in enumerate(order[:top], 1):
        m, r = rows[i]
        dst = os.path.join(out, f"smile_{rank:02d}.wav")
        import soundfile as sf                    # keep the LAST ~6.5 s: the model reads only the first 6 s of
        a, sr = sf.read(m["wav"], dtype="float32")   # a prompt, so the smiling ending must be inside them
        end = min(len(a), int((r["end_t"] + 0.35) * sr))
        a = a[max(0, end - int(6.5 * sr)):end]
        f = int(0.02 * sr)
        a[:f] *= np.linspace(0, 1, f); a[-f:] *= np.linspace(1, 0, f)
        sf.write(dst, a, sr, subtype="PCM_16")
        lines.append(f"smile_{rank:02d}  score {score[i]:+.2f}   F2 {r['f2']:.0f}  F3 {r['f3']:.0f}  pitch {r['f0']:.0f} Hz"
                     f"   {m['episode']}: {m['text']}")
    lines += ["", "lowest (most neutral) for comparison:"]
    for i in order[-3:]:
        m, r = rows[i]
        lines.append(f"  score {score[i]:+.2f}   {m['episode']}: {m['text']}   ({m['wav']})")
    open(os.path.join(out, "smile_scores.txt"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    say("\n".join(lines[:top + 3]))
    say(f"\nsaved to {out} - listen to the smile_XX.wav files, then menu 7 to hear the trained voice with them")


def rank_files(files):
    """rank generated test files by the same smile measure (end rise of F2/F3 + overall F2/F3)."""
    rows = []
    for f in files:
        try:
            r = measure(f)
        except Exception:
            r = None
        if r and all(r[k] == r[k] for k in ("f2", "f3", "end2", "end3")):
            rows.append((f, r))
    if len(rows) < 2:
        return "too few files to rank"
    keys = ["f2", "f3", "end2", "end3", "bright", "f0"]
    M = {k: np.array([r[k] for _, r in rows]) for k in keys}
    z = {k: (M[k] - np.median(M[k])) / (np.std(M[k]) + 1e-9) for k in keys}
    score = 1.5 * z["end2"] + 1.5 * z["end3"] + 0.5 * z["f2"] + 0.5 * z["f3"] + 0.25 * z["bright"]
    out = ["SMILE RANKING of the trained voice with each prompt (higher = brighter / smiling endings):"]
    for i in np.argsort(-score):
        f, r = rows[i]
        out.append(f"  {score[i]:+.2f}  {os.path.basename(f)}   F2 {r['f2']:.0f}  F3 {r['f3']:.0f}  pitch {r['f0']:.0f} Hz"
                   f"  pitch range {r['f0range']:.0f}")
    return "\n".join(out) + "\n"
 
 
# ========================================== 
# File: speak.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\speak.py 
# ========================================== 
"""speak.py - a Sukoon script read in YOUR (generated) voice.

    run.py speak <script.txt | episode folder with script.txt> [--style Burari Kand] [--name N] [--model best|base|<run>]
                 [--redo 3,17]      --style = read in the tone of that episode (default: your Sukoon prompt)

One phrase per line = a breath, (...) = long pause, blank line = paragraph; headings / (notes) are skipped;
a line of only dashes (-----) ends the script (only the FIRST version is read). Every sentence is made on its own, checked by ear (Hindi Whisper) and retaken
if needed; then joined with your kind of pauses and brought to one loudness.
Output  <media>\\narrations\\<name>\\  narration.wav (48 kHz), script.txt (what was read), timing.json
(start/end of every sentence), report.txt (sentences that still sounded off -> --redo those numbers),
lines\\NNNN.wav (one per sentence; delete one = it is made again next run). Crash-safe: re-run continues.
"""
import json, os, re, subprocess, time
import numpy as np
from . import cfg
from .model import load_base, resolve_checkpoint, resolve_reference, use_checkpoint, set_voice, speak_sentence, \
    words_per_second, say
from .textsplit import sentences


CONJ = {"और", "लेकिन", "पर", "जब", "तो", "कि", "क्योंकि", "जैसे", "अगर", "ताकि", "या", "फिर", "मगर", "बल्कि",
        "जहाँ", "जहां", "वरना", "इसलिए"}
OBL = {"उस", "इस", "जिस", "किस", "छत", "मेज़", "मेज", "दीवार", "खिड़की", "दरवाज़े", "दरवाजे", "ज़मीन", "जमीन", "बिस्तर",
       "सड़क", "रास्ते", "कंधे", "चेहरे", "हाथ", "सिर", "पेड़", "घर", "छाती", "पलंग", "कुर्सी", "गाड़ी", "बस", "स्टेशन"}
VERB_END = re.compile(r"(है|हैं|था|थी|थे|हूं|हूँ|हो|गा|गी|गे|ें|ो|ना)$")


def chunk(s, max_words=16):
    """long unpunctuated sentences -> pieces of <= max_words, cut before a joining word or after a verb."""
    w = s.split()
    if len(w) <= max_words:
        return [s]
    mid, best, best_score = len(w) / 2, None, -1e9
    for i in range(4, len(w) - 3):
        score = -abs(i - mid) / len(w)
        if w[i] in CONJ and not (w[i] in ("पर", "तो") and not (VERB_END.search(w[i - 1].strip(",.।")) or
                                                           re.search(r"(ा|ी|े)$", w[i - 1]) and w[i - 1] not in OBL)):
            score += 1.0                                    # 'पर' after a noun = 'on' (खिड़की पर) - never cut there
        elif VERB_END.search(w[i - 1].strip(",.।")):
            score += 0.6
        if w[i - 1].endswith(","):
            score += 1.2
        if score > best_score:
            best, best_score = i, score
    return chunk(" ".join(w[:best]), max_words) + chunk(" ".join(w[best:]), max_words)


def read_script(path):
    """script -> [{"text", "pause"}]. Understands the Sukoon script styles: one phrase per line (line break =
    breath), '(...)' = a long pause, '...' = a pause, blank line = paragraph. Headings (emoji / 'भाग 1:')
    and stage notes in (brackets) are not read. A line of only dashes ends the script (first version only)."""
    if os.path.isdir(path):
        path = os.path.join(path, "script.txt")
    if not os.path.exists(path):
        raise SystemExit(f"no script found: {path}")
    S = cfg.C["speak"]
    raw = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    parts = [p for p in re.split(r"\n\s*-{3,}\s*\n", "\n" + raw + "\n") if p.strip()]
    if len(parts) > 1:
        say("   (the script has several versions separated by ----- : reading the FIRST one)")
    text = parts[0] if parts else ""
    paced = bool(re.search(r"\(\s*paced\s*\)", text, re.I))     # pauses already baked in (dramatic pass)
    text = re.sub(r"[ \t]*\n?[ \t]*\(\s*(?:\.{2,}|…)\s*([0-9]+(?:\.[0-9]+)?)\s*s?\s*\)[ \t]*\n?",
                  lambda m: f"\n\u23f8{m.group(1)}\n", text)           # (... 1.8) = a pause of exactly 1.8 s
    text = re.sub(r"[ \t]*\n?[ \t]*\(\s*(\.{2,}|…)\s*\)[ \t]*\n?", "\n\u23f8\n", text)  # (...) = long pause
    text = re.sub(r"\([^)]*\)", " ", text)                             # stage notes
    text = re.sub(r"^[ \t]*\[([^\]\n]{1,20})\][ \t]*", lambda m: "\u2766" + m.group(1).strip() + "\u2766", text,
                  flags=re.M)                                              # [डर] at a line start = how it feels
    text = re.sub(r"\[[^\]\n]*\]", " ", text)
    text = text.replace("…", "...")
    units, ln = [], 0

    def pause(v):
        if units:
            units[-1]["pause"] = max(units[-1]["pause"], float(v))

    for line in text.split("\n"):
        emo = ""
        em = re.match(r"^\s*\u2766([^\u2766]*)\u2766", line)
        if em:
            emo, line = em.group(1), line[em.end():]
        l = line.strip().strip('"“”\'‘’').strip()
        if not l:
            pause(S["pause_paragraph"]); continue
        if l == "⏸":
            pause(S["pause_marker"]); continue
        if l.startswith("⏸"):
            if units:
                units[-1]["pause"] = float(l[1:]); units[-1]["fixed"] = True
            continue
        q = re.search(r'["“](.*\S.*)$', line)
        if q and re.match(r"^\s*[^\w\"'“‘\u0900-\u097F.]", line):    # heading + text on one line
            l = q.group(1).strip().strip('"“”').strip()
        if re.match(r"^[^\w\"'“‘ऀ-ॿ.]", l) or \
                re.match(r"^(भाग|part|episode|एपिसोड|chapter)\s*\d+\s*[:：.-]", l, re.I):
            pause(S["pause_paragraph"]); continue                       # a heading
        if not re.search("[ऀ-ॿ]", l):
            continue
        ln += 1
        if units and len(l.split()) <= 2 and units[-1]["pause"] <= float(S["pause_line"]) and \
                units[-1].get("emo", "") == emo and \
                not re.search(r"[।.?!]$", units[-1]["text"]):
            units[-1]["text"] += " " + l                                # a line that only finishes the last one
            continue
        for s in sentences(l):
            for c in chunk(s):
                c = c.strip().strip('"“”').strip()
                if not re.search("[ऀ-ॿ]", c):
                    continue
                e = c.rstrip()
                p = S["pause_dots"] if e.endswith("...") else S["pause_comma"] if e.endswith(",") else \
                    S["pause_sentence"] if re.search(r"[।.?!]$", e) else S["pause_comma"]
                units.append(dict({"text": c, "pause": float(p), "ln": ln}, **({"emo": emo} if emo else {})))
        pause(S["pause_line"])
    if units:
        units[-1]["pause"] = 0.0
        units[0]["paced"] = paced
    return path, units


NUMW = {"एक", "दो", "तीन", "चार", "पांच", "पाँच", "छह", "छः", "सात", "आठ", "नौ", "दस", "one", "two", "three", "four",
        "five", "six", "seven", "eight", "nine", "ten"}
ENDING = re.compile(r"सो जाओ|सो जाइए|गुड नाईट|गुड नाइट|शुभ रात्रि|शब्बा ख़ैर|शब्बा खैर")


def slow_counts(units, S):
    """counted breaths ("एक... दो... तीन...") were read fast, in one go. Every number becomes its own phrase with a
    breath-long pause after it (pause_count, default 2.4 s) - the listener really has time to breathe."""
    pc = float(S.get("pause_count", 2.4))
    out = []
    for u in units:
        pieces = [x.strip(" ,।.") for x in re.split(r"\.\.\.|…|,", u["text"])]
        nums = [x for x in pieces if x in NUMW]
        if len(nums) < 2:
            out.append(u); continue
        rest = []
        for x in pieces:
            if not x:
                continue
            if x in NUMW:
                if rest:
                    out.append({"text": "... ".join(rest) + "...", "pause": float(S["pause_dots"])}); rest = []
                out.append({"text": x + "...", "pause": pc, "count": True})
            else:
                rest.append(x)
        if rest:
            out.append({"text": "... ".join(rest) + "...", "pause": u["pause"]})
        else:
            out[-1]["pause"] = max(out[-1]["pause"], u["pause"])
    return out


def gentle_ending(plan, S):
    """the good-night lines are whispered, not commanded: quieter, slower, with long pauses between them"""
    n = len(plan)
    start = next((i for i in range(max(0, n - 8), n) if ENDING.search(plan[i]["text"])), None)
    if start is None:
        return plan
    for i in range(start, n):
        plan[i]["ending"] = True
        if i + 1 < n:
            plan[i]["pause"] = max(plan[i]["pause"] * float(S.get("ending_pause_scale", 1.8)), 2.5)
    if start > 0:
        plan[start - 1]["pause"] = max(plan[start - 1]["pause"], 2.5)
    return plan

# --- dramatic pacing (भय / रहस्य): a held beat after "लेकिन उसके बाद...", a breath before "तभी", room after a reveal.
# Only the SILENCES change - every phrase is still spoken whole at the series' normal expressiveness, so there are
# no pitch jumps; lead-ins are at least two words (a lone word like "तभी" can come out garbled, so it gets a pause
# BEFORE it instead of being cut off on its own).
LEADS = ["लेकिन उसके बाद", "और उसके बाद", "पर उसके बाद", "मगर उसके बाद", "लेकिन तभी", "और तभी", "पर तभी",
         "लेकिन फिर", "और फिर", "तभी अचानक", "और अचानक", "लेकिन अचानक", "उसके बाद", "और उस रात", "उस रात",
         "और वहाँ", "और वहां", "लेकिन वहाँ", "लेकिन वहां", "पर जैसे ही", "और जैसे ही", "जैसे ही", "उसी पल",
         "और उसी पल", "ठीक उसी वक़्त", "ठीक उसी वक्त", "लेकिन जो", "और जो", "but then", "and then", "and suddenly"]
BEATS = ("तभी", "अचानक", "एकदम", "फिर", "suddenly", "then")         # a breath BEFORE these
# every series its own kind of tension (drama_style): horror/thriller = held breath; story = turns of the story;
# soft (romance, ASMR) = tender, lingering moments; explain (knowledge) = a beat before the point that matters
LEADS_BY = {
    "horror": LEADS,
    "story": LEADS + ["लेकिन उस दिन", "पर उस दिन", "और उस दिन", "सालों बाद", "कई साल बाद", "लेकिन किस्मत", "और फिर एक दिन",
                      "फिर एक दिन", "लेकिन उसे क्या पता था", "उसे नहीं पता था"],
    "soft": ["और फिर", "उस पल", "उसी पल", "और उस पल", "धीरे से", "और धीरे से", "पहली बार", "और उस रात", "उस रात",
             "बस एक पल", "और जैसे ही", "जैसे ही", "and then", "slowly"],
    "explain": ["लेकिन असल में", "असल में", "सबसे ज़रूरी बात", "सबसे जरूरी बात", "इसका मतलब", "यानी कि", "लेकिन सवाल यह है",
                "सवाल यह है", "दिलचस्प बात यह है", "हैरानी की बात यह है", "सोचिए", "ज़रा सोचिए", "but here is the thing",
                "in other words"]}
BEATS_BY = {"horror": BEATS, "story": ("तभी", "अचानक", "एकदम"), "soft": ("धीरे", "हल्के"), "explain": ()}


def _jit(text, lo=0.85, hi=1.2):
    import zlib
    return lo + (hi - lo) * (zlib.crc32(text.encode("utf-8")) % 1000) / 999.0


def dramatic(units, S):
    """units -> units with tension silences. Deterministic (same script = same pauses), so a Repair remake of
    single lines keeps the numbering."""
    hold = float(S.get("pause_tension", 1.7))
    style = str(S.get("drama_style", "horror"))
    leads, beats = LEADS_BY.get(style, LEADS), BEATS_BY.get(style, BEATS)
    leads = sorted(leads, key=lambda x: -len(x))
    out = []
    for u in units:
        t = u["text"].strip()
        done = False
        for lead in leads:
            m = re.match(r"^(" + re.escape(lead) + r")[,\s.…]+(.+)$", t, re.I)
            if m and len(m.group(2).split()) >= 3 and (len(lead.split()) >= 2 or S.get("flow")):   # a lone word only
                # when flow mode joins it to the phrase before it (alone it can come out garbled)
                out.append(dict(u, text=m.group(1) + "...", pause=round(hold * _jit(t, 0.9, 1.15), 2), hold=True,
                                fixed=False))
                out.append(dict(u, text=m.group(2), fixed=u.get("fixed", False)))
                done = True
                break
        if not done:
            if out and t.split()[0].strip(",.…") in beats and not out[-1].get("hold"):
                out[-1]["pause"] = max(out[-1]["pause"], round(hold * 0.75 * _jit(out[-1]["text"]), 2))
                out[-1]["hold"] = True
            out.append(dict(u))
    for i, u in enumerate(out):                       # a short line after a long one = a reveal: let it land
        t = u["text"].strip()
        if i and len(t.split()) <= 5 and len(out[i - 1]["text"].split()) >= 5 and re.search(r"[।.!?…]$", t) \
                and not t.endswith("...") and not u.get("fixed"):
            u["pause"] = max(u["pause"], round(hold * 1.2 * _jit(t), 2)); u["hold"] = True
            if not out[i - 1].get("fixed"):
                out[i - 1]["pause"] = max(out[i - 1]["pause"], round(hold * 0.6, 2)); out[i - 1]["hold"] = True
        elif t.endswith("?") and not u.get("fixed"):
            u["pause"] = max(u["pause"], round(float(S["pause_sentence"]) * 1.35, 2)); u["hold"] = True
    for u in out:
        u["pause"] = min(u["pause"], float(S.get("pause_tension_max", 2.6)))
    out = vary(out, S)
    if out:
        out[-1]["pause"] = 0.0
    return out


def vary(units, S):
    """every ordinary line / paragraph silence a little different (0.85-1.2x, fixed per line) - no metronome"""
    for u in units:
        if not u.get("hold") and not u.get("fixed") and u["pause"] >= float(S["pause_line"]) - 0.01:
            u["pause"] = round(u["pause"] * _jit(u["text"]), 2)
    return units


# --- emotions: a line marked [डर] / [उदास] / … is read a little differently - only gentle nudges of the SAME voice
# (expressiveness, loudness, the pause after it); between lines the change is limited, so no sudden pitch jumps.
EMO = {  # expressiveness +/-, temperature +/-, loudness dB, pause x
    "डर": (0.12, 0.0, -1.0, 1.15), "fear": (0.12, 0.0, -1.0, 1.15),
    "फुसफुसाहट": (-0.15, -0.05, -5.0, 1.2), "whisper": (-0.15, -0.05, -5.0, 1.2),
    "रहस्य": (0.05, 0.0, -1.5, 1.2), "mystery": (0.05, 0.0, -1.5, 1.2),
    "उदास": (-0.05, 0.0, -2.0, 1.25), "sad": (-0.05, 0.0, -2.0, 1.25),
    "प्यार": (-0.05, 0.0, -2.0, 1.1), "tender": (-0.05, 0.0, -2.0, 1.1),
    "गुस्सा": (0.15, 0.0, 1.0, 0.9), "angry": (0.15, 0.0, 1.0, 0.9),
    "उत्साह": (0.12, 0.0, 1.0, 0.9), "excited": (0.12, 0.0, 1.0, 0.9),
    "सामान्य": (0, 0, 0, 1), "normal": (0, 0, 0, 1)}


def emotions(plan, S):
    """sets ex / temp / gain per phrase from its [mark]; the step from one phrase to the next is at most
    emotion_step (0.08) of expressiveness - feelings fade in and out instead of switching"""
    k = float(S.get("emotion_strength", 1.0))
    base_ex, base_t = float(S["exaggeration"]), float(S["temperature"])
    step = float(S.get("emotion_step", 0.08))
    lo, hi = float(S.get("emotion_min_ex", 0.25)), float(S.get("emotion_max_ex", 0.75))
    prev = base_ex
    for p in plan:
        d = EMO.get(str(p.get("emo", "")).lower() if p.get("emo", "").isascii() else p.get("emo", ""), (0, 0, 0, 1))
        want = min(hi, max(lo, base_ex + d[0] * k))
        ex = round(min(prev + step, max(prev - step, want)), 3)
        p["ex"], p["temp"], p["gain_db"] = ex, round(base_t + d[1] * k, 3), d[2] * k
        if not p.get("fixed") and not p.get("hold") and p["pause"] > 0:
            p["pause"] = round(p["pause"] * (1 + (d[3] - 1) * k), 2)
        prev = ex
    return plan


def bake_script(spath, units, S):
    """write the paced script back (the original is kept in _old\\): every held silence as (... 1.8), so the
    script itself says how it is read - edit a number there and remake to change one pause"""
    import shutil
    old = os.path.join(os.path.dirname(spath), "_old")
    os.makedirs(old, exist_ok=True)
    base = os.path.splitext(os.path.basename(spath))[0]
    shutil.copy2(spath, os.path.join(old, f"{base}_before_pacing_{time.strftime('%Y%m%d_%H%M')}.txt"))
    para = float(S["pause_paragraph"])
    lines, cur, cur_ln = ["(paced)", ""], [], None
    for i, u in enumerate(units):
        if cur and (u.get("ln") != cur_ln):
            lines.append(" ".join(cur)); cur = []
        cur.append(("[" + u["emo"] + "] " if u.get("emo") and not cur else "") + u["text"].strip())
        cur_ln = u.get("ln")
        last = i + 1 == len(units)
        if u.get("hold") or u.get("fixed"):
            lines.append(" ".join(cur)); cur = []; cur_ln = None
            if not last:
                lines.append(f"(... {u['pause']:.2f})")
        elif u["pause"] >= para * 0.84 and not last:
            lines.append(" ".join(cur)); cur = []; cur_ln = None
            lines.append("")
    if cur:
        lines.append(" ".join(cur))
    tmp = spath + ".part"
    open(tmp, "w", encoding="utf-8").write("\n".join(lines).rstrip() + "\n")
    os.replace(tmp, spath)


def flow_groups(plan, S):
    """flow mode: neighbouring phrases are spoken in ONE breath-group (like your real reading), so the voice
    carries on from one phrase into the next instead of ending every phrase. A long pause ((...) or a new
    paragraph) or ~flow_max_words words ends a group. Joining = the same as the flow training clips
    (short pause = comma, longer = '...')."""
    mx = int(S.get("flow_max_words", 28))
    stop = float(S.get("pause_marker", 1.6)) - 0.01
    out, cur = [], []

    def close():
        if cur:
            t = cur[0]["text"].strip().rstrip(" ,।.")
            for a, b in zip(cur, cur[1:]):
                t += (", " if a["pause"] < 0.6 and not a["text"].strip().endswith("...") else "... ") + \
                    b["text"].strip().rstrip(" ,।.")
            last = cur[-1]["text"].strip()                # keep YOUR script's ending exactly (it carries your style)
            end = "..." if last.endswith("...") or last.endswith("…") else "?" if last.endswith("?") else \
                "!" if last.endswith("!") else "."
            out.append(dict({"text": t.rstrip("?!") + end, "pause": cur[-1]["pause"], "parts": len(cur)},
                            **({"emo": cur[0]["emo"]} if cur[0].get("emo") else {}),
                            **{k: True for k in ("hold", "fixed") if cur[-1].get(k)}))
            cur.clear()

    for p in plan:
        if p.get("count") or p.get("ending"):              # counted breaths + good-night lines stay alone
            close()
            out.append(dict(p, parts=1))
            continue
        if cur and (sum(len(c["text"].split()) for c in cur) + len(p["text"].split()) > mx or
                    cur[-1].get("emo", "") != p.get("emo", "")):
            close()
        cur.append(p)
        if p["pause"] >= stop or p.get("hold") or p.get("fixed"):
            close()
    close()
    return out


def main(src, name=None, model=None, redo="", style=None, pace=None, s3gen=None, flow=None, overrides=None):
    import soundfile as sf
    S = dict(cfg.C["speak"])
    for k, v in (overrides or {}).items():              # --set key=value (one run only, config.json unchanged)
        S[k] = v
        say(f"   setting for this run: {k} = {v}")
    if pace:
        S["pace"] = pace
    spath, plan = read_script(src)
    if not plan:
        raise SystemExit("the script is empty")
    # plan version 2 (slow counts, soft ending) only for NEW narrations: an older narration keeps its line numbering,
    # so remaking single lines of it (Repair) still replaces the right lines
    _nm = name or (os.path.basename(os.path.dirname(spath)) if os.path.basename(spath).lower() == "script.txt"
                   else os.path.splitext(os.path.basename(spath))[0])
    _out = os.path.join(cfg.NARRATIONS, re.sub(r'[<>:"/\\|?*]', "_", _nm))
    _pv = os.path.join(_out, "plan_version.txt")
    _old = os.path.isdir(os.path.join(_out, "lines")) and any(f.endswith(".wav") for f in os.listdir(os.path.join(_out, "lines")))
    if not _old or (os.path.exists(_pv) and open(_pv).read().strip() == "2"):
        n0 = len(plan)
        plan = gentle_ending(slow_counts(plan, S), S)
        if str(S.get("dramatic", "")).lower() in ("1", "true", "yes"):
            if plan[0].get("paced"):
                plan = vary(plan, S)
                say("   dramatic pacing: already baked into the script (paced) - read as written")
            else:
                plan = dramatic(plan, S)
                nh = sum(1 for p in plan if p.get("hold"))
                say(f"   dramatic pacing: {nh} held silences (tension words, reveals, questions) - pitch untouched")
                try:
                    bake_script(spath, plan, S)
                    say("   the paced script was saved back (old one kept in _old) - (... 1.8) = a held pause you can edit")
                except Exception as e:
                    say(f"   (could not save the paced script: {e})")
        if len(plan) != n0:
            say(f"   counted breaths read slowly: one number at a time ({len(plan) - n0} extra pauses)")
        os.makedirs(_out, exist_ok=True)
        open(_pv, "w").write("2")
    if flow is not None:
        S["flow"] = bool(flow)
    if S.get("flow"):
        n0 = len(plan)
        plan = flow_groups(plan, S)
        say(f"   flow mode: {n0} phrases spoken as {len(plan)} breath-groups")
    if str(S.get("emotions", "")).lower() in ("1", "true", "yes"):
        plan = emotions(plan, S)
        ne = sum(1 for p in plan if p.get("emo"))
        say(f"   emotions: {ne} phrases with a feeling mark (gentle: expressiveness {min(p['ex'] for p in plan):.2f}"
            f"-{max(p['ex'] for p in plan):.2f}, pitch untouched)")
    name = name or (os.path.basename(os.path.dirname(spath)) if os.path.basename(spath).lower() == "script.txt"
                    else os.path.splitext(os.path.basename(spath))[0])
    if pace and str(pace).lower() != "auto":
        name = f"{name}_{re.sub(r'[^a-z]+', '_', str(pace).lower()).strip('_')}"   # its own folder -> compare
    out = os.path.join(cfg.NARRATIONS, re.sub(r'[<>:"/\\|?*]', "_", name))
    ldir = os.path.join(out, "lines")
    os.makedirs(ldir, exist_ok=True)
    ck_path = resolve_checkpoint(model or S["model"])
    stamp = os.path.join(out, "voice_used.txt")
    ref = resolve_reference(style)
    from .model import resolve_s3gen
    s3_path = resolve_s3gen(s3gen if s3gen is not None else S.get("s3gen", "base"))
    gen = {k: v for k, v in S.items() if not k.startswith(("pause", "breath", "loudness", "music", "with_music", "_"))}
    used = f"{ck_path or 'base (no training)'}|{s3_path or 's3 base'}|{ref}|{json.dumps(gen, sort_keys=True)}"   # joining-only settings
    # (pauses, breaths, loudness) do not force new phrases
    if os.path.exists(stamp) and open(stamp, encoding="utf-8").read().strip() != used:
        old = os.path.join(out, time.strftime("lines_old_%Y%m%d_%H%M"))
        os.replace(ldir, old)
        prev = os.path.join(out, "narration.wav")              # keep the previous version to compare
        if os.path.exists(prev):
            os.replace(prev, os.path.join(out, time.strftime("narration_old_%Y%m%d_%H%M.wav")))
        os.makedirs(ldir)
        say(f"   voice or settings changed since the last run - old lines kept in {os.path.basename(old)}\\")
    open(stamp, "w", encoding="utf-8").write(used)
    for n in [int(x) for x in re.findall(r"\d+", redo or "")]:
        p = os.path.join(ldir, f"{n:04d}.wav")
        if os.path.exists(p):
            os.replace(p, p[:-4] + time.strftime("_old_%H%M%S.wav"))
    infos_path = os.path.join(out, "_lines.json")
    infos = json.load(open(infos_path, encoding="utf-8")) if os.path.exists(infos_path) else {}
    todo = [i for i in range(len(plan)) if not os.path.exists(os.path.join(ldir, f"{i + 1:04d}.wav"))]
    words = sum(len(p["text"].split()) for p in plan)
    say(f"   {len(plan)} sentences, {words} words (~{words / words_per_second() / 60:.0f} min of voice); "
        f"{len(todo)} to make")
    if todo:
        m = load_base()
        ck = use_checkpoint(m, ck_path) if ck_path else None
        if s3_path:
            from .model import use_s3gen
            use_s3gen(m, s3_path)
        if ck:
            from .model import patient_brakes
            patient_brakes(S)
        else:
            S["repetition_penalty"] = 2.0                  # plain clone: Chatterbox's own setting
        if not ck:
            say("   voice: plain clone of your voice prompt (no trained add-on found / chosen)")
        say(f"   style (voice prompt): {os.path.basename(ref)}")
        from . import match
        do_match = bool(ck) and S.get("match", True) and match.available()
        if ck and S.get("match", True) and not do_match:
            say("   (match to you is off: praat not installed - run setup.bat)")
        if do_match:
            T = match.targets("sukoon" if (not style or "sukoon" in str(style).lower()) else "story")
            say(f"   match to you: pitch -> {T['f0']:.0f} Hz, pace -> {T['rate']:.1f} words/s")
        series = "sukoon" if (not style or "sukoon" in str(style).lower()) else "story"
        set_voice(m, ref, ck, float(S["exaggeration"]), style=series, pace_setting=S.get("pace", "auto"))
        wps = words_per_second()
        t0, done_audio = time.time(), 0.0
        for k, i in enumerate(todo):
            seed = 7 + i + (1000 if redo and f"{i + 1}" in re.findall(r"\d+", redo) else 0)
            Si = dict(S, exaggeration=plan[i]["ex"], temperature=plan[i]["temp"]) if "ex" in plan[i] else S
            a, info = speak_sentence(m, plan[i]["text"], seed, Si, wps, label=f"[{i + 1}] ")
            if ck and S.get("match", True) and do_match:
                a, mi = match.apply(a, m.sr, plan[i]["text"], series, S)
                info.update(mi)
            p = os.path.join(ldir, f"{i + 1:04d}.wav")
            sf.write(p + ".part.wav", a, m.sr, subtype="PCM_16")
            os.replace(p + ".part.wav", p)
            infos[str(i + 1)] = dict(info, text=plan[i]["text"])
            json.dump(infos, open(infos_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            done_audio += len(a) / m.sr
            el = time.time() - t0
            say(f"   sentence {i + 1}/{len(plan)}  {len(a) / m.sr:4.1f} s"
                f"{'' if info.get('ok') else '  (still not perfect - see report)'}   "
                f"[{k + 1}/{len(todo)} done, ~{el / (k + 1) * (len(todo) - k - 1) / 60:.0f} min left]")
    # join
    sr = 24000
    timing, t, pp = [], 0.0, []
    for i, p in enumerate(plan):
        a, r = sf.read(os.path.join(ldir, f"{i + 1:04d}.wav"), dtype="float32")
        sr = r
        if p.get("ending"):                                # whispered good night: softer than the story
            a = a * float(10 ** (float(S.get("ending_db", -5)) / 20))
        if p.get("count"):
            a = a * float(10 ** (-2 / 20))
        if p.get("gain_db"):
            a = a * float(10 ** (float(p["gain_db"]) / 20))
        timing.append({"n": i + 1, "start": round(t, 3), "end": round(t + len(a) / r, 3), "text": p["text"]})
        g = p["pause"] if i + 1 < len(plan) else 0.0
        pp.append((a, g)); t += len(a) / r + g
    from . import breaths
    pieces, nb = breaths.place(pp, sr, S.get("breaths", "light"), seed=len(plan), S=S)
    if nb:
        say(f"   added {nb} of your real breaths in the pauses (setting: {S.get('breaths')})")
    full = np.concatenate([np.zeros(int(0.5 * sr), np.float32)] + pieces + [np.zeros(int(1.0 * sr), np.float32)])
    if S.get("room_tone", False):
        try:
            from . import roomtone
            full, lvl = roomtone.lay_under(full, sr, float(S.get("room_tone_db", 0)))
            if lvl is not None:
                say(f"   room tone of your recordings laid under the pauses ({lvl:.0f} dB below your voice)")
        except Exception as e:
            say(f"   (room tone skipped: {e})")
    for x in timing:
        x["start"] += 0.5; x["end"] += 0.5
    raw = os.path.join(out, "_joined.wav")
    sf.write(raw, full, sr, subtype="FLOAT")
    final = os.path.join(out, "narration.wav")
    lufs = float(S.get("loudness_lufs", -18))
    af = ("adeclick=w=55:o=75:t=4," if S.get("declick") else "") + f"loudnorm=I={lufs}:TP=-2:LRA=11"
    if S.get("declick"):
        say("   de-click: removing small clicks / pops before the final loudness")
    r = subprocess.run([cfg.ffmpeg(), "-v", "error", "-y", "-i", raw, "-af", af,
                        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", final + ".part.wav"])
    if r.returncode == 0:
        os.replace(final + ".part.wav", final)
        os.remove(raw)
    else:
        os.replace(raw, final)
    open(os.path.join(out, "script.txt"), "w", encoding="utf-8").write(
        "".join(p["text"] + ("\n\n" if p["pause"] >= float(S["pause_paragraph"]) else "\n") for p in plan))
    json.dump(timing, open(os.path.join(out, "timing.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    try:                                                   # the word rack: words this voice keeps stumbling on
        rp = os.path.join(os.path.dirname(cfg.NARRATIONS), "word_rack.json")
        rack = json.load(open(rp, encoding="utf-8")) if os.path.exists(rp) else {}
        new = 0
        for k, v in infos.items():
            for w in v.get("missed") or []:
                r = rack.setdefault(w, {"count": 0, "examples": [], "narrations": []})
                if os.path.basename(out) in r["narrations"] and v.get("text") in r["examples"]:
                    continue
                r["count"] += 1; new += 1
                r["last"] = time.strftime("%Y-%m-%d")
                if v.get("text") and v["text"] not in r["examples"]:
                    r["examples"] = (r["examples"] + [v["text"]])[-3:]
                if os.path.basename(out) not in r["narrations"]:
                    r["narrations"] = (r["narrations"] + [os.path.basename(out)])[-10:]
        if new:
            json.dump(rack, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            say(f"   word rack: {new} stumble(s) noted -> {rp}")
    except Exception as e:
        say(f"   (word rack not updated: {e})")
    bad = [(int(k), v) for k, v in infos.items() if not v.get("ok", True) and int(k) <= len(plan)]
    rep = [f"narration of {spath}", f"voice: {ck_path or 'base'}", f"length {len(full) / sr / 60:.1f} min", ""]
    if bad:
        rep.append("sentences that still did not sound right after all retakes (listen; redo with --redo n,n):")
        rep += [f"  {k}: {v['text']}  (letters wrong {v.get('letter_error')}, {v.get('dur')} s)" for k, v in sorted(bad)]
    else:
        rep.append("every sentence passed the check.")
    open(os.path.join(out, "report.txt"), "w", encoding="utf-8").write("\n".join(rep) + "\n")
    if S.get("with_music") and S.get("music"):
        try:
            from . import music
            music.mix(final)
        except BaseException as e:
            say(f"   (music mix skipped: {e})")
    say(f"\nDONE: {final}  ({len(full) / sr / 60:.1f} min)")
    if bad:
        say(f"   {len(bad)} sentences to listen to: {', '.join(str(k) for k, _ in sorted(bad))}  (report.txt)")
 
 
# ========================================== 
# File: textsplit.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\textsplit.py 
# ========================================== 
"""textsplit.py - ONE way to cut Hindi text into sentences, used by the voice (timing of every sentence),
the shot planner (a picture starts at a sentence) and the captions - so they always agree. No imports."""
import re


def sentences(text, max_words=22):
    """'...' pauses kept; long sentences are split at commas."""
    t = re.sub(r"\s+", " ", str(text).replace("…", "...")).strip().strip('"“”')
    parts = [s.strip() for s in re.split(r"(?<=[।?!])\s+|(?<=\.\.\.)\s+(?=\S{2})", t) if s.strip()]
    out = []
    for s in parts:
        if len(s.split()) <= max_words:
            out.append(s)
            continue
        cur = ""
        for c in re.split(r"(?<=,)\s+", s):
            if cur and len((cur + " " + c).split()) > max_words:
                out.append(cur)
                cur = c
            else:
                cur = (cur + " " + c).strip()
        if cur:
            out.append(cur)
    out = [s for s in out if re.search("[\u0900-\u097Fa-zA-Z0-9]", s)]
    merged = []                                   # 1-3 word bits ("गाँव में।") join a neighbour: the voice
    for s in out:                                 # model is unstable on them and captions would flash
        if merged and (len(merged[-1].split()) < 4 or len(s.split()) < 3) and \
                len((merged[-1] + " " + s).split()) <= max_words:
            merged[-1] = merged[-1] + " " + s
        else:
            merged.append(s)
    return merged
 
 
# ========================================== 
# File: train.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\train.py 
# ========================================== 
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
 
 
# ========================================== 
# File: __init__.py 
# Path: E:\Projects\hub_chatgpt\engines\voice\voice\__init__.py 
# ========================================== 
 
 
