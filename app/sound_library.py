"""sound_library.py - the Sounds page: your BGM and SFX libraries, importing tracks, and getting more from
freely licensed sources, with the licence of every file written down.

Where files go (the same folders the pipeline already reads):
  music    <music_dir>\\<mood folder>\\<title>-<author>-<source id>.mp3     register: <music_dir>\\BGM_INDEX.csv
  effects  <SFX>\\<category>\\<title>-<author>-<source id>.mp3              register: <SFX>\\SFX_INDEX.csv
Both registers have the columns of the existing SFX_INDEX.csv plus licence_code and credit.

Sources (searched from the hub, downloaded by a light job):
  Freesound   (freesound.org, your free API key in Settings)  CC0, or CC BY when you allow credited sounds
  Openverse   (api.openverse.org, no key; finds Freesound, Jamendo, Wikimedia Commons, ccMixter)  CC0, PDM, CC BY
The licence is checked again with the source when downloading, not taken from the page.
Not used: NonCommercial (NC) and NoDerivatives (ND) licences - they do not allow a monetised video with music
mixed under it. ShareAlike (BY-SA) is unclear for a mixed video and is not offered; you can import such a file,
but it is never picked automatically until you change its licence.
"""
import csv
import glob
import ipaddress
import json
import os
import re
import socket
import subprocess
import time
import urllib.parse
import urllib.request

LICENSES = {
    # code: (label, allowed for automatic use, needs a credit, note)
    "cc0": ("CC0 (public domain)", True, False, ""),
    "pdm": ("Public Domain Mark", True, False, ""),
    "by": ("CC BY (credit needed)", True, True, "put the credit in the video description (credits.txt is written)"),
    "own": ("Made or bought by you", True, False, ""),
    "yt_library": ("YouTube Audio Library", True, False, "only for YouTube; some tracks ask for a credit - add it below"),
    "pixabay": ("Pixabay Content License", True, False, "allowed, but Content ID claims can happen; keep the page link"),
    "by-sa": ("CC BY-SA (share-alike)", False, True, "unclear for a mixed video: not picked automatically"),
    "nc": ("Non-commercial (CC BY-NC…)", False, True, "not allowed on a monetised channel: never picked"),
    "nd": ("No derivatives (CC BY-ND)", False, True, "mixing changes the work: never picked"),
    "unknown": ("Not sure", False, False, "find the licence first: not picked automatically"),
}
UA = "HubGPT/1.0 (personal video tool; local)"
# words that find background music for each mood folder (Freesound / Openverse search)
MOOD_QUERIES = {"Dark": "dark ambient drone", "Tension": "suspense tension cinematic underscore",
                "Eerie": "eerie horror ambience", "Sad": "sad piano melancholy", "Love": "romantic soft piano",
                "Calm": "calm ambient meditation pad", "Happy": "happy acoustic light"}
# a search hit whose own title or tags say it is a voice or a single effect is not background music (18 Oct 2026:
# 'eerie horror ambience' brought "Throat Vocal Groan" into BGM\Eerie, where it could be picked as a 2-minute bed)
NOT_MUSIC_WORDS = ("vocal", "voice", "groan", "scream", "shout", "speech", "talking", "whisper", "laugh", "cry",
                   "sobbing", "breath", "moan", "footstep", "door", "knock", "impact", "hit", "whoosh", "stinger")
MUSIC_EXT = (".mp3",)
SFX_EXT = (".mp3", ".wav")


# ------------------------------------------------------------------ folders and registers
def music_root(cfg):
    return cfg.get("music_dir") or r"D:\Audio asset\BGM"


def sfx_root(cfg):
    import sfx_lib
    return sfx_lib.root(cfg)


def _index(cfg, kind):
    return os.path.join(music_root(cfg), "BGM_INDEX.csv") if kind == "music" else os.path.join(sfx_root(cfg), "SFX_INDEX.csv")


HEAD = ["file", "category", "site", "page", "author", "licence", "downloaded_on", "used_in", "licence_code", "credit"]


def _read_index(cfg, kind):
    p = _index(cfg, kind)
    rows = []
    if os.path.isfile(p):
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in list(csv.reader(f))[1:]:
                r = (r + [""] * len(HEAD))[:len(HEAD)]
                d = dict(zip(HEAD, r))
                if not d["licence_code"]:
                    d["licence_code"] = "cc0" if "Creative Commons 0" in d["licence"] or "publicdomain/zero" in d["licence"] else ""
                rows.append(d)
    return rows


