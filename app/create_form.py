"""Experimental Create page: one settings form for every series (Sukoon and the six genres), and the build queue.

DEFAULTS holds each series' starting values for that form. It is a Tinker table ("create_form.DEFAULTS"), so it is
edited in Tinker > Series settings (or with "Save as defaults" on the Create page), stored in the Experimental
Tinker settings.json, and can be installed into Working like the other tables.

The build queue is the hub's existing graphics-card line: jobs run one at a time in queue order. Moving a job only
changes its "queue_pos" (kept in the job record, so the order survives a hub restart). A job cut short by a restart
always carries on first.
"""
SERIES = ["sukoon", "bhay", "kisse", "rahasya", "romanch", "kareeb", "gyaan"]

_COMMON = {"language": "hi", "source": "ai", "speech_min": 15, "total_min": 0, "fit": True, "writer": "auto",
           "level": "standard", "voice": "auto", "acoustic": True, "takes": 3, "ex": 0.5, "pause": 0.7,
           "dramatic": True, "emotions": True, "music": "", "tail": "1min", "music_db": 18, "shape": False,
           "reels": 2, "reel_len": 30, "scenes": 0, "best_pictures": False, "by_scene": False, "art_choices": 3, "art_style": "",
           "typo": "mukhtalif", "font": "", "en_speaker": "Ryan", "script_lang": "hi", "category": ""}


def _series(**over):
    d = dict(_COMMON)
    d.update(over)
    return d


DEFAULTS = {
    # the values the two old forms started with
    "sukoon": _series(source="mine", speech_min=12, total_min=30, voice="v3", ex=0.35, pause=1.0, dramatic=False,
                      emotions=False, tail="usual", shape=True, reels=3),
    "bhay": _series(ex=0.5, pause=0.75),
    "kisse": _series(ex=0.55, pause=0.65),
    "rahasya": _series(ex=0.5, pause=0.6),
    "romanch": _series(ex=0.5, pause=0.75),
    "kareeb": _series(ex=0.3, pause=1.0),
    "gyaan": _series(ex=0.5, pause=0.55),
}


def effective(cfg):
    """Working value (code + anything installed) with the Experimental Tinker edits on top"""
    import tinker
    if not tinker._ORIG:
        tinker.install(cfg)
    working = tinker._working_value("tables", "create_form.DEFAULTS")
    over = tinker.exp_settings(cfg)["tables"].get("create_form.DEFAULTS") or {}
    out = tinker.merge(working, over)
    for s in SERIES:                       # a series missing from an edited table still gets every setting
        out[s] = tinker.merge(DEFAULTS.get(s, _COMMON), out.get(s) or {})
    return out


def save_series(cfg, series, values):
    """'Save as defaults' on the Create page: this series' values into the Experimental Tinker table"""
    import tinker
    if series not in SERIES:
        raise ValueError("unknown series " + str(series))
    if not isinstance(values, dict):
        raise ValueError("values must be an object")
    table = effective(cfg)
    known = set(_COMMON)
    table[series] = {**table[series], **{k: v for k, v in values.items() if k in known}}
    return tinker.save(cfg, "tables", "create_form.DEFAULTS", table)


# ------------------------------------------------------------------ build queue
def _gpu_line(JOBS):
    return [j for j in JOBS.jobs if j.get("status") == "queued" and not JOBS._is_light_job(j)]


def _row(j, pos=None):
    p = j.get("params") or {}
    return {"id": j["id"], "title": j.get("title", ""), "kind": j.get("kind", ""), "status": j.get("status"),
            "series": p.get("genre") or ("sukoon" if j.get("kind") == "episode" else ""),
            "experimental": bool(p.get("_experimental_workspace")), "created": j.get("created"),
            "resumes": bool(j.get("step")), "step": j.get("step", 0), "n_steps": len(j.get("steps") or []),
            "step_label": j.get("step_label", ""), "position": pos}


def queue(JOBS):
    with JOBS.lock:
        line = sorted(_gpu_line(JOBS), key=JOBS.queue_key)
        running = [j for j in JOBS.jobs if j.get("status") == "running"]
        light = [j for j in JOBS.jobs if j.get("status") == "queued" and JOBS._is_light_job(j)]
        return {"running": [_row(j) for j in running],
                "queued": [_row(j, i + 1) for i, j in enumerate(line)],
                "beside": [_row(j) for j in light]}


def move(JOBS, jid, to):
    """to: up | down | next | last, or a list of job ids (the whole new order)"""
    with JOBS.lock:
        line = sorted(_gpu_line(JOBS), key=JOBS.queue_key)
        ids = [j["id"] for j in line]
        if isinstance(to, list):
            new = [i for i in to if i in ids] + [i for i in ids if i not in to]
        else:
            if jid not in ids:
                raise ValueError("this job is no longer waiting in the queue")
            i = ids.index(jid)
            new = list(ids)
            new.pop(i)
            at = {"up": max(0, i - 1), "down": min(len(new), i + 1), "next": 0, "last": len(new)}.get(to)
            if at is None:
                raise ValueError("unknown move " + str(to))
            new.insert(at, jid)
        # the same position values, handed out in the new order: new jobs (position = time added) still go last
        slots = sorted(JOBS.queue_key(j)[1] for j in line)
        by_id = {j["id"]: j for j in line}
        for jid2, pos in zip(new, slots):
            by_id[jid2]["queue_pos"] = pos
        if len(set(slots)) < len(slots):           # equal times (added in the same second): spread them
            for k, jid2 in enumerate(new):
                by_id[jid2]["queue_pos"] = slots[0] + k * 0.001
    JOBS.save()
    return queue(JOBS)


def remove(JOBS, jid):
    with JOBS.lock:
        j = next((x for x in JOBS.jobs if x["id"] == jid), None)
        if not j or j.get("status") != "queued":
            raise ValueError("only a waiting job can be taken out of the queue (stop a running one in Activity)")
        j["status"] = "stopped"
        j["note"] = "taken out of the build queue before it started (Run again in Activity puts it back)"
    JOBS.save()
    return queue(JOBS)


# ------------------------------------------------------------------ routes (narrow dispatch from server.py)
def handle_get(cfg, JOBS, p, q):
    if p == "/api/create/defaults":
        import tinker
        return 200, {"defaults": effective(cfg), "file": tinker.exp_file(cfg), "table": "create_form.DEFAULTS"}
    if p == "/api/queue":
        return 200, queue(JOBS)
    return 404, {"error": "unknown"}


def handle_post(cfg, JOBS, p, body):
    try:
        if p == "/api/create/defaults":
            return 200, save_series(cfg, body.get("series"), body.get("values") or {})
        if p == "/api/queue/move":
            return 200, move(JOBS, body.get("id"), body.get("ids") if isinstance(body.get("ids"), list) else body.get("to"))
        if p == "/api/queue/remove":
            return 200, remove(JOBS, body.get("id"))
    except ValueError as e:
        return 400, {"error": str(e)}
    return 404, {"error": "unknown"}
