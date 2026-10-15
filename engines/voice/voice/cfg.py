"""HubGPT Voice settings. Paths come from the HubGPT root config.json; SV_* env vars override them."""
import json, os, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(ROOT, "config.json")
LIBS = os.path.join(ROOT, "libs", "cb")            # Chatterbox + its small libraries (never a 2nd PyTorch)
CACHE = os.path.join(ROOT, ".cache")               # pip / torch caches stay on E:, never C:


def load():
    with open(CONFIG_FILE, encoding="utf-8-sig") as f:
        c = json.load(f)
    hub_root = os.path.dirname(os.path.dirname(ROOT))
    with open(os.path.join(hub_root, "config.json"), encoding="utf-8-sig") as f:
        hub = json.load(f)
    paths = hub.get("paths") or {}
    layout = hub.get("layout") or {}
    c.update({
        "media_dir": paths.get("voice_data_root", c.get("media_dir", "")),
        "model_dir": paths.get("voice_models", os.path.join(ROOT, "models")),
        "model_backup_dir": paths.get("voice_model_backup", c.get("model_backup_dir", "")),
        "hf_models": paths.get("hf_models", c.get("hf_models", "")),
        "shared_pythons": [paths.get("gpu_python", "")],
        "ffmpeg": paths.get("ffmpeg", c.get("ffmpeg", "")),
        "episodes_dir": paths.get("episodes_root", ""),
        "legacy_episodes_dir": paths.get("voice_legacy_episodes_root", ""),
        "import_transcripts_from": (paths.get("voice_import_roots") or [""])[0],
        "legacy_import_transcripts_from": (paths.get("voice_import_roots") or ["", ""])[1],
    })
    if c.get("model_dir") and not os.path.isabs(c["model_dir"]):
        c["model_dir"] = os.path.abspath(os.path.join(hub_root, c["model_dir"]))
    c.setdefault("recordings", {})["folders"] = paths.get("voice_recording_roots", c.get("recordings", {}).get("folders", []))
    c.setdefault("speak", {})["music"] = paths.get("voice_music_default", c.get("speak", {}).get("music", ""))
    c["_hub_layout"] = layout
    c["_hub_paths"] = paths
    for k, e in (("media_dir", "SV_MEDIA"), ("hf_models", "SV_HF_MODELS"), ("ffmpeg", "SV_FFMPEG"),
                 ("model_dir", "SV_MODEL_DIR"), ("model_backup_dir", "SV_MODEL_BACKUP_DIR"),
                 ("episodes_dir", "SV_EPISODES"), ("import_transcripts_from", "SV_IMPORT")):
        if os.environ.get(e):
            c[k] = os.environ[e]
    return c


C = load()
LAYOUT = C.get("_hub_layout", {})
MEDIA = C["media_dir"]
DATASET = os.path.join(MEDIA, LAYOUT.get("voice_dataset", "dataset"))
CLIPS = os.path.join(DATASET, LAYOUT.get("voice_clips", "clips"))
REFS = os.path.join(DATASET, LAYOUT.get("voice_references", "refs"))
META = os.path.join(DATASET, "metadata.jsonl")
MODEL_DIR = C.get("model_dir") or os.path.join(ROOT, "models")
if not os.path.isabs(MODEL_DIR):
    MODEL_DIR = os.path.abspath(os.path.join(ROOT, MODEL_DIR))
MODEL_BACKUP_DIR = C.get("model_backup_dir") or ""
TRAINING = MODEL_DIR
TESTS = os.path.join(MEDIA, LAYOUT.get("voice_tests", "tests"))
PROJECTS = os.path.join(DATASET, LAYOUT.get("voice_projects", "projects"))
NARRATIONS = os.environ.get("SV_NARRATIONS") or C["_hub_paths"].get("narrations_root", os.path.join(MEDIA, LAYOUT.get("narrations", "narrations")))
TMP = os.path.join(MEDIA, LAYOUT.get("temporary", "_tmp"))


def ffmpeg():
    for c in (C.get("ffmpeg"), shutil.which("ffmpeg")):
        if c and os.path.exists(c):
            return c
    raise SystemExit("ffmpeg not found - set \"ffmpeg\" in config.json")


def read_meta():
    if not os.path.exists(META):
        return []
    with open(META, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def runs():
    """training runs, newest first: [(name, folder)]"""
    if not os.path.isdir(TRAINING):
        return []
    r = [(n, os.path.join(TRAINING, n)) for n in os.listdir(TRAINING)
         if os.path.isdir(os.path.join(TRAINING, n)) and not n.startswith("_")]
    return sorted(r, key=lambda x: os.path.getmtime(x[1]), reverse=True)


def best_checkpoint():
    for n, d in runs():
        if n.startswith("s3_") or "_smoke" in n or "_series_" in n:   # a series' own voice is only used when asked
            continue
        p = os.path.join(d, "best.pt")
        if os.path.exists(p):
            return p
    return None
