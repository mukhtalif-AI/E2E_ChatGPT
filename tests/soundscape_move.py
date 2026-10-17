"""soundscape_move.py - decision B (17 Oct 2026, SOUNDSCAPE_MOVE_LIST.md groups 1 and 2): files in the music folders
that are not music move out, names unchanged.

    python3 soundscape_move.py <Audio asset folder> <data folder with audio_index.json> [--undo]

Group 1 (12 soundscapes) -> SFX\\rain, SFX\\sea or SFX\\atmo (ambience beds). Group 2 (5 non-music files, already
blocked by the screening) -> <Audio asset>\\_not_music. Group 3 (5 sparse artist tracks) stays where it is.
Before moving: copies of BGM_INDEX.csv, SFX_INDEX.csv and audio_index.json next to them (_old_20261017_soundscapes).
Licence rows of group 1 move from BGM_INDEX.csv to SFX_INDEX.csv; audio_index.json paths are updated.
Writes <Audio asset>\\_not_music\\soundscape_move_20261017.json (what moved where); --undo moves everything back and
restores the three copies.
"""
import csv, json, os, shutil, sys

ROOT, DATA = sys.argv[1], sys.argv[2]
UNDO = "--undo" in sys.argv
SUF = "_old_20261017_soundscapes"
MOVES = [  # (folder now, file, to)
    ("Calm", "Rain On Rooftop.mp3", "SFX/rain"),
    ("Calm", "freesound_community-air-tone-in-a-city-from-the-9th-floor-32858.mp3", "SFX/atmo"),
    ("Dark", "Haunted rainy street - Cinematic Atmo Amb Dark Fan-szegvari-freesound595406.mp3", "SFX/rain"),
    ("Dark", "Dark London Night Atmo Thriller Soundscape Experim-szegvari-freesound596463.mp3", "SFX/atmo"),
    ("Dark", "City Rain Darkwav-szegvari-freesound581148.mp3", "SFX/rain"),
    ("Dark", "Beach Coast Cave Scape Atmosphere-szegvari-freesound580149.mp3", "SFX/sea"),
    ("Dark", "Space dark atmowav-szegvari-freesound583590.mp3", "SFX/atmo"),
    ("Dark", "Dark Analog Drone-Nielsvdb-freesound658648.mp3", "SFX/atmo"),
    ("Dark", "Long Modular Christmas Dronewav-gis_sweden-freesound371519.mp3", "SFX/atmo"),
    ("Dark", "drone0019flac-Trebblofang-freesound197215.mp3", "SFX/atmo"),
    ("Dark", "Shadow Maker - Kitchen-TheMovieMacher-freesound329083.mp3", "SFX/atmo"),
    ("Dark", "CWD_LT_Der Sternmp3-ColdWombDescent-freesound398533.mp3", "SFX/atmo"),
    ("Dark", "freesound_community-crate-break-1-93926.mp3", "_not_music"),
    ("Sad", "ribhavagrawal-a-woman-sobbing-type-1-265493.mp3", "_not_music"),
    ("Dark", "sci-fi drone 2-kinoton-freesound479328.mp3", "_not_music"),
    ("Dark", "all comments-the_odds-freesound268198.mp3", "_not_music"),
    ("Dark", "drone6wav-phantastonia-freesound272595.mp3", "_not_music"),
]
BGM_CSV = os.path.join(ROOT, "BGM", "BGM_INDEX.csv")
SFX_CSV = os.path.join(ROOT, "SFX", "SFX_INDEX.csv")
IDX = os.path.join(DATA, "audio_index.json")
LOG = os.path.join(ROOT, "_not_music", "soundscape_move_20261017.json")
win = lambda rel: "D:\\Audio asset\\" + rel.replace("/", "\\")


def undo():
    done = json.load(open(LOG, encoding="utf-8"))
    for m in done["moved"]:
        if os.path.exists(m["to"]) and not os.path.exists(m["from"]):
            os.rename(m["to"], m["from"])
            print("back:", m["from"])
    for p in (BGM_CSV, SFX_CSV, IDX):
        if os.path.exists(p + SUF):
            shutil.copy2(p + SUF, p)
            print("restored:", p)


def main():
    os.makedirs(os.path.join(ROOT, "_not_music"), exist_ok=True)
    for p in (BGM_CSV, SFX_CSV, IDX):
        if not os.path.exists(p + SUF):
            shutil.copy2(p, p + SUF)
    moved, missing = [], []
    for folder, name, to in MOVES:
        src = os.path.join(ROOT, "BGM", folder, name)
        dst_dir = os.path.join(ROOT, *to.split("/"))
        dst = os.path.join(dst_dir, name)
        if not os.path.exists(src):
            missing.append(src)
            continue
        os.makedirs(dst_dir, exist_ok=True)
        assert not os.path.exists(dst), dst
        os.rename(src, dst)
        moved.append({"from": src, "to": dst, "win_from": win(f"BGM/{folder}/{name}"), "win_to": win(f"{to}/{name}"),
                      "category": to.split("/")[-1]})
        print("moved:", name, "->", to)
    json.dump({"moved": moved, "missing": missing}, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    by_name = {os.path.basename(m["to"]): m for m in moved}
    # licence rows of the ambience files: BGM register -> SFX register (the not-music files keep their BGM row)
    with open(BGM_CSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        bgm_fields = list(rows[0].keys()) if rows else []
    keep, go = [], []
    for r in rows:
        m = by_name.get(r.get("file"))
        (go if m and m["category"] != "_not_music" else keep).append(r)
    if go:
        with open(BGM_CSV, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=bgm_fields)
            w.writeheader()
            w.writerows(keep)
        with open(SFX_CSV, encoding="utf-8-sig", newline="") as f:
            sfx_fields = csv.DictReader(f).fieldnames
        with open(SFX_CSV, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=sfx_fields, extrasaction="ignore")
            for r in go:
                w.writerow(dict(r, category=by_name[r["file"]]["category"]))
    print(f"licence rows moved to SFX_INDEX.csv: {len(go)}")
    idx = json.load(open(IDX, encoding="utf-8"))
    n = 0
    for t in idx.get("tracks") or []:
        m = by_name.get(os.path.basename(str(t.get("file", "")).replace("\\", "/")))
        if m and t.get("file") == m["win_from"]:
            t["file"], t["mood"] = m["win_to"], m["category"]
            t["kind"] = "background" if m["category"] != "_not_music" else t.get("kind")
            n += 1
    json.dump(idx, open(IDX, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"audio_index.json entries updated: {n}; missing files: {len(missing)}")


undo() if UNDO else main()
