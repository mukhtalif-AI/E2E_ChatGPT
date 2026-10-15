"""install.py - makes SukoonVoice ready on this PC (run by setup.bat). Asks before every download.
Chatterbox's libraries go into libs\\cb one by one (--no-deps) so the shared Python's PyTorch / numpy are never
replaced (same method Kisse used - copied, not imported)."""
import os, re, subprocess, sys
from . import cfg

PY = sys.executable
T = cfg.LIBS
MAP = {"perth": "resemble-perth", "sklearn": "scikit-learn", "yaml": "pyyaml", "spacy_pkuseg": "spacy-pkuseg",
       "antlr4": "antlr4-python3-runtime", "lazy_loader": "lazy-loader", "cv2": "opencv-python-headless",
       "google": "protobuf", "pkg_resources": "setuptools", "attr": "attrs", "dateutil": "python-dateutil",
       "PIL": "pillow", "jwt": "pyjwt", "importlib_metadata": "importlib-metadata", "huggingface_hub": "huggingface-hub",
       "_soundfile_data": "soundfile"}
NEVER = {"torch", "torchaudio", "torchvision", "numpy"}
FIRST = ["chatterbox-tts", "soundfile", "librosa", "s3tokenizer", "resemble-perth", "conformer", "diffusers==0.29.0",
         "omegaconf", "antlr4-python3-runtime==4.9.3", "pykakasi", "audioread", "soxr", "lazy-loader", "pooch",
         "numba", "llvmlite", "msgpack", "decorator", "joblib", "scikit-learn", "threadpoolctl", "einops", "pyloudnorm"]


def ask(q, default=True):
    try:
        a = input(f"  {q} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    except EOFError:
        return default
    return default if not a else a.startswith("y")


def pip(*pk):
    print("  $ pip install", " ".join(pk), flush=True)
    return subprocess.run([PY, "-s", "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                           "--no-deps", "--target", T, "--upgrade", *pk]).returncode


def try_import(code):
    r = subprocess.run([PY, "-s", "-c", f"import sys; sys.path.insert(0, r'{T}'); {code}"], capture_output=True,
                       text=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return r.returncode, r.stdout + r.stderr


def cached(repo, hub_layout=True):
    base = os.path.join(cfg.C["hf_models"], "hub") if hub_layout else cfg.C["hf_models"]
    d = os.path.join(base, "models--" + repo.replace("/", "--"), "snapshots")
    return os.path.isdir(d) and any(os.scandir(d))


def main():
    print("\n== 1/4  Python + graphics card", flush=True)
    rc, out = try_import("import torch, numpy; print('torch', torch.__version__, '| CUDA', torch.cuda.is_available(),"
                         " '|', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'no GPU')")
    print("  " + out.strip())
    if rc:
        raise SystemExit("this Python has no PyTorch - run setup.bat again and choose 2 (own Python)")
    print("\n== 2/4  ffmpeg")
    try:
        print("  ok:", cfg.ffmpeg())
    except SystemExit as e:
        print("  !!", e)
    print("\n== 3/4  Chatterbox libraries ->", T)
    rc, _ = try_import("import chatterbox.mtl_tts")
    if rc:
        if not ask("install Chatterbox + its small libraries into libs\\cb (~300 MB, nothing outside this folder)?"):
            raise SystemExit("stopped")
        os.makedirs(T, exist_ok=True)
        pip(*FIRST)
        for i in range(30):
            rc, out = try_import("import chatterbox.mtl_tts")
            if rc == 0:
                break
            mm = re.search(r"No module named '([\w\.]+)'", out)
            if not mm:
                print(out[-3000:])
                raise SystemExit("import failed (see above) - send logs\\last_run.txt")
            mod = mm.group(1).split(".")[0]
            if mod in NEVER:
                raise SystemExit(f"needs {mod} - not installing a second copy over the shared Python's")
            pk = MAP.get(mod, mod.replace("_", "-"))
            print(f"  missing {mod} -> {pk}")
            if pip(pk):
                raise SystemExit(f"could not install {pk}")
    rc, out = try_import("import chatterbox.mtl_tts, transformers, librosa; print('chatterbox OK | transformers',"
                         " transformers.__version__)")
    print("  " + out.strip().splitlines()[-1])
    rc, _ = try_import("import parselmouth")
    if rc:
        if ask("install praat-parselmouth (~15 MB, into libs\\cb) for 'match to you' - your pitch and pace?"):
            pip("praat-parselmouth")
    rc, out = try_import("import parselmouth; print('praat (pitch / pace matching) OK', parselmouth.VERSION)")
    print("  " + (out.strip().splitlines()[-1] if out.strip() else "praat not installed - matching is off"))
    print("\n== 4/4  models (shared folder", cfg.C["hf_models"] + ")")
    have_cb = cached("ResembleAI/chatterbox")
    have_asr = cached(cfg.C["asr_model"], hub_layout=False)
    print(f"  Chatterbox Multilingual (~3 GB): {'already there' if have_cb else 'not downloaded'}")
    print(f"  Hindi checker {cfg.C['asr_model']} (~3 GB): {'already there' if have_asr else 'not downloaded'}")
    want_cb = have_cb or ask("download Chatterbox Multilingual now (~3 GB)?")
    want_asr = have_asr or ask("download the Hindi checker now (~3 GB; without it takes are not checked by ear)?")
    import importlib                                   # libs\cb did not exist when this Python started:
    sys.path_importer_cache.pop(T, None)               # forget that, so its new libraries are found now
    if T not in sys.path:
        sys.path.insert(0, T)
    importlib.invalidate_caches()
    if want_asr and not have_asr:
        from huggingface_hub import snapshot_download
        snapshot_download(cfg.C["asr_model"], cache_dir=cfg.C["hf_models"],
                          allow_patterns=["*.json", "*.txt", "pytorch_model.bin"])
    if want_cb:
        print("\n  test: one Hindi sentence ...", flush=True)
        import numpy as np, soundfile as sf
        from .model import load_base
        m = load_base()
        import torch
        with torch.inference_mode():
            w = m.generate("आज की रात हम बस आराम करेंगे.", language_id="hi")
        os.makedirs(cfg.TMP, exist_ok=True)
        p = os.path.join(cfg.TMP, "setup_test.wav")
        sf.write(p, w.squeeze(0).cpu().numpy(), m.sr)
        print(f"  ok - {p}")
    print("\nSETUP DONE. Next: sukoon_voice.bat -> 1 (build dataset) -> 2 (train) -> 3 (A/B test)")
