"""test_story_by_scene.py - the scene-by-scene writer (19 Oct 2026) with a fake AI (no Ollama, no GPU).
    python tests/test_story_by_scene.py app
Checks: off by default (old writer + review untouched), on per job or by the Tinker key, Experimental only; plan check
replaces a broken plan; scene list -> one scene at a time with the state; per-scene audit fixes lines; a scene with many
problems is written again once and kept only when better; the rewrite cap; a retelling scene or a bad scene list falls
back to the old writer; Bhay too; the picture step's young/old look, named person and who-line helpers."""
import sys, json, re, tempfile
sys.path.insert(0, sys.argv[1]); import story as S
TMP = tempfile.mkdtemp()
calls, MODE = [], {}
PLAN = {"facts": ["1982 and 2022", "station near Bhopal"], "people": ["Avinash - narrator", "Vaidehi - tea girl"],
        "clues": [{"clue": "blue cup", "answer": "she gave it to him"}], "twist": "she waited to return papers",
        "reveal": "the papers", "beats": ["hook: train whistle", "subscribe", "meet", "cup", "leave", "years",
                                          "old woman", "reveal"]}
FIXED = dict(PLAN, beats=PLAN["beats"][:6] + ["old woman: it is Vaidehi", "reveal"], facts=PLAN["facts"] + ["trains pass but no longer stop"])
SCENES = [{"when": "2022 (now)", "place": "station", "present": [{"name": "Avinash", "sex": "man", "age": 58}], "beats": [1, 2], "happens": "whistle", "words": 100},
          {"when": "1982 (then)", "place": "station", "present": [{"name": "Avinash", "sex": "man", "age": 18}, {"name": "Vaidehi", "sex": "woman", "age": 18}], "beats": [3], "happens": "they meet", "words": 200},
          {"when": "1982 (then)", "place": "tea stall", "present": [{"name": "Avinash", "sex": "man", "age": 18}, {"name": "Vaidehi", "sex": "woman", "age": 18}], "beats": [4], "happens": "cup", "words": 200},
          {"when": "1983", "place": "home", "present": [{"name": "Avinash", "sex": "man", "age": 19}], "beats": [5], "happens": "leave", "words": 150},
          {"when": "2022 (now)", "place": "station", "present": [{"name": "Avinash", "sex": "man", "age": 58}, {"name": "Vaidehi", "sex": "woman", "age": 58}], "beats": [6, 7], "happens": "old woman", "words": 200},
          {"when": "2022 (now)", "place": "station", "present": [{"name": "Avinash", "sex": "man", "age": 58}, {"name": "Vaidehi", "sex": "woman", "age": 58}], "beats": [8], "happens": "reveal", "words": 150}]
import random as _r
SYL = "क ख ग घ च छ ज झ ट ठ ड ढ त थ द ध न प फ ब भ म य र ल व श स ह".split()
VOW = ["", "ा", "ि", "ी", "ु", "ू", "े", "ै", "ो", "ौ"]
def _word(rng):
    return "".join(rng.choice(SYL) + rng.choice(VOW) for _ in range(rng.randint(2, 4)))
def _lines(seed, n, tag=""):
    # made-up words, different in every line, so the retell guard never fires by accident
    rng = _r.Random(seed)
    return [f"{tag}{' ' if tag else ''}" + " ".join(_word(rng) for _ in range(9)) + "।" for _ in range(n)]
def scene_text(n, v=0):
    return "\n".join(_lines(n * 10 + v, 8, f"दृश्य{n} संस्करण{v}"))
