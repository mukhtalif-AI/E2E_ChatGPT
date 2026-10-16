"""split_move.py - move one group of functions out of app\\tools.py into app\\steps\\<module>.py (TOOLS_SPLIT_PLAN.md).

    python tests\\split_move.py 4a            dry run: what would move, which names get "tools." in front
    python tests\\split_move.py 4a --apply    do it: backup, move, re-export, compile, smoke test (undone if it fails)
    python tests\\split_move.py --undo 4a     put tools.py back from that move's backup and remove the new file
    python tests\\split_move.py --list        the moves and whether each is done

Use Python 3.12 or newer (the hub's Python 3.13 is fine): older versions report wrong positions inside f-strings.
The hub may keep running; the moved code is only used after a hub restart. Do not restart while a job runs.

What a move does (the rules are in TOOLS_SPLIT_PLAN.md):
 1 refuses when app\\tools.py has changes that are not committed (--force skips this), or when the move is unsafe
   (a name is missing, staying code needs a moved name while tools.py is still loading, a `global` would split)
 2 backs up tools.py as tools.py_old_<time>_split<step>
 3 cuts the functions / constants (with the comment lines right above them) into app\\steps\\<module>.py, which starts
   with `import tools`; inside the moved code every name that stays elsewhere, and every name another file swaps
   (feedback, narrator, Tinker), is written as tools.X; nothing else changes
 4 adds one line to the re-export block at the end of tools.py, naming every moved name
 5 compiles both files and runs tests\\smoke_tools.py; if anything fails, the move is undone
"""
import argparse, ast, glob, os, py_compile, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP = os.path.join(ROOT, "app")
TOOLS = os.path.join(APP, "tools.py")
STEPS = os.path.join(APP, "steps")
BLOCK = "# ---- moved out of tools.py (TOOLS_SPLIT_PLAN.md). tools stays the front door: keep this block at the END."

MOVES = {
    "4a": ("hub", "Library / System / Settings pages",
           "gpu_info _gpu_cache status _dismissed_path dismiss suggestions _mtime library episodes library_stamp "
           "remove_item open_path system_action _settings_path apply_settings llm_models set_llm set_prompt_model"),
    "4b": ("thumbs", "thumbnail look index",
           "index_path load_index series_of SUKOON_WORDS thumb_sources look_text style_index"),
    "4c": ("voice_series", "series voices and voice training",
           "SERIES_DEFAULT _series_file series_load _norm episode_series_of genre_voice_sets series_model voice_series "
           "voice_series_save _voice_cfg_path voice_scan voice_add_folders sv_dataset classify_recordings "
           "train_all_series sv_train"),
    "4d": ("mix", "music index and voice + music mix",
           "audio_index_path load_audio_index audio_index auto_track harshness tamed _decode shape_mix"),
    "4e": ("sukoon_video", "Sukoon video and slideshow",
           "_first_image _audio_of studio_prepare studio_video _wav_len font_family _ass_color _ass_time make_ass slideshow"),
    "4f": ("pictures", "artwork and pictures",
           "_parts llm_prompts default_style _script_sample art_prompt pick_art HOT_DRINK no_steam comfy_images _judge "
           "pick_scenes repaint"),
    "4g": ("voice", "voice, English voice, word rack, GPU",
           "rack_path word_rack hard_words rack_save avoid_hard_words practice_story free_gpu free_llm sv_python "
           "_story_voice_style sv_speak en_dir en_python en_speak en_setup en_test"),
    "4h": ("script", "scripts, translation, elements, emotion marks",
           "translate llm_translate _clean_script llm_story fit_script _queue_fetch elements_check elements_fetch "
           "write_stories write_script EMOTIONS EMO_BATCH _emo_salvage _emo_reply mark_emotions"),
    "4i": ("fixes", "repair page and reels from a finished video",
           "_secs repair_check repair_keep_old repair_save_voice repair_patch repair_series_subtitles "
           "repair_write_subtitle_text series_of_video _series_note remix_like_before narration_lines repair_rerender "
           "_duration _PREV reel_motions studio_reels"),
    "4j": ("scenes", "genre scene planning and sound cues",
           "_sfx_names _sfx_names_old AMB_WORDS PICTURE_RULES _pic_rules _anchor_text SCENE_SIMILAR_MAX SCENE_VARY_HINTS "
           "_PEOPLE _look_for_prompt _overlap _vary_scene _words_in AMB_SOURCES _amb_fits _fix_cue_words sfx_fetch "
           "_word_time _SENT_END INVENTED WEATHER _wre _beat_parts _invented _strip_words _model_cfg _ask_step story_ask "
           "_loads _clean_scene _picture_prompts _set_aside_scenes horror_scenes"),
    "4k": ("genre", "genre story, sound, video and reels",
           "horror_story write_horror_stories _horror_track _usable_music horror_sound _subtitle_text_for_timing _is_exp "
           "_end_seconds _subscribe_sounds _subscribe_end horror_video genre_reels"),
    "4l": ("planner", "the job planner",
           "presets use_shape voice_sets studio_opts save_upload unique_genre_name _rename_job_values plan"),
}
# swapped by other files at run time: moved code must always go through tools (feedback, narrator, tinker)
SWAPPED = {"_horror_track", "_sfx_names", "shape_mix", "plan", "horror_video", "genre_reels", "narrator_overlay",
           "narrator_art"}
