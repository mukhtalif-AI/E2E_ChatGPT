"""test_sound_words.py - sound words in a script ('ठक... ठक...') are taken out of the speech and become effect cues.

    python tests\\test_sound_words.py [script.txt] [--sfx <SFX folder>]

No GPU, no Ollama, no hub. Everything is written in a temp folder; your episodes and scripts are only read.
script.txt (optional): a real script to show what would be taken out (nothing is changed).
"""
import json, os, shutil, sys, tempfile, wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
import sound_words as sw                                # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f"  - {detail}" if detail else ""), flush=True)


def strip(x):
    new, rem, kept = sw.strip_text(x)
    return new, [(r["kind"], r["said"], r["next_words"], r["in_gap"]) for r in rem], kept


def unit():
    print("strip_text:")
    doll = ("[फुसफुसाहट] तभी उसे छत से एक आवाज़ सुनाई दी—'ठक... ठक... ठक...' यह किसी के छोटे-छोटे कदमों की आवाज़ थी।\n"
            "[फुसफुसाहट] तभी, बेड के नीचे से एक दबी हुई आवाज़ आई।\n[डर] \"खुर्रर्र... खुर्रर्र...\"\n"
            "करण की नज़रें अचानक बिस्तर के नीचे टिकीं।")
    new, rem, _ = strip(doll)
    check("doll: 'ठक ठक ठक' out, sentence closed, pause, rest of the line kept",
          "सुनाई दी।\n(... 1.50)\n[फुसफुसाहट] यह किसी के" in new and "ठक" not in new, new.split("\n")[0])
    check("doll: line 'खुर्रर्र... खुर्रर्र...' replaced by a pause", "खुर्र" not in new and new.count("(... 1.50)") == 2)
    check("doll: kinds knock + scratch, cue on the words after", [r[0] for r in rem] == ["knock", "scratch"]
          and rem[0][2] == "यह किसी के छोटे" and rem[1][2] == "करण की नज़रें अचानक" and rem[0][3] and rem[1][3], str(rem))
    new, rem, _ = strip('तभी फोन बजा—"ट्रिंग ट्रिंग!" उसने उठाया।')
    check("bell: ट्रिंग ट्रिंग out", new == "तभी फोन बजा।\n(... 1.50)\nउसने उठाया।" and rem[0][0] == "bell_ring", repr(new))
    new, rem, _ = strip("दरवाज़े पर खट-खट की आवाज़ हुई और वह रुक गया।")
    check("'खट-खट की आवाज़' -> 'एक आवाज़', cue on those words", new == "दरवाज़े पर एक आवाज़ हुई और वह रुक गया।"
          and rem == [("knock", "खट खट", "एक आवाज़ हुई और", False)], repr(new))
    for x in ("उसका दिल धक-धक कर रहा था।", "वह धीरे-धीरे चलने लगा।", "'धीरे-धीरे...' उसने कहा।", "वह वहाँ टिक नहीं पाया।",
              "घर पहुँचकर उसने देखा।", "वह 'हम-हम' करता रहा", "उसने कहा, 'हाँ हाँ, ठीक है।'", "(... 1.70)",
              "बीचों-बीच... अकेली खड़ी थी।"):
        new, rem, _ = strip(x)
        check(f"unchanged: {x}", new == x and not rem, repr(new))
    new, rem, _ = strip("'छम... छम...' पायल की आवाज़ आई।")
    check("unknown sound said twice on its own: words dropped, no kind", new == "(... 1.50)\nपायल की आवाज़ आई।"
          and rem[0][0] == "", repr(new))
    new, rem, _ = strip("वह रुका।\n(... 1.70)\n[डर] ठक... ठक...\n(... 1.70)\nफिर सन्नाटा।")
    check("pauses on both sides of a removed line: one pause (the longer)", new == "वह रुका।\n(... 1.70)\nफिर सन्नाटा।", repr(new))