def _register(cfg, kind, row):
    p = _index(cfg, kind)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    new = not os.path.exists(p)
    with open(p, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEAD)
        w.writerow([row.get(k, "") for k in HEAD])


def _set_row(cfg, kind, file, **kv):
    """change the register row of one file (the newest row of that name wins when reading)"""
    p = _index(cfg, kind)
    rows = []
    if os.path.isfile(p):
        with open(p, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    if not rows:
        rows = [HEAD]
    if len(rows[0]) < len(HEAD):
        rows[0] = HEAD
    name = os.path.basename(file)
    hit = False
    for r in rows[1:]:
        if r and r[0] == name:
            r += [""] * (len(HEAD) - len(r))
            for k, v in kv.items():
                r[HEAD.index(k)] = v
            hit = True
    if not hit:
        r = [""] * len(HEAD)
        r[0] = name
        r[1] = os.path.basename(os.path.dirname(file))
        for k, v in kv.items():
            r[HEAD.index(k)] = v
        rows.append(r)
    with open(p + ".part", "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)
    os.replace(p + ".part", p)


def _by_name(cfg, kind):
    return {r["file"]: r for r in _read_index(cfg, kind)}


def blocked(cfg, kind):
    """norm(paths) of files whose recorded licence does not allow automatic use"""
    import feedback
    root = music_root(cfg) if kind == "music" else sfx_root(cfg)
    out = set()
    for r in _read_index(cfg, kind):
        code = r.get("licence_code")
        if code and code in LICENSES and not LICENSES[code][1]:
            out.add(feedback.norm(os.path.join(root, r["category"], r["file"])))
    return out


def credits_for(cfg, files):
    """credit lines for the files that need one"""
    lines = []
    idx = {**{("music", k): v for k, v in _by_name(cfg, "music").items()},
           **{("sfx", k): v for k, v in _by_name(cfg, "sfx").items()}}
    for f in files:
        if not f:
            continue
        r = idx.get(("music", os.path.basename(f))) or idx.get(("sfx", os.path.basename(f)))
        if not r:
            continue
        code = r.get("licence_code")
        if r.get("credit") or (code in LICENSES and LICENSES[code][2]):
            lines.append(r.get("credit") or f"\"{os.path.splitext(r['file'])[0]}\" by {r.get('author') or 'unknown'}"
                         f" ({r.get('site', '')} {r.get('page', '')}) - {LICENSES.get(code, (r.get('licence') or code,))[0]}")
    return sorted(set(lines))


def _used_counts(cfg):
    import experimental_workspace
    counts = {}
    for media in (cfg.get("media_dir"), os.path.join(experimental_workspace.root(cfg),
                                                   (cfg.get("layout") or {}).get("experimental_library", "Library"))):
        try:
            with open(os.path.join(media or "", "bhay_music_used.json"), encoding="utf-8") as f:
                for k, v in (json.load(f) or {}).items():
                    counts[os.path.normcase(k)] = counts.get(os.path.normcase(k), 0) + int(v or 0)
        except (OSError, ValueError):
            pass
    return counts


def listing(cfg):
    import audio_screen
    import feedback
    import sfx_lib
    import tools
    out = {"music": [], "sfx": [], "music_folders": [], "sfx_categories": [], "licenses":
           {k: {"label": v[0], "auto": v[1], "credit": v[2], "note": v[3]} for k, v in LICENSES.items()}}
    mr, sr = music_root(cfg), sfx_root(cfg)
    mused = _used_counts(cfg)
    for kind, root, ext in (("music", mr, MUSIC_EXT), ("sfx", sr, SFX_EXT)):
        idx = _by_name(cfg, kind)
        scores = feedback.file_scores(cfg, kind)
        folders = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)) and not d.startswith("_")) \
            if os.path.isdir(root) else []
        if kind == "sfx":
            folders = sorted(set(folders) | set(sfx_lib.CATS))
        out["music_folders" if kind == "music" else "sfx_categories"] = folders
        files = []
        for e in ext:
            files += glob.glob(os.path.join(root, "*", "*" + e))
            if kind == "sfx":
                files += glob.glob(os.path.join(root, "*" + e))
        for f in sorted(files):
            if os.sep + "_old" + os.sep in f:
                continue
            r = idx.get(os.path.basename(f)) or {}
            code = r.get("licence_code") or ""
            sc = scores.get(feedback.norm(f)) or {}
            used = mused.get(os.path.normcase(f), 0) if kind == "music" else len([x for x in (r.get("used_in") or "").split(";") if x.strip()])
            scr = audio_screen.cached(cfg, f)
            out[kind].append({"screen": scr.get("verdict", "") if not scr.get("stale") else "",
                              "screen_why": scr.get("why") or [], "override": scr.get("override", ""),
                              "fits": scr.get("fits") or [],
                              "action": audio_screen.effective(scr) if scr.get("verdict") else "",
                              "file": f, "name": os.path.splitext(os.path.basename(f))[0],
                              "folder": os.path.basename(os.path.dirname(f)) if os.path.dirname(f) != root else "(top)",
                              "licence_code": code, "licence": r.get("licence") or ("" if code else "not recorded (your library)"),
                              "site": r.get("site", ""), "page": r.get("page", ""), "author": r.get("author", ""),
                              "credit": r.get("credit", ""), "auto": (LICENSES.get(code, ("", True))[1] if code else True),
                              "used": used, "score": sc.get("avg"), "weight": sc.get("weight"),
                              "skipped": feedback.rejected(sc) or (code in LICENSES and not LICENSES[code][1]),
                              "size": os.path.getsize(f)})
    import story
    out["freesound_key"] = bool(story.secret("freesound_key"))
    out["roots"] = {"music": mr, "sfx": sr, "music_index": _index(cfg, "music"), "sfx_index": _index(cfg, "sfx")}
    out["series"] = {k: {"categories": v[0], "per_min": v[1], "music": (tools.GENRES.get(k) or {}).get("music", [])}
                     for k, v in sfx_lib.SERIES.items()}
    out["moods"] = {f: {"query": MOOD_QUERIES.get(f, f.lower() + " background music"),
                        "series": [k for k, g in tools.GENRES.items() if f in (g.get("music") or [])]}
                    for f in sorted(set(out["music_folders"]) | set(MOOD_QUERIES))}
    out["missing"] = {k: sfx_lib.missing(cfg, k) for k in sfx_lib.SERIES}
    return out


