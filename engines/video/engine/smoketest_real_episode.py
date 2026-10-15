import os
import sys
import json
import math
import time
import wave
import struct
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

# ==============================================================================
# 1. WORKSPACE & USER RULES (Strictly E: for code, D: for media/tmp, NEVER C:)
# ==============================================================================
os.environ["PYTHONUTF8"] = "1"

HUB_ROOT = Path(r"E:\Projects\hub_chatgpt")
ENGINE_DIR = HUB_ROOT / "engines" / "studio" / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

BASE_DIR = Path(r"D:\HubGPT\Activity\_tmp\smoketest_2d")
ASSETS_DIR = BASE_DIR / "assets_2d"
OUT_DIR = BASE_DIR / "Output"
OLD_DIR = BASE_DIR / "_old"
TMP_DIR = BASE_DIR / "tmp"

for d in (ASSETS_DIR, OUT_DIR, OLD_DIR, TMP_DIR):
    d.mkdir(parents=True, exist_ok=True)

os.environ["TEMP"] = str(TMP_DIR)
os.environ["TMP"] = str(TMP_DIR)

W, H = 1280, 720
FPS = 24
TEST_DURATION_S = 24  # Renders first 24 seconds of a real episode


def safe_archive(path: Path):
    if path.exists():
        stamp = datetime.now().strftime("%H%M%S")
        dest = OLD_DIR / f"{path.stem}_old_{stamp}{path.suffix}"
        shutil.move(str(path), str(dest))
        print(f"  [rule] Archived existing {path.name} -> _old/{dest.name}")


def find_ffmpeg() -> str:
    r"""Finds ffmpeg.exe from HubGPT's config files or E:/D: folders."""
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")

    for p in [
        Path(r"E:\Program_Files\ffmpeg\bin\ffmpeg.exe"),
        HUB_ROOT / "config.json",
    ]:
        if p.suffix == ".exe" and p.exists():
            return str(p)
        if p.suffix == ".json" and p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                for v in data.values():
                    if isinstance(v, str) and "ffmpeg" in v.lower() and Path(v).exists():
                        return str(Path(v))
            except Exception:
                pass

    for root in [Path("E:/"), Path("D:/")]:
        for sub in ["Program_Files/ffmpeg", "ffmpeg", "Tools/ffmpeg"]:
            cand = root / sub
            if cand.exists():
                for hit in cand.rglob("ffmpeg.exe"):
                    return str(hit)

    raise FileNotFoundError("Could not find ffmpeg.exe!")


def ensure_dummy_assets():
    r"""Ensures the modular 2D PNG parts exist in D:\HubGPT\Activity\_tmp\smoketest_2d\assets_2d."""
    bg_path = ASSETS_DIR / "bg_room.png"
    if not bg_path.exists():
        bg = Image.new("RGB", (W + 200, H + 200), (14, 18, 28))
        draw = ImageDraw.Draw(bg)
        draw.rectangle([180, 120, 460, 480], fill=(24, 34, 52), outline=(45, 55, 75), width=6)
        draw.ellipse([280, 170, 360, 250], fill=(230, 235, 210))
        draw.rectangle([950, 520, 1350, 850], fill=(40, 28, 20))
        draw.polygon([(1080, 380), (1180, 380), (1210, 460), (1050, 460)], fill=(240, 180, 80))
        bg.save(bg_path)

    CW, CH = 600, 700
    base_path = ASSETS_DIR / "char_base.png"
    if not base_path.exists():
        im = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rectangle([265, 310, 335, 410], fill=(198, 145, 110, 255))
        d.ellipse([190, 100, 410, 350], fill=(210, 158, 122, 255))
        d.pieslice([185, 80, 415, 250], 180, 360, fill=(25, 22, 28, 255))
        im.save(base_path)

    for idx, offset in [(1, 0), (2, 8)]:
        np = ASSETS_DIR / f"nose_{idx}.png"
        if not np.exists():
            im = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.polygon([(300, 205), (288 - offset, 255), (312 + offset, 255)], fill=(185, 130, 98, 255))
            im.save(np)

    eyes_open = ASSETS_DIR / "eyes_open.png"
    if not eyes_open.exists():
        im = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for ex in (250, 350):
            d.ellipse([ex - 22, 195, ex + 22, 220], fill=(245, 245, 245, 255))
            d.ellipse([ex - 10, 198, ex + 10, 218], fill=(35, 25, 20, 255))
            d.line([ex - 26, 182, ex + 26, 185], fill=(25, 22, 28, 255), width=5)
        im.save(eyes_open)

    eyes_closed = ASSETS_DIR / "eyes_closed.png"
    if not eyes_closed.exists():
        im = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for ex in (250, 350):
            d.line([ex - 22, 210, ex + 22, 210], fill=(40, 30, 25, 255), width=4)
            d.line([ex - 26, 184, ex + 26, 187], fill=(25, 22, 28, 255), width=5)
        im.save(eyes_closed)

    mouth_specs = {
        "A": [275, 288, 325, 293],
        "B": [272, 283, 328, 300],
        "C": [268, 278, 332, 312],
        "D": [282, 278, 318, 314],
    }
    for code, box in mouth_specs.items():
        mp = ASSETS_DIR / f"mouth_{code}.png"
        if not mp.exists():
            im = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rounded_rectangle(box, radius=8, fill=(110, 40, 45, 255), outline=(70, 25, 28, 255), width=2)
            im.save(mp)

    for name, color in [("outfit_1", (165, 95, 55, 255)), ("outfit_2", (42, 58, 78, 255))]:
        op = ASSETS_DIR / f"{name}.png"
        if not op.exists():
            im = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            d.rounded_rectangle([110, 370, 490, 700], radius=60, fill=color)
            d.polygon([(255, 370), (345, 370), (300, 450)], fill=(30, 30, 35, 255))
            im.save(op)


