"""tests/test_fix_music_check.py - music_check fixes on COPIES (the episode is never written): check, fix into
<out dir>/<episode>.wav, check the fixed copy again. Linux device VM: python3 test_fix_music_check.py <app> <data root> <out dir> <narration dir>..."""
import json, ntpath, os, sys, time, types

app, root, outd = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, app)
sys.modules.setdefault("tools", types.ModuleType("tools"))
exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "calib_music_check.py")).read().split("res = {}")[0]
     .split("app, root, outp = sys.argv[1], sys.argv[2], sys.argv[3]")[1].replace("sys.path.insert(0, app)", ""))
os.makedirs(outd, exist_ok=True)
for nar in sys.argv[4:]:
    genre = nar.split("/Series/")[1].split("/")[0] if "/Series/" in nar else ""
    name = os.path.basename(nar)
    t0 = time.time()
    r = music_check.check(cfg, nar, genre)
    f0 = [x["rule"] for x in r["rows"] if not x["ok"]]
    rm, rf, done = music_check.plan_fixes(r, genre)
    trial = music_check.measure(music_check.apply_parts(r["parts"], rm, rf), genre, r["map"])
    rows_t = music_check.judge(trial, r["tfound"], genre)
    out = os.path.join(outd, name + ".wav")
    ok = music_check.render_fixed(cfg, r["parts"], rm, rf, out) if done else False
    r2 = music_check.check(cfg, nar, genre, mix_path=out) if ok else None
    print(f"== {name} ({genre}) {time.time() - t0:.0f}s")
    print("   before:", ", ".join(f0) or "-")
    for rule, what in done:
        print("   fix:", rule, "-", what)
    print("   after (parts):", ", ".join(x["rule"] for x in rows_t if not x["ok"]) or "-")
    if ok:                                                   # does the file hold what the curves promised?
        import numpy as np
        fp = music_check.apply_parts(r["parts"], rm, rf)
        want = fp["music"] + (fp["fx"] if fp["fx"] is not None else 0)
        got = music_check.decode("ffmpeg", out)
        n = min(len(got), len(want))
        g = float(np.dot(got[:n], r["parts"]["mix"][:n]) / np.dot(r["parts"]["mix"][:n], r["parts"]["mix"][:n]))
        vv = r["parts"]["voice"][:n]
        bed = got[:n] - vv * (np.dot(got[:n], vv) / np.dot(vv, vv))
        want = want[:n] * (np.dot(bed, want[:n]) / np.dot(want[:n], want[:n]))
        print(f"   file vs curves: bed error {10 * np.log10(((bed - want) ** 2).mean() / (want ** 2).mean()):.1f} dB")
    if r2:
        print("   after (file): ", ", ".join(x["rule"] for x in r2["rows"] if not x["ok"]) or "-",
              "| swell", r2["meas"]["swell_s"], "jumps", len(r2["meas"]["jumps"]), "clarity", r2["meas"].get("clarity_p10_db"),
              "band", r2["meas"].get("music_band_db"))
    print("   report:\n" + "\n".join("     " + ln for ln in music_check.report_text(name, r, dict(r, rows=rows_t, meas=trial), done, "", []).splitlines()[:14]), flush=True)
