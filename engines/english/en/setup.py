"""setup.py - libraries for the English narrator, asked before anything is copied / installed.
  libs\\common   soundfile, pyloudnorm, scipy
  libs\\qwen     Qwen3-TTS (transformers 4.57.3 etc.)      libs\\qwen_fast   faster-qwen3-tts (optional)
First choice: copy them from E:\\Projects\\MukhtalifAudioBook\\libs (same versions, no internet, ~0.9 GB).
Otherwise: pip --target into libs\\ (never a second torch / numpy into the shared Python)."""
import os, re, shutil, subprocess, sys, time
from .config import ROOT, LIBS, C

AUDIOBOOK_LIBS = r"E:\Projects\MukhtalifAudioBook\libs"
NEVER = {"torch", "torchaudio", "torchvision", "numpy"}
MAP = {"sklearn": "scikit-learn", "yaml": "pyyaml", "huggingface_hub": "huggingface-hub", "lazy_loader": "lazy-loader",
       "_soundfile_data": "soundfile", "typing_extensions": "typing-extensions", "PIL": "pillow"}
YES = False


def say(t=""):
    print(t, flush=True)


def ask(q):
    if YES:
        return True
    try:
        return input(f"   ?  {q} [Y/n] ").strip().lower() in ("", "y", "yes", "haan", "ha")
    except EOFError:
        return False


def try_import(paths, code):
    r = subprocess.run([sys.executable, "-s", "-c", "import sys; sys.path[:0] = " + repr(paths) + "; " + code],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def pip(target, pkgs):
    say("      pip install " + " ".join(pkgs))
    return subprocess.run([sys.executable, "-s", "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                           "--no-deps", "--upgrade", "--target", target, *pkgs]).returncode == 0


def fill_missing(target, paths, code, tries=30):
    for _ in range(tries):
        ok, out = try_import(paths, code)
        if ok:
            return True, out
        m = re.search(r"No module named '([\w\.]+)'", out)
        if not m:
            return False, out
        mod = m.group(1).split(".")[0]
        if mod in NEVER:
            return False, f"needs {mod} - not installing a second copy into {sys.executable}"
        if not pip(target, [MAP.get(mod, mod.replace("_", "-"))]):
            return False, f"could not install {mod}"
    return False, "too many missing modules"


def reqs(p):
    return [l.split("#")[0].strip() for l in open(p, encoding="utf-8") if l.split("#")[0].strip()]


def main(yes=False):
    global YES
    YES = yes
    say("ENGLISH NARRATOR - setup\n" + "=" * 60 + f"\n python  {sys.executable}\n models  {C['hf_models']}")
    ok, out = try_import([], "import torch; print('torch', torch.__version__, 'CUDA', torch.cuda.is_available())")
    say("   " + (out.splitlines()[-1] if out else ""))
    if not ok:
        sys.exit("   !! this Python has no PyTorch - set python.txt to ComfyUI's Python")
    common = os.path.join(LIBS, "common")
    for name, code in (("common", "import soundfile, pyloudnorm, scipy"),
                       ("qwen", open(os.path.join(ROOT, "en", "engines", "qwen", "import_test.txt")).read().strip()),
                       ("qwen_fast", open(os.path.join(ROOT, "en", "engines", "qwen_fast", "import_test.txt")).read().strip())):
        target = os.path.join(LIBS, name)
        paths = [target] + ([common] if name != "common" else [])
        ok, _ = try_import(paths, code)
        if ok:
            say(f"   {name}: ready"); continue
        src = os.path.join(AUDIOBOOK_LIBS, name)
        if os.path.isdir(src) and not os.path.isdir(target) and ask(f"copy libs\\{name} from {src} (no internet)?"):
            t0 = time.time()
            shutil.copytree(src, target)
            ok, out = try_import(paths, code)
            say(f"   {name}: copied ({time.time() - t0:.0f} s) - " + ("works" if ok else "!! " + out[-600:]))
            if ok:
                continue
        if name == "qwen_fast" and not ask("install the optional fast engine (qwen_fast, ~0.4 GB download)?"):
            continue
        if name != "qwen_fast" and not ask(f"download + install libs\\{name}?"):
            continue
        rq = os.path.join(ROOT, "requirements_common.txt") if name == "common" else \
            os.path.join(ROOT, "en", "engines", name, "requirements.txt")
        pip(target, reqs(rq))
        ok, out = fill_missing(target, paths, code)
        say(f"   {name}: " + ("OK" if ok else "!! " + out[-1500:]))
    say("setup finished. The voice model (Qwen3-TTS 1.7B CustomVoice, ~4 GB) is read from / downloads to " + C["hf_models"])
