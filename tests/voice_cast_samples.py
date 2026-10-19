"""voice_cast_samples.py - listen to the ghost voice presets on a real line of a finished episode (nothing changed there).

    python tests\\voice_cast_samples.py <episode folder> "<words of the line>" <out folder> [--ffmpeg path] [--presets a,b]

Finds the line in the episode's timing.json, cuts it out of narration.wav with 1.5 s around it, and writes one mp3
per preset (plus 0_as_now.mp3, the line as the episode has it) into <out folder>.
"""
import glob, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "app"))
import voice_cast as v


def main(argv):
    ff = "ffmpeg"
    if "--ffmpeg" in argv:
        k = argv.index("--ffmpeg"); ff = argv[k + 1]; del argv[k:k + 2]
    presets = list(v.PRESETS)
    if "--presets" in argv:
        k = argv.index("--presets"); presets = argv[k + 1].split(","); del argv[k:k + 2]
    ep, words, out = argv[:3]
    nar = os.path.dirname(sorted(glob.glob(os.path.join(ep, "02_Narration", "narrations", "*", "timing.json")))[0])
    tim = json.load(open(os.path.join(nar, "timing.json"), encoding="utf-8"))
    hit = v.find_lines(tim, words)
    if not hit:
        sys.exit(f"'{words}' is not in {nar}\\timing.json")
    i, j, r = hit
    a, b = float(tim[i]["start"]), float(tim[j]["end"])
    dry, sr, width = v.read_wav(os.path.join(nar, "narration.wav"))
    lo, hi = max(0, int((a - 1.5) * sr)), min(len(dry), int((b + 1.5) * sr))
    part = dry[lo:hi]
    os.makedirs(out, exist_ok=True)
    tag = v._words(words)[0] if v._words(words) else "line"

    def mp3(x, name):
        wav = os.path.join(out, name + ".wav")
        v.write_wav(wav, x, sr, width)
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", "-i", wav, "-b:a", "160k",
                        os.path.join(out, name + ".mp3")], check=True)
        os.remove(wav)
        print("wrote", os.path.join(out, name + ".mp3"))

    mp3(part, f"{os.path.basename(nar)}_0_as_now")
    for p in presets:
        segs = []
        for k in range(i, j + 1):
            nxt = float(tim[k + 1]["start"]) if k + 1 < len(tim) else float(tim[k]["end"]) + 1
            segs.append({"start": float(tim[k]["start"]) - lo / sr, "end": float(tim[k]["end"]) - lo / sr,
                         "tail": max(0.0, min(0.45, nxt - float(tim[k]["end"]) - 0.08)), "preset": p})
        mp3(v.apply(ff, part, sr, segs), f"{os.path.basename(nar)}_{p}")
    print(f"line {tim[i].get('n')}-{tim[j].get('n')} at {a:.1f}-{b:.1f} s, match {r:.2f}")


if __name__ == "__main__":
    main(sys.argv[1:])
