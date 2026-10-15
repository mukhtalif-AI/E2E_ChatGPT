"""setup_hub.py - makes the hub ready on this PC (setup_hub.bat runs it). Asks before every copy / install.

  1 shared things   FFmpeg, ComfyUI (pictures), Ollama (local AI), media folders on D:
  2 voice engine    HubGPT Voice lives in engines\\voice and stores trained models in its own models folder.
                    Its shared library source is configured in config.json.
  3 video engine    HubGPT Video lives in engines\\video. It needs its own Python venv
                    (engines\\video\\.venv: numpy, PyAV, faster-whisper, Pillow + CUDA libraries).
  4 English voice   engines\\english = Qwen3-TTS narrator (libraries copied from MukhtalifAudioBook or pip).
Only D: and E: are used (pip cache -> .cache\\ here, temp -> media_dir\\_tmp). Report: logs\\setup_report.txt
"""
import json, os, shutil, subprocess, sys, time

APP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP)
sys.path.insert(0, APP)
from path_config import load_config
CFG = load_config()
try:
    settings = json.load(open(os.path.join(CFG.get("media_dir", ""), "hub_settings.json"), encoding="utf-8"))
    for key in ("llm", "story_model", "prompt_model", "vision_llm"):
        if settings.get(key):
            CFG[key] = settings[key]
except (OSError, ValueError):
    pass
REPORT = []
STUDIO_PKGS = ["numpy", "av", "faster-whisper", "Pillow", "huggingface_hub", "uharfbuzz", "freetype-py"]   # last two: correct Hindi text
STUDIO_CUDA = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]


def say(t=""):
    print(t, flush=True)


def note(ok, t):
    say(("   OK   " if ok else "   !!   ") + t)
    REPORT.append(("OK   " if ok else "!!   ") + t)