# Tinker swaps these with setattr(tools, ...) and lists long ALL-CAPS texts in tools as prompts: they never move
STAY = {"GENRES", "STYLES", "DIRECTION", "TRANSLATE_ASK", "VISION_ASK", "ART_ASK", "PICK_ASK", "SCENE_ASK",
        "SCENES_ASK", "CUE_WORDS_ASK", "SCENE_VARY_ASK", "SCENE_PICTURE_ASK", "SCENE_FIX_ASK", "END_SECONDS",
        "SUBSCRIBE_END", "SCENE_SECONDS", "MIN_TAIL", "SIZES", "VOICES", "THEMES", "TYPOS", "VIZ", "PAUSES"}


def say(*a):
    print(*a, flush=True)


def top_level(tree):
    """name -> node for every def / class / simple assignment at the top of the module"""
    out = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[n.name] = n
        elif isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) for t in n.targets):
            for t in n.targets:
                out[t.id] = n
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out[n.target.id] = n
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                if a.name != "*":
                    out[(a.asname or a.name).split(".")[0]] = n
    return out


def span(lines, node):
    """first and last line (1-based) of a top-level statement, with decorators and the comment lines right above it"""
    a = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    while a > 1 and lines[a - 2].lstrip().startswith("#"):
        a -= 1
    return a, node.end_lineno


def locals_of(f):
    a = f.args
    loc = {x.arg for x in a.args + a.kwonlyargs + a.posonlyargs}
    loc |= {x.arg for x in (a.vararg, a.kwarg) if x}
    for x in ast.walk(f):
        if isinstance(x, ast.Name) and isinstance(x.ctx, (ast.Store, ast.Del)):
            loc.add(x.id)
        elif isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and x is not f:
            loc.add(x.name)
        elif isinstance(x, ast.ExceptHandler) and x.name:
            loc.add(x.name)
        elif isinstance(x, (ast.Import, ast.ImportFrom)):
            loc |= {(n.asname or n.name).split(".")[0] for n in x.names}
    return loc


def name_loads(stmt):
    """(Name node, is it a local of a function around it) for every name read in a top-level statement"""
    funcs = [(f, locals_of(f)) for f in ast.walk(stmt) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))]

    def inside(f, x):
        body = f.body if isinstance(f.body, list) else [f.body]
        a = (body[0].lineno, body[0].col_offset)
        b = (f.end_lineno, f.end_col_offset)
        return a <= (x.lineno, x.col_offset) <= b

    out = []
    for x in ast.walk(stmt):
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
            out.append((x, any(x.id in loc and inside(f, x) for f, loc in funcs)))
    return out


