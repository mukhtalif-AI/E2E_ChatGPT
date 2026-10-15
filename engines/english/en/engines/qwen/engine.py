"""
Qwen3-TTS 1.7B (Alibaba, Apache 2.0). Item keys:
  mode "custom"  speaker (English: Ryan, Aiden) + instruct (acting direction for the line)
  mode "design"  voice (the voice described in words) + instruct      <- the book pipeline uses this
  mode "clone"   ref_audio (wav) + ref_text - NOTE: Qwen's clone mode takes NO acting direction
say_many() speaks several texts in ONE call (much faster on the GPU); say() = one text.
Models (~3.8 GB each) download on first use into the shared hf_models folder.
"""
import torch

REPO = {"custom": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", "design": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "clone": "Qwen/Qwen3-TTS-12Hz-1.7B-Base"}


class Engine:
    def __init__(self):
        self.dev = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.mode, self.model, self.prompts = None, None, {}

    def _load(self, mode):
        if mode == self.mode:
            return
        from qwen_tts import Qwen3TTSModel
        self.model = None; torch.cuda.empty_cache()          # one model on the graphics card at a time
        print(f"      loading {REPO[mode]} ...", flush=True)
        self.model = Qwen3TTSModel.from_pretrained(REPO[mode], device_map=self.dev, dtype=torch.bfloat16,
                                                   attn_implementation="sdpa")
        self.mode, self.prompts = mode, {}

    def say_many(self, texts, items):
        mode = items[0].get("mode", "custom")
        self._load(mode)
        torch.manual_seed(int(items[0].get("seed", 7)))
        n, kw = len(texts), {"max_new_tokens": 2048}
        if mode == "custom":
            wavs, sr = self.model.generate_custom_voice(text=texts, language=["English"] * n,
                                                        speaker=[it.get("speaker", "Ryan") for it in items],
                                                        instruct=[it.get("instruct") or "" for it in items], **kw)
        elif mode == "design":
            desc = [it["voice"] + (f" Speaking style: {it['instruct']}." if it.get("instruct") else "") for it in items]
            wavs, sr = self.model.generate_voice_design(text=texts, language=["English"] * n, instruct=desc, **kw)
        else:
            wavs = []
            for t, it in zip(texts, items):
                key = it["ref_audio"]
                if key not in self.prompts:
                    self.prompts[key] = self.model.create_voice_clone_prompt(
                        ref_audio=key, ref_text=it.get("ref_text"), x_vector_only_mode=not it.get("ref_text"))
                w, sr = self.model.generate_voice_clone(text=t, language="English",
                                                        voice_clone_prompt=self.prompts[key], **kw)
                wavs.append(w[0])
        return list(wavs), sr

    def say(self, text, it):
        w, sr = self.say_many([text], [it])
        return w[0], sr
