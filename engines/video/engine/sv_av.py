"""Shared helpers: video/audio I/O through PyAV (FFmpeg libraries bundled in the wheel - no ffmpeg.exe needed),
caption drawing with Pillow, and local AI API calls. Works with ComfyUI's embedded Python."""
import json, os, sys, urllib.request
from fractions import Fraction
import numpy as np

ENGINE = os.path.dirname(os.path.abspath(__file__))                         # the .py files
HERE = os.path.dirname(ENGINE) if os.path.basename(ENGINE).lower() == "engine" else ENGINE   # project root
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)

import paths
OLLAMA = paths.CFG.get("ollama_url", "http://127.0.0.1:11434").rstrip("/")
HF_CACHE = paths.HF_CACHE
WHISPER_ID = "openai/whisper-large-v3-turbo"
FONT_CANDIDATES = [r"C:\Windows\Fonts\georgiai.ttf", r"C:\Windows\Fonts\georgia.ttf",
                   r"C:\Windows\Fonts\segoeuil.ttf", r"C:\Windows\Fonts\arial.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"]


# ------------------------------------------------------------------ encoders
def pick_video_codec(w, h, fps, prefer=("h264_nvenc", "libx264")):
    """Return the first H.264 encoder that actually opens on this machine."""
    import av
    for name in prefer:
        try:
            cc = av.CodecContext.create(name, "w")
            cc.width, cc.height, cc.pix_fmt = w, h, "yuv420p"
            cc.time_base = Fraction(1, fps)
            cc.framerate = Fraction(fps, 1)
            cc.open()
            cc.close() if hasattr(cc, "close") else None
            return name
        except Exception:
            continue
    raise RuntimeError("no H.264 encoder available in PyAV")


def ffmpeg_exe():
    import shutil
    for c in [paths.ffmpeg(), shutil.which("ffmpeg")]:
        if c and os.path.exists(c):
            return c
    return None


def codec_options(name, quality="normal"):
    if name == "h264_nvenc":
        return {"preset": "p6", "rc": "vbr", "cq": "21", "b": "0", "maxrate": "10M", "bufsize": "20M", "spatial-aq": "1"}
    if name == "libx264":
        return {"preset": "veryfast" if quality == "fast" else "medium", "crf": "20", "maxrate": "10M", "bufsize": "20M"}
    return {}


class VideoWriter:
    """MP4 writer: rgb24 numpy frames in, optional AAC audio interleaved."""

    def __init__(self, path, w, h, fps, codec=None, with_audio=False):
        import av
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.fps = fps
        self.codec = codec or pick_video_codec(w, h, fps)
        self.out = av.open(path, "w", options={"movflags": "+faststart"})
        self.v = self.out.add_stream(self.codec, rate=fps)
        self.v.width, self.v.height, self.v.pix_fmt = w, h, "yuv420p"
        self.v.codec_context.time_base = Fraction(1, fps)
        self.v.options = codec_options(self.codec)
        self.v.codec_context.gop_size = fps * 2
        self.a = None
        if with_audio:
            self.a = self.out.add_stream("aac", rate=48000)
            self.a.codec_context.layout = "stereo"
            self.a.codec_context.bit_rate = 192000
            self.a.codec_context.time_base = Fraction(1, 48000)
        self.n = 0

    def write(self, rgb):
        import av
        f = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        f.pts = self.n
        f.time_base = Fraction(1, self.fps)
        self.n += 1
        for p in self.v.encode(f):
            self.out.mux(p)

    def write_audio(self, frame):
        for p in self.a.encode(frame):
            self.out.mux(p)

    def close(self):
        for p in self.v.encode():
            self.out.mux(p)
        if self.a is not None:
            for p in self.a.encode():
                self.out.mux(p)
        self.out.close()


