"""sfx_lib.py - the sound effects library, sorted into folders by what the sound is.

D:\\Audio asset\\SFX\\<category>\\<real name>-<author>-<id>.mp3     (your own older files in SFX\\ stay usable)
Source: Freesound (freesound.org API, your free key in Settings), ONLY licence "Creative Commons 0"
(public domain: commercial use, no credit needed). Every file is written to SFX\\SFX_INDEX.csv with its page, author,
licence, date - and `used_in` is filled when a video uses it (in case of a claim). Nothing is ever deleted.
"""
import csv, glob, json, os, re, time, urllib.parse, urllib.request

# category -> (search words, min s, max s, kind)   kind: amb = loops under a scene, cue = one moment in a sentence
CATS = {
    "door_knock": ("door knock", 1, 8, "cue"), "door_creak": ("door creak open", 1, 8, "cue"),
    "door_slam": ("door slam", 0.5, 5, "cue"), "footsteps": ("footsteps wooden floor", 2, 12, "cue"),
    "footsteps_outside": ("footsteps gravel", 2, 12, "cue"), "whisper": ("creepy whisper", 1, 10, "cue"),
    "scream_far": ("distant scream", 1, 8, "cue"), "breathing": ("heavy breathing", 2, 10, "cue"),
    "heartbeat": ("heartbeat", 3, 20, "amb"), "glass_break": ("glass break", 0.5, 5, "cue"),
    "clock_tick": ("clock ticking", 4, 30, "amb"), "dog_howl": ("dog howling night", 2, 12, "cue"),
    "crow": ("crow caw", 1, 8, "cue"), "chains": ("chains rattle", 1, 8, "cue"), "bell_temple": ("temple bell", 1, 10, "cue"),
    "phone_ring": ("old phone ring", 1, 10, "cue"), "phone_vibrate": ("phone vibrate", 1, 6, "cue"),
    "car_pass": ("car passing", 2, 10, "cue"), "car_door": ("car door close", 0.5, 4, "cue"),
    "siren_far": ("police siren distant", 3, 15, "cue"), "gunshot_far": ("distant gunshot", 0.5, 5, "cue"),
    "typing": ("keyboard typing", 2, 12, "cue"), "paper": ("paper rustle", 1, 6, "cue"),
    "train": ("train passing", 4, 20, "cue"), "baby_cry": ("baby crying", 2, 10, "cue"), "laugh_child": ("child laughing", 1, 6, "cue"),
    "thunder": ("thunder", 2, 15, "cue"), "rain": ("rain ambience", 10, 60, "amb"), "wind": ("wind howling", 10, 60, "amb"),
    "night_crickets": ("night crickets", 10, 60, "amb"), "forest_night": ("forest night ambience", 10, 60, "amb"),
    "fan_hum": ("ceiling fan hum", 10, 60, "amb"), "tubelight": ("fluorescent light buzz", 5, 60, "amb"),
    "village": ("indian village ambience", 10, 60, "amb"), "market": ("indian market crowd", 10, 60, "amb"),
    "birds_morning": ("morning birds", 10, 60, "amb"), "kitchen": ("kitchen cooking sounds", 5, 40, "cue"),
    "water_drip": ("water drip", 4, 30, "amb"), "fire_crackle": ("fire crackling", 10, 60, "amb"),
    "sea": ("sea waves", 10, 60, "amb"), "hospital": ("hospital ambience", 10, 60, "amb"),
    "office": ("office ambience", 10, 60, "amb"), "cafe": ("cafe ambience", 10, 60, "amb"),
    "kiss_soft": ("soft cloth movement", 1, 6, "cue"), "pages": ("book pages turning", 1, 6, "cue"),
    "tapping_soft": ("soft tapping asmr", 3, 20, "cue"), "brush_soft": ("soft brushing asmr", 3, 20, "cue"),
    "riser": ("cinematic riser", 2, 10, "cue"), "braam": ("cinematic braam", 2, 10, "cue"),
    "whoosh": ("whoosh transition", 0.5, 3, "cue"), "impact": ("cinematic impact hit", 1, 6, "cue"),
}
# which sounds each series wants in its library, and how busy its sound is (cues per minute of story at most)
SERIES = {
    "bhay": (["door_knock", "door_creak", "door_slam", "footsteps", "footsteps_outside", "whisper", "scream_far", "breathing",
              "heartbeat", "glass_break", "clock_tick", "dog_howl", "crow", "chains", "bell_temple", "thunder", "rain",
              "wind", "night_crickets", "forest_night", "fan_hum", "tubelight", "water_drip", "riser", "braam", "impact"], 4),
    "rahasya": (["door_knock", "door_slam", "footsteps", "phone_ring", "phone_vibrate", "car_pass", "car_door", "siren_far",
                 "gunshot_far", "typing", "paper", "clock_tick", "rain", "thunder", "office", "heartbeat", "riser",
                 "impact", "whoosh"], 3),
    "kisse": (["door_knock", "footsteps", "train", "baby_cry", "laugh_child", "kitchen", "bell_temple", "rain", "wind",
               "village", "market", "birds_morning", "fire_crackle", "hospital", "cafe", "phone_ring", "pages"], 2.5),
    "romanch": (["rain", "cafe", "sea", "night_crickets", "footsteps", "kiss_soft", "fire_crackle", "phone_vibrate",
                 "train"], 1.2),
    "kareeb": (["rain", "fire_crackle", "tapping_soft", "brush_soft", "pages", "kiss_soft", "sea", "clock_tick"], 1),
    "gyaan": (["pages", "whoosh", "typing", "clock_tick"], 0.5),
}
KEEP = 3


