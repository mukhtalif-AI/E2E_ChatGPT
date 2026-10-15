"""Automatic story and narration pacing index for HubGPT.

The scores are explainable rhythm indicators, not a claim that a model can
judge whether a performance is artistically good. No audio tokens are reused.
"""
import json
import math
import os
import re
import statistics
import time


GENRE_NAMES = {
    "sukoon": "Sukoon", "sukoon_en": "Sukoon English", "bhay": "Bhay",
    "horror": "Bhay", "rahasya": "Rahasya", "kisse": "Kisse",
    "romanch": "Romanch", "kareeb": "Kareeb", "gyaan": "Gyaan",
}
_REFERENCE_CACHE = {"stamp": None, "items": {}}


def _read(path, fallback=None):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return fallback


def _words(text):
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)?", str(text or ""), re.UNICODE)


def _cv(values):
    values = [float(x) for x in values if x is not None]
    if len(values) < 2:
        return 0.0
    mean = statistics.mean(values)
    return statistics.pstdev(values) / mean if mean > 1e-6 else 0.0


def _script_features(text):
    lines = [re.sub(r"\[[^\]]+\]", "", x).strip() for x in str(text or "").splitlines()]
    lines = [x for x in lines if x and x not in {"(...)" , "⏸"}]
    counts = [len(_words(x)) for x in lines]
    words = sum(counts)
    terms = len(re.findall(r"[।.!?]+", text or ""))
    commas = len(re.findall(r"[,،]", text or ""))
    markers = len(re.findall(r"\(\s*\.{2,}\s*\)|⏸", text or ""))
    return {
        "words": words,
        "phrases": len(counts),
        "words_per_phrase": round(statistics.mean(counts), 2) if counts else 0.0,
        "phrase_cv": round(_cv(counts), 3),
        "punctuation_per_100": round(100 * (terms + commas) / max(1, words), 2),
        "markers_per_100": round(100 * markers / max(1, words), 2),
        "short_phrase_ratio": round(sum(1 for n in counts if n <= 3) / max(1, len(counts)), 3),
    }


def _profile(text, timing):
    sf = _script_features(text)
    rows = [x for x in (timing or []) if isinstance(x, dict) and x.get("start") is not None and x.get("end") is not None]
    rows.sort(key=lambda x: float(x.get("start") or 0))
    if not rows:
        return None
    durations, gaps, comma_gaps, terminal_gaps = [], [], [], []
    audio_words = 0
    for row in rows:
        try:
            durations.append(max(0.0, float(row["end"]) - float(row["start"])))
        except (ValueError, TypeError):
            continue
        audio_words += len(_words(row.get("text", "")))
    for a, b in zip(rows, rows[1:]):
        try:
            gap = max(0.0, float(b["start"]) - float(a["end"]))
        except (ValueError, TypeError):
            continue
        gaps.append(gap)
        ending = str(a.get("text") or "").rstrip()
        if re.search(r"[,،]$", ending):
            comma_gaps.append(gap)
        elif re.search(r"[।.!?…]$|\(\s*\.{2,}\s*\)$|⏸$", ending):
            terminal_gaps.append(gap)
    elapsed = max(1.0, float(rows[-1]["end"]) - float(rows[0]["start"]))
    wpm = audio_words * 60.0 / elapsed
    long_gaps = sum(1 for x in gaps if x >= 0.7) / max(1, len(gaps))
    terminal_mean = statistics.mean(terminal_gaps) if terminal_gaps else 0.0
    comma_mean = statistics.mean(comma_gaps) if comma_gaps else 0.0
    # Low variation can sound metronomic; extreme variation can sound erratic.
    phrase_cv = _cv([len(_words(x.get("text", ""))) for x in rows])
    gap_cv = _cv(gaps)
    variation_score = max(0.0, 100.0 - abs(phrase_cv - 0.55) * 45 - abs(gap_cv - 0.85) * 22)
    alignment = 70.0 if not comma_gaps or not terminal_gaps else min(100.0, max(0.0, 65 + (terminal_mean - comma_mean) * 35))
    fragmentation = max(0.0, 100.0 - max(0.0, sf["short_phrase_ratio"] - 0.25) * 100)
    score = round(0.35 * variation_score + 0.4 * alignment + 0.25 * fragmentation)
    label = "roomy" if wpm < 82 else "measured" if wpm < 118 else "brisk"
    return {
        **sf, "wpm": round(wpm, 1), "mean_phrase_seconds": round(statistics.mean(durations), 2) if durations else 0,
        "mean_gap_seconds": round(statistics.mean(gaps), 2) if gaps else 0,
        "gap_cv": round(gap_cv, 3), "long_gap_ratio": round(long_gaps, 3),
        "terminal_gap_seconds": round(terminal_mean, 2), "comma_gap_seconds": round(comma_mean, 2),
        "rhythm_score": score, "pace_label": label,
    }