# ------------------------------------------------------------------ searching
def _get_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _fs_code(lic):
    lic = str(lic or "").lower()
    if "publicdomain/zero" in lic or lic == "creative commons 0":
        return "cc0"
    if "-nc" in lic or "noncommercial" in lic or "sampling" in lic:
        return "nc"
    if "-nd" in lic:
        return "nd"
    if "-sa" in lic:
        return "by-sa"
    if "/by/" in lic or lic == "attribution":
        return "by"
    return "unknown"


def _ov_code(lic):
    lic = str(lic or "").lower()
    return {"cc0": "cc0", "pdm": "pdm", "by": "by", "by-sa": "by-sa"}.get(lic, "nc" if "nc" in lic else "nd" if "nd" in lic else "unknown")


def _offer(code, allow_by):
    return code in ("cc0", "pdm") or (allow_by and code == "by")


def search(cfg, body):
    import story
    kind = "music" if body.get("kind") == "music" else "sfx"
    source = str(body.get("source") or "openverse")
    q = str(body.get("query") or "").strip()[:120]
    if not q:
        raise ValueError("type what you are looking for (for example: dark ambient drone, door creak)")
    allow_by = bool(body.get("allow_by"))
    page = max(1, min(20, int(body.get("page") or 1)))
    have = {r["page"] for r in _read_index(cfg, kind) if r.get("page")}
    res = []
    if source == "freesound":
        key = story.secret("freesound_key")
        if not key:
            raise ValueError("add your free Freesound API key in Settings first (freesound.org/apiv2/apply)")
        lic = '("Creative Commons 0" OR "Attribution")' if allow_by else '"Creative Commons 0"'
        dur = "[45 TO 900]" if kind == "music" else "[0.2 TO 90]"
        url = "https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode({
            "query": q, "filter": f"license:{lic} duration:{dur}", "sort": "rating_desc", "page": page,
            "fields": "id,name,username,license,previews,url,duration,tags", "page_size": 20, "token": key})
        for h in _get_json(url).get("results", []):
            code = _fs_code(h.get("license"))
            if not _offer(code, allow_by):
                continue
            res.append({"source": "freesound", "id": str(h.get("id")), "title": h.get("name", ""),
                        "author": h.get("username", ""), "licence_code": code, "licence": h.get("license", ""),
                        "page": h.get("url", ""), "preview": (h.get("previews") or {}).get("preview-hq-mp3", ""),
                        "duration": round(float(h.get("duration") or 0), 1), "have": h.get("url", "") in have,
                        "tags": (h.get("tags") or [])[:8]})
    else:
        lic = "cc0,pdm,by" if allow_by else "cc0,pdm"
        url = "https://api.openverse.org/v1/audio/?" + urllib.parse.urlencode({
            "q": q, "license": lic, "category": "music" if kind == "music" else "sound_effect", "page_size": 20,
            "page": page, "mature": "false"})
        for h in _get_json(url).get("results", []):
            code = _ov_code(h.get("license"))
            if not _offer(code, allow_by):
                continue
            dur = float(h.get("duration") or 0) / 1000.0
            if kind == "music" and dur and dur < 45:
                continue
            res.append({"source": "openverse", "id": str(h.get("id")), "title": h.get("title", ""),
                        "author": h.get("creator", ""), "licence_code": code,
                        "licence": f"{h.get('license', '')} {h.get('license_version', '')}".strip(),
                        "page": h.get("foreign_landing_url", ""), "preview": h.get("url", ""),
                        "duration": round(dur, 1), "have": h.get("foreign_landing_url", "") in have,
                        "provider": h.get("provider") or h.get("source") or "", "tags": [t.get("name") for t in (h.get("tags") or [])[:8] if isinstance(t, dict)]})
    return {"results": res, "page": page, "kind": kind, "source": source}


