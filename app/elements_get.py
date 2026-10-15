"""elements_get.py - download motion elements (smoke, steam, candle ...) from free libraries + keep a claim-proof register.

  get_elements.bat  ->  reads app\\elements_wanted.json, downloads what is missing into  D:\\Audio asset\\Elements\\<kind>\\
  Register: D:\\Audio asset\\Elements\\ELEMENTS_INDEX.json  (+ .csv for Excel): for every file -> where it came from
  (page, author, original file link), licence (commercial use? credit needed?), date downloaded, SHA-256 fingerprint,
  and "used_in" (filled in automatically each time a video uses the clip).
Only clips whose licence allows commercial use WITHOUT credit are accepted. Nothing is ever deleted or overwritten.
"""
import csv, hashlib, json, os, sys, time, urllib.request

APP = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(APP)
sys.path.insert(0, APP)
from path_config import load_config
CFG = load_config()
LIB = CFG.get("elements_dir") or r"D:\Audio asset\Elements"
INDEX = os.path.join(LIB, "ELEMENTS_INDEX.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


PIXABAY_LICENSE = {
    "name": "Pixabay Content License", "summary_url": "https://pixabay.com/service/license-summary/",
    "full_url": "https://pixabay.com/service/license/", "commercial_use": True, "attribution_required": False,
    "checked_on": "2026-10-13",
    "not_allowed": "selling/redistributing the clip on its own (unchanged); using it in trademarks/logos; misleading or illegal use"}


def load_index_at(lib):
    global LIB, INDEX
    LIB, INDEX = lib, os.path.join(lib, "ELEMENTS_INDEX.json")
    return load_index()


def save_index_at(lib, ix):
    global LIB, INDEX
    LIB, INDEX = lib, os.path.join(lib, "ELEMENTS_INDEX.json")
    save_index(ix)


def load_index():
    if os.path.exists(INDEX):
        return json.load(open(INDEX, encoding="utf-8"))
    return {"about": "Register of downloaded motion elements: source, licence, proof, and where each is used.",
            "items": {}}


def save_index(ix):
    tmp = INDEX + ".tmp"
    json.dump(ix, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    os.replace(tmp, INDEX)
    with open(INDEX[:-5] + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["file", "kind", "site", "page", "title", "author", "author page", "original file link", "licence",
                    "licence link", "commercial use", "credit needed", "downloaded on", "sha256", "used in", "ready"])
        for name, it in sorted(ix["items"].items()):
            w.writerow([name, it["kind"], it["site"], it["page"], it["title"], it["author"], it["author_url"],
                        it["original_file"], it["license"]["name"], it["license"]["summary_url"],
                        "yes" if it["license"]["commercial_use"] else "NO",
                        "yes" if it["license"]["attribution_required"] else "no", it["downloaded_on"], it["sha256"],
                        " | ".join(f'{u["video"]} ({u["date"]})' for u in it.get("used_in", [])), it.get("ready", "")])


def main():
    wanted = json.load(open(os.path.join(APP, "elements_wanted.json"), encoding="utf-8"))
    lic = wanted["license"]
    if not lic["commercial_use"] or lic["attribution_required"]:
        sys.exit("licence does not allow commercial use without credit - nothing downloaded")
    os.makedirs(LIB, exist_ok=True)
    ix = load_index()
    ok = 0
    for c in wanted["clips"]:
        name = f'{c["kind"]}/{c["id"]}.mp4'                    # the clip's real Pixabay name + id
        path = os.path.join(LIB, c["kind"], c["id"] + ".mp4")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and name in ix["items"]:
            print(f"  have    {name}"); ok += 1; continue
        print(f"  getting {name}  ({c['bytes'] / 1e6:.1f} MB) ...", flush=True)
        try:
            req = urllib.request.Request(c["file"], headers={"User-Agent": UA, "Referer": "https://pixabay.com/"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
        except Exception as e:
            print(f"  !! failed: {e}"); continue
        if os.path.exists(path):                                 # never overwrite: keep the old one aside
            os.replace(path, path[:-4] + time.strftime("_old_%H%M%S.mp4"))
        open(path, "wb").write(data)
        ix["items"][name] = {
            "kind": c["kind"], "site": "Pixabay", "page": f'https://pixabay.com/videos/{c["id"]}/',
            "title": c["title"], "author": c["author"], "author_url": c["author_url"], "original_file": c["file"],
            "published": c["published"], "duration": c["duration"], "resolution": c["size"], "note": c.get("note", ""),
            "license": lic, "downloaded_on": time.strftime("%Y-%m-%d %H:%M"), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "used_in": []}
        save_index(ix)
        ok += 1
        print(f"          saved {len(data) / 1e6:.1f} MB")
    save_index(ix)
    print(f"\n  {ok}/{len(wanted['clips'])} clips in {LIB}\n  register: {INDEX} (+ .csv)")


if __name__ == "__main__":
    main()
