"""HUBGPT - one window for all your projects (script -> voice -> pictures -> video, + image studio).

    hub.bat   (or: python server.py)   -> http://127.0.0.1:8781 opens in your browser

Only Python's standard library - nothing to install. The hub never changes your other projects: it runs
HubGPT Voice and HubGPT Video commands and talks to ComfyUI / the selected local AI API over
their local web APIs. Generated media is routed under media_dir / episodes_dir; trained voice checkpoints belong to the HubGPT voice engine.
ONE graphics-card job at a time: jobs wait in a queue (two GPU jobs together froze the PC's voice job).
"""
import ctypes, hashlib, json, os, re, shutil, subprocess, sys, threading, time, traceback, urllib.parse, urllib.request, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

APP = os.path.dirname(os.path.abspath(__file__))           # ...\hub_chatgpt\app
ROOT = os.path.dirname(APP)                                   # ...\hub_chatgpt
sys.path.insert(0, APP)
import tools  # noqa: E402

from path_config import load_config
CFG = load_config()
for k, v in list(CFG.items()):                               # "engines\\voice" etc. = inside the hub folder
    if isinstance(v, str) and v.startswith(("engines", "app")) and not os.path.isabs(v):
        CFG[k] = os.path.join(ROOT, *re.split(r"[\\/]", v))
for k, v in os.environ.items():                       # HUB_MEDIA_DIR=... overrides "media_dir" etc.
    if k.startswith("HUB_") and k[4:].lower() in CFG:
        CFG[k[4:].lower()] = v
MEDIA = CFG["media_dir"]
for d in (CFG["logs_dir"], CFG["scripts_dir"], CFG["pictures_dir"], CFG["videos_dir"], CFG["images_dir"], CFG["temporary_dir"]):
    os.makedirs(d, exist_ok=True)
JOBS_FILE = os.path.join(MEDIA, "jobs.json")
WIN = os.name == "nt"


# ------------------------------------------------------------------ jobs (one GPU queue)
LIGHT = {"elements_fetch", "check_video", "sfx_fetch"}


class JobStopped(BaseException):
    """raised inside a job's thread when you press Stop (BaseException, so a step's `except Exception` cannot eat it)"""


_JOB_LOCAL = threading.local()            # which job this thread is running (set by the worker)
_RealPopen = subprocess.Popen


def _shared_service(args):
    """ComfyUI / Ollama servers a step starts are shared by everything: Stop must not kill them"""
    s = " ".join(map(str, args)) if isinstance(args, (list, tuple)) else str(args)
    return ("main.py" in s and "--windows-standalone-build" in s) or s.rstrip().lower().endswith(" serve")


class _TrackedPopen(_RealPopen):
    """every program a job starts (ffmpeg, python engines, ... also through subprocess.run) is remembered,
    so Stop can end all of them, not only the ones started through run_cmd"""
    def __init__(self, args, *a, **kw):
        super().__init__(args, *a, **kw)
        jid = getattr(_JOB_LOCAL, "jid", None)
        if jid and JOBS is not None and not _shared_service(args):
            JOBS.children.setdefault(jid, []).append(self)