def import_time_loads(stmt):
    """names read when the statement runs at import: everything outside function bodies"""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        parts = stmt.decorator_list + stmt.args.defaults + [d for d in stmt.args.kw_defaults if d is not None]
        if stmt.returns:
            parts.append(stmt.returns)
    elif isinstance(stmt, ast.ClassDef):
        parts = stmt.decorator_list + stmt.bases + [s for s in stmt.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
    else:
        parts = [stmt]
    return {x.id for p in parts for x in ast.walk(p) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)}


def outside_users():
    """tools.X names used by the other app modules (to warn about `global` state that would split)"""
    used = set()
    for f in glob.glob(os.path.join(APP, "*.py")) + glob.glob(os.path.join(STEPS, "*.py")):
        if os.path.abspath(f) == os.path.abspath(TOOLS):
            continue
        try:
            t = ast.parse(open(f, encoding="utf-8-sig").read())
        except SyntaxError:
            continue
        used |= {x.attr for x in ast.walk(t) if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name)
                 and x.value.id == "tools"}
    return used


def build(step):
    module, what, names = MOVES[step]
    names = names.split()
    src = open(TOOLS, encoding="utf-8").read()
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)
    top = top_level(tree)
    problems = []
    missing = [n for n in names if n not in top]
    if missing:
        problems.append("not found at the top of tools.py (already moved, or renamed?): " + ", ".join(missing))
        return None, problems
    if set(names) & STAY:
        problems.append("these must stay in tools.py: " + ", ".join(sorted(set(names) & STAY)))
    stmts = []
    for n in names:
        node = top[n]
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            problems.append(f"{n} is an import in tools.py; it cannot move")
        elif node not in stmts:
            stmts.append(node)
    for node in stmts:                                               # an assignment of several names moves whole
        if isinstance(node, ast.Assign):
            extra = [t.id for t in node.targets if t.id not in names]
            if extra:
                problems.append(f"line {node.lineno} also sets {', '.join(extra)}; add it to the move")
    stmts.sort(key=lambda s: s.lineno)
    moved = set(names)
    module_names = {k for k, v in top.items() if isinstance(v, ast.Import)}     # `from comfy import x` -> tools.x
    staying = {k for k in top if k not in moved}
    # staying code that needs a moved name while tools.py is still loading would fail (the re-export is at the end)
    for s in tree.body:
        if s in stmts:
            continue
        early = import_time_loads(s) & moved
        if early:
            problems.append(f"tools.py line {s.lineno} uses {', '.join(sorted(early))} while loading; it cannot move first")
    # `global X` in moved code: X must not be read by code that stays or by other modules
    used_out = outside_users()
    for s in stmts:
        for g in (x for x in ast.walk(s) if isinstance(x, ast.Global)):
            for gn in g.names:
                users = [t.lineno for t in tree.body if t not in stmts and any(
                    isinstance(x, ast.Name) and x.id == gn for x in ast.walk(t))]
                if users or gn in used_out:
                    problems.append(f"`global {gn}` (line {g.lineno}) is also used outside this move; it would split in two")
    # rewrite names inside the moved code
    edits = {}                                                       # line -> [(col, end_col, new)]
    rewrites = {}
    for s in stmts:
        for x, is_local in name_loads(s):
            n = x.id
            if is_local or n in module_names or n not in top:
                continue
            if n in moved and n not in SWAPPED:
                continue
            if n == "WIN":
                continue                                             # the new file has its own WIN
            edits.setdefault(x.lineno, []).append((x.col_offset, x.end_col_offset, "tools." + n))
            rewrites[n] = rewrites.get(n, 0) + 1
        for x in ast.walk(s):                                        # plan() calls itself as globals()["plan"]
            if isinstance(x, ast.Subscript) and isinstance(x.value, ast.Call) and isinstance(x.value.func, ast.Name) \
                    and x.value.func.id == "globals" and isinstance(x.slice, ast.Constant) and x.slice.value in top:
                edits.setdefault(x.lineno, []).append((x.col_offset, x.end_col_offset, "tools." + x.slice.value))
                rewrites[f'globals()["{x.slice.value}"]'] = 1
    for ln, es in edits.items():
        if any(e[0] is None for e in es):
            problems.append(f"line {ln}: position unknown")
    taken, chunks = set(), []
    for s in stmts:
        a, b = span(lines, s)
        if a in taken:
            continue
        taken.update(range(a, b + 1))
        body = []
        for ln in range(a, b + 1):
            text = lines[ln - 1]
            for col, end, new in sorted(edits.get(ln, []), key=lambda e: -e[0]):
                bl = text.encode("utf-8")                            # ast columns count UTF-8 bytes
                text = (bl[:col] + new.encode("utf-8") + bl[end:]).decode("utf-8")
            body.append(text)
        chunks.append((a, b, "".join(body)))
    std = [l for l in lines[:12] if re.match(r"import [a-z_, .]+$", l.strip())]
    uses_win = any(x.id == "WIN" for s in stmts for x in ast.walk(s) if isinstance(x, ast.Name))
    new_mod = (f'"""steps/{module}.py - {what}. Moved out of tools.py on {time.strftime("%d %b %Y")} '
               f'(TOOLS_SPLIT_PLAN.md step {step}).\n\nDo not import this file directly: import tools and use tools.<name>. '
               'Names that stay in tools.py, and names other files swap\n(feedback, narrator, Tinker), are written as '
               'tools.X here on purpose.\n"""\n'
               + "".join(std) + "\nimport tools\n" + ('\nWIN = os.name == "nt"\n' if uses_win else "") + "\n\n"
               + "\n\n".join(c[2].rstrip("\n") + "\n" for c in chunks))
    keep = [l for i, l in enumerate(lines, 1) if i not in taken]
    rest = "".join(keep).rstrip("\n") + "\n"
    order = [n for n in names]
    line = f"from steps.{module} import ({', '.join(order)})  # noqa: E402,F401  ({step})"
    if BLOCK not in rest:
        rest += "\n\n" + BLOCK + "\n"
    rest += line + "\n"
    return {"module": module, "what": what, "names": names, "new": new_mod, "tools": rest, "rewrites": rewrites,
            "chunks": [(a, b) for a, b, _ in chunks], "moved_lines": len(taken)}, problems


