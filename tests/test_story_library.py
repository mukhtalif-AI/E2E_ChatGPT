"""test_story_library.py - story library from free books (19 Oct 2026) with a fake AI and fake web answers.
    python tests/test_story_library.py app
Checks: an .epub (chapters in spine order), a Gutenberg .txt (header/footer cut) and an .html in the inbox are read;
parts spread to the end of the book; one note per book with fit and ideas; a re-run reads nothing again; a stopped
job leaves the rest for later; Gutendex and Wikisource answers are parsed (public domain only, contents page ->
subpages); the size cap deletes texts but keeps notes; the least used note is picked; Working mode gets no book;
Experimental genre stories carry the note in the writing prompt (so the plan too) with the 'do not retell' rule,
the recipe names the book, a share of seeds come from its ideas, a low-fit note is never used."""
import sys, os, json, random, tempfile, zipfile, time
sys.path.insert(0, sys.argv[1])
import story as S
import story_library as B

TMP = tempfile.mkdtemp()
ok = fail = 0
def check(name, cond):
    global ok, fail
    ok += bool(cond); fail += not cond
    print(("PASS " if cond else "FAIL ") + name)

EXP = {"experimental_mode": True, "media_dir": TMP, "hub_output_root": TMP}
WORK = {"media_dir": TMP, "hub_output_root": TMP}
logs = []
say = lambda *a: logs.append(" ".join(map(str, a)))
B.BOOKS["pause_s"] = 0
B.init(EXP)
para = lambda tag, n: "\n\n".join(f"{tag} paragraph {i}. The lamp in the old house flickered while Meera waited. "
                                  "Nobody answered the knock, and the rain kept falling on the tin roof." for i in range(n))

