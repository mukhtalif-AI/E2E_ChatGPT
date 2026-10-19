"""test_genre_fit.py - genre brief + genre-fit check (19 Oct 2026) with a fake AI (no Ollama, no GPU).
    python tests/test_genre_fit.py app
Checks: every story series has a brief; Working mode gets none of it; in Experimental the seed, the prompt (so the plan
and the writer), the review and the fresh-seed rule carry the brief; the plan is redone once when it scores low; a
script under min_score is rewritten once and the better one is kept; a worse or short rewrite keeps the first; a
script that fits is not rewritten; Bhay drops the investigator telling, Kisse its 'years later' tellings; Rahasya has
no 'nobody was killed' angle; the fit report and the rewrite land in the info / versions."""
import sys, json, re, tempfile, random
sys.path.insert(0, sys.argv[1]); import story as S
TMP = tempfile.mkdtemp()
ok = fail = 0
def check(name, cond):
    global ok, fail
    ok += bool(cond); fail += not cond
    print(("PASS " if cond else "FAIL ") + name)

PLAN = {"facts": ["2024", "Pune"], "people": ["Neha - inspector"], "clues": [{"clue": "a bell", "answer": "a trick"}],
        "twist": "the bell hid a sound", "reveal": "smuggling", "beats": ["hook", "subscribe", "bell", "look", "find", "end"]}
PLAN2 = dict(PLAN, reveal="the accountant was poisoned by his partner", beats=PLAN["beats"] + ["murder found"])
rng = random.Random(3)
SYL = "क ख ग घ च ज ट ड त द न प ब म र ल व स ह".split()
def lines(tag, n=40):
    return "\n".join(f"{tag} " + " ".join("".join(rng.choice(SYL) + "ा" for _ in range(3)) for _ in range(9)) + "।"
                     for _ in range(n))
DRAFT = lines("पहला")
NEW = lines("नया")
calls, MODE, seen = [], {}, {}

def fake_ask(cfg, prompt, engine="auto", temp=0.85, as_json=False, say=print, ctx_len=8192):
    p = prompt
    if p.startswith("You plan episodes"):
        calls.append("seed"); seen["seed"] = p; return json.dumps({"topic": "a death in a locked office"}), "m"
    if p.startswith("Before tonight"):
        calls.append("outline"); seen["outline"] = p; return json.dumps(PLAN), "m"
    if p.startswith("You check the PLAN of a story"):
        calls.append("genre_plan"); seen["genre_plan"] = p
        if MODE.get("plan_fits"):
            return json.dumps({"score": 8, "reads_as": "", "missing": []}), "m"
        return json.dumps({"score": 3, "reads_as": "भय", "missing": ["no crime"], "plan": PLAN2}), "m"
    if p.startswith("You judge whether"):
        n = calls.count("fit"); calls.append("fit"); seen.setdefault("fit", []).append(p)
        s = MODE.get("scores", [8])[n]
        return json.dumps({"score": s, "reads_as": "किस्से", "missing": ["no victim"], "changes": ["Neha finds a body"]}), "m"
    if "for LOGIC only" in p:
        calls.append("logic"); return json.dumps({"problems": []}), "m"
    if p.startswith("FACTS CHECK"):
        calls.append("facts"); return json.dumps({"problems": []}), "m"
    if p.startswith(("Review this", "Check this")):
        calls.append("review"); seen["review"] = p; return DRAFT, "m"
    if "does not fit its series well enough" in p:
        calls.append("rewrite"); seen["rewrite"] = p
        return (NEW if MODE.get("rewrite") != "short" else lines("छोटा", 5)), "m"
    calls.append("write"); seen["write"] = p
    return DRAFT, "m"

S.ask = fake_ask
S.local_model = lambda cfg: "m"
S.wpm = lambda cfg, lang="hi", series="": 100
S.ledger = lambda cfg: []
S._story_avoid = lambda cfg, g, n=12: ""
S.facts_check = lambda cfg, script, *a, **k: script
S.genre_rules = lambda cfg, gid: {**S.GENRE_TEXT[gid], "tellings": S.GENRE_TELLINGS.get(gid, []),
                                  "angles": S.GENRE_ANGLES.get(gid, []), "mature": ""}
EXP = {"experimental_mode": True, "media_dir": TMP, "hub_output_root": TMP}
WORK = {"media_dir": TMP, "hub_output_root": TMP}
say = lambda *a: None

for g in ("bhay", "rahasya", "kisse", "romanch", "kareeb", "gyaan"):
    B = S.genre_brief(g)
    check(f"{g}: brief has must/never/now/examples", all(B.get(k) for k in ("must", "never", "now", "examples")))
