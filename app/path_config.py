"""Canonical HubGPT paths and output layout.

The root config.json owns all machine-specific paths. Older modules receive
their historical key names as derived aliases so path changes stay centralized.
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(ROOT, "config.json")

DEFAULT_LAYOUT = {
    "scripts": "scripts", "narrations": "narrations", "pictures": "pictures",
    "images": "images", "videos": "videos", "logs": "logs", "temporary": "_tmp",
    "episodes": "Episodes", "episode_output": "Output", "episode_work": "_work",
    "voice_dataset": "dataset", "voice_training": "training", "voice_tests": "tests",
    "voice_clips": "clips", "voice_references": "refs", "voice_projects": "projects",
}

PATH_ALIASES = {
    "media_dir": "hub_output_root", "narrations_dir": "narrations_root",
    "sukoonvoice_dir": "voice_engine", "voice_models_dir": "voice_models",
    "voice_model_backup_dir": "voice_model_backup", "sukoonvoice_media": "voice_data_root",
    "studio_dir": "video_engine", "studio_python": "studio_python",
    "gpu_python": "gpu_python", "base_python": "base_python",
    "episodes_dir": "episodes_root", "comfy_dir": "comfyui_root",
    "comfy_url": "comfyui_url",
    "ollama_url": "ollama_url", "ollama_exe": "ollama_exe",
    "ollama_models": "ollama_models", "ffmpeg": "ffmpeg",
    "artwork_dir": "artwork_root", "music_dir": "music_root", "audacity_dir": "audacity_root",
    "hf_models": "hf_models", "elements_dir": "elements_root", "programs_dir": "programs_root",
    "experimental_root": "experimental_root",
    "voice_library_source": "voice_library_source",
    "hub_logs_root": "hub_logs_root",
}


def load_config():
    with open(CONFIG_FILE, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    paths = cfg.get("paths") or {}
    layout = dict(DEFAULT_LAYOUT)
    layout.update(cfg.get("layout") or {})
    out = dict(cfg)
    out["paths"] = paths
    out["layout"] = layout
    out["other_windows"] = {
        label: paths.get("other_windows", {}).get(label, url)
        for label, url in (cfg.get("other_windows") or {}).items()
    }
    if not out["other_windows"]:
        out["other_windows"] = dict(paths.get("other_windows") or {})
    for legacy, canonical in PATH_ALIASES.items():
        if paths.get(canonical):
            value = paths[canonical]
            if isinstance(value, str) and value and not os.path.isabs(value) and canonical not in ("ollama_url", "comfyui_url"):
                value = os.path.abspath(os.path.join(ROOT, value))
            out[legacy] = value
    # Derived destinations use layout entries rather than repeating leaf names.
    out["narrations_dir"] = paths.get("narrations_root") or os.path.join(out["media_dir"], layout["narrations"])
    out["episodes_dir"] = paths.get("episodes_root") or os.path.join(out["media_dir"], layout["episodes"])
    for alias, key in (("scripts_dir", "scripts"), ("pictures_dir", "pictures"),
                       ("images_dir", "images"), ("videos_dir", "videos"),
                       ("logs_dir", "logs"), ("temporary_dir", "temporary")):
        out[alias] = os.path.join(out["media_dir"], layout[key])
    out["episode_output_name"] = layout["episode_output"]
    out["episode_work_name"] = layout["episode_work"]
    out["voice_dataset_dir"] = os.path.join(out["sukoonvoice_media"], layout["voice_dataset"])
    out["voice_training_dir"] = os.path.join(out["sukoonvoice_media"], layout["voice_training"])
    out["voice_tests_dir"] = os.path.join(out["sukoonvoice_media"], layout["voice_tests"])
    out["voice_clips_dir"] = os.path.join(out["voice_dataset_dir"], layout["voice_clips"])
    out["voice_references_dir"] = os.path.join(out["voice_dataset_dir"], layout["voice_references"])
    out["voice_projects_dir"] = os.path.join(out["voice_dataset_dir"], layout["voice_projects"])
    for key in ("sukoonvoice_dir", "studio_dir", "voice_models_dir", "studio_python", "gpu_python", "base_python",
                "comfy_dir", "ollama_exe", "ollama_models", "ffmpeg",
                "artwork_dir", "music_dir", "audacity_dir", "hf_models", "elements_dir", "programs_dir", "experimental_root", "voice_library_source", "hub_logs_root"):
        if out.get(key) and not os.path.isabs(out[key]) and not out[key].startswith("http"):
            out[key] = os.path.abspath(os.path.join(ROOT, out[key]))
    return out


def folder(cfg, key):
    return (cfg.get("layout") or DEFAULT_LAYOUT).get(key, key)
