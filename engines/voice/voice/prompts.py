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
