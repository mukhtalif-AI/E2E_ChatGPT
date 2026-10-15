"""series.py - which of your recordings belong to which series, so a voice can be trained per series
(सुकून calm, किस्से emotional, रहस्य thriller, भय horror, रोमांच, करीब ASMR, ज्ञान informational ...).

D:\\SukoonVoice\\series.json = {"series": {id: {"name": "किस्से", "about": "emotional stories"}},
                                "episodes": {"<recording name>": "<series id>"}}
Made on first use: every recording with "sukoon" in its name is सुकून; the rest waits for you (hub Settings).
Training with profile "series_<id>" learns only from that series' recordings (+ their flow clips).
"""
import json, os
from . import cfg

PATH = os.path.join(cfg.MEDIA, "series.json")
DEFAULT = {
    "sukoon": {"name": "सुकून", "about": "calm, sleep, counselling"},
    "kisse": {"name": "किस्से", "about": "emotional stories"},
    "rahasya": {"name": "रहस्य", "about": "thriller, mystery"},
    "bhay": {"name": "भय", "about": "horror"},
    "romanch": {"name": "रोमांच", "about": "romance / sensual"},
    "kareeb": {"name": "करीब", "about": "ASMR, close and soft"},
    "gyaan": {"name": "ज्ञान", "about": "informational"},
}


def load():
    d = {}
    if os.path.exists(PATH):
        try:
            d = json.load(open(PATH, encoding="utf-8"))
        except Exception:
            d = {}
    d.setdefault("series", {})
    for k, v in DEFAULT.items():
        d["series"].setdefault(k, v)
    d.setdefault("episodes", {})
    return d


def save(d):
    tmp = PATH + ".part"
    json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


def _norm(s):
    import re
    return re.sub(r"[^0-9a-z]", "", os.path.splitext(os.path.basename(str(s)))[0].lower())


def of(episode, d=None):
    """the series of a recording (names compared without spaces / case: 'Burari Kand' = 'burarikand')"""
    d = d or load()
    e = d.get("_n") or {_norm(k): v for k, v in d["episodes"].items()}
    d["_n"] = e
    s = e.get(_norm(episode))
    if s:
        return s
    return "sukoon" if "sukoon" in (episode or "").lower() else ""
