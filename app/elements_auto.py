"""elements_auto.py - the elements library keeps itself up to date, in the background (no graphics card).

  needs(text)   which moving elements a script / picture idea will want (steam, candle, rain, fireflies ...)
  missing(...)  which of them have fewer than KEEP ready clips in D:\\Audio asset\\Elements\\ready
  fetch(...)    Pixabay video API (free key in Settings): a few clips per missing element, on black background,
                licence = Pixabay Content License (commercial use, no credit). Each is saved under its real
                Pixabay name, written into the register (ELEMENTS_INDEX.json/.csv: page, author, original link,
                licence, date, sha256), then made into a seamless loop by the video engine (elements_prep.py);
                clips that turn out not to be on black are kept but marked "not used".
Pixabay asks for: real requests only (no mass downloads), searches cached 24 h, showing that results come from
Pixabay. So: at most PER_KIND new clips per element per run, searches cached in elements_search_cache.json.
"""
import hashlib, json, os, re, subprocess, time, urllib.parse, urllib.request

import elements_get as eg                                       # register (load/save) + licence text
import story                                                    # secrets.json (keys)

KEEP = 3                                                        # wanted ready clips per element
PER_KIND = 3                                                    # max new downloads per element per run
WORDS = {                                                       # what in a script / picture idea asks for what
    "steam": r"\b(tea|chai|coffee|cup|mug|kettle|teapot|soup)\b|चाय|कॉफ़ी|कॉफी|कप|प्याला|केतली|भाप",
    "candle": r"\b(candle|diya|oil lamp|lantern)\b|मोमबत्ती|दीया|दीये|दिया जल|लालटेन|चिराग़|चिराग",
    "incense": r"\b(incense|agarbatti)\b|अगरबत्ती|धूप",
    "rain": r"\b(rain|monsoon|drizzle)\b|बारिश|बरसात|बूँद|बूंद|सावन|मानसून",
    "fireflies": r"\b(firefl\w*)\b|जुगनू",
    "snow": r"\b(snow\w*)\b|बर्फ़|बर्फ",
    "fog": r"\b(fog|mist|haze)\b|कोहरा|धुंध",
    "fire": r"\b(fireplace|campfire|bonfire)\b|अलाव|अंगीठी",
}
QUERIES = {
    "steam": ["steam black background", "smoke loop black background"],
    "candle": ["candle flame black background", "candle flame isolated"],
    "incense": ["incense smoke black background"],
    "rain": ["rain overlay black background", "rain falling black background"],
    "fireflies": ["fireflies black background", "glowing particles black background"],
    "snow": ["snow falling black background", "snowfall overlay black"],
    "fog": ["fog overlay black background", "mist smoke black background"],
    "fire": ["fire flames black background"],
}
AVOID = re.compile(r"\b(woman|man|girl|boy|people|person|face|model|logo|text|brand|hand|portrait)\b", re.I)


def lib_dir(cfg):
    return cfg.get("elements_dir") or r"D:\Audio asset\Elements"


def needs(text):
    t = text or ""
    return [k for k, rx in WORDS.items() if re.search(rx, t, re.I)]


def ready_count(cfg, kind):
    d = os.path.join(lib_dir(cfg), "ready")
    n = 0
    for f in os.listdir(d) if os.path.isdir(d) else []:
        if f.endswith(".json"):
            try:
                if kind in json.load(open(os.path.join(d, f), encoding="utf-8")).get("use", []):
                    n += 1
            except Exception:
                pass
    return n


def missing(cfg, kinds):
    return [k for k in kinds if ready_count(cfg, k) < KEEP]


def _search(key, q, cache):
    c = cache.get(q)
    if c and time.time() - c["t"] < 24 * 3600:
        return c["hits"]
    url = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(
        {"key": key, "q": q, "per_page": 30, "safesearch": "true", "order": "popular"})
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": eg.UA}), timeout=30) as r:
        hits = json.loads(r.read()).get("hits", [])
    cache[q] = {"t": time.time(), "hits": hits}
    return hits


