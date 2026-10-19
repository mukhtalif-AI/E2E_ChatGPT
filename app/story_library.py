"""story_library.py - free books as inspiration for the story writer (Experimental, every story series). 19 Oct 2026.

Settings > Story library > Build / update is a hub job on your PC. For each series it finds free books (public domain on
Project Gutenberg through the Gutendex catalogue, Hindi and English Wikisource) and also reads every .epub / .txt /
.html / .md you drop into  <Library>\\StoryBooks\\inbox\\<series>\\ . The local model reads a few parts of each book and
keeps ONE short note per book: its shape, the kind of twist, character types, setting, how it opens, why it works, a
fit score for the series and 3 fresh story seeds set in today's India.

The book text never goes into a story prompt. A genre story gets one note (least used first) as craft inspiration with
the rule to write an ORIGINAL story; for a share of stories (BOOKS["seed_share"]) the seed comes from the note's ideas.
Which note a story used is in its recipe ("book"), so the story ledger rotates them.

Files: <Library>\\StoryBooks\\catalog.json (every book: series, title, author, source, licence, status, fit),
notes\\<id>.json (the notes), books\\<series>\\<id>.txt (plain text, kept until the folder passes max_mb), inbox\\<series>\\.
The job is safe to re-run: books already noted are skipped, a stopped job carries on where it was.
Tinker: story_library.BOOKS, story_library.SOURCES, prompts story_library.PART_ASK / NOTE_ASK / USE_RULE.
"""
import glob, html, html.parser, ipaddress, json, os, random, re, socket, time, urllib.parse, urllib.request, zipfile

UA = "HubGPT/1.0 (personal story tool; reads free public-domain books; local)"
SERIES = ("bhay", "rahasya", "kisse", "romanch", "kareeb", "gyaan")
LABEL = {"bhay": "भय (horror)", "rahasya": "रहस्य (mystery)", "kisse": "किस्से (emotional stories)",
         "romanch": "रोमांच (romance)", "kareeb": "करीब (ASMR)", "gyaan": "ज्ञान (knowledge)"}

BOOKS = {"on": True,             # notes go into Experimental genre stories
         "new_per_series": 3,    # books fetched per series on each Build / update (inbox books are always read)
         "parts": 5,             # parts of a book the model reads (spread from start to end, so the ending is read too)
         "part_chars": 6000,     # characters per part
         "min_chars": 2500,      # shorter texts are skipped (index pages, poems)
         "max_mb": 300,          # books\\ folder cap: the oldest texts already noted are deleted (their notes stay)
         "min_fit": 5,           # notes scored lower for their series are kept but never used
         "seed_share": 0.5,      # share of stories (without a typed topic) whose seed is one of a note's ideas
         "pause_s": 2,           # pause between downloads (be polite to the free sites)
         "model": "",            # "" = the script writer model
         "think": False}         # Gemma 4 / Qwen3 hidden thinking while reading (slow; notes are fine without)

# Where each series looks. gutenberg = Gutendex 'topic' words (subjects and bookshelves, English books, public domain
# only); wikisource_hi / wikisource_en = search words on Hindi / English Wikisource; pages_hi / pages_en = exact page
# titles you want read. Edit freely in Tinker.
SOURCES = {
    "bhay": {"gutenberg": ["ghost stories", "horror tales", "supernatural"],
             "wikisource_hi": ["भूत", "प्रेत"], "wikisource_en": ["ghost story"], "pages_hi": [], "pages_en": []},
    "rahasya": {"gutenberg": ["detective and mystery stories", "mystery fiction"],
                "wikisource_hi": ["जासूस", "रहस्य"], "wikisource_en": ["detective story"], "pages_hi": [], "pages_en": []},
    "kisse": {"gutenberg": ["short stories", "india -- fiction"],
              "wikisource_hi": ["प्रेमचंद कहानी", "कहानी"], "wikisource_en": [], "pages_hi": [], "pages_en": []},
    "romanch": {"gutenberg": ["love stories", "romance fiction"],
                "wikisource_hi": ["प्रेम कहानी"], "wikisource_en": [], "pages_hi": [], "pages_en": []},
    "kareeb": {"gutenberg": ["nature", "fairy tales"],
               "wikisource_hi": ["लोककथा"], "wikisource_en": [], "pages_hi": [], "pages_en": []},
    "gyaan": {"gutenberg": ["mythology, hindu", "india -- history", "fables"],
              "wikisource_hi": ["पंचतंत्र", "जातक", "हितोपदेश"], "wikisource_en": ["jataka"], "pages_hi": [], "pages_en": []},
}