# --- sample books in the inbox
ep = os.path.join(TMP, "StoryBooks", "inbox", "bhay", "Night Visitor.epub")
with zipfile.ZipFile(ep, "w") as z:
    z.writestr("mimetype", "application/epub+zip")
    z.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
    z.writestr("OEBPS/content.opf", '<package><manifest><item id="c2" href="ch2.xhtml" media-type="x"/>'
               '<item id="c1" href="ch1.xhtml" media-type="x"/></manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
    z.writestr("OEBPS/ch1.xhtml", "<html><head><style>p{}</style></head><body><h1>One</h1><p>" + para("FIRST", 20).replace("\n\n", "</p><p>") + "</p></body></html>")
    z.writestr("OEBPS/ch2.xhtml", "<html><body><p>" + para("SECOND", 20).replace("\n\n", "</p><p>") + "</p><p>THE END TWIST.</p></body></html>")
open(os.path.join(TMP, "StoryBooks", "inbox", "rahasya", "case.txt"), "w", encoding="utf-8").write(
    "The Project Gutenberg eBook of Case\n*** START OF THE PROJECT GUTENBERG EBOOK CASE ***\n" + para("CASE", 40)
    + "\n*** END OF THE PROJECT GUTENBERG EBOOK CASE ***\nLicence text here")
open(os.path.join(TMP, "StoryBooks", "inbox", "kisse", "tiny.html"), "w", encoding="utf-8").write("<p>too short</p>")

t = B.epub_text(ep)
check("epub: chapters in spine order, no style text", t.find("FIRST") < t.find("SECOND") and "p{}" not in t and "THE END TWIST" in t)
g = B.file_text(os.path.join(TMP, "StoryBooks", "inbox", "rahasya", "case.txt"))
check("gutenberg txt: header and licence cut", "START OF" not in g and "Licence text" not in g and g.startswith("CASE"))
parts = B.parts_of(t, 5, 800)
check("parts: 5, first from the start, last near the end", len(parts) == 5 and parts[0].startswith("One")
      and "THE END TWIST" in parts[-1])

# --- fake AI
asks = []
def fake_chat(cfg, prompt, temp=0.85, as_json=False, ctx_len=8192):
    asks.append(prompt)
    cfg["_ollama_used"] = True
    if prompt.startswith("You are reading part"):
        return json.dumps({"events": "someone waits", "devices": ["slow reveal"], "characters": ["widow: watcher"], "mood": "dread"}), "fake"
    fit = 3 if "'case'" in prompt else 8
    return json.dumps({"premise": "a visitor who should not exist", "shape": ["calm", "knock", "rule", "break", "end"],
                       "twist": "the watcher was the visitor", "characters": ["widow"], "setting": "old house",
                       "opening": "starts mid-sound", "why_it_works": "small rules broken", "fit": fit,
                       "ideas": ["a night-shift nurse hears her own name paged in an empty ward",
                                 "a food delivery app keeps sending orders to a demolished flat"]}), "fake"
S.local_chat = fake_chat
stop = {"v": False}
ctx = {"cfg": dict(EXP), "say": say, "stopped": lambda: stop["v"]}
B.build(ctx, fetch=False)
cat = B.catalog(EXP)
check("inbox: 3 books listed", len(cat) == 3)
st = {b["title"]: b["status"] for b in cat.values()}
check("epub and txt noted, short html marked", st.get("Night Visitor") == "noted" and st.get("case") == "noted"
      and st.get("tiny") == "too short")
n = B.note(EXP, [b["id"] for b in cat.values() if b["title"] == "Night Visitor"][0])
check("note has shape, fit, ideas, parts, no book text", n and n["fit"] == 8 and len(n["ideas"]) == 2
      and n["parts_read"] >= 1 and "FIRST paragraph" not in json.dumps(n))
check("note ask carries the series brief rule", any("Series rule: It must have" in a for a in asks))
check("think off by default", ctx["cfg"].get("_ollama_used"))
k = len(asks)
B.build(ctx, fetch=False)
check("re-run reads nothing again", len(asks) == k)

# --- stopped halfway
open(os.path.join(TMP, "StoryBooks", "inbox", "gyaan", "fable.txt"), "w", encoding="utf-8").write(para("FABLE", 40))
stop["v"] = True
B.build(ctx, fetch=False)
check("stopped: book left for later", [b for b in B.catalog(EXP).values() if b["title"] == "fable"][0]["status"] == "found")
stop["v"] = False
B.build(ctx, fetch=False)
check("next run reads it", [b for b in B.catalog(EXP).values() if b["title"] == "fable"][0]["status"] == "noted")

# --- web answers (fake)
GUT = {"next": None, "results": [
    {"id": 1, "title": "Ghosts A", "authors": [{"name": "Old Writer"}], "copyright": False, "media_type": "Text",
     "formats": {"text/plain; charset=utf-8": "https://www.gutenberg.org/ebooks/1.txt.utf-8"}},
    {"id": 2, "title": "Still in copyright", "authors": [], "copyright": True, "media_type": "Text",
     "formats": {"text/plain; charset=utf-8": "https://x/2.txt"}},
    {"id": 3, "title": "Audio", "authors": [], "copyright": False, "media_type": "Sound", "formats": {"audio/mpeg": "x"}},
    {"id": 4, "title": "Epub only", "authors": [], "copyright": False, "media_type": "Text",
     "formats": {"application/epub+zip": "https://www.gutenberg.org/ebooks/4.epub3.images"}}]}
urls = []
def fake_json(url, timeout=60):
    urls.append(url)
    if "gutendex" in url:
        return GUT
    if "list=search" in url:
        return {"query": {"search": [{"title": "भूतों की कहानी"}, {"title": "भूतों की कहानी/भाग 1"}]}}
    if "action=parse" in url and "%2F" in url:
        return {"parse": {"text": "<div><p>" + para("भाग", 20) + "</p></div>"}}
    if "action=parse" in url:
        return {"parse": {"text": "<div class='mw-editsection'>edit</div><ul><li>contents</li></ul>",
                          "links": [{"ns": 0, "title": "भूतों की कहानी/भाग 1", "exists": True},
                                    {"ns": 0, "title": "Other page", "exists": True}]}}
    return {}
def fake_get(url, timeout=60, limit=0):
    urls.append(url)
    return ("*** START OF THE PROJECT GUTENBERG EBOOK X ***\n" + para("GHOST", 40) + "\n*** END OF THE PROJECT GUTENBERG EBOOK X ***").encode()
B._get_json, B._get = fake_json, fake_get
got = B._gutenberg("ghost stories", 5, set(), say)
check("gutendex: public-domain text books only", [x["id"] for x in got] == ["pg1", "pg4"] and got[0]["author"] == "Old Writer")
check("gutendex asks English, no copyright", "copyright=false" in urls[0] and "languages=en" in urls[0])
ws = B._wikisource("hi", "भूत", 5, set(), say)
check("wikisource: subpages skipped in search", [x["title"] for x in ws] == ["भूतों की कहानी"])
wt = B._wiki_text("hi", "भूतों की कहानी")
check("wikisource: contents page -> its subpage read, edit links dropped", "भाग paragraph" in wt and "edit" not in wt)
B.SOURCES["bhay"] = {"gutenberg": ["ghost stories"], "wikisource_hi": ["भूत"]}
B.BOOKS["new_per_series"] = 2
B.build(ctx, series="bhay")
cat = B.catalog(EXP)
fetched = [b for b in cat.values() if b.get("source") in ("Project Gutenberg", "Wikisource (hi)")]
check("build: 2 new bhay books downloaded and noted, Hindi first", len(fetched) == 2
      and all(b["status"] == "noted" for b in fetched) and any(b["source"] == "Wikisource (hi)" for b in fetched))
check("build: licence and page kept", all(b.get("licence") and b.get("page") for b in fetched))

# --- size cap
B.BOOKS["max_mb"] = 0
B.build(ctx, fetch=False)
cat = B.catalog(EXP)
check("size cap: texts deleted, notes kept", not [f for f in os.listdir(os.path.join(TMP, "StoryBooks", "books", "bhay")) if f.endswith(".txt")]
      and all(B.note(EXP, b["id"]) for b in fetched))
B.BOOKS["max_mb"] = 300

# --- using notes
rng = random.Random(1)
L = [{"genre": "horror", "recipe": {"book": {"id": fetched[0]["id"]}}}]
picks = {B.pick(EXP, "bhay", L, random.Random(i))["id"] for i in range(10)}
check("pick: least used notes first", fetched[0]["id"] not in picks)
check("low-fit note never used", B.usable(EXP, "rahasya") == [])
S.ledger = lambda cfg: []
S._fresh_topic = lambda *a, **k: "an AI seed"
S.wpm = lambda cfg, lang="hi", series="": 100
S._story_avoid = lambda cfg, g, n=12: ""
r = S.genre_recipe(dict(WORK), "bhay", seed=1)
check("working mode: no book", "book" not in r)
rs = [S.genre_recipe(dict(EXP), "bhay", seed=i) for i in range(30)]
check("exp bhay: every recipe names a book", all(x.get("book", {}).get("id") for x in rs))
ideas = {i for nn in B.usable(EXP, "bhay") for i in nn["ideas"]}
from_book = [x for x in rs if x["topic"] in ideas]
check("exp: about half the seeds come from book ideas", 5 <= len(from_book) <= 25 and all(x["book"]["seed"] for x in from_book))
r2 = S.genre_recipe(dict(EXP), "gyaan", topic="my own topic", seed=2)
check("typed topic is kept, book craft only", r2["topic"] == "my own topic" and r2["book"]["seed"] is False)
check("rahasya (only low fit): no book", "book" not in S.genre_recipe(dict(EXP), "rahasya", seed=3))

# the note reaches the writing prompt (the plan is made from the same prompt)
seen = {}
def fake_ask(cfg, prompt, engine="auto", temp=0.85, as_json=False, say=print, ctx_len=8192):
    if prompt.startswith("Before tonight"):
        seen["outline"] = prompt
        return json.dumps({"facts": ["x"], "people": [], "clues": [], "twist": "t", "reveal": "r",
                           "beats": ["a", "b", "c", "d", "e"]}), "m"
    if prompt.startswith(("You check the PLAN", "You judge whether")):
        return json.dumps({"score": 9, "missing": []}), "m"
    if "for LOGIC only" in prompt or prompt.startswith("FACTS CHECK"):
        return json.dumps({"problems": []}), "m"
    if prompt.startswith(("Review this", "Check this")):
        return seen["draft"], "m"
    seen.setdefault("write", prompt)
    seen["draft"] = "\n".join("कहानी की एक पंक्ति यहाँ है और वह चलती रहती है।" for _ in range(60))
    return seen["draft"], "m"
S.ask = fake_ask
S.local_model = lambda cfg: "m"
S.facts_check = lambda cfg, script, *a, **k: script
logs.clear()
sc, info = S.write_genre(dict(EXP), "bhay", say, minutes=3, rec=rs[0])
check("bhay prompt carries the note and the 'do not retell' rule", "INSPIRATION FROM A BOOK" in seen["write"]
      and "do NOT retell" in seen["write"] and "How it is built" in seen["write"])
check("plan made from the prompt with the note", "INSPIRATION FROM A BOOK" in seen.get("outline", ""))
check("job log names the book", any("book idea:" in x for x in logs))
check("recipe in info keeps the book (ledger rotation)", info["recipe"]["book"]["id"] == rs[0]["book"]["id"])
B.BOOKS["on"] = False
check("BOOKS on=false: no book", "book" not in S.genre_recipe(dict(EXP), "bhay", seed=4))
B.BOOKS["on"] = True
st = B.state(EXP)
check("state: per-series counts and the book list", st["series"]["bhay"]["usable"] >= 2 and len(st["books"]) == len(B.catalog(EXP)))
print(f"\n{ok}/{ok + fail} passed")
sys.exit(1 if fail else 0)
