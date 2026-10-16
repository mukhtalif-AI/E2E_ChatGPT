"""Save / Git page (Experimental sidebar): a fixed set of git commands on the hub folder.

No free shell: every command below is a fixed argument list; user text only goes in as a
commit message, a remote URL, a name/e-mail, or a path that git itself just listed.
Never offered for commit: secrets, model/checkpoint files, media, big files, anything
outside the hub folder (D:\\MukhtalifGPT data lives outside it anyway).
Routes: GET /api/git/status, /api/git/log, /api/git/diff?file=  ·  POST /api/git/<action>
"""
import os, re, shutil, subprocess, threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # E:\Projects\hub_chatgpt
LOCK = threading.Lock()
FLAGS = 0x08000000 if os.name == "nt" else 0                          # no console window

SOURCE_EXT = {".py", ".pyw", ".html", ".htm", ".js", ".mjs", ".css", ".md", ".txt", ".json", ".jsonc", ".bat",
              ".cmd", ".ps1", ".sh", ".cfg", ".ini", ".toml", ".yml", ".yaml", ".csv", ".svg", ".xml"}
SOURCE_NAMES = {".gitignore", ".gitattributes", "requirements.txt", "README", "LICENSE"}
SECRET_NAME = re.compile(r"^(secrets?|credentials?|passwords?|tokens?|api[_-]?keys?)\b|\.env$|^\.env|\.(pem|key|pfx|p12)$|^id_(rsa|ed25519)", re.I)
MODEL_EXT = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx", ".pkl", ".npz", ".npy", ".h5", ".tflite", ".model"}
KEY_IN_TEXT = re.compile(r"""["']?(api[_-]?key|apikey|secret|token|password|access[_-]?key)["']?\s*[:=]\s*["'][A-Za-z0-9_\-./+=]{16,}["']""", re.I)
DATA_PREFIXES = ("logs/", "_tmp/", "_old/", "_backup", "code/all_code", "engines/voice/models/", "engines/voice/training/")
MAX_BYTES = 3_000_000
URL_OK = re.compile(r"^(https://[\w.\-]+(:\d+)?/[\w.\-~/]+|git@[\w.\-]+:[\w.\-~/]+)$")


def _git():
    g = shutil.which("git")
    if g:
        return g
    for c in (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe")):
        if os.path.isfile(c):
            return c
    return None


def run(args, timeout=60):
    """Run one fixed git command in the hub folder -> (returncode, text). args never come from the browser."""
    g = _git()
    if not g:
        return 127, "git is not installed (or not on PATH). Install Git for Windows from git-scm.com, then restart the hub."
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    try:
        p = subprocess.run([g, "-c", "core.quotepath=false", "-c", "color.ui=false"] + args, cwd=ROOT,
                           capture_output=True, timeout=timeout, env=env, creationflags=FLAGS)
    except subprocess.TimeoutExpired:
        return 124, "git " + args[0] + " took longer than " + str(timeout) + " s and was stopped."
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out.strip()


def _cmd(args):
    return "git " + " ".join(a if re.fullmatch(r"[\w.\-/:@=]+", a) else '"' + a.replace('"', '\\"') + '"' for a in args)


def is_repo():
    return os.path.isdir(os.path.join(ROOT, ".git"))


def _job_running(jobs):
    return bool(jobs) and any(j.get("status") == "running" for j in jobs.jobs)


def check_file(path):
    """-> (offer, reason). Only plain source files inside the hub folder are offered for commit."""
    low = path.replace("\\", "/").lower()
    name = low.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1]
    full = os.path.join(ROOT, path)
    if ".." in low.split("/") or os.path.isabs(path):
        return False, "outside the hub folder"
    if SECRET_NAME.search(name):
        return False, "looks like a secrets file"
    if ext in MODEL_EXT:
        return False, "model / checkpoint file"
    if any(low.startswith(d) for d in DATA_PREFIXES) or "/__pycache__/" in "/" + low or re.search(r"_old_20\d|\.bak_|\.part$", low):
        return False, "backup, log or generated data"
    if ext not in SOURCE_EXT and name not in SOURCE_NAMES:
        return False, "not a source file (" + (ext or "no extension") + ")"
    if os.path.isfile(full):
        size = os.path.getsize(full)
        if size > MAX_BYTES:
            return False, "too big ({:.1f} MB)".format(size / 1e6)
        try:
            with open(full, "rb") as f:
                head = f.read(MAX_BYTES).decode("utf-8", "replace")
        except OSError:
            return False, "cannot read it"
        if KEY_IN_TEXT.search(head):
            return False, "contains something that looks like an API key"
    return True, ""