PART_ASK = (
    "You are reading part {k} of {n} of the book '{title}' by {author}. Writers of {series}, a Hindi audio-story "
    "series, want to learn its CRAFT, not copy it. In English, write short notes on this part. Never copy sentences "
    "from the text; describe in your own words.\nAnswer ONLY JSON: {\"events\": \"2-3 sentences: what happens in "
    "this part\", \"devices\": [\"storytelling tricks used here (how suspense, feeling or surprise is built)\"], "
    "\"characters\": [\"type of person: their role\"], \"mood\": \"a few words\"}\n\nTEXT OF PART {k}:\n{text}")
NOTE_ASK = (
    "Here are notes on the parts of the book '{title}' by {author}, in order. Write ONE note for the writers of "
    "{series}, a Hindi audio-story series, about what this book can teach them. Describe in general terms that can "
    "be reused (no names from the book). Series rule: {rule}\n"
    "Answer ONLY JSON: {\"premise\": \"one sentence, in general terms\", \"shape\": [\"5-7 beats of how the story is "
    "built, general terms\"], \"twist\": \"the kind of turn or reveal and how it is prepared\", \"characters\": "
    "[\"character types\"], \"setting\": \"kind of place and time\", \"opening\": \"how it opens (the technique)\", "
    "\"why_it_works\": \"1-2 sentences\", \"fit\": 0-10 (how useful this book is for {series}), \"ideas\": [\"3 fresh "
    "story seeds that borrow its craft but are set in today's India with new people, a new situation and a new "
    "central object; each one English line under 15 words\"]}\n\nNOTES ON THE PARTS:\n{parts}")
USE_RULE = (
    "INSPIRATION FROM A BOOK (craft only): notes on '{title}' ({author}). Learn from how it is built; do NOT retell "
    "it: no plot, names, place or scenes from that book. Write a completely ORIGINAL story that follows this series' "
    "genre brief and is set in today's India.\n{note}")


def root(cfg):
    return os.path.join(cfg["media_dir"], "StoryBooks")


def _p(cfg, *a):
    return os.path.join(root(cfg), *a)


def _load(path, default):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def catalog(cfg):
    return _load(_p(cfg, "catalog.json"), {})


def note(cfg, nid):
    return _load(_p(cfg, "notes", re.sub(r"[^\w.-]", "_", str(nid)) + ".json"), None)


def init(cfg):
    for s in SERIES:
        os.makedirs(_p(cfg, "inbox", s), exist_ok=True)
        os.makedirs(_p(cfg, "books", s), exist_ok=True)
    os.makedirs(_p(cfg, "notes"), exist_ok=True)
    readme = _p(cfg, "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write("Story library (free books as inspiration for the story writer)\n\n"
                    "inbox\\<series>\\  drop your own free .epub / .txt / .html / .md books here, in the folder of the "
                    "series they should inspire (bhay, rahasya, kisse, romanch, kareeb, gyaan), then press Settings > "
                    "Story library > Build / update.\nbooks\\  plain text of downloaded books (deleted oldest first "
                    "when the folder passes the size cap; the notes stay).\nnotes\\  one short note per book: this is "
                    "what the story writer sees, never the book itself.\ncatalog.json  every book, its source, "
                    "licence and state.\n")