subprocess.Popen = _TrackedPopen


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = []
        self.procs = {}
        self.stop_flag = set()
        self.children = {}                     # job id -> programs it started
        self.threads = {}                      # job id -> worker thread running it
        self.abandoned = set()                 # worker threads given up on after Stop (a step would not let go)
        self.pause_flag = set()                # job ids to pause before their next step
        self.lingering = {}                    # job id -> its given-up thread, still finishing one call
        if os.path.exists(JOBS_FILE):
            try:
                self.jobs = json.load(open(JOBS_FILE, encoding="utf-8"))
            except Exception:
                self.jobs = []
        try:
            import experimental_workspace
            for base in [experimental_workspace.root(CFG)] + experimental_workspace.legacy_roots(CFG):
                exp_file = os.path.join(base, "Activity", "jobs.json")   # old data folders: jobs still listed
                if os.path.isfile(exp_file):
                    saved = json.load(open(exp_file, encoding="utf-8"))
                    known = {j.get("id") for j in self.jobs}
                    self.jobs.extend(j for j in saved if j.get("id") not in known)
        except Exception:
            pass
        for j in self.jobs:                            # older Experimental records were saved without their steps:
            if not isinstance(j.get("steps"), list):   # they cannot carry on, so they stop instead of breaking the hub
                j["steps"] = []
                if j.get("status") in ("running", "queued"):
                    j["status"] = "stopped"
                    j["note"] = "stopped: this job was saved without its steps and cannot carry on - start it again"
        for j in self.jobs:                            # Pause was pressed just before the hub closed: keep it paused
            if j.pop("pausing", None) and j.get("status") in ("running", "queued"):
                j["status"] = "paused"; j["note"] = f"paused - Resume carries on from step {int(j.get('step') or 0) + 1}"
        for j in self.jobs:                            # Stop was pressed just before the hub closed: keep it stopped
            if j.pop("stopping", None) and j.get("status") in ("running", "queued"):
                j["status"] = "stopped"; j["note"] = "stopped by you"
        for j in self.jobs:                            # a restart never loses work: unfinished jobs carry on
            if j["status"] in ("running", "queued"):   # (finished steps are skipped, the cut step runs again)
                if j["status"] == "running":
                    orphan_kill(j.get("pid"))              # its engine may still run without the hub
                    j["note"] = f"continues after a hub restart (from step {int(j.get('step') or 0) + 1})"
                    j["resumed"] = int(j.get("resumed") or 0) + 1
                j["status"] = "queued"
            if j.get("resumed", 0) > 3 and j["status"] == "queued":
                j["status"] = "stopped"; j["note"] = "stopped: it was cut short by hub restarts several times"
        self.save()
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=self.worker, args=(True,), daemon=True).start()   # light lane: no graphics card

    def save(self):
        with self.lock:
            tmp = JOBS_FILE + ".part"
            stable = [j for j in self.jobs if not (j.get("params") or {}).get("_experimental_workspace")]
            json.dump(stable[-300:], open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, JOBS_FILE)
        try:
            import experimental_workspace
            exp_file = os.path.join(experimental_workspace.root(CFG), "Activity", "jobs.json")
            if os.path.isfile(exp_file) or any((j.get("params") or {}).get("_experimental_workspace") for j in self.jobs):
                experimental_workspace.sync_jobs(self.jobs, CFG)
        except Exception:
            pass

    def add(self, kind, title, steps, outputs=None, params=None):
        params = params or {}
        requested_id = params.pop("_requested_job_id", None)
        jid = requested_id or time.strftime("%m%d_%H%M%S_") + uuid.uuid4().hex[:4]
        workspace = params.get("_experimental_workspace") or {}
        if workspace and not requested_id:
            workspace["log"] = os.path.join(workspace["root"], "Activity", "Logs", f"job_{jid}.log")
        j = {"id": jid, "kind": kind, "title": title, "status": "queued", "created": time.time(),
             "started": None, "ended": None, "steps": steps, "step": 0, "outputs": outputs or [],
             "params": params, "log": (params.get("_experimental_workspace") or {}).get("log") or os.path.join(CFG["logs_dir"], f"job_{jid}.log"), "note": ""}
        with self.lock:
            self.jobs.append(j)
        self.save()
        return j

    def get(self, jid):
        return next((j for j in self.jobs if j["id"] == jid), None)

    def stop(self, jid):
        with self.lock:
            j = next((x for x in self.jobs if x["id"] == jid), None)
            if not j:
                return False
            if j["status"] == "queued":
                j["status"] = "stopped"
                queued = True
            elif j["status"] == "running":
                self.stop_flag.add(jid)
                j["stopping"] = True; j["note"] = "stopping..."
                queued = False
                procs = [self.procs.get(jid)] + list(self.children.get(jid, []))
                tid = self.threads.get(jid)
            elif j["status"] == "paused":
                j["status"] = "stopped"; j["note"] = "stopped by you"
                queued = True
            else:
                return False
            self.pause_flag.discard(jid); j.pop("pausing", None)
        if queued:
            self.save()
            return True
        for p in procs:                                  # 1. end every program the job started (whole tree)
            try:
                if p and p.poll() is None:
                    kill_tree(p)
            except Exception:
                pass
        if tid:
            comfy_cancel(tid)                            # 2. drop only THIS job's picture in ComfyUI (it is shared)
            raise_in_thread(tid, JobStopped)             # 3. the step itself stops at its next line of Python
            threading.Thread(target=self._stop_watchdog, args=(j, tid), daemon=True).start()
        self.save()
        return True

    def pause(self, jid):
        """a running job finishes the step it is on, then waits as "paused" (the graphics card goes to the next job);
        a waiting job is simply held back"""
        with self.lock:
            j = next((x for x in self.jobs if x["id"] == jid), None)
            if not j or j.get("stopping"):
                return False
            if j["status"] == "queued":
                j["status"] = "paused"; j["note"] = f"paused - Resume carries on from step {int(j.get('step') or 0) + 1}"
            elif j["status"] == "running":
                self.pause_flag.add(jid); j["pausing"] = True
                j["note"] = "pausing after this step..."
            else:
                return False
        self.save()
        return True

    def resume(self, jid):
        """carry on a paused, stopped or failed job in its own folder: finished steps are skipped, the step it was
        on runs again. Pressed while a pause is pending, it cancels the pause."""
        with self.lock:
            j = next((x for x in self.jobs if x["id"] == jid), None)
            if not j:
                return False, "no such job"
            if j["status"] == "running" and j.get("pausing"):
                self.pause_flag.discard(jid); j.pop("pausing", None); j["note"] = ""
            elif j["status"] not in ("paused", "stopped", "failed"):
                return False, f"this job is {j['status']}"
            elif not j.get("steps"):
                return False, "this job has no steps saved - start it fresh from its page"
            elif jid in self.lingering:
                return False, "a step of this job is still finishing a request after Stop - try Resume in a minute"
            else:
                done = int(j.get("step") or 0)
                j["status"] = "queued"; j["ended"] = None; j["resumed"] = 0
                j["user_resumes"] = int(j.get("user_resumes") or 0) + 1
                j.pop("pausing", None); j.pop("stopping", None)
                j["note"] = f"resumed from step {done + 1}" + (f" (steps 1-{done} are kept)" if done else "")
        self.save()
        return True, ""

    def _stop_watchdog(self, j, tid, wait=20):
        """a step stuck inside one long call (a slow web request) cannot be stopped mid-call: after `wait` s the
        job is marked stopped anyway and a fresh worker takes over the queue; the old thread ends when it returns"""
        t0 = time.time()
        while time.time() - t0 < wait:
            time.sleep(0.5)
            if self.threads.get(j["id"]) != tid:
                return
        with self.lock:
            if self.threads.get(j["id"]) != tid or j["status"] != "running":
                return
            self.abandoned.add(tid); self.lingering[j["id"]] = tid
            self.threads.pop(j["id"], None)
            j["status"] = "stopped"; j["ended"] = time.time(); j.pop("stopping", None)
            j["note"] = "stopped by you (a step was still finishing a request; it ends on its own)"
            self.stop_flag.discard(j["id"])
        self.save()
        threading.Thread(target=self.worker, args=(self._is_light_job(j),), daemon=True).start()

    def worker(self, light=False):
        """two lanes: graphics-card jobs one after another; light jobs (scripts written online) beside them"""
        while True:
            with self.lock:
                line = [x for x in self.jobs if x["status"] == "queued" and self._is_light_job(x) == light]
                j = min(line, key=self.queue_key) if line else None
                if j:
                    j["status"] = "running"; j["started"] = time.time()
            if not j:
                time.sleep(0.7); continue
            me = threading.get_ident()
            try:
                self.save()
                self.threads[j["id"]] = me; _JOB_LOCAL.jid = j["id"]
                try:
                    ok = self.run(j)
                except JobStopped:
                    ok = False
                _JOB_LOCAL.jid = None
                raise_in_thread(me, None)                # a Stop that arrived too late must not hit the lines below
                self.children.pop(j["id"], None)
                if me in self.abandoned:                 # Stop already marked it and started a new worker
                    self.abandoned.discard(me); self.lingering.pop(j["id"], None)
                    return
                self.threads.pop(j["id"], None)
                paused = ok == "paused"
                self.pause_flag.discard(j["id"]); j.pop("pausing", None)
                if j["id"] in self.stop_flag:
                    j["status"] = "stopped"; self.stop_flag.discard(j["id"])
                    j["note"] = "stopped by you"
                elif paused:
                    j["status"] = "paused"; j["note"] = f"paused - Resume carries on from step {int(j.get('step') or 0) + 1}"
                else:
                    j["status"] = "done" if ok else "failed"
                j.pop("stopping", None)
                j["ended"] = time.time(); self.save()
                if (j.get("params") or {}).get("_experimental_workspace"):
                    try:
                        import experimental_workspace
                        experimental_workspace.organize(j)
                    except Exception:
                        pass
            except JobStopped:                           # never let a late Stop end the queue itself
                _JOB_LOCAL.jid = None
                if me in self.abandoned:
                    self.abandoned.discard(me); self.lingering.pop(j["id"], None)
                    return
                if j.get("status") == "running":
                    j["status"] = "stopped"; j["ended"] = time.time(); j.pop("stopping", None)
                    self.stop_flag.discard(j["id"]); self.threads.pop(j["id"], None); self.save()

    @staticmethod
    def queue_key(job):
        """queue order: a job cut short by a restart first, then the build-queue position (default: time added)"""
        return (0 if job.get("step") else 1, float(job.get("queue_pos", job.get("created") or 0)))

    @staticmethod
    def _is_light_job(job):
        return job.get("kind") in LIGHT

    def run(self, j):
        os.makedirs(os.path.dirname(j["log"]), exist_ok=True)
        log = open(j["log"], "a", encoding="utf-8", buffering=1)

        def say(s):
            log.write(s.rstrip("\n") + "\n")

        say(f"== {j['title']}   ({time.strftime('%d %b %H:%M:%S')})")
        if j.get("step"):
            say(f"-- continuing: steps 1-{j['step']} are already done, their files are kept")
        run_cfg, run_media = dict(CFG), MEDIA
        if (j.get("params") or {}).get("_experimental_workspace"):
            import experimental_workspace
            run_cfg, run_media = experimental_workspace.job_config(CFG, j["params"])
        tinker_on = False
        if (j.get("params") or {}).get("_tinker") and not self._is_light_job(j):
            try:
                import tinker
                tinker_on = True                   # leave() in finally, even if enter() stops halfway
                tinker.enter(j["params"]["_tinker"])
                for line in tinker.describe(j["params"]["_tinker"]):
                    say("-- tinker: " + line)
            except Exception as e:
                say(f"-- tinker settings were not applied: {e}")
        ctx = {"job": j, "say": say, "cfg": run_cfg, "hub_cfg": CFG, "media": run_media or MEDIA,
               "stopped": lambda: j["id"] in self.stop_flag,
               "run_cmd": lambda cmd, cwd=None, env=None: self.run_cmd(j, cmd, cwd, env, say),
               "add_job": lambda kind, title, steps: self.add(kind, title, steps)}
        def time_summary():
            """where this run's time went, slowest step first (jobs.json "timings" holds the same numbers)"""
            t = [x for x in (j.get("timings") or []) if isinstance(x, (list, tuple)) and len(x) == 2]
            total = sum(x[1] for x in t)
            if total < 60:
                return
            say(f"\nTIME  {int(total // 60)} min {int(total % 60)} s in {len(t)} steps, slowest first:")
            for fn, sec in sorted(t, key=lambda x: -x[1])[:10]:
                say(f"   {fn:<18} {int(sec // 60):>3} min {int(sec % 60):02d} s   {100 * sec / total:3.0f} %")

        steps = j.get("steps") or []
        if not steps:
            say("-- this job has no steps saved, so there is nothing to run - start it again")
            log.close()
            return False
        try:
            for i, st in enumerate(steps):
                if i < j.get("step", 0):
                    continue
                if j["id"] in self.stop_flag:
                    say("\n-- stopped by you")
                    tools.free_llm(ctx)
                    return False
                if j["id"] in self.pause_flag and i > 0:
                    say(f"\n-- paused by you after step {i} ({time.strftime('%H:%M:%S')}); Resume carries on from step {i + 1}")
                    j["step"] = i; self.save()
                    tools.free_llm(ctx)
                    return "paused"
                j["step"] = i; j["step_label"] = st.get("label", st["fn"]); self.save()
                say(f"\n[{i + 1}/{len(steps)}] {st.get('label', st['fn'])}   ({time.strftime('%H:%M:%S')})")
                t_step = time.time()
                try:
                    if st["fn"].startswith("exp_") and (j.get("params") or {}).get("_experimental_workspace"):
                        import experimental_workspace
                        result = getattr(experimental_workspace, st["fn"])(ctx, **st.get("args", {}))
                    else:
                        result = getattr(tools, st["fn"])(ctx, **st.get("args", {}))
                    elapsed = time.time() - t_step
                    say(f"   (step took {int(elapsed // 60)} min {int(elapsed % 60)} s)")
                    j.setdefault("timings", []).append([st["fn"], round(elapsed)])
                    if result is False:
                        say("   this step did not succeed - see the lines above")
                        tools.free_llm(ctx)
                        return False
                except Exception as e:
                    if j["id"] in self.stop_flag:
                        say("\n-- stopped by you")
                    else:
                        say(f"   !! {e}\n" + traceback.format_exc())
                    tools.free_llm(ctx)
                    return False
            j["step"] = len(steps)
            say(f"\nDONE  ({time.strftime('%H:%M:%S')})")
            tools.free_llm(ctx)
            return True
        except JobStopped:
            raise_in_thread(threading.get_ident(), None)
            say("\n-- stopped by you")
            try:
                tools.free_llm(ctx)
            except Exception:
                pass
            return False
        finally:
            try:
                time_summary()
            except Exception:
                pass
            if tinker_on:
                import tinker
                tinker.leave()
            log.close()
    def run_cmd(self, j, cmd, cwd, env, say):
        say("   > " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
        e = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONUNBUFFERED="1", **(env or {}))
        flags = 0x00000200 | 0x08000000 if WIN else 0     # new process group, no console window
        try:
            p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=e, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags)
        except OSError as ex:
            say(f"   !! could not start: {ex}"); return 1
        self.procs[j["id"]] = p
        j["pid"] = p.pid
        self.save()
        buf = b""
        while True:
            ch = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(1)
            if not ch:
                break
            buf += ch
            *lines, buf = re.split(rb"\r\n|\n|\r", buf)
            for ln in lines:
                if ln.strip():
                    say("   " + ln.decode("utf-8", "replace"))
        if buf.strip():
            say("   " + buf.decode("utf-8", "replace"))
        rc = p.wait()
        self.procs.pop(j["id"], None)
        if rc:
            say(f"   (finished with code {rc})")
        return rc


