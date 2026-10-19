"""test_voice_cast.py - checks for app\\voice_cast.py (ghost voice, 19 Oct 2026). No model, no GPU; needs ffmpeg.

    python tests\\test_voice_cast.py [--ffmpeg path]
"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
import numpy as np
import voice_cast as v

FF = sys.argv[sys.argv.index("--ffmpeg") + 1] if "--ffmpeg" in sys.argv else "ffmpeg"
ok = bad = 0


def check(name, cond, info=""):
    global ok, bad
    ok, bad = (ok + 1, bad) if cond else (ok, bad + 1)
    print(("ok   " if cond else "FAIL ") + name + (f"  ({info})" if info and not cond else ""))


SCRIPT = """(paced)
[प्यार] वो हर रात कहता, "भैया, क्या चाय बन गई?"
[फुसफुसाहट] उस तरफ से आवाज़ आई— "भैया, इतनी जल्दी क्या है?
[रहस्य] अभी तो चाय का समय भी नहीं हुआ।" फिर मैं भागा।
[रहस्य] वरुण के नंबर से एक नया संदेश आया था: "भैया, आप बाहर क्यों चले गए?"
[डर] आओ... आओ हमारे साथ।" वो आवाज़ झील से निकल रही थी।
[रहस्य] वह एक 'ध्वनि परजीवी' था।
[फुसफुसाहट] सिर्फ एक फुसफुसाहट सुनाई दी... "भैया, अब चाय का समय हो गया है।"
The repetition of "आओ हमारे साथ" at the end is meant to be unsettling."""

sp = v.spans(SCRIPT)
qs = [x["quote"] for x in sp]
check("five double-quoted lines found (single quotes and the English note left out)", len(sp) == 5, qs)
check("a quote over two lines is one quote", qs[1] == "भैया, इतनी जल्दी क्या है? अभी तो चाय का समय भी नहीं हुआ।", qs[1])
check("a lone closing quote = from the start of its line", qs[3] == "आओ... आओ हमारे साथ।" and sp[3]["orphan"], qs[3])
check("words before the quote kept for the written check", "संदेश" in sp[2]["before"], sp[2]["before"])
ask = v.story_for_ask(SCRIPT, sp)
check("story for the AI marks each quote", all(f"[Q{i}]" in ask for i in range(1, 6)) and "[प्यार]" not in ask)

G = {"role": "ghost", "heard": True, "sure": True, "speaker": "वरुण का भूत"}
H = {"role": "human", "heard": True, "sure": True, "speaker": "वरुण"}
r1 = {1: dict(H, heard=False), 2: G, 3: G, 4: G, 5: G}
r2 = {1: dict(H, heard=False), 2: G, 3: G, 4: dict(G, role="unsure"), 5: G}
s = {"roles": {"ghost": "ghost"}, "max_lines": 4}
ch, rej = v.decide(sp, [r1, r2], s)
why = {r["n"]: r["why"] for r in rej}
check("a human line stays narrator", 1 in why)
check("a written message stays narrator even when the AI says ghost", "written" in why.get(3, ""), why.get(3))
check("two asks that disagree -> narrator", "disagree" in why.get(4, ""), why.get(4))
check("ghost lines chosen", [c["n"] for c in ch] == [2, 5], [c["n"] for c in ch])
ch1, _ = v.decide(sp, [{**r1, 4: dict(G, sure=False)}], s)
check("not sure -> narrator", 4 not in [c["n"] for c in ch1])
ch2, rej2 = v.decide(sp, [{i: G for i in range(1, 6)}], {"roles": {"ghost": "ghost"}, "max_lines": 2}, written_words=[])
check("cap keeps the first and the last", [c["n"] for c in ch2] == [1, 5] and len(rej2) == 3, [c["n"] for c in ch2])
ch3, _ = v.decide(sp, [r1], {"roles": {}, "max_lines": 4})
check("a series with no roles -> nothing", not ch3)
check("reply parsing", v.parse_reply({"lines": [{"q": "Q2", "role": "Ghost", "heard": "true", "sure": True}]})[2]["role"] == "ghost")

new = v.own_lines(SCRIPT, sp, ch)
lines = new.split("\n")
check("chosen quote on its own line", '[फुसफुसाहट] "भैया, इतनी जल्दी क्या है?' in lines and "[फुसफुसाहट] उस तरफ से आवाज़ आई—" in lines, new)
check("words after the quote on their own line with that line's mark", "[रहस्य] फिर मैं भागा।" in lines, new)
check("not-chosen lines untouched", '[प्यार] वो हर रात कहता, "भैया, क्या चाय बन गई?"' in lines)
bare = lambda t: [w for ln in t.split("\n") for w in v._words(v._TAG.sub("", ln))]
check("no word lost or added", bare(new) == bare(SCRIPT))

tim = [{"n": 1, "start": 0.5, "end": 2.0, "text": "उस तरफ से आवाज़ आई—"},
       {"n": 2, "start": 3.0, "end": 5.0, "text": "भैया, इतनी जल्दी क्या है?"},
       {"n": 3, "start": 6.0, "end": 8.0, "text": "अभी तो चाय का समय भी नहीं हुआ।"},
       {"n": 4, "start": 9.0, "end": 10.0, "text": "फिर मैं भागा।"},
       {"n": 5, "start": 11.0, "end": 13.0, "text": "भैया, अब चाय का समय हो गया है।"}]
check("quote over two voiced lines found", v.find_lines(tim, qs[1])[:2] == (1, 2), v.find_lines(tim, qs[1]))
check("found after the previous one only", v.find_lines(tim, qs[4], 3)[:2] == (4, 4))
check("a quote that is not in the voice -> None", v.find_lines(tim, "यह पंक्ति कहीं नहीं है") is None)

sr = 24000
t = np.arange(int(1.5 * sr)) / sr
word = (0.3 * np.sin(2 * np.pi * 180 * t) * (np.sin(2 * np.pi * 3 * t) > 0)).astype(np.float32)
dry = np.concatenate([np.zeros(sr, np.float32), word, np.zeros(sr, np.float32), word, np.zeros(sr, np.float32)])[:, None]
for name in v.PRESETS:
    y = v.render(FF, word, sr, name, 0.4)
    rms = lambda x: float(np.sqrt(np.mean(x ** 2)))
    check(f"preset {name}: same loudness, no clipping", abs(rms(y[:len(word)]) / rms(word) - 1) < 0.05
          and np.abs(y).max() <= 0.98 and len(y) == len(word) + int(0.4 * sr))
pulse = np.zeros(sr, np.float32); pulse[12000:13200] = 0.3
sw = np.abs(v._swell(pulse, sr, 350))
check("reverse swell rises before the word, nothing after it", sw[6000:11800].mean() > 1000 * sw[13400:19000].mean())
out = v.apply(FF, dry, sr, [{"start": 3.5, "end": 5.0, "tail": 0.4, "preset": "ghost"}])
diff = np.abs(out - dry)[:, 0]
check("only the ghost line changes", diff[:int(3.46 * sr)].max() == 0 and diff[int(5.5 * sr):].max() == 0
      and diff[int(3.6 * sr):int(4.9 * sr)].max() > 0.01)
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "a.wav")
    v.write_wav(p, dry, sr, 2)
    back, sr2, w = v.read_wav(p)
    check("wav round trip", sr2 == sr and w == 2 and np.abs(back - dry).max() < 1e-3)
print(f"\n{ok} ok, {bad} failed")
sys.exit(1 if bad else 0)
