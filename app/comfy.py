"""comfy.py - pictures through ComfyUI's local web API (graphs copied from Kisse), started by itself when needed.
ComfyUI itself is a SHARED resource (E:\\ComfyUI_windows_portable); its output goes to <media_dir>\\_comfy."""
import json, os, subprocess, threading, time, urllib.error, urllib.parse, urllib.request


# ------------------------------------------------------------------ ComfyUI (graphs = Kisse's, copied)
def z_image_turbo(prompt, negative, w, h, seed, steps=8, unet="z_image_turbo_bf16.safetensors",
                  clip="qwen_3_4b.safetensors"):
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "6": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
        "7": {"class_type": "EmptySD3LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "8": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0],
                                                   "latent_image": ["7", 0], "seed": seed, "steps": steps, "cfg": 1.0,
                                                   "sampler_name": "res_multistep", "scheduler": "simple", "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "PreviewImage", "inputs": {"images": ["9", 0]}},
    }


def z_image_turbo_fp8(prompt, negative, w, h, seed, steps=8):
    return z_image_turbo(prompt, negative, w, h, seed, steps, unet="z-image-turbo_fp8_scaled_e4m3fn_KJ.safetensors",
                         clip="qwen_3_4b_fp8_mixed.safetensors")


def dreamshaper_xl_turbo(prompt, negative, w, h, seed, steps=8):
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "dreamshaperXL_v21TurboDPMSDE.safetensors"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": prompt}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["1", 1], "text": negative or "text, watermark"}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0],
                                                   "latent_image": ["4", 0], "seed": seed, "steps": steps, "cfg": 2.0,
                                                   "sampler_name": "dpmpp_sde", "scheduler": "karras", "denoise": 1.0}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
        "7": {"class_type": "PreviewImage", "inputs": {"images": ["6", 0]}},
    }


ENGINES = {"z_image_turbo": z_image_turbo, "z_image_turbo_fp8": z_image_turbo_fp8,
           "dreamshaper_xl_turbo": dreamshaper_xl_turbo}


ACTIVE = {}          # worker thread -> the ComfyUI prompt it is waiting for (so Stop can cancel just that one)


def _get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def _post(url, data, timeout=60):
    req = urllib.request.Request(url, json.dumps(data).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def comfy_running(cfg):
    try:
        _get(cfg["comfy_url"] + "/system_stats", 3)
        return True
    except OSError:
        return False


def start_comfy(cfg, say=print, wait=240):
    d = cfg["comfy_dir"]
    py = os.path.join(d, "python_embeded", "python.exe")
    if not os.path.exists(py):
        raise RuntimeError(f"ComfyUI not found in {d}")
    say("   starting ComfyUI ...")
    media = os.path.join(cfg["media_dir"], "_comfy")
    os.makedirs(os.path.join(media, "output"), exist_ok=True)
    subprocess.Popen([py, "-s", os.path.join("ComfyUI", "main.py"), "--windows-standalone-build",
                      "--disable-auto-launch", "--output-directory", os.path.join(media, "output"),
                      "--temp-directory", media], cwd=d,
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(3)
        if comfy_running(cfg):
            say(f"   ComfyUI ready ({time.time() - t0:.0f} s)")
            return True
    raise RuntimeError("ComfyUI did not start - open its run_nvidia_gpu.bat once and look at its window")


def upload_image(cfg, path):
    """a reference picture -> ComfyUI's input folder (multipart upload). Returns its name there."""
    import uuid
    bnd = "----hub" + uuid.uuid4().hex
    name = "hub_ref_" + uuid.uuid4().hex[:8] + os.path.splitext(path)[1].lower()
    body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + open(path, "rb").read() + \
        f"\r\n--{bnd}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{bnd}--\r\n".encode()
    req = urllib.request.Request(cfg["comfy_url"] + "/upload/image", body,
                                 {"Content-Type": f"multipart/form-data; boundary={bnd}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read()).get("name", name)


def with_init_image(graph, image_name, w, h, denoise):
    """turn a text->image graph into image->image: the reference picture (scaled + cropped to the size) is the
    starting point; denoise 0.5 = stays close to it, 0.85 = keeps only its colours / light / layout."""
    vae = next((k for k, n in graph.items() if n["class_type"] == "VAELoader"), None)
    vae_ref = [vae, 0] if vae else next([k, 2] for k, n in graph.items() if n["class_type"] == "CheckpointLoaderSimple")
    graph["90"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    graph["91"] = {"class_type": "ImageScale", "inputs": {"image": ["90", 0], "upscale_method": "lanczos",
                                                         "width": int(w), "height": int(h), "crop": "center"}}
    graph["92"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["91", 0], "vae": vae_ref}}
    for n in graph.values():
        if n["class_type"] == "KSampler":
            n["inputs"]["latent_image"] = ["92", 0]
            n["inputs"]["denoise"] = float(denoise)
    return graph


def comfy_generate(cfg, prompt, out_png, engine, seed, w, h, timeout=900, steps=8, negative="", init_image=None,
                   denoise=0.8):
    graph = ENGINES[engine](prompt, negative or "", int(w), int(h), int(seed), steps=int(steps))
    if init_image:
        graph = with_init_image(graph, init_image, w, h, denoise)
    try:
        pid = json.loads(_post(cfg["comfy_url"] + "/prompt", {"prompt": graph}))["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ComfyUI refused the job: {e.read().decode('utf-8', 'replace')[:400]}")
    ACTIVE[threading.get_ident()] = pid
    t0, fails = time.time(), 0
    while time.time() - t0 < timeout:
        time.sleep(1.0 if not fails else min(10.0, 2.0 * fails))
        try:                                          # a busy ComfyUI (loading a model, VRAM swap) can answer slowly:
            hist = json.loads(_get(cfg["comfy_url"] + f"/history/{pid}", 60)).get(pid)   # wait, don't fail the job
            fails = 0
        except (TimeoutError, OSError, ValueError) as e:
            fails += 1
            if fails >= 30:
                raise RuntimeError(f"ComfyUI stopped answering ({e}) - is it still running?")
            continue
        if not hist:
            continue
        st = hist.get("status", {})
        if st.get("status_str") == "error":
            raise RuntimeError(f"ComfyUI error: {json.dumps(st.get('messages', []))[:400]}")
        for node in hist.get("outputs", {}).values():
            for im in node.get("images", []):
                q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                            "type": im.get("type", "output")})
                for k in range(4):
                    try:
                        data = _get(cfg["comfy_url"] + f"/view?{q}", 120)
                        break
                    except (TimeoutError, OSError):
                        if k == 3:
                            raise
                        time.sleep(5)
                os.makedirs(os.path.dirname(out_png), exist_ok=True)
                open(out_png + ".part", "wb").write(data)
                os.replace(out_png + ".part", out_png)
                return True
    raise RuntimeError("ComfyUI took too long")