check("brief text names MUST and NEVER", "MUST HAVE" in S.brief_text("rahasya") and "NEVER" in S.brief_text("rahasya"))
check("rahasya brief asks for a real crime", "crime" in S.brief_text("rahasya") and "victim" in S.brief_text("rahasya"))
check("rahasya angles: no 'nobody was killed'", not any("nobody was killed" in a for a in S.GENRE_ANGLES["rahasya"]))
check("bhay tellings: investigator dropped", not any("investigator" in t for t in S._brief_tellings("bhay", S.HORROR_BANK["tellings"])))
kt = S._brief_tellings("kisse", S.GENRE_TELLINGS["kisse"])
check("kisse tellings: 'years later', 'many years', 'letter' dropped, 'this year' added",
      not any(x in t for t in kt for x in ("years later", "many years", "letter read")) and any("this year" in t for t in kt))

# Working mode: nothing new
calls.clear(); MODE.clear()
sc, info = S.write_genre(dict(WORK), "rahasya", say, topic="a test", minutes=3)
check("working: no brief in the prompt", "GENRE BRIEF" not in seen["write"])
check("working: no genre calls", "fit" not in calls and "genre_plan" not in calls and info.get("genre_fit") is None)

# Experimental: plan redone, script fits
calls.clear(); MODE.clear(); seen.clear(); MODE["scores"] = [8]
sc, info = S.write_genre(dict(EXP), "rahasya", say, topic="a test", minutes=3)
check("exp: brief in the writing prompt", "GENRE BRIEF" in seen["write"] and "MUST HAVE" in seen["write"])
check("exp: brief in the plan brief", "GENRE BRIEF" in seen["outline"])
check("exp: low plan redone with the brief's plan", "poisoned by his partner" in info["plan"])
check("exp: review keeps the genre", "Keep it a true" in seen["review"])
check("exp: fitting script not rewritten", "rewrite" not in calls and info["genre_fit"]["kept"] == "first")
check("exp: order plan check -> write -> review -> logic -> fit",
      calls.index("genre_plan") < calls.index("write") < calls.index("review") < calls.index("logic") < calls.index("fit"))

# low score, better rewrite -> kept, logic check again
calls.clear(); MODE.clear(); seen.clear(); MODE.update(scores=[4, 8], plan_fits=True)
sc, info = S.write_genre(dict(EXP), "kisse", say, topic="a test", minutes=3)
check("rewrite asked once with the misses", calls.count("rewrite") == 1 and "no victim" in seen["rewrite"]
      and "Neha finds a body" in seen["rewrite"])
check("better rewrite kept", sc.startswith("नया") and info["genre_fit"]["kept"] == "rewrite")
check("kept rewrite logic-checked", calls.count("logic") == 2)
check("rewrite saved in versions, not in the report", info["versions"].get("genre_rewrite", "").startswith("नया")
      and "rewrite" not in info["genre_fit"])
check("only two fit calls (no loop)", calls.count("fit") == 2)

# low score, worse rewrite -> first stays
calls.clear(); MODE.clear(); MODE.update(scores=[4, 4], plan_fits=True)
sc, info = S.write_genre(dict(EXP), "gyaan", say, topic="a test", minutes=3)
check("tie: first version stays", sc.startswith("पहला") and info["genre_fit"]["kept"] == "first"
      and calls.count("logic") == 1)

# short rewrite -> first stays, not scored again
calls.clear(); MODE.clear(); MODE.update(scores=[3], plan_fits=True, rewrite="short")
sc, info = S.write_genre(dict(EXP), "romanch", say, topic="a test", minutes=3)
check("short rewrite: first stays, one fit call", sc.startswith("पहला") and calls.count("fit") == 1)

# Bhay
calls.clear(); MODE.clear(); seen.clear(); MODE.update(scores=[5, 9], plan_fits=True)
sc, info = S.write_genre(dict(EXP), "bhay", say, topic="a test", minutes=3)
check("bhay: brief in prompt, review keeps the genre, rewrite kept",
      "GENRE BRIEF" in seen["write"] and "Keep it a true भय" in seen["review"] and info["genre_fit"]["kept"] == "rewrite")

# fresh seed gets the brief rule
L = [{"genre": "rahasya", "topic": t} for t in S._brief_seeds("rahasya", S.GENRE_TEXT["rahasya"]["topics"])]
S.ledger = lambda cfg: L
calls.clear(); seen.clear()
r = S.genre_recipe(dict(EXP), "rahasya", seed=1)
check("fresh seed told the brief", "seed" in calls and "real crime" in seen["seed"] and r["topic"] == "a death in a locked office")
check("rahasya telling list has the inspector telling", any("inspector" in t for t in S._brief_tellings("rahasya", S.GENRE_TELLINGS["rahasya"])))
# switch off
S.GENRE_FIT["on"] = False
calls.clear(); seen.clear(); S.ledger = lambda cfg: []
sc, info = S.write_genre(dict(EXP), "rahasya", say, topic="a test", minutes=3)
check("GENRE_FIT on=false: old behaviour", "GENRE BRIEF" not in seen["write"] and "fit" not in calls)
S.GENRE_FIT["on"] = True
print(f"\n{ok}/{ok + fail} passed")
sys.exit(1 if fail else 0)