def orphan_kill(pid):
    """after a restart: stop the engine a cut-short job left running (only if that PID is still a python process)"""
    if not pid or not WIN:
        return
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"], capture_output=True,
                           text=True, timeout=10)
        if "python" in r.stdout.lower():
            subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True, timeout=20)
    except Exception:
        pass


def raise_in_thread(tid, exc):
    """exc=None clears a pending one"""
    try:
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), ctypes.py_object(exc) if exc else None)
    except Exception:
        pass


def comfy_cancel(tid):
    """remove the picture this job's thread sent to ComfyUI (waiting -> deleted, being painted -> interrupted)"""
    try:
        import comfy
        pid = comfy.ACTIVE.pop(tid, None)
        if not pid:
            return
        q = json.loads(urllib.request.urlopen(CFG["comfy_url"] + "/queue", timeout=3).read())
        if any(len(x) > 1 and x[1] == pid for x in q.get("queue_running", [])):
            urllib.request.urlopen(urllib.request.Request(CFG["comfy_url"] + "/interrupt", data=b"", method="POST"),
                                   timeout=3).close()
        elif any(len(x) > 1 and x[1] == pid for x in q.get("queue_pending", [])):
            urllib.request.urlopen(urllib.request.Request(CFG["comfy_url"] + "/queue",
                                   json.dumps({"delete": [pid]}).encode(), {"Content-Type": "application/json"}),
                                   timeout=3).close()
    except Exception:
        pass


def kill_tree(p):
    try:
        if WIN:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        else:
            p.kill()
    except Exception:
        pass


JOBS = None
STARTED = time.time()
_EXPERIMENTAL_LINE_CACHE = {}


def code_changed():
    """the hub's own code was updated after it started -> a restart loads it"""
    for f in __import__("glob").glob(os.path.join(APP, "*.py")):
        try:
            if os.path.getmtime(f) > STARTED + 2:
                return True
        except OSError:
            pass
    return False


