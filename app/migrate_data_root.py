"""Bring the old Experimental data folder (D:\\HubGPT) into the one data folder (D:\\MukhtalifGPT).

    python app\\migrate_data_root.py            dry run: only lists what would be copied (default)
    python app\\migrate_data_root.py --copy     copies the missing files (never overwrites, never deletes)
    python app\\migrate_data_root.py --switch   points config.json experimental_root at the new folder

Old and new folders come from config.json (paths.legacy_roots[0] and paths.hub_output_root);
--old / --new override them. Copied .json files and ratings.sqlite get their D:\\HubGPT paths rewritten
to the new folder. The old folder is left exactly as it is: delete it yourself once you are happy.
Report: logs\\migrate_data_root_<time>.txt
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.json")


def load_cfg():
    with open(CONFIG, encoding="utf-8-sig") as f:
        return json.load(f)


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def running_jobs(old):
    try:
        jobs = json.load(open(os.path.join(old, "Activity", "jobs.json"), encoding="utf-8"))
    except Exception:
        return []
    return [j.get("id") for j in jobs if j.get("status") in ("running", "queued")]


def rewrite_text(path, old, new):
    pairs = [(old.replace("\\", "\\\\"), new.replace("\\", "\\\\")), (old, new),
             (old.replace("\\", "/"), new.replace("\\", "/"))]
    try:
        s = open(path, encoding="utf-8").read()
    except (UnicodeDecodeError, OSError):
        return False
    t = s
    for a, b in pairs:
        t = t.replace(a, b)
    if t != s:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(t)
    return t != s


def rewrite_sqlite(path, old, new):
    con = sqlite3.connect(path)
    try:
        for col in ("path", "context", "item_id"):
            con.execute(f"UPDATE ratings SET {col}=replace({col}, ?, ?) WHERE {col} LIKE ?",
                        (old, new, "%" + old + "%"))
            con.execute(f"UPDATE ratings SET {col}=replace({col}, ?, ?) WHERE {col} LIKE ?",
                        (old.replace("\\", "\\\\"), new.replace("\\", "\\\\"), "%" + old.replace("\\", "\\\\") + "%"))
        con.commit()
    finally:
        con.close()


def main():
    cfg = load_cfg()
    paths = cfg.get("paths") or {}
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default=(paths.get("legacy_roots") or [r"D:\HubGPT"])[0])
    ap.add_argument("--new", default=paths.get("hub_output_root") or r"D:\MukhtalifGPT")
    ap.add_argument("--copy", action="store_true")
    ap.add_argument("--switch", action="store_true")
    ap.add_argument("--report-dir", default=os.path.join(ROOT, "logs"))
    a = ap.parse_args()
    old, new = os.path.abspath(a.old), os.path.abspath(a.new)
    if not os.path.isdir(old):
        sys.exit(f"old folder not found: {old}")
    os.makedirs(new, exist_ok=True)

    plan = {"copy": [], "same": [], "differs": []}
    for d, _dirs, files in os.walk(old):
        for name in files:
            src = os.path.join(d, name)
            rel = os.path.relpath(src, old)
            dst = os.path.join(new, rel)
            size = os.path.getsize(src)
            if not os.path.exists(dst):
                plan["copy"].append((rel, size))
            elif os.path.getsize(dst) == size:
                plan["same"].append((rel, size))
            else:
                plan["differs"].append((rel, size))

    by_top = {}
    for rel, size in plan["copy"]:
        top = os.path.join(*rel.split(os.sep)[:2]) if os.sep in rel else rel
        n, b = by_top.get(top, (0, 0))
        by_top[top] = (n + 1, b + size)
    lines = [f"Data folder move   {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"from  {old}", f"into  {new}",
             f"mode  {'COPY' if a.copy else 'dry run (nothing copied)'}", "",
             f"would copy {len(plan['copy'])} files, {human(sum(s for _, s in plan['copy']))}",
             f"already there (same size, skipped) {len(plan['same'])}",
             f"exists with a different size (skipped, never overwritten) {len(plan['differs'])}", "",
             "By folder:"]
    lines += [f"  {top:<60} {n:>5} files  {human(b):>10}" for top, (n, b) in sorted(by_top.items())]
    if plan["differs"]:
        lines += ["", "Not copied because the new folder already has a different file:"]
        lines += [f"  {rel}" for rel, _ in plan["differs"]]
    lines += ["", "Every file:"] + [f"  {rel}  ({human(s)})" for rel, s in plan["copy"]]

    if a.copy:
        busy = running_jobs(old)
        if busy:
            sys.exit("Experimental jobs are still running or queued (" + ", ".join(busy) +
                     "). Let them finish or stop them, then run --copy again.")
        changed = 0
        for rel, _ in plan["copy"]:
            src, dst = os.path.join(old, rel), os.path.join(new, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            if dst.lower().endswith((".json", ".txt", ".log")) and rewrite_text(dst, old, new):
                changed += 1
            elif dst.lower().endswith(".sqlite"):
                rewrite_sqlite(dst, old, new)
                changed += 1
        lines.insert(4, f"copied {len(plan['copy'])} files, paths rewritten in {changed}")

    if a.switch:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        shutil.copy2(CONFIG, CONFIG + f"_old_{stamp}")
        cfg.setdefault("paths", {})["experimental_root"] = new
        legacy = cfg["paths"].setdefault("legacy_roots", [])
        if old not in legacy:
            legacy.append(old)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        lines.insert(4, f"config.json experimental_root -> {new} (backup config.json_old_{stamp}); restart the hub")

    os.makedirs(a.report_dir, exist_ok=True)
    report = os.path.join(a.report_dir, f"migrate_data_root_{time.strftime('%Y%m%d_%H%M%S')}.txt")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:12 + len(by_top)]))
    print(f"\nfull list: {report}")


if __name__ == "__main__":
    main()