# ------------------------------------------------------------------ downloading
def _safe_url(url):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname:
        raise ValueError("not a web address")
    for info in socket.getaddrinfo(u.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise ValueError("refusing a local address")
    return url


def _get(url, timeout=60, limit=12 * 1024 * 1024):
    req = urllib.request.Request(_safe_url(url), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("file too big")
    return data


def _get_json(url, timeout=60):
    return json.loads(_get(url, timeout).decode("utf-8"))


def _gutenberg(term, n, have, say):
    """public-domain English books for a topic -> [{id, title, author, url, licence}]"""
    out, url = [], "https://gutendex.com/books/?" + urllib.parse.urlencode(
        {"topic": term, "languages": "en", "copyright": "false"})
    for _ in range(3):                                   # first pages of results, most downloaded first
        d = _get_json(url)
        for b in d.get("results") or []:
            bid = f"pg{b.get('id')}"
            fm = b.get("formats") or {}
            link = next((v for k, v in fm.items() if k.startswith("text/plain") and not v.endswith(".zip")), "") \
                or fm.get("application/epub+zip", "")
            if bid in have or not link or b.get("copyright") or b.get("media_type", "Text") != "Text":
                continue
            out.append({"id": bid, "source": "Project Gutenberg", "title": str(b.get("title") or "")[:160],
                        "author": "; ".join(a.get("name", "") for a in b.get("authors") or [])[:120],
                        "url": link, "page": f"https://www.gutenberg.org/ebooks/{b.get('id')}",
                        "licence": "public domain (Project Gutenberg)", "lang": "en"})
            if len(out) >= n:
                return out
        url = d.get("next")
        if not url:
            break
    return out


def _wiki_text(lang, title):
    """a Wikisource page as plain text (its subpages too when the page is only a contents list)"""
    api = f"https://{lang}.wikisource.org/w/api.php?"
    d = _get_json(api + urllib.parse.urlencode({"action": "parse", "page": title, "prop": "text|links",
                                                "format": "json", "formatversion": 2, "redirects": 1}))
    p = d.get("parse") or {}
    text = html_text(p.get("text") or "")
    if len(text) < BOOKS["min_chars"]:           # a contents page: read its subpages in order
        subs = [x["title"] for x in p.get("links") or [] if x.get("exists", True) and x.get("ns", 0) == 0
                and str(x.get("title", "")).startswith(title + "/")][:40]
        for s in subs:
            try:
                q = _get_json(api + urllib.parse.urlencode({"action": "parse", "page": s, "prop": "text",
                                                            "format": "json", "formatversion": 2}))
                text += "\n\n" + html_text((q.get("parse") or {}).get("text") or "")
            except Exception:
                continue
            time.sleep(0.5)
    return text


def _wikisource(lang, term, n, have, say, exact=False):
    if exact:
        titles = [term]
    else:
        d = _get_json(f"https://{lang}.wikisource.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "list": "search", "srsearch": term, "srnamespace": 0, "srlimit": 25,
             "format": "json"}))
        titles = [x.get("title", "") for x in (d.get("query") or {}).get("search") or []]
    out = []
    for t in titles:
        bid = f"ws{lang}_" + re.sub(r"\W+", "_", t)[:80]
        if not t or bid in have or "/" in t and not exact:
            continue
        out.append({"id": bid, "source": f"Wikisource ({lang})", "title": t[:160], "author": "",
                    "wiki": [lang, t], "page": f"https://{lang}.wikisource.org/wiki/" + urllib.parse.quote(t),
                    "licence": "public domain / free licence (Wikisource)", "lang": lang})
        if len(out) >= n:
            break
    return out


