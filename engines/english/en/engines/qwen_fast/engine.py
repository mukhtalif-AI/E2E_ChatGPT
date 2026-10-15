"""
qwen_fast - the SAME Qwen3-TTS 1.7B models (same voices, same quality) run through faster-qwen3-tts (MIT):
the whole decode step is captured as a CUDA graph -> several times faster, native Windows, no Triton/vLLM.
Item keys exactly as engine "qwen" (mode custom / design / clone + instruct). One line per call (no batch).
"""
import torch

REPO = {"custom": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", "design": "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        "clone": "Qwen/Qwen3-TTS-12Hz-1.7B-Base"}


def _rope_fix():
    """transformers 5.19+ keeps rope_theta inside config.rope_parameters; Qwen's compat helper (written for
    5.15) reads config.rope_theta. Read it from either place (no change to the installed libraries)."""
    try:
        import torch as _t
        from transformers.modeling_rope_utils import ROPE_INIT_FUNCTIONS
        import qwen_tts._transformers_compat as compat
    except Exception:
        return

    def default_rope(config, device=None, seq_len=None, layer_type=None):
        rp = getattr(config, "rope_parameters", None) or {}
        if isinstance(rp, dict) and layer_type and isinstance(rp.get(layer_type), dict):
            rp = rp[layer_type]
        try:
            base = config.rope_theta
        except AttributeError:
            base = (rp.get("rope_theta") if isinstance(rp, dict) else None) or 10000.0
        part = getattr(config, "partial_rotary_factor", None) or (rp.get("partial_rotary_factor") if isinstance(rp, dict) else None) or 1.0
        head_dim = getattr(config, "head_dim", None) or config.hidden_size // config.num_attention_heads
        dim = int(head_dim * part)
        inv = 1.0 / (base ** (_t.arange(0, dim, 2, dtype=_t.int64).to(device=device, dtype=_t.float) / dim))
        return inv, 1.0

    compat._default_rope_parameters = default_rope
    ROPE_INIT_FUNCTIONS["default"] = default_rope


class Engine:
    def __init__(self):
        self.mode, self.model = None, None

    def _load(self, mode):
        if mode == self.mode:
            return
        from faster_qwen3_tts import FasterQwen3TTS
        _rope_fix()
        self.model = None; torch.cuda.empty_cache()
        print(f"      loading {REPO[mode]} (CUDA graphs) ...", flush=True)
        self.model = FasterQwen3TTS.from_pretrained(REPO[mode])
        try:
            self.model.warmup(prefill_len=100)
        except Exception as e:
            print("      warmup skipped:", e, flush=True)
        self.mode = mode

    def say(self, text, it):
        mode = it.get("mode", "custom")
        self._load(mode)
        torch.manual_seed(int(it.get("seed", 7)))
        if mode == "custom":
            wavs, sr = self.model.generate_custom_voice(text=text, language="English", speaker=it.get("speaker", "Ryan"),
                                                        instruct=it.get("instruct") or None)
        elif mode == "design":
            desc = it["voice"] + (f" Speaking style: {it['instruct']}." if it.get("instruct") else "")
            wavs, sr = self.model.generate_voice_design(text=text, language="English", instruct=desc)
        else:
            wavs, sr = self.model.generate_voice_clone(text=text, language="English", ref_audio=it["ref_audio"],
                                                       ref_text=it.get("ref_text"), xvec_only=not it.get("ref_text"))
        return wavs[0], sr
