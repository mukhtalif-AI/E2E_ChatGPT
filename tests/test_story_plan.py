"""test_story_plan.py - Experimental story plan + logic check with a fake AI (no Ollama, no GPU).
    python tests/test_story_plan.py app <a finished script.txt>
Checks: plan -> write -> check in Experimental (16k, plan + check no-think, both capped at max_tokens), lines fixed by number or by text,
bad answers skipped, Working and table on=false keep the old full review, a failed plan or check falls back."""
import sys, json, re, tempfile
sys.path.insert(0, sys.argv[1]); import story as S
real = open(sys.argv[2], encoding="utf-8").read()
TMP = tempfile.mkdtemp()
real = re.sub(r"\(\.\.\.[^)]*\)", "(...)", real)
calls = []
FACTS_SEEN, FACTS_REPLY = [], [{"problems": []}]
MODE = {"outline": "good", "check": "good"}
def fake_ask(cfg, prompt, engine="auto", temp=0.85, as_json=False, say=print, ctx_len=8192):
    kind = "outline" if prompt.startswith("Before tonight") else "check" if "for LOGIC only" in prompt else \
           "facts" if prompt.startswith("FACTS CHECK") else \
           "review" if prompt.startswith(("Review this", "Check this")) else "write"
    calls.append((kind, as_json, ctx_len, cfg.get("_no_think", False), len(prompt), cfg.get("_num_predict")))
    if kind == "outline":
        if MODE["outline"] == "bad": return "", "m"
        return json.dumps({"facts": ["1998 fire", "money taken for five years"], "people": ["Vikram - journalist"],
                           "clues": [{"clue": "Present while school shut", "answer": "register filled later"}],
                           "twist": "Meera was Aryan", "reveal": "the school kept the name for grants",
                           "beats": ["a", "b", "c", "d", "e"]}), "m"
    if kind == "check":
        if MODE["check"] == "bad": return "sorry", "m"
        lines = [l for l in real.splitlines() if l.strip() and l.strip() != "(...)"]
        n = next(i + 1 for i, l in enumerate(lines) if "पिछले तीन महीनों" in l)
        m = next(i + 1 for i, l in enumerate(lines) if "Present" in l)
        return json.dumps({"problems": [
            {"n": n, "line": lines[n-1], "why": "three months vs five years", "fix": lines[n-1].replace("पिछले तीन महीनों से", "पिछले पाँच सालों से")},
            {"n": m + 3, "line": lines[m-1], "why": "clue never answered (wrong n on purpose)", "fix": lines[m-1] + " यह सवाल मेरे मन में अटक गया।"},
            {"n": 9999, "line": "nope", "why": "bad", "fix": "कुछ"},
            {"n": 3, "line": "", "why": "empty fix", "fix": ""}]}), "m"
    if kind == "facts":
        FACTS_SEEN.append(prompt)
        return json.dumps(FACTS_REPLY[0]), "m"
    if kind == "review": return real, "m"
    planned = MODE["outline"] == "good" and any(c[0] == "outline" for c in calls)
    assert ("PLAN TO FOLLOW" in prompt) == planned, "plan block"
    if planned: assert "Quotes: say who is speaking" in prompt and prompt.index("PLAN TO FOLLOW") < prompt.rindex("Now write")
    return real, "m"
S.ask = fake_ask
S.local_model = lambda cfg: "m"
S.wpm = lambda *a, **k: 150
S._story_avoid = lambda *a, **k: ""
log = []
say = lambda s: log.append(s)
rec = {"genre": "rahasya", "topic": "the school topper who never existed", "telling": "two timelines that meet at the end"}
def run(cfg, gid="rahasya"):
    cfg["media_dir"] = TMP
    calls.clear(); log.clear()
    if gid == "bhay":
        out, info = S.write_horror(cfg, say, "", 15, "", "auto", {"topic": "x", "telling": "t", "fear": "f", "ending": "e"})
    else:
        out, info = S.write_genre(cfg, gid, say, "", 15, "", "auto", rec)
    return out
ok = 0
def check(c, msg):
    global ok; print(("PASS " if c else "FAIL ") + msg); ok += bool(c)
