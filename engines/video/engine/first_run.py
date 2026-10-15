"""
first_run.py - check and set up the HubGPT Video engine on this PC.
Started by first_run.bat.  Only the Python standard library is used here.

It CHECKS everything the studio needs and, for anything missing, ASKS before installing:
  1 folders        project folders, the Episodes folder, the artwork folder
  2 code           every engine file is present
  3 fonts          copied from Windows when they are there, the free Google fonts downloaded
  4 FFmpeg         found, or downloaded into <programs>\\ffmpeg
  5 GPU            NVIDIA card + driver (information)
  6 main Python    a venv with faster-whisper, PyAV, numpy, Pillow (+ CUDA libraries for the GPU)
  7 GPU Python     ComfyUI's Python, or a separate venv with PyTorch CUDA + transformers
  8 Local AI       Ollama API and the configured model roles
  9 speech models  Whisper turbo + the Hindi Whisper model
 10 settings       machine-specific paths are read from the HubGPT root config.json
 11 test           a quick real test of both Pythons, Hindi text and the video encoder
Nothing is ever deleted or uninstalled.   Options:  --check (only report)   --yes (install without asking)
"""
import json, os, platform, re, shutil, subprocess, sys, time, urllib.request, zipfile

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)
sys.path.insert(0, ENGINE)
import paths  # noqa: E402

CHECK_ONLY = "--check" in sys.argv
YES = "--yes" in sys.argv
WIN = os.name == "nt"
REPORT = []
CFG = dict(paths.CFG)          # machine-specific paths are derived from HubGPT root config.json


# ------------------------------------------------------------------ small helpers
def say(t=""):
    print(t, flush=True)


def head(n, t):
    say(f"\n[{n}/11] {t}\n" + "-" * 70)


def ok(t):
    say(f"   [ OK ] {t}"); REPORT.append(("OK", t))


def warn(t):
    say(f"   [ !! ] {t}"); REPORT.append(("!!", t))


def info(t):
    say(f"          {t}")


def ask(q, default=True):
    """Yes/no question. --check never installs, --yes always does."""
    if CHECK_ONLY:
        info(f"(check only - not installing) {q}")
        return False
    if YES:
        return True
    d = "Y/n" if default else "y/N"
    while True:
        try:
            a = input(f"   ?  {q} [{d}] ").strip().lower()
        except EOFError:
            return default
        if not a:
            return default
        if a in ("y", "yes", "j", "haan", "ha"):
            return True
        if a in ("n", "no", "nahi", "na"):
            return False


def ask_path(q, default):
    if CHECK_ONLY or YES:
        return default
    while True:
        try:
            a = input(f"   ?  {q}\n      [Enter = {default}] ").strip().strip('"')
        except EOFError:
            return default
        if not a:
            return default
        if os.path.isabs(a) and drive_ok(a):
            return a
        say("      please type a full path like D:\\Mukhtalif\\Episodes (or just press Enter)")


def run(cmd, timeout=None, show=False):
    """-> (returncode, output)."""
    try:
        if show:
            r = subprocess.run(cmd, timeout=timeout)
            return r.returncode, ""
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def py_ok(py, code, timeout=180):
    """Run a snippet in another Python; -> (worked, last output line)."""
    if not py or not os.path.exists(py):
        return False, "not found"
    rc, out = run([py, "-s", "-c", code], timeout=timeout)
    lines = [l for l in out.strip().splitlines() if l.strip()]
    return rc == 0, (lines[-1] if lines else "")