def root(cfg):
    return os.path.join(os.path.dirname(cfg.get("music_dir") or r"D:\Audio asset\BGM"), "SFX")


def library(cfg):
    """{name: path}; files in a category folder are named '<category>: <words>' so the AI sees what they are"""
    d, out = root(cfg), {}
    for f in glob.glob(os.path.join(d, "**", "*.mp3"), recursive=True) + glob.glob(os.path.join(d, "**", "*.wav"), recursive=True):
        if os.sep + "_old" + os.sep in f:
            continue
        k = re.sub(r"^(freesound_community|dragon-studio|brvhrtz|bryansantosbreton|dreamwriter07|emg5991)-", "",
                   os.path.splitext(os.path.basename(f))[0].lower())
        k = re.sub(r"-\d{4,}$", "", k).replace("-", " ").replace("_", " ").strip()
        cat = os.path.basename(os.path.dirname(f))
        if os.path.dirname(f) != d:
            k = f"{cat}: {k[:40]}"
        out[k] = f
    return out


def kind_of(cfg, path):
    cat = os.path.basename(os.path.dirname(path))
    if cat in CATS:
        return CATS[cat][3]
    n = os.path.basename(path).lower()
    return "amb" if any(w in n for w in ("wind", "rain", "cricket", "drips", "fan", "tubelight", "drone", "pads", "water",
                                           "heartbeat", "ambience", "ambient")) else "cue"


def missing(cfg, series=None):
    want = set()
    for sid, (cats, _) in SERIES.items():
        if not series or sid == series:
            want |= set(cats)
    d = root(cfg)
    return sorted(c for c in want if len(glob.glob(os.path.join(d, c, "*.mp3"))) < KEEP)


def _index(d):
    return os.path.join(d, "SFX_INDEX.csv")


def _register(d, row):
    p = _index(d)
    new = not os.path.exists(p)
    with open(p, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["file", "category", "site", "page", "author", "licence", "downloaded_on", "used_in"])
        w.writerow(row)


def mark_used(cfg, files, video):
    """the register says where each sound is used"""
    p = _index(root(cfg))
    if not files or not os.path.exists(p):
        return
    rows = list(csv.reader(open(p, encoding="utf-8-sig")))
    names = {os.path.basename(f) for f in files}
    for r in rows[1:]:
        if len(r) >= 8 and r[0] in names and video not in r[7]:
            r[7] = (r[7] + "; " if r[7] else "") + video
    with open(p + ".part", "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)
    os.replace(p + ".part", p)


def fetch(cfg, key, cats, say=print, per=KEEP):
    """CC0 sounds from Freesound into SFX\\<category>\\ (previews: high-quality mp3), registered"""
    d, got = root(cfg), 0
    have = set()
    if os.path.exists(_index(d)):
        have = {r[3] for r in csv.reader(open(_index(d), encoding="utf-8-sig")) if len(r) > 3}
    for c in cats:
        q, lo, hi, _ = CATS[c]
        cd = os.path.join(d, c)
        os.makedirs(cd, exist_ok=True)
        need = per - len(glob.glob(os.path.join(cd, "*.mp3")))
        if need <= 0:
            continue
        url = "https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode({
            "query": q, "filter": f'license:"Creative Commons 0" duration:[{lo} TO {hi}]', "sort": "rating_desc",
            "fields": "id,name,username,license,previews,url,duration", "page_size": 12, "token": key})
        try:
            res = json.loads(urllib.request.urlopen(url, timeout=30).read()).get("results", [])
        except Exception as e:
            say(f"   {c}: search failed ({e})"); continue
        n = 0
        for h in res:
            if n >= need:
                break
            if "creativecommons.org/publicdomain/zero" not in str(h.get("license", "")) or h.get("url") in have:
                continue
            src = (h.get("previews") or {}).get("preview-hq-mp3")
            if not src:
                continue
            nm = re.sub(r"[^\w\- ]+", "", os.path.splitext(h.get("name", "sound"))[0]).strip()[:50] or "sound"
            f = os.path.join(cd, f"{nm}-{h.get('username', '')}-{h['id']}.mp3")
            try:
                data = urllib.request.urlopen(src, timeout=60).read()
            except Exception as e:
                say(f"   {c}: download failed ({e})"); continue
            open(f + ".part", "wb").write(data)
            os.replace(f + ".part", f)
            _register(d, [os.path.basename(f), c, "Freesound", h.get("url", ""), h.get("username", ""),
                          "Creative Commons 0 (public domain, no credit needed)", time.strftime("%Y-%m-%d %H:%M"), ""])
            n += 1
            got += 1
        say(f"   {c}: +{n}")
        time.sleep(1.0)
    return got
