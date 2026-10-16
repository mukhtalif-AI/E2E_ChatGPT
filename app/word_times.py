"""word_times.py - the exact time of every spoken word in a narration (faster-whisper, word timestamps).

Runs in the video engine's Python (it already has faster-whisper and the large-v3-turbo model):
  <studio_python> -u -s app\\word_times.py <narration.wav> <words.json> [hi|en]     (cwd = the video engine folder)
Writes {"lang", "model", "made", "words": [{"w", "start", "end", "p"}]} - times in seconds of narration.wav.
"""
import glob
import json
import os
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main():
    wav, out = sys.argv[1], sys.argv[2]
    lang = sys.argv[3] if len(sys.argv) > 3 else "hi"
    sys.path.insert(0, os.path.join(os.getcwd(), "engine"))
    hf_cache = None
    load = None
    try:
        import sv_av
        hf_cache = getattr(sv_av, "HF_CACHE", None)
        load = getattr(sv_av, "load_audio_16k_mono", None)
    except Exception as e:
        print(f"   (video engine helpers not found: {e}; using faster-whisper's own decoder)")
    import site
    for sp in site.getsitepackages():                     # pip-installed cuBLAS / cuDNN DLLs, as the video engine does
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
    from faster_whisper import WhisperModel
    t0 = time.time()
    try:
        m = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16", download_root=hf_cache)
        dev = "GPU"
    except Exception as e:
        print(f"   GPU not available for Whisper ({e}); using the CPU (slower)")
        m = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", download_root=hf_cache)
        dev = "CPU"
    audio = load(wav) if load else None
    if not hasattr(audio, "shape"):
        from faster_whisper.audio import decode_audio
        audio = decode_audio(wav, sampling_rate=16000)
    sr = 16000

    def run(a, offset=0.0, vad=True, prompt=None):
        # condition_on_previous_text=False: one misheard stretch no longer makes Whisper skip the next ones
        segs, _ = m.transcribe(a, language=lang, vad_filter=vad, word_timestamps=True,
                               vad_parameters={"min_silence_duration_ms": 500}, beam_size=5,
                               condition_on_previous_text=False, initial_prompt=prompt)
        out = []
        for s in segs:
            for w in s.words or []:
                if w.word.strip():
                    out.append({"w": w.word.strip(), "start": round(float(w.start) + offset, 3),
                                "end": round(float(w.end) + offset, 3), "p": round(float(w.probability), 3)})
        return out
    words = run(audio)
    # second pass: lines that timing.json says are spoken but Whisper heard (almost) nothing of (Bhay 16 Oct:
    # 56-73 s were missing) are transcribed again on their own, without VAD, with the line as a hint
    filled = 0
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(wav)), "timing.json"), encoding="utf-8") as f:
            tim = json.load(f)
    except (OSError, ValueError):
        tim = []
    for line in tim:
        a, b = float(line.get("start", 0)), float(line.get("end", 0))
        expect = len(str(line.get("text", "")).split())
        if b - a < 0.8 or not expect:
            continue
        inside = lambda w: a - 0.25 <= w["start"] <= b + 0.25
        heard = [w for w in words if inside(w)]
        if len(heard) >= 0.5 * expect:
            continue
        lo = max(0.0, a - 0.3)
        new = [w for w in run(audio[int(lo * sr):int((b + 0.3) * sr)], lo, False, str(line.get("text", ""))[:200])
               if inside(w)]
        if len(new) > len(heard):
            words = [w for w in words if not inside(w)] + new
            filled += 1
    words.sort(key=lambda w: w["start"])
    if filled:
        print(f"   {filled} line(s) Whisper skipped were timed again on their own")
    data = {"lang": lang, "model": "faster-whisper large-v3-turbo (" + dev + ")",
            "made": time.strftime("%Y-%m-%d %H:%M:%S"), "source": wav, "words": words}
    tmp = out + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    os.replace(tmp, out)
    print(f"   {len(words)} words timed in {time.time() - t0:.0f} s on the {dev}")


if __name__ == "__main__":
    main()