def download(url, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    info(f"downloading {url.split('/')[-1]} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "MukhtalifHubGPT Video-first-run"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total, done, t0 = int(r.headers.get("Content-Length") or 0), 0, time.time()
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b); done += len(b)
            if total and time.time() - t0 > 2:
                print(f"\r          {done / 1e6:.0f} / {total / 1e6:.0f} MB", end="", flush=True)
    if total:
        print()
    os.replace(tmp, dst)
    return dst


def free_gb(p):
    d = os.path.splitdrive(os.path.abspath(p))[0] + os.sep if WIN else "/"
    try:
        return shutil.disk_usage(d).free / 1e9
    except OSError:
        return 0


def drive_ok(p):
    d = os.path.splitdrive(p)[0]
    return not d or os.path.exists(d + os.sep)


def near_project(*parts):
    """A location on the project's own drive (used when the original drive does not exist here)."""
    drive = os.path.splitdrive(ROOT)[0] or ROOT
    return os.path.join(drive + os.sep, *parts)


# ------------------------------------------------------------------ 1 folders
def step_folders():
    head(1, "Folders")
    for d in ("engine", os.path.join("assets", "fonts"), "loops", "work", "output"):
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            ok(f"{p}")
        elif CHECK_ONLY:
            warn(f"{p} is missing")
        else:
            os.makedirs(p, exist_ok=True); ok(f"{p} (created)")
    for key, label, alt in (("episodes_dir", "Episodes folder (one folder per episode)", ("Mukhtalif", "Episodes")),
                            ("images_dir", "artwork folder (your episode pictures)", ("Mukhtalif", "Images")),
                            ("hf_models", "speech-model folder (Whisper, ~5 GB)", ("AI", "hf_models")),
                            ("programs_dir", "programs folder (FFmpeg goes here)", ("Program_Files",))):
        cur = CFG[key]
        if not drive_ok(cur):
            info(f"{label}: {cur} - that drive does not exist on this PC")
            cur = near_project(*alt)
        cur = ask_path(f"{label}:", cur) if not os.path.isdir(cur) else cur
        if not os.path.isdir(cur):
            if CHECK_ONLY:
                warn(f"{label}: {cur} does not exist")
                continue
            os.makedirs(cur, exist_ok=True)
            ok(f"{label}: {cur} (created)")
        else:
            ok(f"{label}: {cur}")
        CFG[key] = cur
    ep = CFG["episodes_dir"]
    tpl = os.path.join(ep, "_TEMPLATE")
    if os.path.isdir(ep) and not os.path.isdir(tpl) and not CHECK_ONLY:
        os.makedirs(tpl, exist_ok=True)
        open(os.path.join(tpl, "README.txt"), "w", encoding="utf-8").write(
            "One folder per episode: the Audacity project (.aup3), the exported audio (.mp3/.wav),\n"
            "script.txt (optional), title.txt (optional), the picture. Results go into <episode>\\Output\\.\n")


# ------------------------------------------------------------------ 2 code
NEEDED = ["sleep_pipeline.py", "sv_av.py", "paths.py", "aup3_voice.py", "aup3_doc.py", "aup3_mix.py", "asr_hindi.py",
          "script_align.py", "compose.py", "gpu_compose.py", "overlay.py", "kinetic.py", "artloop.py", "subject.py",
          "qa.py", "clips.py", "reels.py", "extras.py", "new_video.ps1", "title_picker.ps1"]
LAUNCHERS = ["New Sukoon Video.bat", "make_video.bat", "make_reels.bat"]


def step_code():
    head(2, "Program files")
    miss = [f for f in NEEDED if not os.path.exists(os.path.join(ENGINE, f))]
    missl = [f for f in LAUNCHERS if not os.path.exists(os.path.join(ROOT, f))]
    if miss or missl:
        warn("missing: " + ", ".join(miss + missl) + "  ->  copy the WHOLE MukhtalifHubGPT Video folder again "
             "(Code\\engine and Code\\launchers hold copies)")
    else:
        ok(f"all {len(NEEDED)} engine files and the {len(LAUNCHERS)} launchers are here")


# ------------------------------------------------------------------ 3 fonts
GF = "https://github.com/google/fonts/raw/main/ofl/"
GOOGLE_FONTS = {   # file name the code uses -> Google Fonts download
    "TiroDevanagariHindi-Regular.ttf": "tirodevanagarihindi/TiroDevanagariHindi-Regular.ttf",
    "Amita-Bold.ttf": "amita/Amita-Bold.ttf", "Amita-Regular.ttf": "amita/Amita-Regular.ttf",
    "Kalam-Light.ttf": "kalam/Kalam-Light.ttf", "Kalam-Regular.ttf": "kalam/Kalam-Regular.ttf",
    "Kalam-Bold.ttf": "kalam/Kalam-Bold.ttf",
    "Biryani-Bold.ttf": "biryani/Biryani-Bold.ttf", "Biryani-Black.ttf": "biryani/Biryani-Black.ttf",
    "RozhaOne-Regular.ttf": "rozhaone/RozhaOne-Regular.ttf",
    "Akshar-VariableFont_wght.ttf": "akshar/Akshar%5Bwght%5D.ttf",
    "Montserrat-VariableFont_wght.ttf": "montserrat/Montserrat%5Bwght%5D.ttf",
    "CormorantGaramond-Italic-VariableFont_wght.ttf": "cormorantgaramond/CormorantGaramond-Italic%5Bwght%5D.ttf",
}
WINDOWS_FONTS = ["aparaj.ttf", "aparajb.ttf", "aparaji.ttf", "aparajbi.ttf", "Nirmala.ttc", "mangal.ttf",
                 "kokila.ttf", "kokilab.ttf", "utsaah.ttf", "utsaahb.ttf"]
MUST = ["aparajb.ttf", "TiroDevanagariHindi-Regular.ttf", "Amita-Bold.ttf", "Kalam-Light.ttf", "Biryani-Bold.ttf"]


def step_fonts():
    head(3, "Fonts (assets\\fonts)")
    fd = paths.FONTS_DIR
    if not os.path.isdir(fd):
        os.makedirs(fd, exist_ok=True)
    have = lambda f: os.path.exists(os.path.join(fd, f))
    for f in WINDOWS_FONTS:                               # Windows' own Hindi fonts: copy them in
        if not have(f):
            for d in (paths.WIN_FONTS, paths.USER_FONTS):
                src = os.path.join(d, f)
                if os.path.exists(src) and not CHECK_ONLY:
                    shutil.copy2(src, os.path.join(fd, f)); info(f"copied {f} from {d}"); break
    gmiss = [f for f in GOOGLE_FONTS if not have(f)]
    if gmiss and ask(f"download {len(gmiss)} free Google fonts ({', '.join(gmiss[:4])}{' ...' if len(gmiss) > 4 else ''})?"):
        for f in gmiss:
            try:
                download(GF + GOOGLE_FONTS[f], os.path.join(fd, f))
            except Exception as e:
                warn(f"could not download {f}: {e}")
    miss = [f for f in MUST if not have(f)]
    if miss:
        warn("still missing: " + ", ".join(miss) + "  (aparaj*.ttf come from Windows' Hindi language pack:"
             " Settings > Time & language > Language > add Hindi, or copy them from the old PC)")
    else:
        ok(f"all key fonts present ({len([f for f in os.listdir(fd) if f.lower().endswith(('.ttf', '.ttc', '.otf'))])} font files)")


# ------------------------------------------------------------------ 4 FFmpeg
FFMPEG_ZIP = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def ffmpeg_version(exe):
    rc, out = run([exe, "-hide_banner", "-version"], timeout=30)
    m = re.search(r"ffmpeg version (\S+)", out)
    return m.group(1) if rc == 0 and m else None


def step_ffmpeg():
    head(4, "FFmpeg (video encoder)")
    for c in (CFG.get("ffmpeg"), shutil.which("ffmpeg")):
        if c and os.path.exists(c) and ffmpeg_version(c):
            CFG["ffmpeg"] = c
            ok(f"FFmpeg {ffmpeg_version(c)} at {c}")
            rc, out = run([c, "-hide_banner", "-encoders"], timeout=30)
            info("NVIDIA encoder (h264_nvenc): " + ("available" if "h264_nvenc" in out else "not in this build"))
            return
    dst = os.path.join(CFG["programs_dir"], "ffmpeg")
    if not WIN:
        warn("FFmpeg not found - install it with your package manager"); return
    if ask(f"FFmpeg not found. Download it (~100 MB) into {dst}?"):
        try:
            z = download(FFMPEG_ZIP, os.path.join(CFG["programs_dir"], "_downloads", "ffmpeg-essentials.zip"))
            with zipfile.ZipFile(z) as zf:
                zf.extractall(os.path.join(CFG["programs_dir"], "_downloads", "ffmpeg_unzip"))
            unz = os.path.join(CFG["programs_dir"], "_downloads", "ffmpeg_unzip")
            inner = next(os.path.join(unz, d) for d in os.listdir(unz) if os.path.isdir(os.path.join(unz, d)))
            if not os.path.exists(dst):
                shutil.copytree(inner, dst)
            exe = os.path.join(dst, "bin", "ffmpeg.exe")
            if ffmpeg_version(exe):
                CFG["ffmpeg"] = exe; ok(f"FFmpeg {ffmpeg_version(exe)} installed at {exe}")
                info("(the downloaded zip stays in _downloads - delete it yourself if you want the space)")
                return
        except Exception as e:
            warn(f"FFmpeg download failed: {e}")
    warn("no FFmpeg - videos cannot be made. Get it from https://www.gyan.dev/ffmpeg/builds/ and run this again")


# ------------------------------------------------------------------ 5 GPU
GPU = {"nvidia": False}


def step_gpu():
    head(5, "Graphics card")
    smi = shutil.which("nvidia-smi") or r"C:\Windows\System32\nvidia-smi.exe"
    rc, out = run([smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"], timeout=30)
    if rc == 0 and out.strip():
        GPU["nvidia"] = True
        ok("NVIDIA: " + out.strip().splitlines()[0])
        m = re.search(r"(\d+)\s*MiB", out)
        if m and int(m.group(1)) < 10000:
            info("less than 10 GB of video memory: local AI models may be slow while the Hindi model is loaded")
    else:
        warn("no NVIDIA graphics card/driver found - everything runs on the processor (MUCH slower). "
             "If there is an NVIDIA card, install its driver from nvidia.com first")


# ------------------------------------------------------------------ 6 main Python (venv)
BASE_PKGS = ["numpy", "av", "faster-whisper", "Pillow", "huggingface_hub"]
CUDA_PKGS = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]
VENV_TEST = ("import numpy, av, faster_whisper, ctranslate2, PIL; "
             "print('numpy', numpy.__version__, '| PyAV', av.__version__, '| faster-whisper', faster_whisper.__version__,"
             " '| GPUs seen:', ctranslate2.get_cuda_device_count())")


def venv_python(d):
    return os.path.join(d, "Scripts", "python.exe") if WIN else os.path.join(d, "bin", "python")


def pip_install(py, pkgs, extra=()):
    info("pip install " + " ".join(pkgs) + " ...")
    rc, _ = run([py, "-m", "pip", "install", "--disable-pip-version-check", *extra, *pkgs], show=True)
    return rc == 0


def step_venv():
    head(6, "Main Python (speech timing, video, reels)")
    py = CFG["venv_python"]
    good, line = py_ok(py, VENV_TEST)
    if good:
        ok(f"{py}\n          {line}")
        if GPU["nvidia"] and "GPUs seen: 0" in line and ask("faster-whisper does not see the GPU. Install the CUDA libraries for it (~1 GB)?"):
            pip_install(py, CUDA_PKGS)
        return
    if os.path.exists(py):
        info(f"{py} is missing packages: {line}")
        if ask("install the missing packages into it?"):
            pip_install(py, BASE_PKGS + (CUDA_PKGS if GPU["nvidia"] else []))
            good, line = py_ok(py, VENV_TEST)
            if good:
                ok(line); return
    vd = os.path.join(ROOT, ".venv")
    if ask(f"create a fresh Python environment in {vd} and install {', '.join(BASE_PKGS)} (~{'2' if GPU['nvidia'] else '1'} GB)?"):
        rc, out = run([sys.executable, "-m", "venv", vd], timeout=600)
        if rc:
            warn(f"could not create the venv: {out[-300:]}"); return
        py = venv_python(vd)
        run([py, "-m", "pip", "install", "-q", "--upgrade", "pip"], timeout=600)
        pip_install(py, BASE_PKGS + (CUDA_PKGS if GPU["nvidia"] else []))
        good, line = py_ok(py, VENV_TEST)
        if good:
            CFG["venv_python"] = py; ok(f"{py}\n          {line}"); return
        warn(f"the new venv does not work yet: {line}")
    else:
        warn("no working main Python - the studio cannot run")


# ------------------------------------------------------------------ 7 GPU Python
GPU_TEST = ("import torch, transformers, numpy; ok = torch.cuda.is_available(); "
            "print('torch', torch.__version__, '| CUDA', ok, '|', torch.cuda.get_device_name(0) if ok else '-',"
            " '| transformers', transformers.__version__)")
TORCH_INDEX = "https://download.pytorch.org/whl/cu124"


def step_gpu_python():
    head(7, "GPU Python (Hindi speech model, fast video drawing, reels)")
    cands = [CFG["gpu_python"]] + [os.path.join(d, "ComfyUI_windows_portable", "python_embeded", "python.exe")
                                   for d in ("C:\\", "D:\\", "E:\\", "F:\\")] + [venv_python(os.path.join(ROOT, ".venv_gpu"))]
    for py in dict.fromkeys(cands):
        good, line = py_ok(py, GPU_TEST, timeout=300)
        if good and "CUDA True" in line:
            CFG["gpu_python"] = py; GPU["python_ok"] = True; ok(f"{py}\n          {line}")
            for mod, pkg in (("huggingface_hub", "huggingface_hub"), ("PIL", "Pillow")):
                if not py_ok(py, f"import {mod}")[0] and ask(f"{pkg} is missing in it - install?"):
                    pip_install(py, [pkg])
            return
        if os.path.exists(py):
            info(f"{py}: {line or 'no CUDA'}")
    if not GPU["nvidia"]:
        warn("no NVIDIA GPU: the Hindi speech model and the GPU renderer are skipped (CPU fallbacks are used)")
        return
    py_in = ask_path("Do you have ComfyUI portable? Path to its python_embeded\\python.exe (Enter = no):", "")
    if py_in:
        good, line = py_ok(py_in, GPU_TEST, timeout=300)
        if good and "CUDA True" in line:
            CFG["gpu_python"] = py_in; GPU["python_ok"] = True; ok(f"{py_in}\n          {line}"); return
        warn(f"{py_in}: {line}")
    vd = os.path.join(ROOT, ".venv_gpu")
    if ask(f"create a GPU Python in {vd} with PyTorch (CUDA) + transformers (~4 GB download)?"):
        if not os.path.exists(venv_python(vd)):
            rc, out = run([sys.executable, "-m", "venv", vd], timeout=600)
            if rc:
                warn(f"could not create it: {out[-300:]}"); return
        py = venv_python(vd)
        run([py, "-m", "pip", "install", "-q", "--upgrade", "pip"], timeout=600)
        pip_install(py, ["torch"], extra=["--index-url", TORCH_INDEX])
        pip_install(py, ["transformers", "huggingface_hub", "numpy", "Pillow", "av", "accelerate"])
        good, line = py_ok(py, GPU_TEST, timeout=300)
        if good and "CUDA True" in line:
            CFG["gpu_python"] = py; GPU["python_ok"] = True; ok(f"{py}\n          {line}"); return
        warn(f"GPU Python not working yet: {line}")
    else:
        warn("no GPU Python - Hindi recognition (no-script episodes) will not work, rendering is slower")


# ------------------------------------------------------------------ 8 Local AI
def step_local_ai():
    base = CFG.get("ollama_url", "http://127.0.0.1:11434")
    url = base.rstrip("/") + ("/api/tags" if backend == "ollama" else "/v1/models")
    head(8, f"{backend.capitalize()} (local AI API: scripts, moments, titles and vision)")
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read())
        names = ([m.get("name") for m in data.get("models", [])] if backend == "ollama"
                 else [m.get("id") for m in data.get("data", [])])
        ok(f"{backend.capitalize()} is running at {base} ({len(names)} model(s))")
        for model in (CFG.get("llm"), CFG.get("vision_llm")):
            if model and model in names:
                ok(f"model {model}")
            elif model:
                warn(f"model {model} not listed by {backend.capitalize()}; check its served model id")
    except Exception as e:
        warn(f"{backend.capitalize()} is not reachable at {url} - start it and run this check again ({e})")


# ------------------------------------------------------------------ 9 speech models
def hf_complete(repo):
    d = os.path.join(CFG["hf_models"], "models--" + repo.replace("/", "--"), "blobs")
    if not os.path.isdir(d):
        return False
    fs = os.listdir(d)
    return bool(fs) and not any(f.endswith(".incomplete") for f in fs) and \
        max(os.path.getsize(os.path.join(d, f)) for f in fs) > 500e6


def step_models():
    head(9, f"Speech models (in {CFG['hf_models']})")
    hf = CFG["hf_models"]
    jobs = [("mobiuslabsgmbh/faster-whisper-large-v3-turbo", "Whisper turbo - timing of your words", "1.6 GB",
             CFG["venv_python"]),
            ("vasista22/whisper-hindi-large-v2", "Hindi Whisper - words of no-script episodes", "3 GB",
             CFG["gpu_python"])]
    for repo, what, size, py in jobs:
        if hf_complete(repo):
            ok(f"{what}: {repo}"); continue
        if not os.path.exists(py):
            warn(f"{what}: not downloaded, and no Python to download it with yet"); continue
        if free_gb(hf) < 8:
            info(f"only {free_gb(hf):.0f} GB free on that drive")
        if ask(f"download {what} ({repo}, ~{size})?"):
            code = (f"from huggingface_hub import snapshot_download; "
                    f"print(snapshot_download({repo!r}, cache_dir={hf!r}))")
            rc, _ = run([py, "-s", "-c", code], show=True)
            (ok if rc == 0 and hf_complete(repo) else warn)(f"{what}: {'downloaded' if rc == 0 else 'download failed'}")
        else:
            warn(f"{what}: not downloaded (the first video will ask / fail)")


# ------------------------------------------------------------------ 10 settings
def step_settings():
    head(10, "Settings")
    info("HubGPT Video uses shared paths from the HubGPT root config.json")
    ok(os.path.join(paths.HUB_ROOT, "config.json"))
    ep = CFG["episodes_dir"]
    if os.path.isdir(ep):                                  # a shortcut to the launcher inside the Episodes folder
        sc = os.path.join(ep, "New Sukoon Video.bat")
        with open(sc, "w", encoding="utf-8") as f:
            f.write(f'@echo off\nrem Shortcut to the studio launcher (the real one lives in {ROOT})\n'
                    f'call "{os.path.join(ROOT, "New Sukoon Video.bat")}"\n')
        ok(f"shortcut {sc}")


# ------------------------------------------------------------------ 11 test
def step_test():
    head(11, "Quick real test")
    env = dict(os.environ, SV_GPU_PYTHON=CFG["gpu_python"], SUKOON_FFMPEG=CFG["ffmpeg"] or "",
               SUKOON_HF_MODELS=CFG["hf_models"], PYTHONIOENCODING="utf-8")
    test = os.path.join(ROOT, "work", "_first_run_test")
    os.makedirs(test, exist_ok=True)                    # (a scratch folder for the encoder test)
    code = ("import os, sys; sys.path.insert(0, %r); import numpy as np, overlay as ov, sleep_pipeline, compose, reels;"
            "m = ov.text_mask(['सुकून मुख़्तलिफ़ आवाज़'], ov.BOLD_HI(), 60, 900);"
            "print('Hindi text drawn' if m.max() > 0.5 else 'Hindi text EMPTY')") % ENGINE
    for name, py in (("main Python", CFG["venv_python"]), ("GPU Python", CFG["gpu_python"])):
        if not os.path.exists(py) or (name == "GPU Python" and not GPU.get("python_ok")):
            continue
        r = subprocess.run([py, "-s", "-c", code], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=300)
        last = (r.stdout + r.stderr).strip().splitlines()
        (ok if r.returncode == 0 and "drawn" in r.stdout else warn)(f"{name}: studio code loads, "
                                                                    f"{last[-1] if last else ''}")
    ff = CFG.get("ffmpeg")
    if ff and os.path.exists(ff):
        out = os.path.join(test, "encode_test.mp4")
        for enc in ("h264_nvenc", "libx264"):
            rc, msg = run([ff, "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:d=1:r=24",
                           "-c:v", enc, out], timeout=120)
            if rc == 0:
                ok(f"video encoding works ({enc})"); break
        else:
            warn(f"video encoding failed: {msg[-200:]}")
    if GPU.get("python_ok"):
        good, line = py_ok(CFG["gpu_python"], "import torch; x = torch.randn(2048, 2048, device='cuda');"
                                              "(x @ x).sum().item(); print('GPU math works on', torch.cuda.get_device_name(0))")
        (ok if good else warn)(f"GPU Python: {line}")


# ------------------------------------------------------------------ main
def main():
    bar = "=" * 70
    say(f"{bar}\n  HUBGPT VIDEO - first run / health check\n  studio : {ROOT}\n  PC     : "
        f"{platform.node()}  ({platform.system()} {platform.release()}), setup Python {sys.version.split()[0]}\n"
        f"  mode   : {'CHECK ONLY (nothing is installed)' if CHECK_ONLY else 'check + ask before installing anything'}"
        f"\n{bar}")
    for st in (step_folders, step_code, step_fonts, step_ffmpeg, step_gpu, step_venv, step_gpu_python, step_local_ai,
               step_models, step_settings, step_test):
        try:
            st()
        except KeyboardInterrupt:
            say("\n  stopped."); break
        except Exception as e:
            warn(f"{st.__name__} crashed: {type(e).__name__}: {e}")
    bad = [t for k, t in REPORT if k == "!!"]
    say(f"\n{bar}\n  " + ("READY - everything the studio needs is here." if not bad else
                          f"{len(bad)} thing(s) still need attention:") + "".join(f"\n   - {t}" for t in bad) + f"\n{bar}")
    rep = os.path.join(ROOT, "first_run_report.txt")
    with open(rep, "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M") + f"  {platform.node()}\n\n" +
                "\n".join(f"[{k}] {t}" for k, t in REPORT) + "\n")
    say(f"  report: {rep}")


if __name__ == "__main__":
    main()
