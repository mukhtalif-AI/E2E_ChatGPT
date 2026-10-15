"""run.py - the hub's English narrator (Qwen3-TTS, Apache 2.0). Runs with the Python named in python.txt.
    run.py speak <script.txt> --out <narrations folder> [--name N] [--redo 3,17] [--speaker Ryan] [--engine qwen]
    run.py test [--engine qwen_fast]     three short lines -> <tmp>\\english_test\\ (speed + sound check)
    run.py setup [--yes]                 libraries into libs\\ (copied from MukhtalifAudioBook if found, else pip)
"""
import argparse, os, sys
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
for s_ in (sys.stdout, sys.stderr):
    try:
        s_.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from en import config  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("cmd", choices=["speak", "test", "setup"])
ap.add_argument("script", nargs="?")
ap.add_argument("--out", default="")
ap.add_argument("--name", default=None)
ap.add_argument("--redo", default="")
ap.add_argument("--speaker", default=None)
ap.add_argument("--engine", default=None)
ap.add_argument("--ffmpeg", default=None)
ap.add_argument("--yes", action="store_true")
a = ap.parse_args()

config.set_env()
if a.cmd == "setup":
    from en import setup
    setup.main(a.yes)
    sys.exit(0)
config.use_libs(a.engine or config.C["engine"])
from en import speak  # noqa: E402

if a.cmd == "test":
    d = os.path.join(config.C["tmp_dir"], "english_test")
    os.makedirs(d, exist_ok=True)
    sp = os.path.join(d, "test_script.txt")
    open(sp, "w", encoding="utf-8").write(
        "Welcome to tonight, my friend... let your shoulders soften.\n(...)\n"
        "It is okay if today felt heavy, you carried it as well as you could.\n(...)\n"
        "Now breathe in slowly... and let the night hold you.\n")
    import time
    t0 = time.time()
    speak.main(sp, d, "test_" + (a.engine or config.C["engine"]), "", a.speaker, a.engine, a.ffmpeg)
    print(f"TEST DONE in {time.time() - t0:.0f} s (includes loading the model)")
else:
    if not a.script or not a.out:
        sys.exit("speak needs a script and --out")
    speak.main(a.script, a.out, a.name, a.redo, a.speaker, a.engine, a.ffmpeg)