def status():
    if not _git():
        return {"git": False, "repo": False, "message": run(["--version"])[1]}
    if not is_repo():
        return {"git": True, "repo": False, "root": ROOT,
                "message": "This folder is not a git repository yet. Press “Start git here” to run git init (nothing is committed until you commit)."}
    rc, raw = run(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--branch"])
    if rc:
        return {"git": True, "repo": True, "root": ROOT, "error": raw}
    parts = raw.split("\0")
    head, files, i = "", [], 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if not e:
            continue
        if e.startswith("## "):
            head = e[3:]
            continue
        x, y, path = e[0], e[1], e[3:]
        old = ""
        if x in "RC":                      # rename/copy: the next entry is the old path
            old = parts[i] if i < len(parts) else ""
            i += 1
        state = ("new" if x + y == "??" else "deleted" if "D" in x + y else "renamed" if x == "R"
                 else "added" if x == "A" else "conflict" if "U" in x + y else "modified")
        offer, why = (True, "") if state == "deleted" else check_file(path)
        if state == "conflict":
            offer, why = False, "merge conflict - fix it in an editor first"
        files.append({"path": path, "old": old, "state": state, "staged": x not in " ?", "offer": offer, "why": why})
    branch = re.split(r"\.\.\.| ", head)[0] if head else ""
    ahead = re.search(r"ahead (\d+)", head)
    behind = re.search(r"behind (\d+)", head)
    _, remotes = run(["remote", "-v"])
    _, name = run(["config", "user.name"])
    _, email = run(["config", "user.email"])
    return {"git": True, "repo": True, "root": ROOT, "branch": branch.replace("No commits yet on ", ""),
            "upstream": "..." in head, "ahead": int(ahead.group(1)) if ahead else 0, "behind": int(behind.group(1)) if behind else 0,
            "remote": sorted({l.split()[1] for l in remotes.splitlines() if len(l.split()) > 1 and l.split()[0] == "origin"}),
            "remotes": remotes, "user": {"name": name if "\n" not in name else "", "email": email if "\n" not in email else ""},
            "files": files}


def handle_get(cfg, p, q):
    if p == "/api/git/status":
        return 200, status()
    if not is_repo():
        return 400, {"error": "not a git repository yet"}
    if p == "/api/git/log":
        args = ["log", "-n", "40", "--date=format:%d %b %Y %H:%M", "--pretty=format:%h  %ad  %an  %s%d"]
        rc, out = run(args)
        return 200, {"cmd": _cmd(args), "rc": rc, "out": out or "No commits yet."}
    if p == "/api/git/diff":
        f = q.get("file", "")
        if f:                              # one file, only if git lists it as changed
            known = {x["path"]: x for x in status().get("files", [])}
            if f not in known:
                return 400, {"error": "that file has no changes"}
            if known[f]["state"] == "new":
                return 200, {"cmd": "(new file)", "rc": 0, "out": "New file, not in git yet."}
            args = ["diff", "HEAD", "--", f]
            rc, out = run(args)
            return 200, {"cmd": _cmd(args), "rc": rc, "out": out[:200_000] or "No text changes (only line endings or mode)."}
        outs = []
        for args in (["diff", "--stat", "HEAD"], ["status", "--short", "--untracked-files=normal"]):
            rc, out = run(args)
            outs.append("$ " + _cmd(args) + "\n" + (out or "(nothing)"))
        return 200, {"cmd": "summary", "rc": 0, "out": "\n\n".join(outs)}
    return 404, {"error": "unknown"}


def handle_post(cfg, p, body, jobs=None):
    act = p.rsplit("/", 1)[-1]
    with LOCK:
        if act == "init":
            if is_repo():
                return 400, {"error": "this folder is already a git repository"}
            if _job_running(jobs):
                return 409, {"error": "a job is running; start git when it has finished"}
            rc, out = run(["init"])
            return 200, {"cmd": "git init", "rc": rc, "out": out}
        if not is_repo():
            return 400, {"error": "not a git repository yet - press “Start git here” first"}
        if act == "identity":
            name, email = str(body.get("name") or "").strip(), str(body.get("email") or "").strip()
            if not (0 < len(name) <= 80) or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or "\n" in name:
                return 400, {"error": "enter a name and a valid e-mail"}
            out = [run(["config", "user.name", name])[1], run(["config", "user.email", email])[1]]
            return 200, {"cmd": "git config user.name / user.email (this folder only)", "rc": 0, "out": "\n".join(o for o in out if o) or "Saved."}
        if act == "remote":
            url = str(body.get("url") or "").strip()
            if not URL_OK.fullmatch(url):
                return 400, {"error": "enter a repository address like https://github.com/you/hub.git"}
            has = "origin" in run(["remote"])[1].split()
            args = ["remote", "set-url" if has else "add", "origin", url]
            rc, out = run(args)
            return 200, {"cmd": _cmd(args), "rc": rc, "out": out or "Remote origin set to " + url}
        if act == "commit":
            msg = str(body.get("message") or "").strip()
            if not msg or len(msg) > 2000:
                return 400, {"error": "write a short commit message"}
            want = [str(x) for x in body.get("files") or []]
            if not want:
                return 400, {"error": "tick at least one file"}
            known = {x["path"]: x for x in status().get("files", [])}
            bad = [f for f in want if f not in known or not known[f]["offer"]]
            if bad:
                return 400, {"error": "these files cannot be committed here: " + ", ".join(bad[:5])}
            new = [f for f in want if known[f]["state"] == "new"]
            paths = want + [known[f]["old"] for f in want if known[f]["old"]]
            log = []
            if new:
                rc, out = run(["add", "--"] + new)
                log.append("$ git add (" + str(len(new)) + " new file(s))\n" + (out or "ok"))
                if rc:
                    return 200, {"cmd": "git add", "rc": rc, "out": "\n\n".join(log)}
            args = ["commit", "--only", "-m", msg, "--"] + paths
            rc, out = run(args, timeout=120)
            log.append("$ git commit --only -m \"" + msg.splitlines()[0][:80] + "\" -- (" + str(len(paths)) + " file(s))\n" + out)
            if rc and "tell me who you are" in out.lower():
                log.append("Set your name and e-mail on this page (Who commits), then commit again.")
            return 200, {"cmd": "commit", "rc": rc, "out": "\n\n".join(log)}
        if act == "push":
            st = status()
            if not st.get("remote"):
                return 400, {"error": "no remote is set - add the repository address first"}
            args = ["push"] if st.get("upstream") else ["push", "-u", "origin", st.get("branch") or "HEAD"]
            rc, out = run(args, timeout=180)
            return 200, {"cmd": _cmd(args), "rc": rc, "out": out or "Pushed."}
        if act == "pull":
            if _job_running(jobs):
                return 409, {"error": "a job is running; pull changes the hub's code files, so wait for it to finish"}
            if not status().get("remote"):
                return 400, {"error": "no remote is set - add the repository address first"}
            args = ["pull", "--ff-only"]
            rc, out = run(args, timeout=180)
            if not rc and "up to date" not in out.lower():
                out += "\n\nRestart the hub (restart_hub.bat) so updated code is used."
            return 200, {"cmd": _cmd(args), "rc": rc, "out": out}
        if act == "fetch":
            if not status().get("remote"):
                return 400, {"error": "no remote is set"}
            rc, out = run(["fetch", "origin"], timeout=180)
            return 200, {"cmd": "git fetch origin", "rc": rc, "out": out or "Up to date with the remote."}
    return 404, {"error": "unknown"}