# ------------------------------------------------------------------ audio
def audio_duration(path):
    try:
        import av
    except ImportError:                                   # fallback: ffprobe
        import subprocess
        fp = (ffmpeg_exe() or "ffmpeg").replace("ffmpeg.exe", "ffprobe.exe").replace("/ffmpeg", "/ffprobe")
        return float(subprocess.run([fp, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                    capture_output=True, text=True).stdout)
    with av.open(path) as c:
        s = c.streams.audio[0]
        if s.duration and s.time_base:
            return float(s.duration * s.time_base)
        return c.duration / 1e6


def load_audio_16k_mono(path):
    """Decode any audio file to float32 mono 16 kHz (what Whisper wants)."""
    import av
    rs = av.AudioResampler(format="flt", layout="mono", rate=16000)
    chunks = []
    with av.open(path) as c:
        for fr in c.decode(audio=0):
            for o in rs.resample(fr):
                chunks.append(o.to_ndarray().reshape(-1))
    for o in rs.resample(None):
        chunks.append(o.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.float32)


def aac_frames(path, total, fade_in=8.0, fade_out=10.0):
    """Yield (av.AudioFrame of 1024 samples, end_time_seconds), 48 kHz stereo fltp, with fades."""
    import av
    rs = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
    fifo = av.AudioFifo()
    sr, done, limit = 48000, 0, int(total * 48000)

    def emit(final=False):
        nonlocal done
        while fifo.samples >= 1024 or (final and fifo.samples > 0):
            n = min(1024, fifo.samples)
            fr = fifo.read(n)
            arr = fr.to_ndarray().astype(np.float32)
            if n < 1024:
                arr = np.pad(arr, ((0, 0), (0, 1024 - n)))
            t = (done + np.arange(1024)) / sr
            g = np.clip(t / fade_in, 0, 1) * np.clip((total - t) / fade_out, 0, 1)
            arr = (arr * g).astype(np.float32)
            out = av.AudioFrame.from_ndarray(arr, format="fltp", layout="stereo")
            out.sample_rate = sr
            out.pts = done
            out.time_base = Fraction(1, sr)
            done += 1024
            yield out, done / sr
            if done >= limit:
                return

    with av.open(path) as c:
        for fr in c.decode(audio=0):
            for o in rs.resample(fr):
                o.pts = None
                fifo.write(o)
            for item in emit():
                yield item
                if done >= limit:
                    return
    for o in rs.resample(None):
        o.pts = None
        fifo.write(o)
    yield from emit(final=True)


# ------------------------------------------------------------------ looping source video
class LoopReader:
    """Endless rgb24 frames from a (seamless) loop video."""

    def __init__(self, path):
        self.path = path
        self._open()

    def _open(self):
        import av
        self.c = av.open(self.path)
        self.it = self.c.decode(video=0)

    def next(self):
        try:
            return next(self.it).to_ndarray(format="rgb24")
        except StopIteration:
            self.c.close()
            self._open()
            return next(self.it).to_ndarray(format="rgb24")


# ------------------------------------------------------------------ captions with Pillow
def find_font(preferred=None):
    for f in ([preferred] if preferred else []) + FONT_CANDIDATES:
        if f and os.path.exists(f):
            return f
    return None


def render_caption(text, width=1920, font_path=None, size=58, color=(244, 236, 222), opacity=0.82):
    """Return (alpha[h,w], rgb[h,w,3]) for a soft, shadowed caption band. Pillow if present, else ffmpeg.exe."""
    try:
        import PIL  # noqa
    except ImportError:
        return render_caption_ffmpeg(text, width, font_path, size, color, opacity)
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    font = ImageFont.truetype(find_font(font_path), size) if find_font(font_path) else ImageFont.load_default()
    draw = ImageDraw.Draw(Image.new("L", (10, 10)))
    words, lines, cur = text.split(), [], ""
    for wd in words:                                       # wrap at ~75% width
        test = (cur + " " + wd).strip()
        if draw.textlength(test, font=font) > width * 0.75 and cur:
            lines.append(cur); cur = wd
        else:
            cur = test
    lines.append(cur)
    lh = int(size * 1.35)
    band_h = lh * len(lines) + 60
    txt = Image.new("L", (width, band_h), 0)
    d = ImageDraw.Draw(txt)
    for i, ln in enumerate(lines):
        x = (width - d.textlength(ln, font=font)) / 2
        d.text((x, 30 + i * lh), ln, font=font, fill=255)
    shadow = txt.filter(ImageFilter.GaussianBlur(10))
    soft = txt.filter(ImageFilter.GaussianBlur(0.8))
    a_txt = np.asarray(soft, np.float32) / 255 * opacity
    a_sh = np.asarray(shadow, np.float32) / 255 * 0.55
    # composite: shadow (black) under text (warm white)
    alpha = a_txt + a_sh * (1 - a_txt)
    rgb = np.zeros((band_h, width, 3), np.float32)
    with np.errstate(invalid="ignore", divide="ignore"):
        rgb[:] = np.nan_to_num((np.array(color, np.float32) * a_txt[..., None]) / np.maximum(alpha, 1e-6)[..., None])
    return alpha, rgb


def blend(frame, y0, alpha, rgb, strength):
    """In-place blend of a caption band onto an rgb24 frame."""
    h = alpha.shape[0]
    region = frame[y0:y0 + h].astype(np.float32)
    a = (alpha * strength)[..., None]
    frame[y0:y0 + h] = (region * (1 - a) + rgb * a).astype(np.uint8)


# ------------------------------------------------------------------ Ollama
def local_tags(timeout=5):
    with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=timeout) as r:
        data = json.loads(r.read())
    return [m["name"] for m in data.get("models", []) if m.get("name")]