def cues(sfx_dir):
    print("cues into shots.json (temp copy):")
    import tools
    tmp = tempfile.mkdtemp(prefix="soundwords_")
    try:
        os.makedirs(os.path.join(tmp, "Scripts"))
        pics = os.path.join(tmp, "pics")
        os.makedirs(pics)
        shots = {"shots": [{"text": "रात थी। तभी उसे छत से एक आवाज़ सुनाई दी।", "start": 0, "end": 5, "cues": []},
                           {"text": "यह किसी के छोटे-छोटे कदमों की आवाज़ थी। वह डर गया।", "start": 5, "end": 10, "cues": []}]}
        json.dump(shots, open(os.path.join(pics, "shots.json"), "w", encoding="utf-8"), ensure_ascii=False)
        cfg = {"layout": {"scripts": "Scripts"}, "music_dir": os.path.join(sfx_dir, "..", "BGM")}
        say = lambda m: print("      " + m)
        ctx = {"cfg": cfg, "say": say, "media": tmp}
        open(os.path.join(tmp, "Scripts", "T.txt"), "w", encoding="utf-8").write(
            "रात थी। तभी उसे छत से एक आवाज़ सुनाई दी—'ठक... ठक...' यह किसी के छोटे-छोटे कदमों की आवाज़ थी। वह डर गया।\n"
            "[डर] \"ट्रिंग ट्रिंग\"\nवह डर गया।")
        sw.step(ctx, "T", "bhay")
        txt = open(os.path.join(tmp, "Scripts", "T.txt"), encoding="utf-8").read()
        check("step: script rewritten without the sound words", "ठक" not in txt and "ट्रिंग" not in txt, repr(txt))
        check("step: original kept in _old", any("before_sound_words" in f for f in os.listdir(os.path.join(tmp, "Scripts", "_old"))))
        rows = json.load(open(os.path.join(tmp, "Scripts", "T.sound_words.json"), encoding="utf-8"))["removed"]
        check("step: knock file found in the library", rows[0]["kind"] == "knock" and "knock" in rows[0]["file"].lower(),
              os.path.basename(rows[0]["file"]))
        sw.cues(ctx, "T", pics)
        sw.cues(ctx, "T", pics)                           # a second run adds nothing
        meta = json.load(open(os.path.join(pics, "shots.json"), encoding="utf-8"))
        c = meta["shots"][1]["cues"]
        check("cues: knock cue in scene 2 on 'यह किसी के छोटे', in_gap; bell on 'वह डर गया'; nothing twice",
              len(c) == 2 and c[0]["words"] == "यह किसी के छोटे" and c[0]["in_gap"] and c[0]["from_script"] == "ठक ठक"
              and c[1]["words"] == "वह डर गया", str(c))
        # the in_gap timing in self_review.cue_times: sound in the pause before the words, not on them
        import self_review
        nar = os.path.join(tmp, "nar", "narrations", "T")
        os.makedirs(nar)
        with wave.open(os.path.join(nar, "narration.wav"), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(8000), w.writeframes(b"\0\0" * 800)
        json.dump([{"n": 1, "start": 0, "end": 4, "text": "तभी उसे छत से एक आवाज़ सुनाई दी।"},
                   {"n": 2, "start": 5.6, "end": 9, "text": "यह किसी के छोटे-छोटे कदमों की आवाज़ थी।"}],
                  open(os.path.join(nar, "timing.json"), "w", encoding="utf-8"), ensure_ascii=False)
        ws = [{"w": x, "start": 0.3 * i, "end": 0.3 * i + 0.25} for i, x in enumerate("तभी उसे छत से एक आवाज़ सुनाई दी".split())]
        ws += [{"w": x, "start": 5.6 + 0.3 * i, "end": 5.85 + 0.3 * i} for i, x in enumerate("यह किसी के छोटे छोटे कदमों".split())]
        json.dump({"words": ws}, open(os.path.join(nar, "words.json"), "w", encoding="utf-8"), ensure_ascii=False)
        cfg.update({"sukoonvoice_media": os.path.join(tmp, "nar"), "experimental_mode": True})
        self_review.cue_times(ctx, "T", pics, "bhay")
        at = json.load(open(os.path.join(pics, "shots.json"), encoding="utf-8"))["shots"][1]["cues"][0].get("at")
        check("cue_times: effect in the pause (after 'दी' ends at 2.35, before 'यह' at 5.6)", at is not None and 2.35 < at < 3.0, str(at))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def real(path):
    print(f"what would be taken out of {path} (nothing changed):")
    new, rem, kept = sw.strip_text(open(path, encoding="utf-8").read())
    for r in rem:
        print(f"      '{r['said']}' ({r['kind'] or 'unknown'}) -> effect before '{r['next_words']}'")
    for k in kept:
        print(f"      kept (part of the sentence): '{k}'")
    print(f"      {len(rem)} removed, {len(kept)} kept")


if __name__ == "__main__":
    args = sys.argv[1:]
    sfx = args[args.index("--sfx") + 1] if "--sfx" in args else r"D:\Audio asset\SFX"
    rest = [a for i, a in enumerate(args) if a != "--sfx" and (i == 0 or args[i - 1] != "--sfx")]
    unit()
    if os.path.isdir(sfx):
        cues(sfx)
    else:
        print(f"(no SFX folder at {sfx} - cue checks skipped)")
    if rest:
        real(rest[0])
    bad = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} ok" + (f"; FAILED: {', '.join(bad)}" if bad else ""))
    sys.exit(1 if bad else 0)