def fake_ask(cfg, prompt, engine="auto", temp=0.85, as_json=False, say=print, ctx_len=8192):
    p = prompt
    if p.startswith("Before tonight"):
        calls.append("outline"); return json.dumps(PLAN), "m"
    if p.startswith("You check a story PLAN"):
        calls.append("plan_check")
        if MODE.get("plan_check") == "bad": return "nope", "m"
        return json.dumps({"problems": ["the train no longer comes but its whistle is heard - trains pass, they no longer stop"], "plan": FIXED}), "m"
    if p.startswith("Cut this story plan"):
        calls.append("scene_list")
        if MODE.get("scene_list") == "bad": return json.dumps({"scenes": [{"happens": "x"}]}), "m"
        assert "trains pass but no longer stop" in p, "the scene list gets the checked plan"
        return json.dumps({"people": [{"name": "Avinash", "sex": "man", "ages": {"then": 18, "now": 58}}, {"name": "Vaidehi", "sex": "woman", "ages": {"then": 18, "now": 58}}], "scenes": SCENES}), "m"
    if p.startswith("Update the STATE"):
        calls.append("state")
        st = json.loads(p.split("STATE:\n", 1)[1].split("\n\nSCENE", 1)[0])
        n = re.search(r"SCENE (\d+)", p).group(1)
        st["told"] = (st.get("told") or []) + [f"scene {n} told"]
        if n == "3": st["objects"] = {"blue cup": "Vaidehi gave it to Avinash"}
        return json.dumps(st), "m"
    if p.startswith("You check ONE new scene"):
        lines = [l.split(": ", 1)[1] for l in p.split("SCENE LINES:\n", 1)[1].splitlines() if ": " in l]
        v = re.search(r"संस्करण(\d)", lines[0]).group(1)
        n = int(re.search(r"दृश्य(\d+)", lines[0]).group(1))
        calls.append(f"audit{n}v{v}")
        if n == 5 and "Vaidehi gave it to Avinash" not in p:
            raise AssertionError("the audit of scene 5 must see the state from scene 3")
        many = MODE.get("many") == "all" or n == 3
        if many and v == "0":
            return json.dumps({"problems": [{"n": k, "line": lines[k - 1], "why": f"fault {k}", "fix": lines[k - 1] + " सही।"} for k in (1, 2, 3)]}), "m"
        if n == 3 and v == "1" and MODE.get("rewrite_worse"):
            return json.dumps({"problems": [{"n": k, "line": lines[k - 1], "why": "worse", "fix": lines[k - 1] + " फिर।"} for k in (1, 2, 3, 4)]}), "m"
        if n == 5:   # 'बेटा' from a woman of the same age: one line fixed
            return json.dumps({"problems": [{"n": 2, "line": lines[1], "why": "बेटा from the same age", "fix": lines[1].rstrip("।") + ", अविनाश।"}]}), "m"
        return json.dumps({"problems": []}), "m"
    if "for LOGIC only" in p:
        calls.append("logic"); return json.dumps({"problems": []}), "m"
    if p.startswith("FACTS CHECK"):
        calls.append("facts"); return json.dumps({"problems": []}), "m"
    if p.startswith(("Review this", "Check this")):
        calls.append("review"); return OLD, "m"
    m = re.search(r"Now write ONLY scene (\d+) of (\d+)", p)
    if m:
        n = int(m.group(1))
        assert "STORY STATE SO FAR" in p and "PLAN OF THE WHOLE STORY" in p and "Quotes: say who" in p
        if n > 1: assert f"दृश्य{n - 1} संस्करण" in p, "the scene gets the end of the story so far"
        if n == len(SCENES): assert "last scene" in p
        if MODE.get("retell") == n:
            calls.append(f"write{n}retold"); return scene_text(1), "m"
        v = 1 if "An earlier version" in p else 0
        calls.append(f"write{n}v{v}"); return scene_text(n, v), "m"
    calls.append("write"); return OLD, "m"
OLD = "\n".join(_lines(999, 70))
S.ask = fake_ask
S.local_model = lambda cfg: "m"
S.wpm = lambda *a, **k: 120
S._story_avoid = lambda *a, **k: ""
log = []
say = log.append
rec = {"genre": "kisse", "topic": "a love that waited at a station", "telling": "small moments"}
def run(cfg, by=None, gid="kisse"):
    cfg["media_dir"] = TMP
    calls.clear(); log.clear()
    if gid == "bhay":
        return S.write_horror(cfg, say, "", 8, "", "auto", {"topic": "x", "telling": "t", "fear": "f", "ending": "e"}, by_scene=by)
    return S.write_genre(cfg, gid, say, "", 8, "", "auto", rec, by_scene=by)
