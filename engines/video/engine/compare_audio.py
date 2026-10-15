import sys, subprocess, numpy as np
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths
ff = paths.ffmpeg() or "ffmpeg"
def load(p):
    return np.frombuffer(subprocess.run([ff, "-v", "error", "-i", p, "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                                        capture_output=True).stdout, np.float32)
a, b = load(sys.argv[1]), load(sys.argv[2])
n = min(len(a), len(b))
print(f"length: export {len(a)/8000/60:.2f} min, auto-mix {len(b)/8000/60:.2f} min")
a, b = a[:n], b[:n]
rms = lambda x: 20 * np.log10(np.sqrt((x.astype(np.float64) ** 2).mean()) + 1e-12)
print(f"level : export {rms(a):.1f} dB, auto-mix {rms(b):.1f} dB  (difference {rms(b)-rms(a):+.1f} dB)")
c = np.corrcoef(a, b)[0, 1]
print(f"match : {c*100:.1f}%  (above 95% = the auto-mix sounds like your export)")