# 1 experimental, good plan + check
out = run({"experimental_mode": True})
check([c[0] for c in calls] == ["outline", "write", "review", "check", "facts"], f"exp calls {[c[0] for c in calls]}")
check(calls[0][3] is True and calls[3][3] is True, "outline and check no-think")
check(calls[0][5] == calls[3][5] == S.STORY_PLAN["max_tokens"] and calls[1][5] is None and calls[2][5] is None, "plan + check capped, writer + review not")
check(all(c[2] == 16384 for c in calls), "16k context for plan, write, review, check")
check("पिछले पाँच सालों से" in out and "पिछले तीन महीनों से" not in out, "line fixed by number")
check("यह सवाल मेरे मन में अटक गया" in out, "line found by its text when the number was off")
check(sum("logic fix" in l for l in log) == 2 and any("2 line(s) fixed, 2 answer(s) not usable" in l for l in log), "2 fixed, 2 skipped logged")
check(abs(S.nwords(out) - S.nwords(S.clean_horror(real))) < 20, f"length kept {S.nwords(out)} vs {S.nwords(S.clean_horror(real))}")
print("\n".join(log[:14]))
# 2 working mode: old path untouched
out = run({})
check([c[0] for c in calls] == ["write", "review"] and calls[0][2] == 8192, f"working calls {[c[:3] for c in calls]}")
# 3 plan fails -> old way
MODE["outline"] = "bad"; out = run({"experimental_mode": True})
check([c[0] for c in calls] == ["outline", "outline", "write", "review"], f"bad plan -> old review {[c[0] for c in calls]}")
MODE["outline"] = "good"
# 4 check fails -> script kept
MODE["check"] = "bad"; out = run({"experimental_mode": True})
check(out == S.clean_horror(real) or S.nwords(out) == S.nwords(S.clean_horror(real)), "bad check -> script kept")
check(any("logic check unavailable" in l for l in log), "logged")
check(calls[-1][0] == "facts", "facts pass still runs when the logic check fails")
MODE["check"] = "good"
# 4b review off in the table -> plan, write, check only (the 13b8319 writer)
S.STORY_PLAN["review"] = False
run({"experimental_mode": True})
check([c[0] for c in calls] == ["outline", "write", "check", "facts"], f"review off calls {[c[0] for c in calls]}")
S.STORY_PLAN["review"] = True
# 4c rahasya never-supernatural rule reaches the plan + writer prompts
G = S.genre_rules({"media_dir": TMP}, "rahasya")
check("NOT HORROR" in S._rules_for(G, "standard") and "NOT HORROR" in S._rules_for(G, "mature"), "rahasya not-horror rule kept")
# 5 bhay experimental
out = run({"experimental_mode": True}, "bhay")
check([c[0] for c in calls] == ["outline", "write", "review", "check", "facts"], f"bhay exp calls {[c[0] for c in calls]}")
out = run({}, "bhay")
check([c[0] for c in calls] == ["write", "review"], "bhay working unchanged")
# 6 plan off via table
S.STORY_PLAN["on"] = False; out = run({"experimental_mode": True})
check([c[0] for c in calls] == ["write", "review"], "table on=false -> old way"); S.STORY_PLAN["on"] = True
# 7 _ollama_used copied back
cfg = {"experimental_mode": True}
def fa(c, *a, **k): c["_ollama_used"] = True; return '{"x":1}', "m"
S.ask = fa; S._json_ask(cfg, "p", "auto", 0.2, False, say); check(cfg.get("_ollama_used") is True, "_ollama_used copied back")
S.ask = fake_ask
# 8 facts pass (17 Oct, Kisse-Memories-Old-Atlas-Cycle): 'पास के ही ... कस्बे' then '450 किलोमीटर का लंबा सफर'
kis = ["मुझे खबर मिली कि पास के ही एक छोटे से कस्बे में एक बुजुर्ग व्यक्ति है जो पुरानी साइकिलों का काम करता है।",
       "मैंने ज़्यादा नहीं सोचा।",
       "उस जगह तक पहुँचने के लिए मैंने लगभग 450 किलोमीटर का लंबा सफर तय किया।",
       "सन 1994 में हम दोनों साथ स्कूल जाते थे।", "यह सब बहुत पुरानी बात है।"]
idx = list(range(len(kis)))
check(S._near_far(kis, idx) == [(1, 3)], f"near/far pair found {S._near_far(kis, idx)}")
check(S._near_far(kis[1:], idx[:-1]) == [], "no pair without the near line")
FACTS_SEEN.clear()
FACTS_REPLY[0] = {"problems": [{"n": 3, "line": kis[2], "why": "near town, not 450 km",
                                "fix": "उस जगह तक पहुँचने के लिए मैंने बस आधे घंटे का छोटा सा सफर तय किया।"}]}
plan = "Fixed facts (the same everywhere): Year 1994; the repair town is near home\nThe one twist: x\nBeat 1: a"
out = S.facts_check({"experimental_mode": True}, "\n".join(kis), plan, "किस्से", "auto", say, lambda t: t)
check(FACTS_SEEN and "The code found: line 1 calls a place near, but line 3" in FACTS_SEEN[0], "pair flagged in the ask")
check("the repair town is near home" in FACTS_SEEN[0] and "Beat 1" not in FACTS_SEEN[0], "plan facts only in the ask")
check("450" not in out and "आधे घंटे" in out and kis[0] in out, "far line fixed, near line kept")
S.STORY_PLAN["facts"] = False; FACTS_SEEN.clear()
check(S.facts_check({}, "\n".join(kis), plan, "x", "auto", say, lambda t: t) == "\n".join(kis) and not FACTS_SEEN, "facts off in the table")
S.STORY_PLAN["facts"] = True; FACTS_REPLY[0] = {"problems": []}
# a 'continue' reply that starts the story over is dropped (Bhay-Mystery-Mansion 18 Oct held the story twice)
rl = [l for l in real.splitlines() if l.strip() and l.strip() != "(...)"]
half = "\n".join(rl[:len(rl) // 2])
keep_ask = S.ask
S.ask = lambda *a, **k: (half, "m")
check(S._long_enough({}, half, S.nwords(half) * 3, "P", "auto", say, lambda t: t) == half, "retold continuation dropped")
S.ask = lambda *a, **k: ("\n".join(rl[len(rl) // 2:]), "m")
check(S.nwords(S._long_enough({}, half, S.nwords(real) + 1, "P", "auto", say, lambda t: t)) > S.nwords(half), "real continuation kept")
S.ask = keep_ask
print(f"{ok} checks passed")