def _genre(name, narration, script):
    for path in (os.path.splitext(script)[0] + ".story.json", os.path.join(narration, "hub_series.json")):
        meta = _read(path, {}) or {}
        gid = str(meta.get("genre") or "").lower()
        if gid:
            return "bhay" if gid == "horror" else gid
    low = str(name).lower()
    for gid in ("rahasya", "kisse", "romanch", "kareeb", "gyaan", "bhay"):
        if low.startswith(GENRE_NAMES[gid].lower()) or GENRE_NAMES[gid].lower() in low:
            return gid
    if "sukoon en" in low:
        return "sukoon_en"
    if "sukoon" in low:
        return "sukoon"
    return "unknown"


def index_path(cfg):
    return os.path.join(cfg["media_dir"], "narration_pacing_index.json")


def refresh(cfg, episodes):
    """Refresh cached profiles from existing scripts and speech timing; retain choices and metadata."""
    path = index_path(cfg)
    data = _read(path, {}) or {}
    old = data.get("items", {})
    items, changed = {}, False
    for ep in episodes:
        name, script, narration = ep.get("name", ""), ep.get("script", ""), ep.get("narration", "")
        timing = os.path.join(narration, "timing.json") if narration else ""
        if not name or not script or not narration or not os.path.isfile(script) or not os.path.isfile(timing):
            continue
        try:
            signature = f"{os.path.getsize(script)}:{os.path.getmtime(script):.3f}:{os.path.getsize(timing)}:{os.path.getmtime(timing):.3f}"
        except OSError:
            continue
        prior = old.get(name, {})
        if prior.get("signature") == signature and prior.get("profile"):
            entry = dict(prior)
        else:
            text = open(script, encoding="utf-8-sig", errors="replace").read()
            tm = _read(timing, []) or []
            profile = _profile(text, tm)
            if not profile:
                continue
            entry = {"name": name, "genre": _genre(name, narration, script), "signature": signature,
                     "profile": profile, "updated": int(time.time())}
            changed = True
        entry["name"] = name
        entry["genre"] = _genre(name, narration, script) if entry.get("genre") in (None, "", "unknown") else entry["genre"]
        entry["narration"] = narration
        entry["script"] = script
        entry["video"] = ep.get("video", "")
        items[name] = entry
    # Preserve cached entries for episodes not currently represented in the library (for recovery/index stability).
    for name, entry in old.items():
        if name not in items:
            items[name] = entry
    result = {"version": 1, "items": items}
    if changed or not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".part", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        os.replace(path + ".part", path)
    for ep in episodes:
        item = items.get(ep.get("name", ""))
        if item:
            ep["pacing"] = {"genre": item.get("genre", "unknown"), **(item.get("profile") or {})}
    return result


def script_features(text):
    return _script_features(text)