# ==============================================================================
# 2. AUTO-DISCOVER REAL ASSETS ON D: (Voice, _lines.json, and Artwork)
# ==============================================================================
def find_real_narration():
    r"""Searches D:\HubGPT, D:\MukhtalifGPT, and D:\SukoonVoice for a real voice wav + _lines.json."""
    search_roots = [
        Path(r"D:\HubGPT"),
        Path(r"D:\MukhtalifGPT"),
        Path(r"D:\SukoonVoice"),
    ]
    candidates = []
    for root in search_roots:
        if not root.exists():
            continue
        for wav in root.rglob("*.wav"):
            s = str(wav).lower()
            if "_old" in s or "smoketest" in s:
                continue
            try:
                sz = wav.stat().st_size
                if sz > 350_000:
                    has_lines = (wav.parent / "_lines.json").exists() or (wav.with_name(wav.stem + "_lines.json")).exists()
                    candidates.append((1 if has_lines else 0, wav.stat().st_mtime, wav))
            except Exception:
                pass

    if not candidates:
        return None, None

    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_wav = candidates[0][2]

    lines_json = None
    for p in [best_wav.parent / "_lines.json", best_wav.with_name(best_wav.stem + "_lines.json")]:
        if p.exists():
            lines_json = p
            break

    return best_wav, lines_json


def find_real_artworks():
    r"""Finds 2 real generated paintings from D:\HubGPT or D:\MukhtalifGPT."""
    search_roots = [
        Path(r"D:\HubGPT"),
        Path(r"D:\MukhtalifGPT"),
        HUB_ROOT / "engines" / "studio",
    ]
    arts = []
    for root in search_roots:
        if not root.exists():
            continue
        for ext in ("*.png", "*.jpg", "*.webp"):
            for img in root.rglob(ext):
                s = str(img).lower()
                if "_old" in s or "smoketest" in s or "assets_2d" in s or "motions" in s:
                    continue
                try:
                    if img.stat().st_size > 80_000:
                        arts.append((img.stat().st_mtime, img))
                except Exception:
                    pass

    arts.sort(key=lambda x: x[0], reverse=True)
    picked = []
    for _, p in arts:
        try:
            with Image.open(p) as im:
                if im.width >= 600 and im.height >= 400:
                    picked.append(p)
                    if len(picked) >= 2:
                        break
        except Exception:
            pass
    return picked


