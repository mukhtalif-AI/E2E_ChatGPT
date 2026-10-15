"""
check_env.py - verifies every piece the pipeline needs, using only what's already installed.
Downloads nothing, installs nothing. Writes check_report.txt next to this file.
"""
import io, json, os, shutil, sys, time, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
TEST_AUDIO = r"D:\Audacity\Sukoon_Sleep Therapy.mp3"
LOG = io.StringIO()
RESULTS = []


def out(msg=""):
    print(msg, flush=True)
    LOG.write(msg + "\n")


def check(name, fn):
    t0 = time.time()
    try:
        info = fn()
        RESULTS.append((name, True))
        out(f"[ OK ] {name}: {info}  ({time.time()-t0:.1f}s)")
    except Exception as e:
        RESULTS.append((name, False))
        out(f"[FAIL] {name}: {type(e).__name__}: {e}")
        out("       " + traceback.format_exc().strip().splitlines()[-2][:200])


out(f"Python {sys.version.split()[0]} at {sys.executable}")


def c_numpy():
    import numpy; return numpy.__version__
def c_torch():
    try:
        import torch
    except ImportError:
        import ctranslate2
        n = ctranslate2.get_cuda_device_count()
        if not n:
            raise RuntimeError("no torch here and CTranslate2 sees no CUDA GPU")
        return f"(no torch in this env) CTranslate2 sees {n} CUDA GPU(s)"
    if not torch.cuda.is_available():
        raise RuntimeError(f"torch {torch.__version__} installed but CUDA not available")
    x = torch.randn(2048, 2048, device="cuda", dtype=torch.float16)
    torch.cuda.synchronize(); (x @ x).sum().item()
    free, tot = torch.cuda.mem_get_info()
    return f"torch {torch.__version__}, {torch.cuda.get_device_name(0)}, {free/1e9:.1f}/{tot/1e9:.1f} GB free"
def c_transformers():
    import sleep_pipeline as sp
    b, repo = sp.whisper_backend()
    if b == "faster-whisper":
        import faster_whisper, ctranslate2
        n = ctranslate2.get_cuda_device_count()
        return f"faster-whisper {faster_whisper.__version__}, CTranslate2 sees {n} CUDA GPU(s)"
    import transformers
    from transformers import WhisperForConditionalGeneration, pipeline  # noqa
    return f"transformers {transformers.__version__} (Whisper classes import fine)"
def c_whisper_model():
    import sleep_pipeline as sp
    from sv_av import HF_CACHE
    b, repo = sp.whisper_backend()
    if sp.whisper_cached():
        return f"{repo} fully downloaded in {HF_CACHE}"
    return f"{repo} NOT fully downloaded - first run will ask before fetching ~1.6 GB into {HF_CACHE}"
def c_av():
    import av
    enc = [c for c in ("h264_nvenc", "libx264", "aac") if c in av.codecs_available]
    from sv_av import pick_video_codec
    best = pick_video_codec(1920, 1080, 24)
    return f"PyAV {av.__version__}; encoders present: {', '.join(enc)}; H.264 encoder that opens: {best}"
def c_pillow():
    from sv_av import find_font, ffmpeg_exe, render_caption
    f = find_font()
    if not f:
        raise RuntimeError("no usable .ttf font found")
    try:
        import PIL; how = f"Pillow {PIL.__version__}"
    except ImportError:
        how = f"no Pillow -> using {ffmpeg_exe()} drawtext"
    a, _ = render_caption("Test caption", 640, size=26)
    return f"{how}, font {f}, caption drawn ({a.shape[1]}x{a.shape[0]})"
def c_audio():
    import sv_av
    if not os.path.exists(TEST_AUDIO):
        raise FileNotFoundError(TEST_AUDIO)
    d = sv_av.audio_duration(TEST_AUDIO)
    return f"{os.path.basename(TEST_AUDIO)} decodes, {d/60:.1f} min"
def c_local_ai():
    import sv_av
    tags = sv_av.local_tags()
    model = tags[0] if tags else sv_av.paths.CFG.get("llm", "Gemma-4-26B-A4B-NVFP4")
    t0 = time.time()
    r = sv_av.local_chat(model, 'Translate to soft English, JSON {"en": "..."}: "अपनी साँसों को धीमा होने दो"',
                             num_ctx=2048, timeout=300)
    return f"{sv_av.BACKEND}: models {tags}; {model} answered in {time.time()-t0:.0f}s -> {r.strip()[:80]}"
def c_end_to_end():
    """Tiny real run: 4 s loop at 640x360 + caption + 6 s of the real audio -> MP4."""
    import numpy as np, sleepgen, sv_av
    d = os.path.join(HERE, "work", "_check"); os.makedirs(d, exist_ok=True)
    loop = os.path.join(d, "loop.mp4")
    sleepgen.render("spiral", "ember", 2, 24, 640, 360, loop, backend="av")
    alpha, rgb = sv_av.render_caption("Let your breath slow down, gently", 640, size=26)
    wr = sv_av.VideoWriter(os.path.join(d, "check.mp4"), 640, 360, 24, with_audio=True)
    src, afr, at = sv_av.LoopReader(loop), sv_av.aac_frames(TEST_AUDIO, 6.0, 1, 1), 0
    for i in range(144):
        f = np.ascontiguousarray(src.next())
        sv_av.blend(f, 360 - 40 - alpha.shape[0], alpha, rgb, min(1, i / 24))
        wr.write(f)
        while at <= i / 24 + 0.5:
            try:
                af, at = next(afr); wr.write_audio(af)
            except StopIteration:
                break
    for af, _ in afr:
        wr.write_audio(af)
    wr.close()
    p = os.path.join(d, "check.mp4")
    return f"wrote {p} ({os.path.getsize(p)//1024} KB, {sv_av.audio_duration(p):.1f}s with audio)"
def c_disk():
    return ", ".join(f"{dr} {shutil.disk_usage(dr).free/1e9:.0f} GB free" for dr in ("C:\\", "D:\\", "E:\\") if os.path.exists(dr))


for name, fn in [("numpy", c_numpy), ("GPU / PyTorch CUDA", c_torch), ("Whisper engine", c_transformers),
                 ("Whisper model", c_whisper_model), ("PyAV video/audio encoders", c_av), ("Caption drawing", c_pillow),
                 ("Test audio", c_audio), ("Local AI", c_local_ai), ("End-to-end mini video", c_end_to_end),
                 ("Disk space", c_disk)]:
    check(name, fn)

ok = all(r for _, r in RESULTS)
out("\nRESULT: " + ("EVERYTHING WORKS - nothing needs installing." if ok else
                    "Some checks failed - see [FAIL] lines (nothing was changed)."))
open(os.path.join(HERE, "check_report.txt"), "w", encoding="utf-8").write(LOG.getvalue())