def ask(q, default=True):
    a = input(f"   {q} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not a else a.startswith("y")


def run(cmd, **kw):
    say("   > " + " ".join(str(c) for c in cmd))
    return subprocess.run([str(c) for c in cmd], **kw).returncode


def env():
    e = dict(os.environ)
    e["PIP_CACHE_DIR"] = os.path.join(ROOT, ".cache", "pip")
    tmp = os.path.join(CFG["media_dir"], "_tmp")
    os.makedirs(tmp, exist_ok=True)
    e["TEMP"] = e["TMP"] = tmp
    return e


def shared():
    say("\n== 1  shared things")
    note(os.path.exists(CFG["ffmpeg"]), f"FFmpeg  {CFG['ffmpeg']}")
    note(os.path.exists(CFG["gpu_python"]), f"ComfyUI's Python (voice + GPU)  {CFG['gpu_python']}")
    note(os.path.isdir(CFG["comfy_dir"]), f"ComfyUI (pictures)  {CFG['comfy_dir']}")
    try:
        import urllib.request
        base = CFG.get("ollama_url", "http://127.0.0.1:11434")
        endpoint = base.rstrip("/") + "/api/tags"
        urllib.request.urlopen(endpoint, timeout=3).read()
        note(True, f"Ollama is running ({CFG.get('story_model')} for stories, {CFG.get('prompt_model')} for prompts)")
    except Exception:
        note(False, "Ollama is not running now (needed for local model requests)")
    for k in ("media_dir", "sukoonvoice_media", "episodes_dir"):
        try:
            os.makedirs(CFG[k], exist_ok=True)
            note(True, f"{k}: {CFG[k]}")
        except OSError as e:
            note(False, f"{k}: {CFG[k]} ({e})")
    note(os.path.isdir(CFG["voice_models_dir"]),
         f"HubGPT Voice models: {CFG['voice_models_dir']} (backup: {CFG['voice_model_backup_dir']})")


def voice():
    say("\n== 2  voice engine (engines\\voice)")
    d = CFG["sukoonvoice_dir"]
    libs = os.path.join(d, "libs", "cb")
    pt = os.path.join(d, "python.txt")
    if not os.path.exists(pt) or not os.path.exists(open(pt, encoding="utf-8-sig").read().strip().strip('"')):
        open(pt, "w", encoding="utf-8").write(CFG["gpu_python"] + "\n")
    if os.path.isdir(libs) and os.listdir(libs):
        note(True, f"libraries present: {libs}"); return
    src = CFG.get("voice_library_source", "")
    if os.path.isdir(src):
        say(f"   The voice libraries (Chatterbox + small helpers, ~330 MB) are missing in the hub.")
        if ask(f"Copy them from {src} (fast, no internet)?"):
            t0 = time.time()
            shutil.copytree(src, libs)
            note(True, f"libraries copied ({time.time() - t0:.0f} s)"); return
    if ask("Install them fresh with the voice engine's own setup (downloads, asks again there)?", False):
        run(["cmd", "/c", os.path.join(d, "setup.bat")], cwd=d, env=env())
        note(os.path.isdir(libs), "voice libraries installed" if os.path.isdir(libs) else "voice libraries still missing")
    else:
        note(False, "voice libraries missing - Script -> Voice will not work until this step is done")


def studio():
    say("\n== 3  video engine (engines\\video)")
    d = CFG["studio_dir"]
    for sub in ("loops", "work", "output"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    py = CFG["studio_python"]
    test = "import numpy, av, faster_whisper, PIL; print('numpy', numpy.__version__, '| PyAV', av.__version__)"
    if os.path.exists(py) and subprocess.run([py, "-c", test], capture_output=True).returncode == 0:
        note(True, f"own Python ready: {py}"); return
    base = CFG.get("base_python") or ""
    if not os.path.exists(base):
        note(False, f"no base Python at {base} - set base_python in config.json"); return
    say("   The video engine needs its own small Python (venv) with numpy, PyAV, faster-whisper, Pillow")
    say("   + the NVIDIA libraries for the graphics card. Download about 1-2 GB, stays in engines\\video\\.venv.")
    if not ask("Create it now?"):
        note(False, "video engine Python missing - Make video (Sukoon video) will not work until this step is done")
        return
    vd = os.path.join(d, ".venv")
    if run([base, "-m", "venv", vd], env=env()):
        note(False, "could not create the venv"); return
    vpy = os.path.join(vd, "Scripts", "python.exe")
    run([vpy, "-m", "pip", "install", "--upgrade", "pip"], env=env())
    gpu = shutil.which("nvidia-smi") is not None
    rc = run([vpy, "-m", "pip", "install", *STUDIO_PKGS, *(STUDIO_CUDA if gpu else [])], env=env())
    ok = rc == 0 and subprocess.run([vpy, "-c", test], capture_output=True).returncode == 0
    note(ok, f"video engine Python {'ready' if ok else 'NOT working - see the lines above'}: {vpy}")


def english():
    say("\n== 4  English narrator (engines\\english, Qwen3-TTS)")
    d = CFG.get("english_dir") or os.path.join(ROOT, "engines", "english")
    pt = os.path.join(d, "python.txt")
    if not os.path.exists(pt) or not os.path.exists(open(pt, encoding="utf-8-sig").read().strip().strip('"')):
        open(pt, "w", encoding="utf-8").write(CFG["gpu_python"] + "\n")
    if os.path.isdir(os.path.join(d, "libs", "qwen")) and os.path.isdir(os.path.join(d, "libs", "common")):
        note(True, f"English narrator libraries present: {os.path.join(d, 'libs')}"); return
    say("   Its libraries (Qwen3-TTS, ~0.9 GB) are copied from MukhtalifAudioBook if it is on this PC, else downloaded.")
    if ask("Set up the English narrator now?"):
        run([CFG["gpu_python"], "-s", os.path.join(d, "run.py"), "setup"], cwd=d, env=env())
        ok = os.path.isdir(os.path.join(d, "libs", "qwen"))
        note(ok, "English narrator ready" if ok else "English narrator libraries still missing")
    else:
        note(False, "English narrator not set up - English episodes will not work until this step is done")


def main():
    say("=" * 70 + "\n  HUBGPT - setup (asks before every copy / install)\n" + "=" * 70)
    shared()
    voice()
    studio()
    english()
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    open(os.path.join(ROOT, "logs", "setup_report.txt"), "w", encoding="utf-8").write(
        time.strftime("%Y-%m-%d %H:%M\n") + "\n".join(REPORT) + "\n")
    bad = [r for r in REPORT if r.startswith("!!")]
    say("\n" + ("  All set - start the hub with hub.bat" if not bad else f"  {len(bad)} thing(s) to look at (above)."))


if __name__ == "__main__":
    main()