def git_snapshot():
    """Save non-ignored repo changes as a local checkpoint before a Hub restart; never push."""
    base = ["git", "-C", ROOT]
    try:
        repo = subprocess.run(base + ["rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, timeout=10)
        if repo.returncode or repo.stdout.strip() != "true":
            return {"saved": False, "message": "Git repository not detected; restarted without a snapshot."}
        add = subprocess.run(base + ["add", "-A", "--", ".",
                                     ":(exclude)secrets.json", ":(exclude)**/secrets.json",
                                     ":(exclude)**/.env", ":(exclude)**/.env.*",
                                     ":(exclude)**/*.pem", ":(exclude)**/*.key"],
                            capture_output=True, text=True, timeout=30)
        if add.returncode:
            return {"saved": False, "message": "Git could not stage changes: " + (add.stderr or add.stdout).strip()[:240]}
        staged = subprocess.run(base + ["diff", "--cached", "--quiet"], capture_output=True, timeout=10)
        if staged.returncode == 0:
            return {"saved": False, "message": "No changes to snapshot."}
        if staged.returncode != 1:
            return {"saved": False, "message": "Git could not inspect staged changes."}
        files = subprocess.run(base + ["diff", "--cached", "--name-only"], capture_output=True, text=True, timeout=10)
        count = len([line for line in files.stdout.splitlines() if line.strip()])
        message = "HubGPT snapshot before restart " + time.strftime("%Y-%m-%d %H:%M:%S")
        commit = subprocess.run(base + ["-c", "user.name=HubGPT Local Snapshot", "-c",
                                        "user.email=hubgpt-snapshot@localhost", "commit", "-m", message],
                                capture_output=True, text=True, timeout=30)
        if commit.returncode:
            return {"saved": False, "message": "Git snapshot failed: " + (commit.stderr or commit.stdout).strip()[:240]}
        head = subprocess.run(base + ["rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10)
        return {"saved": True, "files": count, "commit": head.stdout.strip(), "message": message}
    except Exception as e:
        return {"saved": False, "message": f"Git snapshot unavailable ({e}); restarted without a snapshot."}


def restart_self():
    """start a fresh hub (it waits until this one has let go of the port), then leave"""
    if WIN:                                            # a new hub.bat window (CREATE_NEW_CONSOLE)
        subprocess.Popen(["cmd", "/c", os.path.join(ROOT, "hub.bat"), "--no-browser", "--wait-port"], cwd=ROOT,
                         creationflags=0x00000010, close_fds=True)
    else:
        subprocess.Popen([sys.executable, os.path.join(APP, "server.py"), "--no-browser", "--wait-port"], cwd=ROOT,
                         close_fds=True)
    print("\n  The hub restarted in a NEW window (updates loaded) - this window can be closed.\n", flush=True)
    threading.Timer(1.0, lambda: os._exit(0)).start()


# ------------------------------------------------------------------ files the browser may see
def roots():
    r = [MEDIA, CFG.get("sukoonvoice_media"), CFG.get("episodes_dir"), CFG.get("artwork_dir"), CFG.get("music_dir"),
         os.path.join(CFG.get("studio_dir", ""), "assets", "fonts")]
    import experimental_workspace
    r.append(experimental_workspace.root(CFG))
    r.extend(experimental_workspace.legacy_roots(CFG))       # older data folders (D:\HubGPT) stay viewable
    return [os.path.normcase(os.path.abspath(x)) for x in r if x]


def allowed(p):
    a = os.path.normcase(os.path.abspath(p))
    return any(a == r or a.startswith(r + os.sep) for r in roots())


TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "video/mp4", ".png": "image/png",
         ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".txt": "text/plain; charset=utf-8",
         ".json": "application/json; charset=utf-8", ".html": "text/html; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
         ".md": "text/markdown; charset=utf-8"}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def file(self, path):
        if not os.path.isfile(path):
            return self.send(404, {"error": "not found"})
        size = os.path.getsize(path)
        ctype = TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        # Include the path: two different HTML pages can have the same size and
        # timestamp, and must never share a validator in the browser cache.
        path_tag = hashlib.sha256(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:12]
        etag = f'"{path_tag}-{int(os.path.getmtime(path) * 1000):x}-{size:x}"'
        cache_control = "no-store" if ctype.startswith("text/html") else "no-cache"
        if self.headers.get("If-None-Match") == etag and not self.headers.get("Range"):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            return
        rng = self.headers.get("Range")
        a, b = 0, size - 1
        code = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    a = int(m.group(1))
                    b = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    a = max(0, size - int(m.group(2)))
                b = min(b, size - 1)
                code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache_control)                 # HTML modes are never reused from cache.
        self.send_header("Content-Length", str(b - a + 1))
        if code == 206:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(a)
            left = b - a + 1
            try:
                while left > 0:
                    ch = f.read(min(1 << 16, left))
                    if not ch:
                        break
                    self.wfile.write(ch); left -= len(ch)
            except (ConnectionError, OSError):
                pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        p = u.path
        if p in ("/experimental", "/experimental/"):
            return self.file(os.path.join(APP, "ui", "experimental", "index.html"))
        if p in ("/", "/index.html"):
            return self.file(os.path.join(APP, "ui", "index.html"))
        if p.startswith("/ui/"):
            f = os.path.normpath(os.path.join(APP, "ui", p[4:]))
            if f.startswith(os.path.join(APP, "ui")):
                return self.file(f)
            return self.send(404, {"error": "not found"})
        if p == "/media":
            f = q.get("p", "")
            if not allowed(f):
                return self.send(403, {"error": "not an allowed folder"})
            return self.file(f)
        if p == "/api/status":
            return self.send(200, {**tools.status(CFG, JOBS), "update_ready": code_changed()})
        if p.startswith("/api/narrator"):            # corner narrator page (app/narrator.py)
            import narrator
            try:
                return self.send(*narrator.handle_get(CFG, p, q))
            except ValueError as e:
                return self.send(400, {"error": str(e)})
        if p == "/api/thumb":                      # Thumbnails page (app/thumbnail.py)
            import thumbnail
            try:
                return self.send(*thumbnail.handle_get(CFG, p, q))
            except ValueError as e:
                return self.send(400, {"error": str(e)})
        if p.startswith("/api/create/") or p == "/api/queue":   # Experimental Create form defaults, build queue
            import create_form
            return self.send(*create_form.handle_get(CFG, JOBS, p, q))
        if p == "/api/tinker":                      # Experimental settings panel (app/tinker.py)
            import tinker
            return self.send(200, tinker.state(CFG))
        if p.startswith("/api/git/"):              # Save / Git page (app/git_save.py, fixed git commands only)
            import git_save
            return self.send(*git_save.handle_get(CFG, p, q))
        if p == "/api/books" or p.startswith("/api/books/"):   # Experimental story library (app/story_library.py)
            import story_library
            try:
                return self.send(*story_library.handle_get(CFG, p, q))
            except Exception as e:
                return self.send(500, {"error": str(e)})
        if p.startswith("/api/rate/") or p == "/api/sounds":   # Experimental ratings (feedback.py) and Sounds page
            import feedback, sound_library
            try:
                return self.send(*(feedback if p.startswith("/api/rate/") else sound_library).handle_get(CFG, p, q))
            except Exception as e:
                return self.send(500, {"error": str(e)})
        if p == "/api/jobs":
            exp = self.headers.get("X-Hub-Experimental") == "1"
            rows = [j for j in JOBS.jobs if bool((j.get("params") or {}).get("_experimental_workspace")) == exp]
            return self.send(200, [{**{k: v for k, v in j.items() if k != "steps"}, "n_steps": len(j.get("steps") or [])}
                                   for j in reversed(rows[-100:])])
        m = re.match(r"^/api/jobs/([\w]+)/log$", p)
        if m:
            j = JOBS.get(m.group(1))
            if not j:
                return self.send(404, {"error": "no such job"})
            off = int(q.get("from", 0))
            text, end = "", off
            if os.path.exists(j["log"]):
                with open(j["log"], "rb") as f:
                    size = os.path.getsize(j["log"])
                    trimmed = off == 0 and size > 100_000
                    if trimmed:
                        off = size - 100_000
                    f.seek(off)
                    data = f.read(100_000)
                    end = off + len(data)
                    text = data.decode("utf-8", "replace")
            return self.send(200, {"text": text, "next": end, "trimmed": locals().get("trimmed", False), "status": j["status"],
                                   "step": j.get("step"), "n_steps": len(j.get("steps") or []),
                                   "step_label": j.get("step_label", "")})
        m = re.match(r"^/api/jobs/([\w]+)/notes$", p)
        if m:
            j = JOBS.get(m.group(1))
            if not j:
                return self.send(404, {"error": "no such job"})
            return self.send(200, {"notes": j.get("notes", [])})
        if p == "/api/library":
            if self.headers.get("X-Hub-Experimental") == "1":
                import experimental_workspace
                return self.send(200, experimental_workspace.library(CFG))
            return self.send(200, tools.library(CFG, q.get("kind", "all")))
        if p == "/api/library/stamp":
            if self.headers.get("X-Hub-Experimental") == "1":
                import experimental_workspace
                return self.send(200, experimental_workspace.library_stamp(CFG))
            return self.send(200, tools.library_stamp(CFG))
        if p == "/api/suggestions":
            return self.send(200, tools.suggestions(CFG, JOBS))
        if p == "/api/presets":
            return self.send(200, tools.presets(CFG))
        if p == "/api/story/status":
            import story
            return self.send(200, story.status(CFG))
        m = re.fullmatch(r"/api/experimental/([A-Za-z0-9_-]{8,64})", p)
        if m:
            name = m.group(1)
            if not name.startswith("EXP_"):
                return self.send(400, {"error": "invalid experimental run"})
            import experimental_workspace
            meta = experimental_workspace.find_run(name, JOBS.jobs, CFG)
            ep_dir = meta.get("episode_dir", "")
            d = os.path.join(ep_dir, "00_Input", "ExperimentalRun") if ep_dir else os.path.join(MEDIA, "experimental_runs", name)
            report = os.path.join(d, "feedback.json")
            data = {}
            if os.path.isfile(report):
                try:
                    data = json.load(open(report, encoding="utf-8"))
                except (OSError, ValueError):
                    data = {}
            ep = os.path.join(ep_dir, CFG.get("episode_output_name", "Output")) if ep_dir else os.path.join(CFG.get("episodes_dir", ""), name, CFG.get("episode_output_name", "Output"))
            vids = sorted(__import__("glob").glob(os.path.join(ep, "*.mp4")), key=os.path.getmtime, reverse=True)
            line_cfg = dict(CFG, sukoonvoice_media=os.path.join(ep_dir, CFG.get("layout", {}).get("experimental_narration", "02_Narration"))) if ep_dir else CFG
            nar = tools.narration_dir(line_cfg, name, existing=True)
            lines = []
            if os.path.isdir(nar):
                try:
                    stamp = tuple(os.path.getmtime(os.path.join(nar, f)) if os.path.isfile(os.path.join(nar, f)) else 0
                                  for f in ("timing.json", "narration.wav"))
                    key = (nar, stamp)
                    if key not in _EXPERIMENTAL_LINE_CACHE:
                        _EXPERIMENTAL_LINE_CACHE.clear()
                        _EXPERIMENTAL_LINE_CACHE[key] = tools.narration_lines(line_cfg, nar).get("lines", [])
                    lines = _EXPERIMENTAL_LINE_CACHE[key]
                except Exception:
                    lines = []
            recfile = os.path.join(ep_dir, "02_Narration", "recordings.json") if ep_dir else os.path.join(d, "recordings.json")
            try:
                recordings = json.load(open(recfile, encoding="utf-8")).get("takes", [])
            except (OSError, ValueError, AttributeError):
                recordings = []
            audio_review = {}
            audio_review_path = os.path.join(nar, "experimental_audio_review.json")
            if os.path.isfile(audio_review_path):
                try:
                    audio_review = json.load(open(audio_review_path, encoding="utf-8"))
                except (OSError, ValueError):
                    audio_review = {}
            report_out = os.path.join(ep, "experimental_report.txt")
            data.update({"name": name, "video": vids[0] if vids else "",
                         "script": os.path.join(ep_dir, "01_Script", "script.txt") if ep_dir else os.path.join(CFG["scripts_dir"], name + ".txt"),
                         "lines": lines,
                         "recordings": recordings,
                         "audio_review": audio_review,
                         "report": report_out if os.path.isfile(report_out) else os.path.join(d, "experimental_report.txt")})
            return self.send(200, data)
        return self.send(404, {"error": "unknown"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 60_000_000:
            return self.send(413, {"error": "too big"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self.send(400, {"error": "bad json"})
        p = urllib.parse.urlparse(self.path).path
        try:
            m = re.match(r"^/api/jobs/([\w]+)/notes$", p)
            if m:
                j = JOBS.get(m.group(1))
                if not j:
                    return self.send(404, {"error": "no such job"})
                note = str(body.get("note") or "").strip()
                stage = str(body.get("stage") or "other").strip()[:60]
                result = str(body.get("result") or "issue")
                if not note or len(note) > 5000:
                    return self.send(400, {"error": "enter a note (up to 5000 characters)"})
                if result not in ("worked", "issue"):
                    return self.send(400, {"error": "invalid result"})
                entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "job_id": j["id"],
                         "job_title": j.get("title", ""), "stage": stage, "result": result, "note": note}
                with JOBS.lock:
                    j.setdefault("notes", []).append(entry)
                if (j.get("params") or {}).get("_experimental_workspace"):
                    try:
                        import experimental_workspace
                        experimental_workspace.organize(j)
                    except Exception:
                        pass
                JOBS.save()
                return self.send(200, {"ok": True, "entry": entry})
            if p == "/api/experimental/recording":
                import base64
                name = str(body.get("name") or "")
                if not re.fullmatch(r"EXP_[A-Za-z0-9_-]{8,60}", name):
                    return self.send(400, {"error": "invalid experimental run"})
                try:
                    line = max(1, min(99999, int(body.get("line") or 1)))
                except (TypeError, ValueError):
                    return self.send(400, {"error": "invalid script line"})
                sentence = str(body.get("sentence") or "").strip()
                emotion = str(body.get("emotion") or "").strip()[:60]
                if not sentence or len(sentence) > 2000:
                    return self.send(400, {"error": "enter the replacement sentence (up to 2000 characters)"})
                encoded = str(body.get("data") or "")
                if not encoded.startswith("data:audio/") or len(encoded) > 28_000_000:
                    return self.send(400, {"error": "audio recording is missing or too large (max ~20 MB)"})
                try:
                    raw = base64.b64decode(encoded.split(",", 1)[1], validate=True)
                except (IndexError, ValueError):
                    return self.send(400, {"error": "invalid audio data"})
                if len(raw) < 1000:
                    return self.send(400, {"error": "recording is too short"})
                import experimental_workspace
                run_meta = experimental_workspace.find_run(name, JOBS.jobs, CFG)
                run_ep = run_meta.get("episode_dir", "")
                d = os.path.join(run_ep, "02_Narration", "recordings") if run_ep else os.path.join(MEDIA, "experimental_runs", name, "recordings")
                os.makedirs(d, exist_ok=True)
                stem = "line_{:04d}".format(line)
                existing = [f for f in os.listdir(d) if re.fullmatch(re.escape(stem) + r"_take_\d+\.wav", f)]
                take = max([int(re.search(r"_take_(\d+)", f).group(1)) for f in existing] or [0]) + 1
                wav = os.path.join(d, "{}_take_{:02d}.wav".format(stem, take))
                src = os.path.join(d, stem + "_input_" + uuid.uuid4().hex[:8] + ".webm")
                try:
                    with open(src, "wb") as f:
                        f.write(raw)
                    proc = subprocess.run([CFG["ffmpeg"], "-v", "error", "-y", "-i", src, "-ac", "1", "-ar", "48000", wav],
                                          capture_output=True, timeout=90)
                    if proc.returncode or not os.path.isfile(wav) or os.path.getsize(wav) < 1000:
                        try:
                            os.remove(wav)
                        except OSError:
                            pass
                        return self.send(400, {"error": "could not convert browser audio to WAV: " +
                                               proc.stderr.decode("utf-8", "replace")[-500:]})
                finally:
                    try:
                        os.remove(src)
                    except OSError:
                        pass
                meta_path = os.path.join(os.path.dirname(d), "recordings.json")
                try:
                    meta = json.load(open(meta_path, encoding="utf-8")) if os.path.isfile(meta_path) else {"name": name, "takes": []}
                except (OSError, ValueError):
                    meta = {"name": name, "takes": []}
                entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "line": line, "sentence": sentence,
                         "emotion": emotion, "take": take, "file": wav}
                meta.setdefault("takes", []).append(entry)
                with open(meta_path + ".part", "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
                os.replace(meta_path + ".part", meta_path)
                ep = os.path.join(run_ep, CFG.get("episode_output_name", "Output")) if run_ep else os.path.join(CFG.get("episodes_dir", ""), name, CFG.get("episode_output_name", "Output"))
                os.makedirs(ep, exist_ok=True)
                note = "[{}] [voice line {}] RECORDED take {} — {}{}\nFile: {}\n\n".format(
                    entry["time"], line, take, sentence, (" (emotion: " + emotion + ")") if emotion else "", wav)
                report_root = os.path.join(run_ep, "00_Input") if run_ep else os.path.dirname(d)
                for report_path in (os.path.join(report_root, "experimental_report.txt"),
                                    os.path.join(ep, "experimental_report.txt")):
                    with open(report_path, "a", encoding="utf-8") as f:
                        f.write(note)
                return self.send(200, {"ok": True, "recording": wav, "take": take})
            if p == "/api/experimental/feedback":
                name = str(body.get("name") or "")
                if not re.fullmatch(r"EXP_[A-Za-z0-9_-]{8,60}", name):
                    return self.send(400, {"error": "invalid experimental run"})
                note = str(body.get("note") or "").strip()
                if not note or len(note) > 5000:
                    return self.send(400, {"error": "enter a note (up to 5000 characters)"})
                stage = str(body.get("stage") or "video")[:40]
                result = str(body.get("result") or "issue")
                if result not in ("worked", "issue"):
                    return self.send(400, {"error": "invalid result"})
                import experimental_workspace
                run_meta = experimental_workspace.find_run(name, JOBS.jobs, CFG)
                run_ep = run_meta.get("episode_dir", "")
                d = os.path.join(run_ep, "00_Input", "ExperimentalRun") if run_ep else os.path.join(MEDIA, "experimental_runs", name)
                os.makedirs(d, exist_ok=True)
                report = os.path.join(d, "feedback.json")
                try:
                    data = json.load(open(report, encoding="utf-8")) if os.path.isfile(report) else {"name": name, "entries": []}
                except (OSError, ValueError):
                    data = {"name": name, "entries": []}
                data.setdefault("entries", []).append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "stage": stage,
                                                       "result": result, "note": note})
                tmp = report + ".part"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, report)
                txt = os.path.join(d, "experimental_report.txt")
                ep = os.path.join(run_ep, CFG.get("episode_output_name", "Output")) if run_ep else os.path.join(CFG.get("episodes_dir", ""), name, CFG.get("episode_output_name", "Output"))
                os.makedirs(ep, exist_ok=True)
                report_out = os.path.join(ep, "experimental_report.txt")
                for report_path in (txt, report_out):
                    with open(report_path, "w", encoding="utf-8") as f:
                        f.write("HUBGPT EXPERIMENTAL RUN: " + name + "\nFeedback log\n\n")
                        for entry in data["entries"]:
                            f.write("[{}] [{}] {}\n{}\n\n".format(entry["time"], entry["stage"], entry["result"].upper(), entry["note"]))
                return self.send(200, {"ok": True, "entries": len(data["entries"]), "report": report_out})
            if p.startswith("/api/git/"):          # Save / Git page (app/git_save.py)
                import git_save
                return self.send(*git_save.handle_post(CFG, p, body, JOBS))
            if p.startswith("/api/books/"):           # Experimental story library: Build / update (app/story_library.py)
                import story_library
                try:
                    return self.send(*story_library.handle_post(CFG, p, body, JOBS))
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
            if p.startswith("/api/rate/") or p.startswith("/api/sounds/"):   # ratings (feedback.py), Sounds page
                import feedback, sound_library
                try:
                    return self.send(*(feedback if p.startswith("/api/rate/") else sound_library).handle_post(CFG, p, body, JOBS))
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
            if p.startswith("/api/narrator/"):         # corner narrator page (app/narrator.py)
                import narrator
                try:
                    return self.send(*narrator.handle_post(CFG, p, body, JOBS))
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
            if p.startswith("/api/thumb/"):            # Thumbnails page (app/thumbnail.py)
                import thumbnail
                try:
                    return self.send(*thumbnail.handle_post(CFG, p, body, JOBS))
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
            if p.startswith("/api/create/") or p.startswith("/api/queue/"):   # Create form defaults, build queue
                import create_form
                return self.send(*create_form.handle_post(CFG, JOBS, p, body))
            if p.startswith("/api/tinker/"):           # Experimental settings panel (app/tinker.py)
                import tinker
                if p == "/api/tinker/save":
                    return self.send(200, tinker.save(CFG, body.get("section"), body.get("key"), body.get("value")))
                if p in ("/api/tinker/promote", "/api/tinker/working_reset"):
                    if any(j["status"] == "running" for j in JOBS.jobs):
                        return self.send(409, {"error": "a job is running; promote when it has finished"})
                    if p == "/api/tinker/promote":
                        return self.send(200, tinker.promote(CFG, body.get("items") or []))
                    return self.send(200, tinker.working_reset(CFG, body.get("section"), body.get("key")))
            if p == "/api/jobs":
                kind = body.get("kind")
                if self.headers.get("X-Hub-Experimental") == "1" or body.get("_experimental_mode") is True:
                    import experimental_workspace
                    job_id = time.strftime("%m%d_%H%M%S_") + uuid.uuid4().hex[:4]
                    plan, params, _ = experimental_workspace.prepare(kind, body, CFG, job_id)
                    params["_requested_job_id"] = job_id
                    j = JOBS.add(kind, plan["title"], plan["steps"], plan.get("outputs"), params)
                else:
                    plan = tools.plan(kind, body, CFG)          # -> title, steps, outputs
                    j = JOBS.add(kind, plan["title"], plan["steps"], plan.get("outputs"), body)
                return self.send(200, {"id": j["id"]})
            m = re.match(r"^/api/jobs/([\w]+)/(stop|again|pause|resume)$", p)
            if m:
                jid, act = m.groups()
                if act == "stop":
                    return self.send(200, {"ok": JOBS.stop(jid)})
                if act == "pause":
                    return self.send(200, {"ok": JOBS.pause(jid)})
                if act == "resume":
                    ok, why = JOBS.resume(jid)
                    return self.send(200, {"ok": ok, "error": why})
                j = JOBS.get(jid)
                if not j:
                    return self.send(404, {"error": "no such job"})
                if not j.get("steps"):
                    return self.send(400, {"error": "this job has no steps saved - start it fresh from its page"})
                k = JOBS.add(j["kind"], j["title"] + " (again)", j["steps"], j.get("outputs"), j.get("params"))
                return self.send(200, {"id": k["id"]})
            if p == "/api/open":
                f = body.get("path", "")
                if not allowed(f) and f not in (CFG.get("other_windows") or {}).values():
                    return self.send(403, {"error": "not an allowed folder"})
                if body.get("directory"):
                    os.makedirs(f, exist_ok=True)
                tools.open_path(f)
                return self.send(200, {"ok": True})
            if p == "/api/translate":
                return self.send(200, {"text": tools.translate(CFG, body.get("text", ""))})
            if p == "/api/upload":
                upload_cfg = CFG
                if self.headers.get("X-Hub-Experimental") == "1":
                    import experimental_workspace
                    upload_cfg = dict(CFG, media_dir=os.path.join(experimental_workspace.root(CFG), "Library"))
                return self.send(200, {"file": tools.save_upload(upload_cfg, body.get("name", "upload.png"), body.get("data", ""))})
            if p == "/api/story/ideas":
                import story
                return self.send(200, {"ideas": story.ideas(CFG, body.get("topic", ""), int(body.get("n") or 3),
                                                            body.get("writer", "auto"), body.get("category", ""),
                                                            "en" if body.get("language") == "en" else "hi")})
            if p == "/api/horror/ideas":
                import story
                return self.send(200, {"ideas": story.genre_ideas(CFG, body.get("genre") or "bhay", body.get("topic", ""), 3,
                                                                  body.get("writer", "auto"))})
            if p == "/api/voice/styles":
                d = CFG.get("voice_references_dir", os.path.join(CFG.get("sukoonvoice_media", ""), "dataset", "refs"))
                names = sorted(f[:-4] for f in os.listdir(d) if f.endswith(".wav") and not f.startswith(("room_tone", "smile"))) \
                    if os.path.isdir(d) else []
                import pacing
                profile_cfg = CFG
                episode_rows = tools.episodes(CFG)
                if self.headers.get("X-Hub-Experimental") == "1":
                    import experimental_workspace
                    exp_root = experimental_workspace.root(CFG)
                    profile_cfg = dict(CFG, media_dir=os.path.join(exp_root, CFG.get("layout", {}).get("experimental_library", "Library")))
                    episode_rows = (experimental_workspace.library(CFG).get("episodes") or [])
                indexed = pacing.refresh(profile_cfg, episode_rows)
                preferred = {"bhay": "burarikand", "rahasya": "walkingcorpse", "kisse": "aarav",
                             "romanch": "pehlapyar", "kareeb": "hypnosis", "gyaan": "psychology"}.get(body.get("genre"), "")
                series_data = tools.series_load(CFG)
                style_genres = {name: tools.episode_series_of(name, series_data) for name in names}
                # The reference clips' source stories are already indexed with text, episode and duration.
                # Use those measurements when no completed HubGPT timing profile matches a reference.
                for ep_name, row in pacing.reference_episode_profiles(CFG).items():
                    if ep_name not in indexed.get("items", {}):
                        row = dict(row)
                        sid = tools.episode_series_of(ep_name, series_data)
                        row["genre"] = sid if sid in tools.GENRES or sid in ("sukoon", "sukoon_en") else "unknown"
                        indexed.setdefault("items", {})[ep_name] = row
                ranked = pacing.rank_voice_references(names, indexed.get("items", {}), body.get("script", ""),
                                                      body.get("genre", "unknown"), body.get("minutes") or 0,
                                                      preferred, style_genres)
                result = {"styles": [x["name"] for x in ranked], "ranking": ranked,
                          "recommended": ranked[0]["name"] if ranked else ""}
                if body.get("suggest_genre") and str(body.get("script") or "").strip():
                    allowed_genres = {**{k: v["label"] for k, v in tools.GENRES.items()}, "mixed": "Mixed",
                                      "sukoon": "सुकून Hindi", "sukoon_en": "सुकून English"}
                    prompt = ("Classify the primary genre of this Hindi/Urdu or English story for narration. "
                              "Return JSON only: {\"genre\": one of " + json.dumps(list(allowed_genres), ensure_ascii=False) +
                              ", \"confidence\": number from 0 to 1, \"reason\": a short phrase}. "
                              "Choose mixed only if it genuinely spans genres. Do not rewrite the story.\n\nSTORY:\n" +
                              str(body.get("script") or "")[:9000])
                    try:
                        if any(j.get("status") == "running" for j in JOBS.jobs):
                            raise RuntimeError("wait until the current job finishes before asking the local model")
                        model = CFG.get("prompt_model") or CFG.get("llm")
                        answer = tools.model_chat(CFG, model, prompt, temperature=0.1, as_json=True, timeout=90)
                        suggestion = json.loads(answer)
                        gid = str(suggestion.get("genre", "")).lower()
                        if gid in allowed_genres:
                            result["genre_suggestion"] = {"id": gid, "name": allowed_genres[gid],
                                                          "confidence": max(0, min(1, float(suggestion.get("confidence", 0)))),
                                                          "reason": str(suggestion.get("reason", ""))[:160]}
                    except Exception as e:
                        result["genre_error"] = str(e)[:180]
                return self.send(200, result)
            if p == "/api/llm":
                if body.get("llm"):
                    if any(j["status"] == "running" for j in JOBS.jobs):
                        return self.send(409, {"error": "a job is running; wait for it to finish before switching models"})
                    return self.send(200, tools.set_llm(CFG, body["llm"]))
                if body.get("prompt_model"):
                    if any(j["status"] == "running" for j in JOBS.jobs):
                        return self.send(409, {"error": "a job is running; wait for it to finish before switching models"})
                    return self.send(200, tools.set_prompt_model(CFG, body["prompt_model"]))
                return self.send(200, tools.llm_models(CFG))
            if p == "/api/voice/scan":
                return self.send(200, tools.voice_scan(CFG, body.get("folder", "")))
            if p == "/api/voice/folders":
                f = body.get("folder", "")
                if not f or not os.path.isdir(f) or not re.match(r"^[DdEe]:", f):
                    return self.send(400, {"error": "a folder on D: or E:"})
                return self.send(200, {"folders": tools.voice_add_folders(CFG, f)})
            if p == "/api/voice/rack":
                return self.send(200, tools.rack_save(CFG, body))
            if p == "/api/voice/series":
                if body.get("episodes") is not None or body.get("add"):
                    return self.send(200, tools.voice_series_save(CFG, body.get("episodes") or {}, body.get("add")))
                return self.send(200, tools.voice_series(CFG))
            if p == "/api/suggestions/dismiss":
                tools.dismiss(CFG, str(body.get("id", "")))
                return self.send(200, {"ok": True})
            if p == "/api/remove":
                f = body.get("path", "")
                if not allowed(f) or os.path.normcase(os.path.abspath(f)) in roots():
                    return self.send(403, {"error": "not an allowed folder"})
                return self.send(200, {"moved_to": tools.remove_item(CFG, f)})
            if p == "/api/jobs/clear":
                ids = set(body.get("ids") or [])
                exp_mode = self.headers.get("X-Hub-Experimental") == "1"
                with JOBS.lock:
                    keep, gone = [], []
                    for j in JOBS.jobs:
                        fin = j["status"] in ("done", "failed", "stopped")
                        same_mode = bool((j.get("params") or {}).get("_experimental_workspace")) == exp_mode
                        (gone if fin and same_mode and (not ids or j["id"] in ids) else keep).append(j)
                    JOBS.jobs = keep
                if exp_mode:
                    import experimental_workspace
                    arch = os.path.join(experimental_workspace.root(CFG), "Activity", "jobs_archive.json")
                else:
                    arch = os.path.join(MEDIA, "jobs_archive.json")
                try:
                    old = json.load(open(arch, encoding="utf-8")) if os.path.exists(arch) else []
                except Exception:
                    old = []
                json.dump(old + gone, open(arch, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                JOBS.save()
                return self.send(200, {"cleared": len(gone)})
            if p == "/api/narration/lines":
                return self.send(200, tools.narration_lines(CFG, body.get("dir", "")))
            if p == "/api/repair/report":
                f = os.path.splitext(body.get("video", ""))[0] + ".check.json"
                return self.send(200, json.load(open(f, encoding="utf-8")) if os.path.exists(f) else {})
            if p == "/api/reels/motions":
                return self.send(200, tools.reel_motions(CFG, body.get("video", "")))
            if p == "/api/story/estimate":
                import story
                return self.send(200, story.estimate(CFG, body.get("text", ""), body.get("language") or None))
            if p == "/api/story/scripts":
                import story
                script_cfg = CFG
                if self.headers.get("X-Hub-Experimental") == "1":
                    import experimental_workspace
                    exp_root = experimental_workspace.root(CFG)
                    script_cfg = dict(CFG, media_dir=os.path.join(exp_root, "Library"))
                    script_pattern = os.path.join(exp_root, CFG.get("layout", {}).get("experimental_library", "Library"),
                                                  CFG.get("layout", {}).get("experimental_scripts", "Scripts"), "*.txt")
                else:
                    script_pattern = os.path.join(CFG["scripts_dir"], "*.txt")
                out = []
                for f in sorted(__import__("glob").glob(script_pattern),
                                key=os.path.getmtime, reverse=True)[:200]:
                    if re.search(r"(_old_\d+|_full|\.en)\.txt$", f):
                        continue
                    try:
                        t = open(f, encoding="utf-8").read()
                    except OSError:
                        continue
                    meta = {}
                    if os.path.exists(f[:-4] + ".story.json"):
                        try:
                            meta = json.load(open(f[:-4] + ".story.json", encoding="utf-8"))
                        except Exception:
                            meta = {}
                    e = story.estimate(script_cfg, t)
                    nm = os.path.basename(f)[:-4]
                    if self.headers.get("X-Hub-Experimental") == "1":
                        eps = __import__("glob").glob(os.path.join(exp_root, "Series", "*", "Episodes", nm))
                        ep_dir = eps[0] if eps else ""
                        ep_out = os.path.join(ep_dir, CFG.get("episode_output_name", "Output"))
                        nar_file = os.path.join(ep_dir, "02_Narration", "narrations", nm, "narration.wav")
                    else:
                        ep_out = os.path.join(CFG.get("episodes_dir", ""), nm, CFG.get("episode_output_name", "Output"))
                        nar_file = os.path.join(tools.narration_dir(CFG, nm, existing=True), "narration.wav")
                    vids = [v for v in __import__("glob").glob(os.path.join(ep_out, "*.mp4")) if "_preview" not in v]
                    if vids:
                        state = "made"                          # the whole process ran: video exists
                    elif os.path.exists(nar_file):
                        state = "voice"
                    else:
                        state = "new"
                    out.append({"file": f, "name": nm, "minutes": e["minutes"], "state": state,
                                "video": vids[0] if vids else "",
                                "words": e["words"], "category": (meta.get("recipe") or {}).get("category", ""),
                                "lang": e["lang"], "wpm": e["wpm"], "genre": meta.get("genre", ""),
                                "title": (meta.get("summary") or {}).get("title_hi", ""), "text": t})
                order = {"new": 0, "voice": 1, "made": 2}
                out.sort(key=lambda x: order[x["state"]])          # unused scripts first, made ones last
                return self.send(200, {"scripts": out, "wpm": story.wpm(script_cfg), "wpm_en": story.wpm(script_cfg, "en")})
            if p == "/api/story/prompt":
                import story
                r = body.get("recipe") or story.recipe(CFG, body.get("topic", ""), category=body.get("category", ""),
                                                       lang="en" if body.get("language") == "en" else "hi")
                return self.send(200, {"recipe": r, "prompt": story.build_prompt(
                    CFG, r, float(body.get("minutes") or 12), body.get("notes", ""), True, True)})
            if p == "/api/story/settings":
                import story
                return self.send(200, story.save_settings(CFG, body))
            if p == "/api/restart":
                if any(j["status"] == "running" for j in JOBS.jobs) and not body.get("force"):
                    return self.send(409, {"error": "a job is running - it would be cut short (it continues after the "
                                                    "restart, from its current step). Restart anyway?"})
                snapshot = git_snapshot()
                restart_self()
                return self.send(200, {"ok": True, "git_snapshot": snapshot})
            if p == "/api/system":
                return self.send(200, tools.system_action(body.get("action"), CFG, JOBS,
                                                          body.get("model"), body.get("backend")))
        except ValueError as e:
            return self.send(400, {"error": str(e)})
        except Exception as e:
            return self.send(500, {"error": str(e), "trace": traceback.format_exc()[-1500:]})
        return self.send(404, {"error": "unknown"})


def main():
    global JOBS
    port = int(CFG.get("port", 8781))
    url = f"http://127.0.0.1:{port}"
    srv = None
    for _ in range(40 if "--wait-port" in sys.argv else 1):    # after a restart: wait for the old hub to let go
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), H)
            break
        except OSError:
            time.sleep(0.5)
    if srv is None:                                    # already running -> just open it
        print(f"\n  The hub is already running - opening {url}\n", flush=True)
        import webbrowser; webbrowser.open(url)
        return
    tools.apply_settings(CFG)                          # your choices from Settings (e.g. the local writer model)
    try:                                               # prompts and tables promoted from Experimental mode
        import tinker
        tinker.install(CFG)
    except Exception as e:
        print(f"  (working_tinker.json was not applied: {e})", flush=True)
    try:                                               # Experimental runs pick music / effects with your ratings
        import feedback
        feedback.install(CFG)
    except Exception as e:
        print(f"  (ratings were not connected to music / effects selection: {e})", flush=True)
    try:                                               # corner narrator steps (app/narrator.py; off until a series turns it on)
        import narrator
        narrator.install(CFG)
    except Exception as e:
        print(f"  (corner narrator not connected: {e})", flush=True)
    try:                                               # Experimental genre runs make a thumbnail after the video (app/thumbnail.py)
        import thumbnail
        thumbnail.install(CFG)
    except Exception as e:
        print(f"  (thumbnails not connected: {e})", flush=True)
    JOBS = Jobs()
    try:                                               # a small note for whoever checks the hub from outside
        os.makedirs(CFG["hub_logs_root"], exist_ok=True)
        open(os.path.join(CFG["hub_logs_root"], "hub_started.txt"), "w", encoding="utf-8").write(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {url}  python {sys.version.split()[0]}  media {MEDIA}\n")
    except OSError:
        pass
    print(f"\n  HUBGPT  ->  {url}\n  (keep this window open; close it to stop the hub)\n", flush=True)
    if "--no-browser" not in sys.argv:
        try:
            import webbrowser; webbrowser.open(url)
        except Exception:
            pass
    srv.serve_forever()


if __name__ == "__main__":
    main()