# ==============================================================================
# 3. REAL RMS AUDIO ENVELOPE ANALYZER (For Frame-Accurate Lip Sync)
# ==============================================================================
def extract_audio_clip_and_rms(src_wav: Path, ffmpeg_exe: str, duration_s: int):
    r"""Converts first N seconds of real audio to 24kHz mono PCM and computes per-frame RMS."""
    clean_wav = TMP_DIR / "real_test_audio.wav"
    safe_archive(clean_wav)

    subprocess.run([
        ffmpeg_exe, "-y", "-nostdin",
        "-i", str(src_wav),
        "-t", str(duration_s),
        "-ac", "1", "-ar", "24000", "-sample_fmt", "s16",
        str(clean_wav)
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    rms_per_frame = []
    with wave.open(str(clean_wav), "r") as wf:
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
        samples = struct.unpack(f"<{n_frames}h", raw)

        samples_per_vid_frame = max(1, sr // FPS)
        total_vid_frames = int((n_frames / sr) * FPS)

        for f in range(total_vid_frames):
            start = f * samples_per_vid_frame
            chunk = samples[start:start + samples_per_vid_frame]
            if not chunk:
                rms_per_frame.append(0.0)
                continue
            mean_sq = sum(s * s for s in chunk) / len(chunk)
            rms_per_frame.append(math.sqrt(mean_sq))

    sorted_rms = sorted(rms_per_frame)
    p95 = sorted_rms[int(len(sorted_rms) * 0.95)] if sorted_rms else 1.0
    p95 = max(p95, 200.0)
    norm_rms = [min(1.0, r / p95) for r in rms_per_frame]
    return clean_wav, norm_rms


def load_subtitles_schedule(lines_json: Path, duration_s: float):
    r"""Reads real _lines.json if available, else returns automatic timeline segments."""
    subs = []
    if lines_json and lines_json.exists():
        try:
            data = json.loads(lines_json.read_text(encoding="utf-8"))
            items = data.get("lines", data) if isinstance(data, dict) else data
            if isinstance(items, list):
                for idx, item in enumerate(items):
                    if isinstance(item, dict):
                        st = float(item.get("start", item.get("start_s", idx * 4.0)))
                        en = float(item.get("end", item.get("end_s", st + 3.5)))
                        txt = str(item.get("text", item.get("line", "")))
                        if st < duration_s and txt:
                            subs.append({"start": st, "end": en, "text": txt, "idx": idx})
        except Exception as e:
            print(f"  [note] Could not parse _lines.json directly ({e}), using audio envelope segments.")

    if not subs:
        subs = [
            {"start": 0.5, "end": 4.5, "text": "Raat ki khamoshi mein kuch kahaniyan jaag uthti hain...", "idx": 0},
            {"start": 5.0, "end": 10.5, "text": "Khidki ke bahar sirf hawa ki aawaz thi.", "idx": 1},
            {"start": 11.5, "end": 17.5, "text": "Aur phir darwaze par ek halki si dastak hui...", "idx": 2},
            {"start": 18.0, "end": 23.5, "text": "Bas aankhein band karo... aur sukoon mehsoos karo.", "idx": 3},
        ]
    return subs


# ==============================================================================
# 4. RENDER FULL HYBRID TEST (Real Art + Real Voice Lip-Sync)
# ==============================================================================
def run_full_test():
    t0 = time.time()
    ffmpeg_exe = find_ffmpeg()
    ensure_dummy_assets()

    print(f"      Using FFmpeg : {ffmpeg_exe}")
    real_wav, lines_json = find_real_narration()
    real_arts = find_real_artworks()

    print(f"      Real Voice   : {real_wav if real_wav else 'Using synthetic fallback'}")
    print(f"      Lines JSON   : {lines_json if lines_json else 'Auto-envelope mode'}")
    print(f"      Real Artwork : {[p.name for p in real_arts] if real_arts else 'Using fallback bg'}")

    if not real_wav:
        raise FileNotFoundError("No real .wav found on D:! Run smoketest_2d.py first or check D:\\SukoonVoice.")

    audio_path, norm_rms = extract_audio_clip_and_rms(real_wav, ffmpeg_exe, TEST_DURATION_S)
    total_frames = len(norm_rms)
    actual_duration = total_frames / FPS
    subs = load_subtitles_schedule(lines_json, actual_duration)

    # Prepare 2 overscaled backgrounds (1480x920) for smooth camera pans
    bgs = []
    if real_arts:
        for p in real_arts:
            im = Image.open(p).convert("RGBA").resize((W + 200, H + 200), Image.Resampling.LANCZOS)
            bgs.append(im)
    if not bgs:
        bgs.append(Image.open(ASSETS_DIR / "bg_room.png").convert("RGBA"))
    if len(bgs) == 1:
        bgs.append(bgs[0].transpose(Image.Transpose.FLIP_LEFT_RIGHT))

    # Load modular 2D puppet layers
    char_base = Image.open(ASSETS_DIR / "char_base.png")
    noses = {1: Image.open(ASSETS_DIR / "nose_1.png"), 2: Image.open(ASSETS_DIR / "nose_2.png")}
    eyes = {"open": Image.open(ASSETS_DIR / "eyes_open.png"), "closed": Image.open(ASSETS_DIR / "eyes_closed.png")}
    mouths = {k: Image.open(ASSETS_DIR / f"mouth_{k}.png") for k in ("A", "B", "C", "D")}
    outfits = {1: Image.open(ASSETS_DIR / "outfit_1.png"), 2: Image.open(ASSETS_DIR / "outfit_2.png")}

    vignette = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vignette)
    vd.rectangle([0, H - 150, W, H], fill=(5, 7, 12, 165))

    out_mp4 = OUT_DIR / "smoketest_real_episode_2d.mp4"
    safe_archive(out_mp4)

    print(f"[Run] Rendering {actual_duration:.1f}s ({total_frames} frames) with Real Audio RMS Lip-Sync...")
    ffmpeg_cmd = [
        ffmpeg_exe, "-y", "-nostdin",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{W}x{H}", "-pix_fmt", "rgb24", "-r", str(FPS),
        "-i", "-",
        "-i", str(audio_path),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        "-movflags", "+faststart",
        str(out_mp4)
    ]

    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    for f in range(total_frames):
        t = f / FPS
        vol = norm_rms[f]

        # Alternate STILL vs CHAR_ANIM every ~6 seconds
        shot_index = int(t // 6.0)
        is_char_shot = (shot_index % 2 == 1) or (vol > 0.25 and int(t // 4.0) % 2 == 1)
        bg_img = bgs[shot_index % len(bgs)]

        pan_x = int(100 + 80 * math.sin(t * 0.28 + shot_index))
        pan_y = int(100 + 40 * math.cos(t * 0.22 + shot_index))
        frame = bg_img.crop((pan_x, pan_y, pan_x + W, pan_y + H)).copy()

        if is_char_shot:
            frame = ImageEnhance.Brightness(frame).enhance(0.72)

            outfit_id = 1 if (shot_index % 4 < 2) else 2
            nose_id = 1 if (shot_index % 4 < 2) else 2

            # Real Audio RMS -> Mouth Shape mapping
            if vol < 0.12:
                mouth_code = "A"
            elif vol < 0.38:
                mouth_code = "B"
            elif vol < 0.68:
                mouth_code = "C"
            else:
                mouth_code = "D"

            bob_y = int(4 * math.sin(t * 2.6))
            eye_state = "closed" if (t % 3.6) > 3.44 else "open"

            puppet = Image.new("RGBA", char_base.size, (0, 0, 0, 0))
            puppet = Image.alpha_composite(puppet, char_base)
            puppet = Image.alpha_composite(puppet, outfits[outfit_id])
            puppet = Image.alpha_composite(puppet, noses[nose_id])
            puppet = Image.alpha_composite(puppet, eyes[eye_state])
            puppet = Image.alpha_composite(puppet, mouths[mouth_code])

            frame.paste(puppet, (340, 55 + bob_y), puppet)

        frame = Image.alpha_composite(frame, vignette)

        draw = ImageDraw.Draw(frame)
        mode_str = "2D CHAR_ANIM (Live Voice Lip-Sync)" if is_char_shot else "STILL ATMOSPHERE (Artloop / Pan)"
        draw.rectangle([25, 20, 535, 62], fill=(10, 14, 22, 210))
        draw.text((40, 34), f"{mode_str} | RMS Vol: {int(vol*100):02d}%", fill=(245, 215, 130))

        bar_w = int(vol * 180)
        draw.rectangle([335, 36, 335 + bar_w, 46], fill=(90, 220, 130))

        active_sub = next((s["text"] for s in subs if s["start"] <= t <= s["end"]), "")
        if active_sub:
            draw.text((80, H - 75), active_sub[:90], fill=(255, 255, 255))

        proc.stdin.write(frame.convert("RGB").tobytes())

    proc.stdin.close()
    stderr_out = proc.stderr.read().decode("utf-8", errors="replace")
    proc.wait()

    if proc.returncode != 0:
        print("FFmpeg Error:\n", stderr_out[-800:])
        sys.exit(1)

    elapsed = time.time() - t0
    print(f"[DONE] Finished in {elapsed:.2f}s! ({actual_duration / elapsed:.1f}x faster than real-time)")
    print(f"       Watch Video : {out_mp4}")


if __name__ == "__main__":
    run_full_test()
