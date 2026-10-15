"""
extras.py - (re)make the short clips (16:9) and Instagram reels (9:16) of an episode that is already rendered,
without rendering the long video again.  Used by make_reels.bat.

    python extras.py <output\\episode folder | episode folder | video.mp4>  [--reels 3] [--clips 4]
"""
import argparse, os, re, sys, time

ENGINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ENGINE)
from sv_av import HERE      # noqa: E402
import paths                # noqa: E402


def resolve(p):
    p = os.path.abspath(p.strip('"'))
    if p.lower().endswith(".mp4"):
        name = os.path.splitext(os.path.basename(p))[0]
        return name, p
    p = p.rstrip("\\/")
    if os.path.basename(p).lower() == paths.EPISODE_OUTPUT_NAME.lower():   # dropped <episode>\Output
        p = os.path.dirname(p)
    name = re.sub(r"[^\w\-]+", "_", os.path.basename(p)).strip("_")
    for v in (os.path.join(p, paths.EPISODE_OUTPUT_NAME, name + ".mp4"),    # episode folder
              os.path.join(p, name + ".mp4"),                              # an output folder
              os.path.join(paths.VIDEO_OUTPUT_DIR, name, name + ".mp4")):   # standalone HubGPT Video output
        if os.path.exists(v):
            return name, v
    return name, os.path.join(p, paths.EPISODE_OUTPUT_NAME, name + ".mp4")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--reels", type=int, default=3)
    ap.add_argument("--clips", type=int, default=4)
    a = ap.parse_args()
    name, video = resolve(a.path)
    work = os.path.join(paths.VIDEO_WORK_DIR, name)
    bar = "=" * 70
    print(f"{bar}\n  MUKHTALIF STUDIO - clips + Instagram reels\n  episode : {name}\n{bar}", flush=True)
    if not os.path.exists(video):
        sys.exit(f"\n  No finished video found: {video}\n  Make the video first with make_video.bat.\n")
    if not os.path.exists(os.path.join(work, "moments.json")):
        sys.exit(f"\n  No key moments found in {work}\n")
    t0 = time.time()
    n_c = n_r = 0
    if a.clips:
        print(f"\n[1/2] Short clips (16:9) around the best moments ...", flush=True)
        import clips
        n_c = len(clips.make(work, video, a.clips))
    if a.reels:
        print(f"\n[2/2] Instagram reels (9:16) - about 1 min each ...", flush=True)
        import reels
        n_r = len(reels.make(work, video, a.reels))
    od = os.path.dirname(video)
    el = time.time() - t0
    print(f"\n{bar}\n  DONE in {int(el // 60)} min {int(el % 60)} s\n  clips : {n_c} in {os.path.join(od, 'clips')}"
          f"\n  reels : {n_r} in {os.path.join(od, 'reels')}  (caption .txt next to each)"
          f"\n  change a hook: edit work\\{name}\\reels.json and run this again\n{bar}", flush=True)


if __name__ == "__main__":
    main()