ok = n_ = 0
def check(c, msg):
    global ok, n_; n_ += 1; print(("PASS " if c else "FAIL ") + msg); ok += bool(c)
EXP = {"experimental_mode": True}
# 1 default: old writer, review, no scene steps
out, info = run(dict(EXP))
check("write" in calls and "review" in calls and not any(c.startswith(("plan_check", "scene_list")) for c in calls) and info["by_scene"] is None,
      "off by default: the usual writer + review, no scene steps")
check(set(info["versions"]) >= {"draft", "review", "final"}, "old flow keeps draft / review / final versions")
# 2 per job on
out, info = run(dict(EXP), True)
doc = info["by_scene"]
check(calls[:3] == ["outline", "plan_check", "scene_list"], "plan -> plan check -> scene list")
check("review" not in calls and "write" not in calls, "no whole-story writer or review in scene mode")
check([c for c in calls if c.startswith("write")] == ["write1v0", "write2v0", "write3v0", "write3v1", "write4v0", "write5v0", "write6v0"],
      "one scene at a time; scene 3 (3 problems) written again once")
check(calls.count("state") == 5, "state updated after every scene but the last")
check(all(f"दृश्य{k} " in out for k in range(1, 7)) and out.index("दृश्य1 ") < out.index("दृश्य6 "), "every scene in order in the script")
check("संस्करण1" in doc["scenes"][2]["text"] and doc["scenes"][2]["rewritten"], "the better rewrite of scene 3 is kept")
check("अविनाश" in out, "an audit line fix is applied (scene 5)")
check(calls[-2:] == ["logic", "facts"], "the whole-story logic + facts checks still run at the end")
check(doc["plan_fixes"] and "trains pass" in info["plan"], "the checked plan is the plan used afterwards")
check(doc["scenes"][1]["present"][1]["age"] == 18 and doc["scenes"][4]["when"].startswith("2022"), "scene list keeps who / age / when for the pictures")
check(set(doc["timings"]) >= {"plan_check", "scene_list", "write", "audit", "state", "all"}, "timings for every part")
check(any("story flow: scene by scene" in l for l in log) and any("written scene by scene" in l for l in log), "the log says which flow ran")
# 3 rewrite worse -> first version stays (but its fixes applied)
MODE["rewrite_worse"] = True
out, info = run(dict(EXP), True)
s3 = info["by_scene"]["scenes"][2]
check("संस्करण0" in s3["text"] and not s3["rewritten"] and s3["fixed"] == 3, "a worse rewrite is dropped; the first version's lines are fixed")
MODE.pop("rewrite_worse")
# 4 rewrite cap
MODE["many"] = "all"
out, info = run(dict(EXP), True)
check(sum(1 for c in calls if c.endswith("v1") and c.startswith("write")) == 2, "at most 2 scenes written again per story")
MODE.pop("many")
# 5 a retelling scene is asked again, then the old writer takes over
MODE["retell"] = 3
out, info = run(dict(EXP), True)
check(calls.count("write3retold") == 2 and "write" in calls and "review" in calls and info["by_scene"] is None,
      "a scene that retells the story twice -> the usual writer")