def _strip_gutenberg(t):
    a = re.search(r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG[^\n]*\n", t, re.I)
    b = re.search(r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG", t, re.I)
    return t[a.end() if a else 0: b.start() if b else len(t)].strip()


# ------------------------------------------------------------------ reading files
class _Text(html.parser.HTMLParser):
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "section", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        if tag in ("script", "style", "head", "sup") or self.skip or "noprint" in cls or "mw-editsection" in cls \
                or "reference" in cls or "navbox" in cls or "ws-noexport" in cls:
            self.skip += 1 if tag not in ("br", "img", "meta", "link", "hr", "input") else 0
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_text(s):
    p = _Text()
    try:
        p.feed(s)
        p.close()
        t = "".join(p.out)
    except Exception:
        t = html.unescape(re.sub(r"<[^>]+>", " ", s))
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    return re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", t).strip()


def epub_text(path):
    """an .epub as plain text, chapters in reading order (the spine of its package file)"""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        order = []
        try:
            cont = z.read("META-INF/container.xml").decode("utf-8", "ignore")
            opf = re.search(r'full-path="([^"]+)"', cont).group(1)
            o = z.read(opf).decode("utf-8", "ignore")
            base = os.path.dirname(opf)
            items = {m.group(1): m.group(2) for m in re.finditer(
                r'<item\b[^>]*?\bid="([^"]+)"[^>]*?\bhref="([^"]+)"', o)}
            items.update({m.group(2): m.group(1) for m in re.finditer(
                r'<item\b[^>]*?\bhref="([^"]+)"[^>]*?\bid="([^"]+)"', o)})
            for ref in re.findall(r'<itemref\b[^>]*?\bidref="([^"]+)"', o):
                if ref in items:
                    order.append(urllib.parse.unquote(os.path.normpath(os.path.join(base, items[ref])).replace("\\", "/")))
        except Exception:
            order = []
        if not order:
            order = sorted(n for n in names if n.lower().endswith((".xhtml", ".html", ".htm")))
        parts = []
        for n in order:
            if n in names:
                parts.append(html_text(z.read(n).decode("utf-8", "ignore")))
        return "\n\n".join(p for p in parts if p)


def file_text(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".epub":
        return epub_text(path)
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            t = raw.decode(enc)
            break
        except UnicodeError:
            continue
    else:
        t = raw.decode("utf-8", "ignore")
    if ext in (".html", ".htm", ".xhtml"):
        t = html_text(t)
    return _strip_gutenberg(t) if "PROJECT GUTENBERG" in t[:5000].upper() else t


def parts_of(text, n, size):
    """n pieces of `size` characters spread evenly from the start to the end (cut at a paragraph or sentence end)"""
    text = text.strip()
    if len(text) <= size * n:
        step = max(size, -(-len(text) // max(1, n)))
        return [text[i:i + step] for i in range(0, len(text), step)][:n]
    out = []
    span = len(text) - size
    for k in range(n):
        a = int(span * k / max(1, n - 1))
        if k:
            m = re.search(r"\n\s*\n|[.!?।]\s", text[a:a + 600])
            a += m.end() if m else 0
        out.append(text[a:a + size])
    return out


# ------------------------------------------------------------------ the notes
def _json(t):
    t = (t or "").strip()
    m = re.search(r"\{.*\}", t, re.S)
    return json.loads(m.group(0) if m else t)


def _ask(cfg, prompt):
    import story
    c = dict(cfg, _no_think=not BOOKS.get("think"))
    if BOOKS.get("model"):
        c["_llm_override"] = BOOKS["model"]
    try:
        t, model = story.local_chat(c, prompt, 0.3, True, 12288)
    finally:
        if c.get("_ollama_used"):
            cfg["_ollama_used"] = True
    return _json(t), model


def _rule(series):
    try:
        import story
        return story.brief_rule(series) or "-"
    except Exception:
        return "-"


def make_note(cfg, book, text, say, stopped=lambda: False):
    """read a book in parts -> the note dict (None when stopped)"""
    t0 = time.time()
    ser = LABEL.get(book["series"], book["series"])
    parts = parts_of(text, int(BOOKS["parts"]), int(BOOKS["part_chars"]))
    seen = []
    for k, part in enumerate(parts, 1):
        if stopped():
            return None
        try:
            d, model = _ask(cfg, PART_ASK.replace("{k}", str(k)).replace("{n}", str(len(parts)))
                            .replace("{title}", book["title"]).replace("{author}", book.get("author") or "unknown")
                            .replace("{series}", ser).replace("{text}", part))
        except Exception as e:
            say(f"      (part {k} not read: {e})")
            continue
        seen.append(f"Part {k}: {d.get('events', '')} | tricks: {'; '.join(map(str, d.get('devices') or []))} | "
                    f"people: {'; '.join(map(str, d.get('characters') or []))} | mood: {d.get('mood', '')}")
    if not seen:
        raise RuntimeError("no part could be read")
    d, model = _ask(cfg, NOTE_ASK.replace("{title}", book["title"]).replace("{author}", book.get("author") or "unknown")
                    .replace("{series}", ser).replace("{rule}", _rule(book["series"])).replace("{parts}", "\n".join(seen)))
    try:
        fit = max(0, min(10, int(round(float(d.get("fit"))))))
    except (TypeError, ValueError):
        fit = 5
    keep = {k: d.get(k) for k in ("premise", "shape", "twist", "characters", "setting", "opening", "why_it_works")}
    ideas = [str(x).strip() for x in d.get("ideas") or [] if str(x).strip()][:5]
    return {"id": book["id"], "series": book["series"], "title": book["title"], "author": book.get("author", ""),
            "source": book.get("source", ""), "page": book.get("page", ""), **keep, "fit": fit, "ideas": ideas,
            "parts_read": len(seen), "chars": len(text), "model": model, "seconds": round(time.time() - t0),
            "made": time.strftime("%Y-%m-%d %H:%M")}


def _cap(cfg, cat, say):
    """keep books\\ under max_mb: delete the texts of the oldest noted books (their notes stay)"""
    files = [(os.path.getmtime(f), os.path.getsize(f), f) for f in glob.glob(_p(cfg, "books", "*", "*.txt"))]
    total, cap = sum(s for _, s, _ in files), float(BOOKS["max_mb"]) * 1024 * 1024
    for _, s, f in sorted(files):
        if total <= cap:
            break
        bid = os.path.splitext(os.path.basename(f))[0]
        if (cat.get(bid) or {}).get("status") != "noted":
            continue
        os.remove(f)
        total -= s
        cat[bid]["text"] = ""
        say(f"   size cap {BOOKS['max_mb']} MB: text of '{cat[bid]['title']}' deleted (its note stays)")


def build(ctx, series="", fetch=True, rebuild=False):
    """the Build / update job: inbox books + new free books per series -> one note each"""
    cfg, say, stopped = ctx["cfg"], ctx["say"], ctx["stopped"]
    init(cfg)
    cat = catalog(cfg)
    if rebuild:
        for b in cat.values():
            if b.get("status") in ("noted", "failed") and b.get("text") and os.path.exists(b["text"]):
                b["status"] = "downloaded"
    todo = [s for s in SERIES if not series or s == series]
    say(f"   story library: {root(cfg)}  ({len(cat)} books so far, {sum(1 for b in cat.values() if b.get('status') == 'noted')} with notes)")
    # 1. your own files in inbox\<series>
    for s in todo:
        for f in sorted(glob.glob(_p(cfg, "inbox", s, "*"))):
            if not f.lower().endswith((".epub", ".txt", ".html", ".htm", ".xhtml", ".md")):
                continue
            bid = "in_" + re.sub(r"\W+", "_", os.path.splitext(os.path.basename(f))[0])[:80] + f"_{s}"
            if bid in cat and cat[bid].get("status") != "failed":
                continue
            cat[bid] = {"id": bid, "series": s, "title": os.path.splitext(os.path.basename(f))[0], "author": "",
                        "source": "your inbox", "licence": "your own file", "file": f, "status": "found",
                        "added": time.strftime("%Y-%m-%d %H:%M")}
            say(f"   inbox {s}: {os.path.basename(f)}")
    # 2. new free books per series
    if fetch:
        for s in todo:
            if stopped():
                break
            src = SOURCES.get(s) or {}
            want, have = int(BOOKS["new_per_series"]), set(cat)
            found = []
            for kind, terms in (("gutenberg", src.get("gutenberg")), ("wikisource_hi", src.get("wikisource_hi")),
                                ("pages_hi", src.get("pages_hi")), ("wikisource_en", src.get("wikisource_en")),
                                ("pages_en", src.get("pages_en"))):
                for term in terms or []:
                    if len(found) >= want * 3:
                        break
                    try:
                        if kind == "gutenberg":
                            got = _gutenberg(term, want, have, say)
                        else:
                            got = _wikisource("hi" if kind.endswith("_hi") else "en", term, want, have, say,
                                              exact=kind.startswith("pages"))
                    except Exception as e:
                        say(f"   ({kind} '{term}' not reachable: {e})")
                        continue
                    for g in got:
                        if g["id"] not in have:
                            have.add(g["id"])
                            found.append(g)
                    time.sleep(float(BOOKS["pause_s"]))
            random.shuffle(found)                         # a mix of sources, not always the first search's books
            found.sort(key=lambda g: 0 if g.get("lang") == "hi" else 1)
            added = 0
            for g in found:
                if added >= want or stopped():
                    break
                try:
                    if g.get("wiki"):
                        text = _wiki_text(*g["wiki"])
                    else:
                        data = _get(g["url"])
                        if g["url"].endswith(".epub") or data[:2] == b"PK":
                            tmp = _p(cfg, "books", s, g["id"] + ".epub")
                            open(tmp, "wb").write(data)
                            text = epub_text(tmp)
                            os.remove(tmp)
                        else:
                            text = _strip_gutenberg(data.decode("utf-8", "ignore"))
                except Exception as e:
                    say(f"   (could not download '{g['title']}': {e})")
                    continue
                if len(text) < int(BOOKS["min_chars"]):
                    cat[g["id"]] = {**{k: v for k, v in g.items() if k != "wiki"}, "series": s, "status": "too short",
                                    "added": time.strftime("%Y-%m-%d %H:%M")}
                    continue
                path = _p(cfg, "books", s, g["id"] + ".txt")
                open(path, "w", encoding="utf-8").write(text)
                cat[g["id"]] = {**{k: v for k, v in g.items() if k != "wiki"}, "series": s, "text": path,
                                "status": "downloaded", "chars": len(text), "added": time.strftime("%Y-%m-%d %H:%M")}
                added += 1
                say(f"   {s}: downloaded '{g['title']}'{' - ' + g['author'] if g.get('author') else ''} "
                    f"({g['source']}, {len(text) // 1000}k characters)")
                time.sleep(float(BOOKS["pause_s"]))
            if not added:
                say(f"   {s}: no new free book found this time")
            _save(_p(cfg, "catalog.json"), cat)
    _save(_p(cfg, "catalog.json"), cat)
    # 3. notes for every book that has none yet
    wait = [b for b in cat.values() if b.get("series") in todo and b.get("status") in ("found", "downloaded")]
    say(f"   {len(wait)} book(s) to read for notes")
    done = 0
    for b in wait:
        if stopped():
            say("   stopped: the books not read yet are read on the next Build / update")
            break
        try:
            text = file_text(b["file"]) if b.get("file") else open(b["text"], encoding="utf-8").read()
            if len(text) < int(BOOKS["min_chars"]):
                b["status"] = "too short"
                continue
            say(f"   reading '{b['title']}' for {b['series']} ...")
            n = make_note(cfg, b, text, say, stopped)
            if n is None:
                break
            _save(_p(cfg, "notes", re.sub(r"[^\w.-]", "_", b["id"]) + ".json"), n)
            b.update({"status": "noted", "fit": n["fit"], "noted": n["made"]})
            done += 1
            say(f"      note ({n['seconds']} s, fit {n['fit']}/10): {str(n.get('premise') or '')[:160]}")
            for i in n["ideas"]:
                say(f"         idea: {i}")
        except Exception as e:
            b["status"], b["error"] = "failed", str(e)[:200]
            say(f"      (no note: {e})")
        _save(_p(cfg, "catalog.json"), cat)
    _cap(cfg, cat, say)
    _save(_p(cfg, "catalog.json"), cat)
    say(f"   story library: {done} new note(s); {sum(1 for b in cat.values() if b.get('status') == 'noted')} books "
        "with notes in all")
    return True


# ------------------------------------------------------------------ using the notes (story.py)
def usable(cfg, series):
    s = "bhay" if series == "horror" else series
    out = []
    for b in catalog(cfg).values():
        if b.get("series") == s and b.get("status") == "noted" and int(b.get("fit") or 0) >= int(BOOKS["min_fit"]):
            n = note(cfg, b["id"])
            if n:
                out.append(n)
    return out


def pick(cfg, series, ledger, rng):
    """the note this story learns from: least used by earlier stories (all series' ledgers), ties at random"""
    notes = usable(cfg, series)
    if not notes:
        return None
    used = {}
    for e in ledger:
        bid = ((e.get("recipe") or {}).get("book") or {}).get("id")
        if bid:
            used[bid] = used.get(bid, 0) + 1
    low = min(used.get(n["id"], 0) for n in notes)
    return rng.choice([n for n in notes if used.get(n["id"], 0) == low])


def seed(n, used, rng):
    left = [i for i in n.get("ideas") or [] if i.strip().lower() not in used]
    return rng.choice(left) if left else ""


def prompt_block(n):
    """the note as the writer sees it"""
    lines = []
    for key, label in (("premise", "Premise"), ("shape", "How it is built"), ("twist", "The turn"),
                       ("characters", "Character types"), ("opening", "How it opens"), ("why_it_works", "Why it works")):
        v = n.get(key)
        if isinstance(v, list):
            v = " -> ".join(str(x) for x in v if x) if key == "shape" else "; ".join(str(x) for x in v if x)
        if v:
            lines.append(f"- {label}: {v}")
    return USE_RULE.replace("{title}", str(n.get("title") or "")).replace("{author}", str(n.get("author") or "unknown")) \
        .replace("{note}", "\n".join(lines))


# ------------------------------------------------------------------ hub routes
def state(cfg):
    cat = catalog(cfg)
    rows = sorted(cat.values(), key=lambda b: (b.get("series", ""), b.get("status") != "noted", b.get("title", "")))
    try:
        import story
        L = story.ledger(cfg)
    except Exception:
        L = []
    used = {}
    for e in L:
        bid = ((e.get("recipe") or {}).get("book") or {}).get("id")
        if bid:
            used[bid] = used.get(bid, 0) + 1
    size = sum(os.path.getsize(f) for f in glob.glob(_p(cfg, "books", "*", "*.txt")))
    per = {s: {"noted": sum(1 for b in rows if b.get("series") == s and b.get("status") == "noted"),
               "usable": sum(1 for b in rows if b.get("series") == s and b.get("status") == "noted"
                             and int(b.get("fit") or 0) >= int(BOOKS["min_fit"])),
               "waiting": sum(1 for b in rows if b.get("series") == s and b.get("status") in ("found", "downloaded"))}
           for s in SERIES}
    return {"root": root(cfg), "on": bool(BOOKS.get("on")), "mb": round(size / 1048576, 1), "max_mb": BOOKS["max_mb"],
            "series": per, "labels": LABEL,
            "books": [{k: b.get(k) for k in ("id", "series", "title", "author", "source", "licence", "status", "fit",
                                             "page", "added", "error")} | {"used": used.get(b.get("id"), 0)}
                      for b in rows]}


def _exp(cfg):
    """the hub config pointed at the Experimental Library (where Experimental story jobs read the notes)"""
    import experimental_workspace
    base = experimental_workspace.initialize(cfg)
    L = cfg.get("layout") or {}
    return base, dict(cfg, media_dir=os.path.join(base, L.get("experimental_library", "Library")))


def handle_get(cfg, p, q):
    _, cfg = _exp(cfg)
    if p == "/api/books":
        return 200, state(cfg)
    m = re.fullmatch(r"/api/books/note/([\w.-]+)", p)
    if m:
        n = note(cfg, m.group(1))
        return (200, n) if n else (404, {"error": "no note"})
    return 404, {"error": "unknown"}


def handle_post(cfg, p, body, jobs):
    if p != "/api/books/build":
        return 404, {"error": "unknown"}
    import tinker
    base, ecfg = _exp(cfg)
    L = cfg.get("layout") or {}
    init(ecfg)
    meta = {"root": base, "series": "", "episode_dir": "", "layout": L,
            "media_dir": os.path.join(base, L.get("experimental_library", "Library")),
            "sukoonvoice_media": os.path.join(base, L.get("experimental_library", "Library"), "Narration")}
    s = str(body.get("series") or "")
    if s and s not in SERIES:
        raise ValueError("unknown series")
    fetch = body.get("fetch", True) is not False
    steps = [{"fn": "exp_story_library", "label": ("find free books for " + (s or "every story series") + ", then "
                                                    if fetch else "") + "the local AI reads each book once and keeps a short note",
              "args": {"series": s, "fetch": fetch, "rebuild": bool(body.get("rebuild"))}},
             {"fn": "free_llm", "label": "free the graphics card"}]
    params = {"_experimental_workspace": meta}
    snap = tinker.snapshot(cfg)
    if snap:
        params["_tinker"] = snap
    title = ("Story library: " + ("new free books + notes" if fetch else "notes for books already there")
             + (f" ({s})" if s else ""))
    j = jobs.add("story_library", title, steps, [os.path.join(meta["media_dir"], "StoryBooks")], params)
    return 200, {"ok": True, "id": j["id"]}
