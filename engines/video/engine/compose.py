"""
compose.py - final video: living-artwork loop + audio-reactive light line + captions + brand + audio.
Uses PyAV when available (your PC), otherwise ffmpeg.exe pipes.
"""
import sys as _sys
for _s in (_sys.stdout, _sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # Hindi text in logs/consoles on Windows
    except Exception:
        pass
import json, os, shutil, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import overlay as ov

W, H = ov.W, ov.H


def _ff():
    return ov.ffmpeg_exe()


def load_wav16k(path, start=0.0, dur=None):
    cmd = [_ff(), "-v", "error", "-ss", str(start)] + (["-t", str(dur)] if dur else []) + \
          ["-i", path, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.float32)


class FFLoop:
    def __init__(self, path):
        self.path = path; self._open()

    def _open(self):
        self.p = subprocess.Popen([_ff(), "-v", "error", "-i", self.path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                  stdout=subprocess.PIPE, bufsize=W * H * 3 * 4)

    def next(self):
        b = self.p.stdout.read(W * H * 3)
        if len(b) < W * H * 3:
            self.p.wait(); self._open(); b = self.p.stdout.read(W * H * 3)
        return np.frombuffer(b, np.uint8).reshape(H, W, 3)


class FFWriter:
    """Video via ffmpeg pipe, audio muxed from the source file in the same command."""

    def __init__(self, out, fps, audio, a_start, total, enc="libx264"):
        venc = ["-c:v", "h264_nvenc", "-preset", "p6", "-rc", "vbr", "-cq", "18", "-b:v", "0"] if enc == "h264_nvenc" \
            else ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-maxrate", "12M", "-bufsize", "24M"]
        af = f"afade=t=in:d=6,afade=t=out:st={max(0, total - 8):.2f}:d=8"
        self.p = subprocess.Popen([_ff(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                                   "-r", str(fps), "-i", "-", "-ss", str(a_start), "-t", f"{total:.3f}", "-i", audio,
                                   "-map", "0:v", "-map", "1:a", *venc, "-pix_fmt", "yuv420p", "-af", af,
                                   "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out],
                                  stdin=subprocess.PIPE)
        self.codec = "ffmpeg:" + enc

    def write(self, rgb):
        self.p.stdin.write(rgb.tobytes())

    def close(self):
        self.p.stdin.close(); self.p.wait()


class Band:
    """Float view of rows [r0, r1) of a uint8 frame; overlays index it with absolute coordinates."""

    def __init__(self, frame, r0, r1):
        self.frame, self.r0, self.r1 = frame, r0, r1
        self.a = frame[r0:r1].astype(np.float32) * (1 / 255.0)

    def __getitem__(self, key):
        if not isinstance(key, tuple):
            key = (key,)
        ys = key[0]
        ys = slice((ys.start or 0) - self.r0, (ys.stop if ys.stop is not None else self.r1) - self.r0)
        return self.a[(ys,) + key[1:]]

    def commit(self):
        np.clip(self.a, 0, 1, out=self.a)
        self.frame[self.r0:self.r1] = (self.a * 255 + 0.5).astype(np.uint8)


def _build(audio, captions, fps, start, total, viz, brand, moon_xy, lang, title, goodnight, kinetic=None):
    Cap = ov.CaptionHi if lang == "hi" else ov.Caption
    if kinetic:                                       # Mukhtalif word-by-word typography replaces line captions
        import kinetic as kn
        lines = [kn.Line([dict(w=w["w"], start=w["start"] - start, end=w["end"] - start) for w in ln["words"]],
                         ln.get("key")) for ln in kinetic["lines"]
                 if ln["words"][-1]["end"] > start and ln["words"][0]["start"] < start + total]
        caps = [kn.KineticTrack(lines, kinetic.get("style", "mukhtalif"), kinetic.get("region"))]
    else:
        caps = [Cap(t, s - start, e - start) for s, e, t in captions if e > start and s < start + total]
    label = (brand.get("label") if isinstance(brand, dict) else None) or ov.BRAND_WORD
    cards = []
    if brand:                                         # channel opening: big "सुकून 8.0" + episode title
        cards.append(ov.TitleCard(label, 1.0, 12.0, size=150, sub=title, sub_size=54, y_center=450,
                                  col=ov.BLUE, fontfile=ov.BOLD_HI()))
    elif title:
        cards.append(ov.TitleCard(title, 1.5, 11.0, sub="मुख़्तलिफ़ आवाज़" if ov.BRAND_WORD == "सुकून" else "Mukhtalif Awaaz"))
    br = None
    if brand:                                         # ...then it settles into the corner badge for the whole video
        br = ov.Brand(title=label, t_in=13.0)
        br.extras = ov.opening_extras(hold=min(60.0, max(20.0, total * 0.3)), total=total)
    if goodnight and total > 60:
        cards.append(ov.TitleCard(goodnight, max(0, total - 16), total - 1.0, size=96))
    eq = ov.LightLine() if viz == "line" else ov.SoftBars() if viz == "bars" else None
    halo = None
    if moon_xy:
        mx, my = int(moon_xy[0]), int(moon_xy[1])
        hb = (max(0, my - 320), min(H, my + 320), max(0, mx - 320), min(W, mx + 320))
        yy, xx = np.mgrid[hb[0]:hb[1], hb[2]:hb[3]]
        d = np.hypot(xx - mx, yy - my)
        halo = ((np.exp(-(d / 260) ** 2))[..., None].astype(np.float32) * ov.LAV, hb)
    return caps, br, cards, eq, halo


def _draw(frame, i, t, total, fps, bands, level, caps, br, cards, eq, halo, dim_last, dim_amount):
    lv = float(level[min(i, len(level) - 1)])
    if halo is not None and lv > 0.02:
        hm, (hy0, hy1, hx0, hx1) = halo
        b = Band(frame, hy0, hy1)
        b.a[:, hx0:hx1] += hm * (0.05 * lv)
        b.commit()
    if caps and hasattr(caps[0], "bbox"):                         # kinetic track
        kt, caps = caps[0], []
        ys =[kt.bbox(li) for li in kt.active(t)]
        if ys:
            y0, y1 = min(a for a, _ in ys), max(c for _, c in ys)
            b = Band(frame, y0, y1)
            kt.draw(b.a, t, y_off=y0)
            b.commit()
    active = [cp for cp in caps if cp.start <= t <= cp.end]
    lo = min([cp.y0 - 30 for cp in active] + ([eq.top - 10] if eq is not None else [H]))
    if active or eq is not None:
        b = Band(frame, max(0, lo), H)
        if eq is not None:
            eq.draw(b, bands[min(i, len(bands) - 1)], t, lv)
        for cp in active:
            cp.draw(b, t)
        b.commit()
    for cd in cards:
        if cd.start <= t <= cd.end:
            b = Band(frame, max(0, cd.top), min(H, cd.bottom))
            cd.draw(b, t)
            b.commit()
    if br is not None:
        if t >= br.t_in:
            b = Band(frame, 0, 200)
            br.draw(b, t=t)
            b.commit()
        lvs = float(level[max(0, min(i, len(level) - 1) - 36):min(i, len(level) - 1) + 1].mean())   # 1.5 s calm level
        for sp in getattr(br, "extras", []):
            if sp.start <= t <= sp.end:
                b = Band(frame, sp.y, sp.y + sp.h)
                sp.draw(b, t, lvs)
                b.commit()
    g = min(1.0, t / 4.0, (total - t) / 6.0)
    if dim_last and t > total * (1 - dim_last):                  # sleep-friendly gradual dimming
        g *= 1 - dim_amount * min(1.0, (t - total * (1 - dim_last)) / (total * dim_last))
    if g < 1:
        frame = (frame.astype(np.float32) * max(g, 0.0)).astype(np.uint8)
    return frame


def _open_loop(loop, use_av, skip):
    if use_av:
        import sv_av
        src = sv_av.LoopReader(loop)
    else:
        src = FFLoop(loop)
    for _ in range(skip):
        src.next()
    return src


def _video_writer(path, fps, use_av):
    if use_av:
        import sv_av
        return sv_av.VideoWriter(path, W, H, fps, with_audio=False)
    p = subprocess.Popen([_ff(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                          "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                          "-maxrate", "10M", "-bufsize", "20M", "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)

    class _W:
        codec = "ffmpeg:libx264"
        def write(self, f): p.stdin.write(f.tobytes())
        def close(self): p.stdin.close(); p.wait()
    return _W()


def _loop_len(loop):
    out = subprocess.run([_ff().replace("ffmpeg.exe", "ffprobe.exe").replace("/ffmpeg", "/ffprobe"), "-v", "error",
                          "-count_packets", "-select_streams", "v:0", "-show_entries", "stream=nb_read_packets",
                          "-of", "csv=p=0", loop], capture_output=True, text=True).stdout.strip()
    return int(out or 0)


def render_part(job):
    """Worker: render frames [i0, i1) to a video-only file. job is a plain dict (picklable)."""
    import pickle
    st = pickle.load(open(job["state"], "rb"))
    d = np.load(job["bands"])
    bands, level = d["bands"], d["level"]
    caps, br, cards, eq, halo = _build(st["audio"], st["captions"], st["fps"], st["start"], st["total"], st["viz"],
                                       st["brand"], st["moon_xy"], st["lang"], st["title"], st["goodnight"],
                                       st.get("kinetic"))
    fps, total = st["fps"], st["total"]
    i0, i1 = job["i0"], job["i1"]
    src = _open_loop(st["loop"], st["use_av"], i0 % max(1, st["loop_len"]) if st["loop_len"] else 0)
    wr = _video_writer(job["out"], fps, st["use_av"])
    t0 = time.time()
    for i in range(i0, i1):
        frame = np.array(src.next(), copy=True)
        frame = _draw(frame, i, i / fps, total, fps, bands, level, caps, br, cards, eq, halo,
                      st["dim_last"], st["dim_amount"])
        wr.write(frame)
        if job.get("report") and (i - i0) and (i - i0) % (fps * 30) == 0:
            el = time.time() - t0; k = i - i0
            print(f"      CPU render part {job['k']}: {k}/{i1-i0} frames ({k/el:.0f} fps, ~{(i1-i0-k)/(k/el)/60:.1f} min left)", flush=True)
    wr.close()
    return job["out"]


def _dim_at(t, total, dl, da):
    """The slow sleep-dimming factor at time t (same formula as the renderers)."""
    if dl and t > total * (1 - dl):
        return 1 - da * min(1.0, (t - total * (1 - dl)) / (total * dl))
    return 1.0


def _reuse_plan(st, chunks, level, fps, total):
    """Chunks of the calm, music-only part that can re-use an already drawn chunk instead of being drawn again.
    -> {chunk index: template chunk index}.  Only when nothing else is on screen (no words, no title/goodnight,
    no fades) and the music's loudness is steady and close to the template's."""
    L = int(st.get("loop_len") or 0)
    step = chunks[0][1] - chunks[0][0] if chunks else 0
    if not L or not step or step % L:                      # copies must keep the artwork loop seamless
        return {}
    busy = []
    for ln in (st.get("kinetic") or {}).get("lines", []):
        ws = ln.get("words") or []
        if ws:
            busy.append((float(ws[0]["start"]) - st["start"] - 1.0, float(ws[-1]["end"]) - st["start"] + 3.5))
    plan, tmpl = {}, None
    for k, (c0, c1, f) in enumerate(chunks):
        t0, t1 = c0 / fps, c1 / fps
        plain = (t0 >= 20 and t1 <= total - 20 and c1 - c0 == step
                 and not any(a < t1 and b > t0 for a, b in busy))
        lv = np.asarray(level[c0:c1], np.float32)
        if not plain or len(lv) < step:
            tmpl = None
            continue
        p5, p95, mean = float(np.percentile(lv, 5)), float(np.percentile(lv, 95)), float(lv.mean())
        calm = (p95 - p5) < 0.35
        if tmpl is not None and calm and abs(mean - tmpl[1]) < 0.12 and k - tmpl[0] <= 8:   # fresh one every ~18 min
            plan[k] = tmpl[0]
        else:
            tmpl = (k, mean) if calm else None             # this one is drawn, and can be re-used next
    return plan


def _make_copies(chunks, copies, st, fps, total):
    """Write the re-used chunks: an exact copy, or the copy slightly darker when the sleep-dimming has moved on."""
    dl, da = st.get("dim_last") or 0, st.get("dim_amount") or 0
    venc = ["-c:v", "h264_nvenc", "-preset", "p6", "-rc", "vbr", "-cq", "21", "-b:v", "0", "-maxrate", "10M",
            "-bufsize", "20M", "-spatial-aq", "1", "-g", str(fps * 2)]
    for k, tk in sorted(copies.items()):
        c0, c1, f = chunks[k]
        if os.path.exists(f):
            continue
        src = chunks[tk][2]
        r = _dim_at((c0 + c1) / 2 / fps, total, dl, da) / _dim_at((chunks[tk][0] + chunks[tk][1]) / 2 / fps, total, dl, da)
        part = f + ".part.mp4"
        if abs(r - 1) < 0.004:
            shutil.copyfile(src, part)
        else:
            lut = f"lutyuv=y='16+(val-16)*{r:.4f}':u='128+(val-128)*{r:.4f}':v='128+(val-128)*{r:.4f}'"
            subprocess.run([_ff(), "-y", "-v", "error", "-i", src, "-vf", lut, *venc, "-an", "-f", "mp4", part],
                           check=True)
        os.replace(part, f)
    print(f"      re-used {len(copies)} calm chunks", flush=True)


def compose(audio, loop, captions, out, fps=24, start=0.0, duration=None, viz="line", brand=True,
            moon_xy=None, use_av=None, lang="hi", title=None, goodnight="शुभ रात्रि", dim_last=0.33,
            dim_amount=0.25, workers=None, kinetic=None):
    """captions: list of (start, end, text) in seconds of the ORIGINAL audio."""
    import pickle, shutil
    total_audio = float(subprocess.run([_ff().replace("ffmpeg.exe", "ffprobe.exe").replace("/ffmpeg", "/ffprobe"),
                                        "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio],
                                       capture_output=True, text=True).stdout.strip() or 0)
    total = min(duration, total_audio - start) if duration else total_audio - start
    if use_av is None:
        try:
            import av  # noqa
            use_av = True
        except ImportError:
            use_av = False
    workers = workers or max(1, min(4, (os.cpu_count() or 2) // 2))
    print(f"      preparing audio-reactive light line ...", flush=True)
    wav = load_wav16k(audio, start, total)
    bands, level = ov.audio_bands(wav, 16000, fps)
    tmp = os.path.join(os.path.dirname(os.path.abspath(out)), "_parts_" + os.path.splitext(os.path.basename(out))[0])
    os.makedirs(tmp, exist_ok=True)
    np.savez(os.path.join(tmp, "bands.npz"), bands=bands, level=level)
    st = dict(audio=audio, captions=captions, fps=fps, start=start, total=total, viz=viz, brand=brand,
              moon_xy=moon_xy, lang=lang, title=title, goodnight=goodnight, loop=loop, use_av=use_av, kinetic=kinetic,
              loop_len=_loop_len(loop), dim_last=dim_last, dim_amount=dim_amount)
    if kinetic and kinetic.get("region") is None:      # place text in the calmest parts of THIS artwork
        import kinetic as kn
        f0 = np.array(_open_loop(loop, use_av, 0).next(), copy=True)
        boxes = []
        try:
            import subject
            boxes = subject.subject_boxes(loop, _ff())
            if boxes:
                print(f"      keeping text away from {len(boxes)} person/subject area(s) in the artwork", flush=True)
        except Exception as e:
            print(f"      (subject check skipped: {e})", flush=True)
        try:
            import paths
            import sv_av
            sv_av.unload_model(paths.CFG.get("vision_llm"))
        except Exception as e:
            print(f"      (could not unload Ollama vision model before GPU rendering: {e})", flush=True)
        regs = kn.calm_region(f0, 880, 420, k=2, avoid=boxes)
        kinetic = dict(kinetic, region=[list(r) for r in (regs if isinstance(regs, list) else [regs])])
        st["kinetic"] = kinetic
        print(f"      text will sit in the calm areas at {kinetic['region']}", flush=True)
    pickle.dump(st, open(os.path.join(tmp, "state.pkl"), "wb"))
    try:                                               # what the Instagram reels need later (kept after the render)
        json.dump(dict(audio=os.path.abspath(audio), loop=os.path.abspath(loop), fps=fps, start=start,
                       label=(brand.get("label") if isinstance(brand, dict) else None), title=title),
                  open(os.path.splitext(out)[0] + ".reel_info.json", "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    import hashlib
    code = b"".join(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), f), "rb").read()
                    for f in ("overlay.py", "kinetic.py", "gpu_compose.py", "compose.py")
                    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), f)))
    # the drawing code is part of the fingerprint too: after a look change (e.g. the corner badge) a resumed
    # render must not mix old-look chunks with new ones
    fp = hashlib.sha1(pickle.dumps(st) + bands.tobytes()[:100000] + code).hexdigest()
    fpf = os.path.join(tmp, "fingerprint.txt")
    if not os.path.exists(fpf) or open(fpf).read() != fp:
        for f in os.listdir(tmp):
            if f.startswith("gpu_") and f.endswith(".mp4"):
                os.remove(os.path.join(tmp, f))           # settings changed -> old chunks are stale
        open(fpf, "w", encoding="utf-8").write(fp)
    # 1st choice: draw every frame on the GPU (ComfyUI's Python has PyTorch + CUDA) and encode with NVENC
    import paths
    comfy = paths.GPU_PYTHON
    if os.environ.get("SV_GPU", "1") != "0" and os.path.exists(comfy):
        # GPU render in ~2-minute chunks: after a crash / power cut only the unfinished chunk is redone
        t0 = time.time()
        n = int(total * fps)
        step_f = fps * 120
        cuts = list(range(0, n, step_f)) + [n]
        chunks = [(cuts[k], cuts[k + 1], os.path.join(tmp, f"gpu_{k:03d}.mp4")) for k in range(len(cuts) - 1)]
        done = sum(1 for c0, c1, f in chunks if os.path.exists(f))
        if done:
            print(f"      resuming: {done} of {len(chunks)} video chunks were already finished", flush=True)
        copies = _reuse_plan(st, chunks, level, fps, total)   # calm music-only stretches: draw once, reuse
        if copies:
            saved = sum(c1 - c0 for k, (c0, c1, f) in enumerate(chunks) if k in copies) / fps / 60
            print(f"      calm music-only part: {len(copies)} of {len(chunks)} chunks are re-used instead of drawn"
                  f" (~{saved:.0f} min of video)", flush=True)
        print("      drawing every frame on the GPU + NVENC encoding ...", flush=True)
        pending = [[c0, c1, f] for k, (c0, c1, f) in enumerate(chunks) if not os.path.exists(f) and k not in copies]
        jp = os.path.join(tmp, "gpu_jobs.json")
        json.dump(pending, open(jp, "w", encoding="utf-8"))
        r = subprocess.run([comfy, "-s", os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpu_compose.py"),
                            os.path.join(tmp, "state.pkl"), os.path.join(tmp, "bands.npz"), jp])
        if r.returncode == 0 and copies:
            _make_copies(chunks, copies, st, fps, total)
        ok = r.returncode == 0 and all(os.path.exists(f) for _, _, f in chunks)
        if ok:
            lst = os.path.join(tmp, "list_gpu.txt")
            open(lst, "w", encoding="utf-8").write("".join(f"file '{os.path.abspath(f).replace(os.sep, '/')}'\n" for _, _, f in chunks))
            af = f"afade=t=in:d=6,afade=t=out:st={max(0, total - 8):.2f}:d=8"
            tmp_out = out + ".part.mp4"
            subprocess.run([_ff(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
                            "-ss", str(start), "-t", f"{total:.3f}", "-i", audio, "-map", "0:v", "-map", "1:a",
                            "-c:v", "copy", "-af", af, "-c:a", "aac", "-b:a", "192k", "-shortest",
                            "-movflags", "+faststart", tmp_out], check=True)
            os.replace(tmp_out, out)
            shutil.rmtree(tmp, ignore_errors=True)
            print(f"      video finished in {(time.time()-t0)/60:.1f} min (GPU)", flush=True)
            return
        print("      (GPU render failed - using the slower CPU renderer instead)", flush=True)
    n = int(total * fps)
    cuts = [round(n * k / workers) for k in range(workers + 1)]
    jobs = [dict(state=os.path.join(tmp, "state.pkl"), bands=os.path.join(tmp, "bands.npz"), i0=cuts[k],
                 i1=cuts[k + 1], k=k, report=(k == 0), out=os.path.join(tmp, f"part{k}.mp4")) for k in range(workers)]
    t0 = time.time()
    procs = []
    for j in jobs:
        jp = j["out"] + ".json"
        json.dump(j, open(jp, "w", encoding="utf-8"))
        procs.append(subprocess.Popen([sys.executable, "-s", os.path.abspath(__file__), "--part", jp]))
    codes = [p.wait() for p in procs]
    if any(codes):
        raise RuntimeError(f"a render part failed (exit codes {codes})")
    parts = [j["out"] for j in jobs]
    print(f"      frames done in {(time.time()-t0)/60:.1f} min; adding the audio ...", flush=True)
    lst = os.path.join(tmp, "list.txt")
    open(lst, "w", encoding="utf-8").write("".join(f"file '{os.path.abspath(p).replace(os.sep, '/')}'\n" for p in parts))
    af = f"afade=t=in:d=6,afade=t=out:st={max(0, total - 8):.2f}:d=8"
    subprocess.run([_ff(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
                    "-ss", str(start), "-t", f"{total:.3f}", "-i", audio, "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-af", af, "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", out], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"      video finished in {(time.time()-t0)/60:.1f} min (CPU)", flush=True)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--part":
        render_part(json.load(open(sys.argv[2])))