def git(*args):
    try:
        r = subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True, encoding="utf-8")
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except OSError as e:
        return 1, str(e)


def done_moves():
    src = open(TOOLS, encoding="utf-8").read()
    return {m.group(1) for m in re.finditer(r"^from steps\.\w+ import .*\((4[a-z])\)\s*$", src, re.M)}


def undo(step):
    module = MOVES[step][0]
    backs = sorted(glob.glob(TOOLS + f"_old_*_split{step}"))
    if not backs:
        say(f"no backup tools.py_old_*_split{step} found; use git (Save / Git page or `git revert`)")
        return 1
    shutil.copy2(backs[-1], TOOLS)
    p = os.path.join(STEPS, module + ".py")
    if os.path.exists(p):
        os.replace(p, p + f"_old_{time.strftime('%Y%m%d_%H%M%S')}_undone")
    say(f"tools.py restored from {os.path.basename(backs[-1])}; steps\\{module}.py set aside. Restart the hub.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("step", nargs="?")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", metavar="STEP")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true", help="move even when tools.py has uncommitted changes")
    ap.add_argument("--no-smoke", action="store_true", help="skip the smoke test (only for testing this script)")
    ap.add_argument("--smoke-args", default="", help="extra arguments for smoke_tools.py, e.g. --quick")
    o = ap.parse_args()
    if sys.version_info < (3, 12):
        say("needs Python 3.12 or newer (f-string positions); use the hub's Python")
        return 2
    if o.undo:
        return undo(o.undo)
    done = done_moves()
    if o.list or not o.step:
        for k, (m, what, names) in MOVES.items():
            say(f"  {k}  {'done ' if k in done else '     '} steps\\{m}.py  {what}  ({len(names.split())} names)")
        return 0
    if o.step not in MOVES:
        say("unknown step; --list shows them")
        return 2
    if o.step in done:
        say(f"{o.step} is already done")
        return 0
    plan, problems = build(o.step)
    if plan:
        say(f"{o.step}: {len(plan['names'])} names, {plan['moved_lines']} lines -> app\\steps\\{plan['module']}.py "
            f"({plan['what']})")
        say("  from tools.py lines " + ", ".join(f"{a}-{b}" for a, b in plan["chunks"]))
        say("  written as tools.X inside the moved code: " +
            (", ".join(f"{k} x{v}" for k, v in sorted(plan["rewrites"].items())) or "nothing"))
        say(f"  tools.py after the move: {plan['tools'].count(chr(10))} lines")
    for p in problems:
        say("  PROBLEM: " + p)
    if problems or not plan:
        return 1
    if not o.apply:
        say("dry run only; add --apply to move")
        return 0
    rc, out = git("status", "--porcelain", "--", "app/tools.py")
    if rc == 0 and out.strip() and not o.force:
        say("app\\tools.py has changes that are not committed; commit them first (Save / Git page) or add --force")
        return 1
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = TOOLS + f"_old_{stamp}_split{o.step}"
    mtime = os.path.getmtime(TOOLS)
    shutil.copy2(TOOLS, backup)
    os.makedirs(STEPS, exist_ok=True)
    init = os.path.join(STEPS, "__init__.py")
    if not os.path.exists(init):
        open(init, "w", encoding="utf-8").write(
            '"""Code moved out of tools.py (TOOLS_SPLIT_PLAN.md). Never import steps.* directly: import tools."""\n')
    target = os.path.join(STEPS, plan["module"] + ".py")
    if os.path.exists(target):
        say(f"{target} already exists; not overwriting")
        return 1
    if os.path.getmtime(TOOLS) != mtime:
        say("tools.py changed while preparing the move (another session?); nothing written")
        return 1
    open(target, "w", encoding="utf-8", newline="\n").write(plan["new"])
    open(TOOLS, "w", encoding="utf-8", newline="\n").write(plan["tools"])
    say(f"  backup: {os.path.basename(backup)}\n  wrote:  app\\steps\\{plan['module']}.py and app\\tools.py")
    ok = True
    for f in (TOOLS, target):
        try:
            py_compile.compile(f, doraise=True, cfile=os.path.join(ROOT, "_tmp", "split_compile.pyc"))
        except py_compile.PyCompileError as e:
            say(f"  compile failed: {e.msg}")
            ok = False
    if ok and not o.no_smoke:
        say("  running the smoke test ...")
        cmd = [sys.executable, "-s", os.path.join(HERE, "smoke_tools.py")] + o.smoke_args.split()
        r = subprocess.run(cmd, cwd=ROOT)
        ok = r.returncode == 0
    if not ok:
        shutil.copy2(backup, TOOLS)
        os.replace(target, target + f"_old_{stamp}_failed")
        say(f"  FAILED: the move is undone (tools.py restored, the new file kept as {os.path.basename(target)}_old_{stamp}_failed)")
        return 1
    say(f"\n{o.step} moved. Next: restart the hub when no job runs, check the pages / a short job (TOOLS_SPLIT_PLAN.md), "
        f"commit (\"split tools.py {o.step}: steps/{plan['module']}.py\") and add a CHANGELOG row.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
