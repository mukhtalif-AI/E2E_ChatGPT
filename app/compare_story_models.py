"""Compare installed local Ollama text models on one identical, brand-neutral Hindi story prompt."""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
import story
from path_config import load_config as _load_hub_config


def load_config():
    return _load_hub_config()


def main():
    cfg = load_config()
    print("\nHubGPT local story model comparison")
    topic = input("Story topic (same for every model): ").strip() or "overthinking at night"
    notes = input("Optional story notes (same for every model): ").strip()
    raw_minutes = input("Story length in minutes [3]: ").strip() or "3"
    try:
        minutes = min(15.0, max(1.0, float(raw_minutes)))
    except ValueError:
        print("Please enter a number of minutes.")
        return 2

    # Coder models are deliberately included: they can write stories, and this tool compares
    # every installed text-capable Ollama model. Exclude only known non-text/vision models.
    names = [name for name in story.local_models(cfg)
             if not re.search(r"(qwen[^/]*vl|vision|embed|whisper|tts|audio|clip)", name, re.I)]
    if not names:
        print("No text models found in Ollama. Pull a model and run this file again.")
        return 1
    print("\nModels tested sequentially:")
    for name in names:
        print("  -", name)
    print("\nA model loads at a time. This can take a while for several models.\n")

    recipe = story.recipe(cfg, topic, category="story", lang="hi")
    target_words = round(minutes * story.wpm(cfg, "hi"))
    prompt = (
        "Write one complete, coherent story in natural spoken Hindi (Devanagari). This is a neutral story-writing "
        "test: do not mention any channel or brand and do not include a subscribe/follow request unless the notes "
        "explicitly ask for one. Follow the topic, notes, and shared creative brief below; use its details naturally "
        "and keep imagery consistent. Give the story a clear beginning, development, and satisfying ending. Use short "
        "spoken phrases, one phrase per line, with occasional (...) pause lines. No title, headings, explanations, "
        "lists, or stage directions. Aim for the requested length. Output only the story script.\n\n"
        f"TOPIC: {topic}\n"
        f"NOTES: {notes or '(none)'}\n"
        f"LENGTH: about {target_words} Hindi words, approximately {minutes:g} minutes spoken.\n"
        f"SHARED CREATIVE BRIEF: {json.dumps(recipe, ensure_ascii=False)}\n"
    )
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    out_dir = os.path.join(cfg["media_dir"], "model_comparisons")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(out_dir, f"story_model_comparison_{stamp}.txt")
    results = []

    with open(report_path, "w", encoding="utf-8") as report:
        report.write("HUBGPT STORY MODEL COMPARISON\n")
        report.write(f"Topic: {topic}\nNotes: {notes or '(none)'}\nLength: {minutes:g} minutes\n")
        report.write(f"Shared recipe: {json.dumps(recipe, ensure_ascii=False)}\n")
        report.write(f"Exact prompt SHA-256 (same for each model): {prompt_hash}\n")
        report.write("Each model receives the exact same prompt, settings, and context length (8192).\n")
        report.write("Models run sequentially. Review the scripts below for Hindi naturalness, story flow,\n")
        report.write("format, image continuity, and whether the requested length was met.\n\n")
        for index, name in enumerate(names, 1):
            print(f"[{index}/{len(names)}] Generating with {name} ...", flush=True)
            report.write(f"\n{'=' * 78}\nMODEL: {name}\n")
            report.flush()
            started = time.time()
            try:
                model_cfg = dict(cfg, _llm_override=name)
                text, _ = story.local_chat(model_cfg, prompt, temp=0.95, ctx_len=8192)
                text = story.clean_for(text, "hi")
                elapsed = time.time() - started
                words = story.nwords(text)
                results.append((name, elapsed, words, "completed"))
                report.write(f"Time: {elapsed:.1f} seconds | Words: {words}\n\n{text}\n")
                print(f"    done: {words} words in {elapsed:.0f}s", flush=True)
            except Exception as exc:
                elapsed = time.time() - started
                results.append((name, elapsed, 0, f"error: {exc}"))
                report.write(f"FAILED after {elapsed:.1f} seconds: {exc}\n")
                print(f"    failed: {exc}", flush=True)
            report.flush()
        report.write("\n\nCOMPARISON SUMMARY\n")
        report.write("Model | Seconds | Words | Result\n")
        for name, elapsed, words, result in results:
            report.write(f"{name} | {elapsed:.1f} | {words} | {result}\n")
        report.write("\nQuality is not auto-ranked. Fill in a score from 1 to 5 for each model after reading the scripts.\n")
        report.write("Model | Hindi naturalness | Story flow | Format | Length fit | Overall\n")
        for name, _, _, _ in results:
            report.write(f"{name} | __ | __ | __ | __ | __\n")
    print(f"\nComparison saved to:\n{report_path}")
    return 0 if any(item[3] == "completed" for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
