"""config.py - every setting of the English voice engine in one place (config.json next to run.py).
The folder is self-contained: its own libraries in libs\\common + libs\\<engine>, models in the shared
hf_models folder, temp files on the media drive. Copy the folder anywhere and run setup.bat."""
import json, os

PKG = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PKG)
LIBS = os.path.join(ROOT, "libs")
CACHE = os.path.join(ROOT, ".cache")
CONFIG_FILE = os.path.join(ROOT, "config.json")

DEFAULTS = {
    "hf_models": r"D:\AI\hf_models",
    "tmp_dir": r"D:\MukhtalifHub\_tmp",
    "ffmpeg": "ffmpeg",
    "engine": "qwen",                 # qwen (batches, proven) | qwen_fast (CUDA graphs)
    "speaker": "Ryan",                # Qwen3-TTS built-in English voices: Ryan, Aiden
    "instruct": ("Speak very slowly, softly and warmly, like a calm friend telling a bedtime story late at night. "
                 "Gentle, intimate and unhurried, a low relaxed voice, soft endings, never dramatic."),
    "seed": 7,
    "batch": 6,
    "takes": 3,                       # at most this many tries per line when a line comes out too short / too long
    "loudness_lufs": -18,
    "pauses": {"comma": 0.35, "line": 0.9, "sentence": 1.0, "dots": 1.2, "marker": 1.6, "paragraph": 2.2},
}


def load():
    c = json.loads(json.dumps(DEFAULTS))
    try:
        with open(CONFIG_FILE, encoding="utf-8-sig") as f:
            for k, v in json.load(f).items():
                if k.startswith("_") or v in (None, ""):
                    continue
                if isinstance(v, dict) and isinstance(c.get(k), dict):
                    c[k].update(v)
                else:
                    c[k] = v
    except (OSError, ValueError):
        pass
    return c


C = load()


def use_libs(engine):
    """private libraries first on the path (ComfyUI's Python ignores PYTHONPATH because of its ._pth file)"""
    import sys
    for p in reversed([os.path.join(LIBS, engine + "_pin"), os.path.join(LIBS, engine), os.path.join(LIBS, "common"), ROOT]):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


def set_env():
    import tempfile
    os.makedirs(C["tmp_dir"], exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = C["tmp_dir"]
    tempfile.tempdir = C["tmp_dir"]
    os.environ["HF_HOME"] = C["hf_models"]
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ["PIP_CACHE_DIR"] = os.path.join(CACHE, "pip")
    os.environ.setdefault("TORCH_HOME", os.path.join(CACHE, "torch"))
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