def reference_episode_profiles(cfg):
    """Measure reference-story cadence from the voice dataset's transcript/duration index."""
    path = os.path.join(cfg.get("voice_dataset_dir", ""), "metadata.jsonl")
    try:
        stamp = (os.path.getsize(path), os.path.getmtime(path))
        if _REFERENCE_CACHE["stamp"] == stamp:
            return _REFERENCE_CACHE["items"]
    except OSError:
        return {}
    grouped = {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    ep, text, dur = str(row.get("episode") or "").strip(), str(row.get("text") or "").strip(), float(row.get("dur") or 0)
                except (ValueError, TypeError):
                    continue
                if not ep or not text or dur <= 0:
                    continue
                g = grouped.setdefault(ep, {"texts": [], "durations": [], "words": 0, "series": set()})
                g["texts"].append(text)
                g["durations"].append(dur)
                g["words"] += len(_words(text))
                if row.get("series"):
                    g["series"].add(str(row["series"]))
    except OSError:
        return {}
    out = {}
    for name, g in grouped.items():
        text = "\n".join(g["texts"])
        sf = _script_features(text)
        elapsed = sum(g["durations"])
        wpm = g["words"] * 60 / max(1, elapsed)
        profile = {**sf, "wpm": round(wpm, 1), "mean_phrase_seconds": round(statistics.mean(g["durations"]), 2),
                   "rhythm_score": round(max(0, min(100, 100 - abs(_cv([len(_words(x)) for x in g["texts"]]) - .55) * 45))),
                   "pace_label": "roomy" if wpm < 82 else "measured" if wpm < 118 else "brisk"}
        out[name] = {"name": name, "genre": next(iter(g["series"])) if len(g["series"]) == 1 else "unknown",
                     "profile": profile}
    _REFERENCE_CACHE.update(stamp=stamp, items=out)
    return out


def recommend(items, text, genre="unknown", minutes=0, selected="auto"):
    target = _script_features(text)
    target_wpm = target["words"] * 60 / float(minutes) if float(minutes or 0) > 0 else 0
    candidates = []
    for name, item in (items or {}).items():
        p = item.get("profile") or {}
        if not p.get("wpm"):
            continue
        if selected and selected != "auto" and name != selected:
            continue
        if selected == "auto" and genre not in ("unknown", "") and item.get("genre") not in (genre, "unknown"):
            continue
        def closeness(a, b, scale):
            return max(0.0, 1.0 - min(1.0, abs(float(a or 0) - float(b or 0)) / scale))
        tempo = closeness(p.get("wpm"), target_wpm or p.get("wpm"), 50)
        phrase = closeness(p.get("words_per_phrase"), target.get("words_per_phrase"), 6)
        variability = closeness(p.get("phrase_cv"), target.get("phrase_cv"), 0.8)
        punctuation = closeness(p.get("punctuation_per_100"), target.get("punctuation_per_100"), 8)
        match = 100 * (0.35 * tempo + 0.3 * phrase + 0.2 * variability + 0.15 * punctuation)
        genre_bonus = 8 if item.get("genre") == genre and genre not in ("unknown", "") else 0
        score = round(min(100, 0.88 * match + 0.12 * float(p.get("rhythm_score", 50)) + genre_bonus))
        candidates.append({"name": name, "genre": item.get("genre", "unknown"), "score": score,
                           "profile": p, "narration": item.get("narration", "")})
    candidates.sort(key=lambda x: (-x["score"], -float(x["profile"].get("rhythm_score", 0)), x["name"]))
    return {"target": target, "target_wpm": round(target_wpm, 1) if target_wpm else 0,
            "best": candidates[0] if candidates else None, "candidates": candidates[:8]}


def rank_voice_references(styles, items, text="", genre="unknown", minutes=0, preferred="", style_genres=None):
    """Order available prompt recordings using indexed episode cadence when we can link them.

    Unlinked prompts remain available and sort after the genre's established fallback;
    the code does not invent acoustic measurements for them.
    """
    def norm(value):
        return re.sub(r"[^0-9a-z]", "", str(value or "").lower())

    profiles = []
    style_genres = style_genres or {}
    for style in styles:
        key = norm(style)
        linked = []
        for ep_name, item in (items or {}).items():
            ep_key = norm(ep_name)
            # Avoid weak matches such as 'maa' matching unrelated titles.
            same_title = key == ep_key or (len(key) >= 4 and (ep_key.startswith(key) or key.startswith(ep_key))) \
                or (len(key) >= 6 and (key in ep_key or ep_key in key))
            if same_title and (genre in ("", "unknown") or item.get("genre") in (genre, "unknown")):
                linked.append(item)
        best = None
        if linked and text.strip():
            subset = {x.get("name", ""): x for x in linked}
            best = recommend(subset, text, genre, minutes).get("best")
        elif linked:
            linked.sort(key=lambda x: float((x.get("profile") or {}).get("rhythm_score", 0)), reverse=True)
            best = {"name": linked[0].get("name"), "score": None,
                    "profile": linked[0].get("profile") or {}}
        matched_genre = style_genres.get(style, "")
        profiles.append({"name": style, "linked": bool(linked), "score": best.get("score") if best else None,
                         "cadence": (best.get("profile") or {}).get("rhythm_score") if best else None,
                         "genre_match": bool(genre and matched_genre == genre), "genre": matched_genre,
                         "episode": best.get("name", "") if best else ""})

    # The genre page already defines a tested fallback reference (for example,
    # Burari Kand for भय). Keep it first unless measured episode pacing wins.
    profiles.sort(key=lambda x: (-int(x["genre_match"]),
                                 -(x["score"] if x["score"] is not None else (x["cadence"] if x["linked"] else -1)),
                                 0 if x["name"] == preferred else 1, x["name"].lower()))
    if preferred:
        preferred_row = next((x for x in profiles if x["name"] == preferred), None)
        if preferred_row and not preferred_row["linked"]:
            profiles.remove(preferred_row)
            profiles.insert(0, preferred_row)
    return profiles

