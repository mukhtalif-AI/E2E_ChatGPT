"""subject.py - find people / faces / the main subject in the artwork with the local Ollama vision model,
so the typography is never placed over someone's face. Cached next to the art loop. Safe fallback: no boxes."""
import base64, json, os, re, subprocess
import sv_av

PROMPT = ("This picture is {w} x {h} pixels. Find every person, face, child, animal and the main subject. "
          "Reply ONLY with JSON: {{\"boxes\": [[x1, y1, x2, y2], ...]}} using pixel coordinates of this picture. "
          "If there is nobody, reply {{\"boxes\": []}}.")


def _models():
    try:
        return sv_av.local_tags()
    except Exception:
        return []


def subject_boxes(loop_path, ffmpeg, W=1920, H=1080):
    cache = loop_path + ".subject.json"
    if os.path.exists(cache):
        return json.load(open(cache))["boxes"]
    w, h = 1000, 562
    jpg = loop_path + ".frame0.jpg"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-i", loop_path, "-frames:v", "1", "-vf", f"scale={w}:{h}", jpg],
                   check=True)
    img = base64.b64encode(open(jpg, "rb").read()).decode()
    boxes = []
    for model in _models():
        try:
            txt = sv_av.local_chat(model, PROMPT.format(w=w, h=h), json_mode=True, timeout=300, image=img)
            raw = json.loads(txt).get("boxes", [])
            for b in raw:
                if isinstance(b, dict):
                    b = b.get("bbox_2d") or b.get("box") or list(b.values())[:4]
                x1, y1, x2, y2 = [float(v) for v in b[:4]]
                if max(x1, x2, y1, y2) <= 1.0:                       # normalised 0..1
                    x1, x2, y1, y2 = x1 * w, x2 * w, y1 * h, y2 * h
                elif max(x1, x2) > w * 1.05 and max(x1, x2) <= 1000 and max(y1, y2) <= 1000:   # 0..1000 grid
                    y1, y2 = y1 * h / 1000, y2 * h / 1000
                x1, x2 = sorted((x1, x2)); y1, y2 = sorted((y1, y2))
                if x2 - x1 > 5 and y2 - y1 > 5:
                    boxes.append([int(x1 * W / w), int(y1 * H / h), int(x2 * W / w), int(y2 * H / h)])
            break
        except Exception:
            continue
    json.dump({"boxes": boxes}, open(cache, "w", encoding="utf-8"))
    try:
        os.remove(jpg)
    except OSError:
        pass
    return boxes