# ------------------------------------------------------------------ downloading (a light job)
def _safe_url(url):
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise ValueError("only https downloads")
    for info in socket.getaddrinfo(u.hostname, 443):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise ValueError("not a public address")
    return url


def _details(cfg, source, sid):
    """the licence and file as the source says now (never trust what the page sent)"""
    import story
    if source == "freesound":
        key = story.secret("freesound_key")
        h = _get_json("https://freesound.org/apiv2/sounds/" + urllib.parse.quote(sid) + "/?" + urllib.parse.urlencode(
            {"fields": "id,name,username,license,previews,url,duration", "token": key}))
        return {"title": h.get("name", ""), "author": h.get("username", ""), "code": _fs_code(h.get("license")),
                "licence": h.get("license", ""), "page": h.get("url", ""), "site": "Freesound",
                "file": (h.get("previews") or {}).get("preview-hq-mp3", "")}
    if source == "openverse":
        h = _get_json("https://api.openverse.org/v1/audio/" + urllib.parse.quote(sid) + "/")
        return {"title": h.get("title", ""), "author": h.get("creator", ""), "code": _ov_code(h.get("license")),
                "licence": f"{h.get('license', '')} {h.get('license_version', '')} {h.get('license_url', '')}".strip(),
                "page": h.get("foreign_landing_url", ""), "file": h.get("url", ""),
                "site": "Openverse / " + str(h.get("provider") or h.get("source") or ""),
                "attribution": h.get("attribution", "")}
    raise ValueError("unknown source " + str(source))


def _clean(s, n=50):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\- ]+", "", str(s or ""))).strip()[:n] or "sound"


def _folder(cfg, kind, folder):
    folder = _clean(folder, 40).replace(" ", "_") if kind == "sfx" else _clean(folder, 40)
    if not folder:
        raise ValueError("choose a folder")
    d = os.path.join(music_root(cfg) if kind == "music" else sfx_root(cfg), folder)
    os.makedirs(d, exist_ok=True)
    return d


def _to_mp3(cfg, src, dst):
    """decode anything ffmpeg reads into mp3 (also proves it really is audio)"""
    r = subprocess.run([cfg["ffmpeg"], "-v", "error", "-y", "-i", src, "-vn", "-ac", "2", "-ar", "48000",
                        "-c:a", "libmp3lame", "-b:a", "192k", dst], capture_output=True, timeout=300,
                       creationflags=0x08000000 if os.name == "nt" else 0)
    if r.returncode or not os.path.isfile(dst) or os.path.getsize(dst) < 2000:
        raise ValueError("not a readable audio file: " + r.stderr.decode("utf-8", "replace")[-300:])