MODE.pop("retell")
# 6 bad scene list -> old writer
MODE["scene_list"] = "bad"
out, info = run(dict(EXP), True)
check("write" in calls and info["by_scene"] is None, "a bad scene list -> the usual writer")
MODE.pop("scene_list")
# 7 bad plan check -> the first plan is kept, scenes still written
MODE["plan_check"] = "bad"
FIXED_SAVE = FIXED["facts"]
PLAN["facts"] = PLAN["facts"] + ["trains pass but no longer stop"]   # so the scene-list assert holds
out, info = run(dict(EXP), True)
check(info["by_scene"] is not None and not info["by_scene"]["plan_fixes"], "plan check unusable -> the plan as made, scene mode goes on")
PLAN["facts"] = PLAN["facts"][:-1]; MODE.pop("plan_check")
# 8 Working never, Tinker key on
out, info = run({}, True)
check(not any(c.startswith("plan_check") for c in calls), "Working: never scene by scene")
S.STORY_PLAN["by_scene"] = True
out, info = run(dict(EXP))
check(info["by_scene"] is not None, "Tinker key by_scene=True turns it on for every Experimental run")
out, info = run(dict(EXP), False)
check(info["by_scene"] is None, "the job's own 'off' wins over the Tinker key")
S.STORY_PLAN["by_scene"] = False
# 9 Bhay
out, info = run(dict(EXP), True, "bhay")
check(info["by_scene"] is not None and "review" not in calls and "दृश्य6 " in out, "Bhay (write_horror) scene by scene too")
# 10 stop mid-way: what is written is kept
stops = {"n": 0}
def stopped():
    return len([c for c in calls if c.startswith("write")]) >= 2
calls.clear()
res = S.write_by_scene(dict(EXP, media_dir=TMP), "brief\nNow write it.", PLAN, "किस्से", 960, "auto", say, S.clean_horror, stopped)
check(res and len(res[2]["scenes"]) == 2, "Stop during the scenes keeps the scenes written so far")

# ---------------- picture step helpers
import tools  # noqa: F401  (tools loads the steps modules in its own order)
SC = sys.modules["steps.scenes"]
chars = {"protagonist_young": {"look": "An 18-year-old man, lanky", "outfit": "teal kurta"},
         "protagonist_old": {"look": "A 58-year-old man, grey hair", "outfit": "white kurta"},
         "vaidehi_young": {"look": "An 18-year-old woman", "outfit": "yellow salwar"},
         "vaidehi_old": {"look": "A 58-year-old woman, white hair in a bun", "outfit": "cotton saree"},
         "Sameer": {"look": "A 25-year-old man", "outfit": "jacket"}, "Abhimanyu": {"look": "A 23-year-old man", "outfit": "hoodie"}}
sc = SCENES
check(SC._age_variant("protagonist", chars, "A man on a bench", period=SC._period(sc[1], sc)) == "protagonist_young",
      "a 1982 scene -> the young look")
check(SC._age_variant("protagonist", chars, "A man on a bench", period=SC._period(sc[4], sc)) == "protagonist_old",
      "a 2022 scene -> the old look")
check(SC._age_variant("vaidehi_old", chars, "x", age=SC._age_of(sc[2], "vaidehi_old")) == "vaidehi_young",
      "the scene list's age picks the look (Vaidehi at 18)")
check(SC._age_variant("protagonist", chars, "an old wooden bench on an old platform") in ("protagonist_young", "protagonist_old")
      and SC._age_variant("protagonist_young", chars, "an old wooden bench") == "protagonist_young",
      "'old bench' does not make the man old")
check(SC._age_variant("protagonist_young", chars, "an elderly man with grey hair") == "protagonist_old", "words: an elderly man -> old look")
check(SC._named_character("A close-up shot of Sameer, sweating", list(chars)) == "Sameer", "the picture names Sameer")
check(SC._named_character("Vaidehi smiles at him", list(chars)) == "vaidehi_young", "one person at two ages counts once")
w = SC._who_line(sc[1])
check(w == "In this picture: an 18-year-old Indian man and an 18-year-old Indian woman, nobody else", "who-line: " + w)
check(SC._who_line(sc[0]) == "", "no who-line for one person")
check(SC._scene_of(scene_text(3).splitlines()[1], [dict(s, text=scene_text(k + 1)) for k, s in enumerate(sc)])["happens"] == "cup",
      "a shot's narration finds its story scene")
print(f"\n{ok}/{n_} passed")
sys.exit(0 if ok == n_ else 1)
