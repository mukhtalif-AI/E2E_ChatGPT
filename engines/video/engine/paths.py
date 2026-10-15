"""HubGPT Video engine paths; machine locations are owned by root config.json."""
import os
import shutil
import sys

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE) if os.path.basename(ENGINE).lower() == "engine" else ENGINE
HUB_ROOT = os.path.dirname(os.path.dirname(ROOT))
sys.path.insert(0, os.path.join(HUB_ROOT, "app"))
from path_config import load_config

HUB = load_config()
DEFAULTS = {
    "venv_python": HUB.get("studio_python", ""),
    "gpu_python": HUB.get("gpu_python", ""),
    "ffmpeg": HUB.get("ffmpeg", ""),
    "episodes_dir": HUB.get("episodes_dir", ""),
    "images_dir": HUB.get("artwork_dir", ""),
    "hf_models": HUB.get("hf_models", ""),
    "programs_dir": HUB.get("programs_dir", ""),
    "ollama_url": HUB.get("ollama_url", "http://127.0.0.1:11434"),
    "llm": HUB.get("llm", ""),
    "story_model": HUB.get("story_model", HUB.get("llm", "")),
    "prompt_model": HUB.get("prompt_model", HUB.get("llm", "")),
    "vision_llm": HUB.get("vision_llm", ""),
}
ENV = {"gpu_python": "SV_GPU_PYTHON", "episodes_dir": "EPISODES", "images_dir": "SUKOON_IMAGES",
       "hf_models": "SUKOON_HF_MODELS", "ffmpeg": "SUKOON_FFMPEG", "venv_python": "SUKOON_PY"}


def load():
    cfg = dict(DEFAULTS)
    # Runtime LLM selection remains a user setting, not a duplicated path config.
    try:
        import json
        settings_path = os.path.join(HUB["media_dir"], "hub_settings.json")
        with open(settings_path, encoding="utf-8") as f:
            settings = json.load(f)
        for key in ("llm", "vision_llm", "prompt_model", "story_model"):
            cfg[key] = settings.get(key) or cfg.get(key)
    except (OSError, ValueError, KeyError):
        pass
    for key, env in ENV.items():
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


CFG = load()
GPU_PYTHON = CFG["gpu_python"]
VENV_PYTHON = CFG["venv_python"]
EPISODES_DIR = CFG["episodes_dir"]
IMAGES_DIR = CFG["images_dir"]
HF_CACHE = CFG["hf_models"]
_LAYOUT = HUB.get("layout") or {}
VIDEO_CACHE_ROOT = os.path.join(HUB["temporary_dir"], "video")
VIDEO_WORK_DIR = os.path.join(VIDEO_CACHE_ROOT, _LAYOUT.get("video_work", "video_work"))
VIDEO_LOOP_DIR = os.path.join(VIDEO_CACHE_ROOT, _LAYOUT.get("video_loops", "video_loops"))
VIDEO_OUTPUT_DIR = os.path.join(HUB["videos_dir"], _LAYOUT.get("video_standalone_output", "video_output"))
EPISODE_OUTPUT_NAME = HUB.get("episode_output_name", "Output")
EPISODE_WORK_NAME = HUB.get("episode_work_name", "_work")
EPISODE_REELS_NAME = _LAYOUT.get("episode_reels", "reels")
EPISODE_CLIPS_NAME = _LAYOUT.get("episode_clips", "clips")
EPISODE_MOTION_CACHE_NAME = _LAYOUT.get("episode_motion_cache", "_motions")
FONTS_DIR = os.path.join(ROOT, "assets", "fonts")
USER_FONTS = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")
WIN_FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def ffmpeg():
    for candidate in (CFG.get("ffmpeg"), shutil.which("ffmpeg")):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def ffprobe():
    binary = ffmpeg()
    if not binary:
        return shutil.which("ffprobe")
    probe = os.path.join(os.path.dirname(binary), "ffprobe.exe" if binary.lower().endswith(".exe") else "ffprobe")
    return probe if os.path.exists(probe) else shutil.which("ffprobe")