def local_chat(model, prompt, json_mode=True, num_ctx=16384, timeout=900, think=False, image=None):
    message = {"role": "user", "content": prompt}
    if image:
        message["images"] = [image]
    body = {"model": model, "stream": False, "options": {"temperature": 0.4, "num_ctx": num_ctx},
            "messages": [message]}
    # Honor callers' thinking preference only for Qwen3 text models. Gemma 3 does not
    # expose Ollama's native think control; vision variants are excluded as well.
    if model.lower().startswith("qwen3") and not any(tag in model.lower() for tag in ("vl", "vision")):
        body["think"] = bool(think)
    if json_mode:
        body["format"] = "json"
    req = urllib.request.Request(OLLAMA + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return data["message"]["content"]


def unload_model(model, timeout=30):
    """Release one Ollama model's GPU/CPU allocation while leaving Ollama running."""
    if not model:
        return
    body = {"model": model, "prompt": "", "keep_alive": 0}
    req = urllib.request.Request(OLLAMA + "/api/generate", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        response.read()


# ------------------------------------------------------------------ caption fallback without Pillow
def _wrap_chars(text, max_chars):
    lines, cur = [], ""
    for wd in text.split():
        test = (cur + " " + wd).strip()
        if len(test) > max_chars and cur:
            lines.append(cur); cur = wd
        else:
            cur = test
    lines.append(cur)
    return lines


def _ff_path(p):
    return p.replace("\\", "/").replace(":", "\\:")


def _png_to_rgba(path):
    try:
        import av
        with av.open(path) as c:
            return next(c.decode(video=0)).to_ndarray(format="rgba")
    except ImportError:
        import subprocess
        from PIL import Image  # pragma: no cover
        return np.asarray(Image.open(path).convert("RGBA"))


def render_caption_ffmpeg(text, width=1920, font_path=None, size=58, color=(244, 236, 222), opacity=0.82):
    import subprocess, tempfile
    ff = ffmpeg_exe()
    if not ff:
        raise RuntimeError("Need Pillow or ffmpeg.exe to draw captions")
    font = find_font(font_path)
    lines = _wrap_chars(text, int(width * 0.75 / (size * 0.48)))
    lh = int(size * 1.35)
    h = lh * len(lines) + 60
    tmp = tempfile.mkdtemp(prefix="cap_")
    draws = []
    for i, ln in enumerate(lines):
        tf = os.path.join(tmp, f"l{i}.txt")
        open(tf, "w", encoding="utf-8").write(ln)
        draws.append(f"drawtext=fontfile='{_ff_path(font)}':textfile='{_ff_path(tf)}':fontsize={size}:"
                     f"fontcolor=white:x=(w-text_w)/2:y={30 + i * lh}")
    png_t, png_s = os.path.join(tmp, "t.png"), os.path.join(tmp, "s.png")
    base = f"color=c=black:s={width}x{h}:d=1,format=gray"
    for out, extra in ((png_t, ",gblur=sigma=0.8"), (png_s, ",gblur=sigma=9")):
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", base,
                        "-vf", ",".join(draws) + extra, "-frames:v", "1", out], check=True)
    def norm(p):                                   # ffmpeg "black" may be 16, not 0 -> rescale to 0..1
        v = _png_to_rgba(p)[..., 0].astype(np.float32)
        lo = np.percentile(v, 5)
        return np.clip((v - lo) / max(1.0, 255 - lo), 0, 1)
    a_txt = norm(png_t) * opacity
    a_sh = norm(png_s) * 0.55
    alpha = a_txt + a_sh * (1 - a_txt)
    rgb = np.nan_to_num((np.array(color, np.float32) * a_txt[..., None]) / np.maximum(alpha, 1e-6)[..., None])
    return alpha, rgb.astype(np.float32)