def fetch(ctx, kind, folder, items, allow_by=False, count=False):
    cfg, say = ctx["cfg"], ctx["say"]
    d = _folder(cfg, kind, folder)
    have = {r["page"] for r in _read_index(cfg, kind) if r.get("page")}
    got = 0
    for it in items[:40]:
        if ctx["stopped"]():
            return got if count else False
        try:
            info = _details(cfg, str(it.get("source")), str(it.get("id")))
        except Exception as e:
            say(f"   {it.get('title') or it.get('id')}: could not check it with the source ({e})")
            continue
        if not _offer(info["code"], allow_by):
            say(f"   {info['title']}: skipped - licence is {info['licence'] or info['code']} (not CC0 / PDM"
                + (" / CC BY" if allow_by else "") + ")")
            continue
        if info["page"] and info["page"] in have:
            say(f"   {info['title']}: already in the library")
            continue
        if not info["file"]:
            say(f"   {info['title']}: the source offers no file to download")
            continue
        name = f"{_clean(info['title'])}-{_clean(info['author'], 30)}-{it.get('source')}{_clean(it.get('id'), 40)}"
        dst = os.path.join(d, name + ".mp3")
        tmp = os.path.join(d, "_download_" + _clean(it.get("id"), 40))
        try:
            req = urllib.request.Request(_safe_url(info["file"]), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                left = 80 * 1024 * 1024
                while True:
                    ch = r.read(1 << 16)
                    if not ch:
                        break
                    left -= len(ch)
                    if left < 0:
                        raise ValueError("larger than 80 MB")
                    f.write(ch)
            _to_mp3(cfg, tmp, dst)
        except Exception as e:
            say(f"   {info['title']}: download failed ({e})")
            continue
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        try:
            import audio_screen
            scr = audio_screen.screen(cfg, dst, kind)
        except Exception as e:
            scr = {"verdict": "ok", "why": [f"not checked: {e}"]}
        if scr.get("verdict") == "reject":
            rej = os.path.join(_rejected_dir(cfg), kind)
            os.makedirs(rej, exist_ok=True)
            os.replace(dst, os.path.join(rej, os.path.basename(dst)))
            say(f"   {info['title']}: not added - {'; '.join(scr.get('why') or [])} (kept in {rej})")
            have.add(info["page"])
            continue
        credit = ""
        if LICENSES[info["code"]][2]:
            credit = info.get("attribution") or f"\"{info['title']}\" by {info['author']} ({info['page']}) - {info['licence']}"
        _register(cfg, kind, {"file": os.path.basename(dst), "category": os.path.basename(d), "site": info["site"],
                              "page": info["page"], "author": info["author"], "licence": info["licence"],
                              "downloaded_on": time.strftime("%Y-%m-%d %H:%M"), "used_in": "",
                              "licence_code": info["code"], "credit": credit})
        have.add(info["page"])
        got += 1
        say(f"   + {os.path.basename(dst)}  [{LICENSES[info['code']][0]}]"
            + (f"  - risky, will be treated: {'; '.join(scr.get('why') or [])}" if scr.get("verdict") == "risky" else ""))
        time.sleep(0.5)
    say(f"   {got} new file(s) in {d}; register: {_index(cfg, kind)}")
    return got if count else True


def _rejected_dir(cfg):
    import experimental_workspace
    return os.path.join(experimental_workspace.root(cfg), (cfg.get("layout") or {}).get("experimental_library", "Library"),
                        "_rejected_downloads")


def topup_music(ctx, series="", per=2, allow_by=False):
    """more background music for the mood folders a series uses (Freesound, CC0; screened before it is kept)"""
    import tools
    cfg, say = ctx["cfg"], ctx["say"]
    folders = sorted({f for sid, g in tools.GENRES.items() if not series or sid == series for f in g.get("music") or []})
    total = 0
    for folder in folders:
        if ctx["stopped"]():
            return False
        q = MOOD_QUERIES.get(folder, folder.lower() + " background music")
        say(f"   {folder}: searching Freesound for '{q}'")
        got, page = 0, 1
        while got < int(per) and page <= 3:
            try:
                res = [r for r in search(cfg, {"kind": "music", "source": "freesound", "query": q, "allow_by": allow_by,
                                               "page": page})["results"] if not r["have"]]
            except Exception as e:
                say(f"   {folder}: search failed ({e})")
                break
            if not res:
                break
            for r in res:
                if got >= int(per):
                    break
                said = " ".join([str(r.get("title") or "")] + [str(t) for t in r.get("tags") or []]).lower()
                bad = [w for w in NOT_MUSIC_WORDS if re.search(r"\b" + w + r"s?\b", said)]
                if bad:
                    say(f"   {r.get('title')}: not added - its title/tags say '{bad[0]}' (a voice or an effect, not music)")
                    continue
                got += fetch(ctx, "music", folder, [r], allow_by, count=True) or 0
            page += 1
        total += got
    say(f"   {total} new track(s) for {series or 'every series'}")
    return True


def topup(ctx, series="", per=3):
    """more CC0 effects for every category a series uses (Freesound), on top of what is there"""
    import sfx_lib
    import story
    cfg, say = ctx["cfg"], ctx["say"]
    key = story.secret("freesound_key")
    if not key:
        say("   add your free Freesound API key in Settings first")
        return False
    cats = sorted({c for sid, (cs, _) in sfx_lib.SERIES.items() if not series or sid == series for c in cs})
    want = {c: len(glob.glob(os.path.join(sfx_lib.root(cfg), c, "*.mp3"))) + int(per) for c in cats}
    n = 0
    for c in cats:
        n += sfx_lib.fetch(cfg, key, [c], say, per=want[c])
    say(f"   {n} new CC0 effect(s) for {series or 'all series'}")
    return True


# ------------------------------------------------------------------ importing your own files
def import_file(cfg, body):
    import base64
    kind = "music" if body.get("kind") == "music" else "sfx"
    code = str(body.get("licence_code") or "")
    if code not in LICENSES:
        raise ValueError("choose the licence of this file (choose 'Not sure' if you do not know)")
    data = str(body.get("data") or "")
    if "," not in data or len(data) > 110_000_000:
        raise ValueError("the file is missing or larger than about 80 MB")
    raw = base64.b64decode(data.split(",", 1)[1])
    d = _folder(cfg, kind, body.get("folder"))
    stem = _clean(os.path.splitext(str(body.get("name") or "track"))[0], 70)
    ext = os.path.splitext(str(body.get("name") or ""))[1].lower()
    dst = os.path.join(d, stem + (".wav" if kind == "sfx" and ext == ".wav" else ".mp3"))
    n = 2
    while os.path.exists(dst):
        dst = os.path.join(d, f"{stem} ({n})" + os.path.splitext(dst)[1])
        n += 1
    tmp = dst + ".upload" + (ext or "")
    with open(tmp, "wb") as f:
        f.write(raw)
    try:
        if dst.endswith(".wav") or ext == ".mp3":
            os.replace(tmp, dst)
            probe = subprocess.run([cfg["ffmpeg"], "-v", "error", "-i", dst, "-f", "null", "-t", "2", "-"],
                                   capture_output=True, timeout=60, creationflags=0x08000000 if os.name == "nt" else 0)
            if probe.returncode:
                os.replace(dst, tmp)
                raise ValueError("not a readable audio file")
        else:
            _to_mp3(cfg, tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    _register(cfg, kind, {"file": os.path.basename(dst), "category": os.path.basename(d),
                          "site": str(body.get("site") or "imported")[:80], "page": str(body.get("page") or "")[:300],
                          "author": str(body.get("author") or "")[:80], "licence": LICENSES[code][0],
                          "downloaded_on": time.strftime("%Y-%m-%d %H:%M"), "used_in": "", "licence_code": code,
                          "credit": str(body.get("credit") or "")[:400]})
    warn = LICENSES[code][3] if not LICENSES[code][1] or LICENSES[code][2] else ""
    try:
        import audio_screen
        scr = audio_screen.screen(cfg, dst, kind)
        if scr["verdict"] != "ok":
            warn = (warn + "; " if warn else "") + scr["verdict"] + ": " + "; ".join(scr["why"]) + \
                (" - never picked unless you allow it" if scr["verdict"] == "reject" else " - a treated copy is used")
    except Exception:
        pass
    return {"ok": True, "file": dst, "warning": warn}


def set_licence(cfg, body):
    kind = "music" if body.get("kind") == "music" else "sfx"
    f = str(body.get("file") or "")
    root = music_root(cfg) if kind == "music" else sfx_root(cfg)
    if not os.path.normcase(os.path.abspath(f)).startswith(os.path.normcase(os.path.abspath(root)) + os.sep) or not os.path.isfile(f):
        raise ValueError("not a file of this library")
    code = str(body.get("licence_code") or "")
    if code not in LICENSES:
        raise ValueError("unknown licence")
    kv = {"licence_code": code, "licence": LICENSES[code][0]}
    for k in ("site", "page", "author", "credit"):
        if body.get(k) is not None:
            kv[k] = str(body.get(k))[:400]
    _set_row(cfg, kind, f, **kv)
    return {"ok": True}


# ------------------------------------------------------------------ routes (server.py dispatches /api/sounds*)
def handle_get(cfg, p, q):
    if p == "/api/sounds":
        return 200, listing(cfg)
    return 404, {"error": "unknown"}


def handle_post(cfg, p, body, jobs=None):
    import experimental_workspace
    if p == "/api/sounds/search":
        return 200, search(cfg, body)
    if p == "/api/sounds/import":
        return 200, import_file(cfg, body)
    if p == "/api/sounds/licence":
        return 200, set_licence(cfg, body)
    if p == "/api/sounds/override":
        import audio_screen
        kind = "music" if body.get("kind") == "music" else "sfx"
        f = str(body.get("file") or "")
        root = music_root(cfg) if kind == "music" else sfx_root(cfg)
        if not os.path.normcase(os.path.abspath(f)).startswith(os.path.normcase(os.path.abspath(root)) + os.sep):
            raise ValueError("not a file of this library")
        return 200, audio_screen.set_override(cfg, f, str(body.get("value") or ""))
    if p == "/api/sounds/check":
        import audio_screen
        kind = "music" if body.get("kind") == "music" else "sfx"
        return 200, audio_screen.screen(cfg, str(body.get("file") or ""), kind, force=True)
    if p in ("/api/sounds/fetch", "/api/sounds/topup", "/api/sounds/screen"):
        base = experimental_workspace.initialize(cfg)
        L = cfg.get("layout") or {}
        meta = {"root": base, "series": "", "episode_dir": "", "layout": L,
                "media_dir": os.path.join(base, L.get("experimental_library", "Library")),
                "sukoonvoice_media": os.path.join(base, L.get("experimental_library", "Library"), "Narration")}
        if p == "/api/sounds/fetch":
            kind = "music" if body.get("kind") == "music" else "sfx"
            items = [{"source": str(i.get("source")), "id": str(i.get("id")), "title": str(i.get("title") or "")[:80]}
                     for i in (body.get("items") or []) if isinstance(i, dict) and i.get("id")]
            if not items:
                raise ValueError("tick the sounds you want first")
            _folder(cfg, kind, body.get("folder"))
            steps = [{"fn": "exp_sound_fetch", "label": f"download {len(items)} {'track' if kind == 'music' else 'sound'}(s), licence checked with the source",
                      "args": {"kind": kind, "folder": str(body.get("folder")), "items": items, "allow_by": bool(body.get("allow_by"))}}]
            title = f"Get {len(items)} {'music track' if kind == 'music' else 'sound effect'}(s) → {body.get('folder')}"
        elif p == "/api/sounds/screen":
            steps = [{"fn": "exp_sound_screen", "label": "index the library: every music track and effect measured (voice band, beat, brightness) and judged for its mood folder",
                      "args": {}}]
            title = "Index the sound library (speech band, piercing highs, mood fit)"
        elif body.get("kind") == "music":
            series = str(body.get("series") or "")
            steps = [{"fn": "exp_music_topup", "label": "more CC0 background music from Freesound for " + (series or "every series"),
                      "args": {"series": series, "per": max(1, min(10, int(body.get("per") or 2))),
                               "allow_by": bool(body.get("allow_by"))}}]
            title = "More music: " + (series or "all series")
        else:
            series = str(body.get("series") or "")
            steps = [{"fn": "exp_sound_topup", "label": "more CC0 sound effects from Freesound for " + (series or "every series"),
                      "args": {"series": series, "per": max(1, min(10, int(body.get("per") or 3)))}}]
            title = "More sound effects: " + (series or "all series")
        # kind "sfx_fetch" runs in the light lane, beside a running video job
        j = jobs.add("sfx_fetch", title, steps, [music_root(cfg) if "music" in title.lower() else sfx_root(cfg)],
                     {"_experimental_workspace": meta})
        return 200, {"ok": True, "id": j["id"]}
    return 404, {"error": "unknown"}
