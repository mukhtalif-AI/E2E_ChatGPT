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