def _slug_from_page(page, vid):
    m = re.search(r"/videos/([a-z0-9-]+-\d+)/?$", page or "")
    return m.group(1) if m else f"pixabay-{vid}"


def fetch(cfg, kinds, say=print, studio_python=None):
    """download up to PER_KIND new clips per missing element; register + prepare them. -> number of new clips"""
    key = story.secret("pixabay_key")
    if not key:
        say("   no Pixabay key yet (Settings > Elements) - nothing fetched"); return 0
    lib = lib_dir(cfg)
    os.makedirs(lib, exist_ok=True)
    cpath = os.path.join(cfg["media_dir"], "elements_search_cache.json")
    try:
        cache = json.load(open(cpath, encoding="utf-8"))
    except Exception:
        cache = {}
    ix = eg.load_index_at(lib)
    have_ids = set()
    for k, it in ix["items"].items():
        m = re.search(r"-(\d+)\.mp4$", k)
        have_ids.add(str(it.get("pixabay_id") or (m.group(1) if m else "")))
    lic = dict(eg.PIXABAY_LICENSE)
    new = 0
    for kind in kinds:
        want = min(PER_KIND, max(0, KEEP - ready_count(cfg, kind)))
        if not want:
            continue
        say(f"   {kind}: {ready_count(cfg, kind)} ready, looking for {want} more on Pixabay ...")
        got = 0
        for q in QUERIES.get(kind, [kind + " black background"]):
            if got >= want:
                break
            try:
                hits = _search(key, q, cache)
            except Exception as e:
                say(f"   !! search '{q}': {e}"); continue
            for h in hits:
                if got >= want:
                    break
                vid = str(h.get("id"))
                tags = h.get("tags", "")
                if vid in have_ids or AVOID.search(tags) or not (4 <= int(h.get("duration") or 0) <= 60):
                    continue
                v = h.get("videos", {})
                pick = next((v[s] for s in ("medium", "large", "small") if v.get(s, {}).get("url")
                             and (v[s].get("size") or 0) < 40_000_000), None)
                if not pick:
                    continue
                name = _slug_from_page(h.get("pageURL"), vid)
                rel = f"{kind}/{name}.mp4"
                path = os.path.join(lib, kind, name + ".mp4")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                try:
                    req = urllib.request.Request(pick["url"], headers={"User-Agent": eg.UA, "Referer": "https://pixabay.com/"})
                    with urllib.request.urlopen(req, timeout=180) as r:
                        data = r.read()
                except Exception as e:
                    say(f"   !! download {name}: {e}"); continue
                if os.path.exists(path):
                    os.replace(path, path[:-4] + time.strftime("_old_%H%M%S.mp4"))
                open(path, "wb").write(data)
                ix["items"][rel] = {
                    "kind": kind, "site": "Pixabay", "page": h.get("pageURL", ""), "pixabay_id": vid,
                    "title": tags, "author": h.get("user", ""),
                    "author_url": f"https://pixabay.com/users/{h.get('user', '')}-{h.get('user_id', '')}/",
                    "original_file": pick["url"].split("?")[0], "published": "", "duration": f"{h.get('duration')} s",
                    "resolution": f"{pick.get('width')}x{pick.get('height')}", "note": f"found automatically ('{q}')",
                    "license": lic, "downloaded_on": time.strftime("%Y-%m-%d %H:%M"), "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(), "used_in": []}
                eg.save_index_at(lib, ix)
                have_ids.add(vid)
                got += 1; new += 1
                say(f"   + {rel}  ({len(data) / 1e6:.1f} MB, by {h.get('user')})")
        if not got:
            say(f"   {kind}: nothing new found")
    json.dump(cache, open(cpath, "w", encoding="utf-8"))
    if new and studio_python and os.path.exists(studio_python):
        say("   making them seamless loops (video engine) ...")
        prep = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "engines", "studio", "engine", "elements_prep.py")
        r = subprocess.run([studio_python, "-u", prep, "--lib", lib], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        for ln in (r.stdout + r.stderr).splitlines()[-20:]:
            if ln.strip():
                say("   " + ln.strip())
    return new
