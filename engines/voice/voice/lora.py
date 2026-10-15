"""lora.py - a small trainable add-on (LoRA) on the attention + MLP layers of Chatterbox's speech model (T3).
The shared model files are never changed: the add-on is saved separately (~45 MB) and merged in memory."""
import math
import torch
from torch import nn
import torch.nn.functional as F

TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")


class LoRALinear(nn.Module):
    def __init__(self, base: nn.Linear, r: int, alpha: float):
        super().__init__()
        self.base, self.r, self.scale = base, r, alpha / r
        dev = base.weight.device
        self.lora_A = nn.Parameter(torch.empty(r, base.in_features, device=dev, dtype=torch.float32))
        self.lora_B = nn.Parameter(torch.zeros(base.out_features, r, device=dev, dtype=torch.float32))
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))

    def forward(self, x):
        return self.base(x) + F.linear(F.linear(x, self.lora_A.to(x.dtype)), self.lora_B.to(x.dtype)) * self.scale


def apply(t3, r, alpha):
    """wrap every target Linear inside t3.tfmr; returns the new trainable parameters."""
    params = []
    for name, mod in list(t3.tfmr.named_modules()):
        for child_name, child in list(mod.named_children()):
            if child_name in TARGETS and isinstance(child, nn.Linear):
                w = LoRALinear(child, r, alpha)
                setattr(mod, child_name, w)
                params += [w.lora_A, w.lora_B]
    return params


def state(t3):
    return {k: v.detach().to("cpu", torch.float16) for k, v in t3.state_dict().items() if ".lora_" in k}


def load_into(t3, ckpt):
    """apply the add-on from a checkpoint dict and merge it into the weights (fast speaking afterwards)."""
    apply(t3, ckpt["rank"], ckpt["alpha"])
    missing = t3.load_state_dict({k: v.float() for k, v in ckpt["lora"].items()}, strict=False)
    bad = [k for k in missing.unexpected_keys]
    if bad:
        raise SystemExit(f"checkpoint does not fit this model ({len(bad)} unknown weights)")
    merge(t3)


@torch.no_grad()
def merge(t3):
    for name, mod in list(t3.tfmr.named_modules()):
        for child_name, child in list(mod.named_children()):
            if isinstance(child, LoRALinear):
                b = child.base
                b.weight += ((child.lora_B @ child.lora_A) * child.scale).to(device=b.weight.device, dtype=b.weight.dtype)
                setattr(mod, child_name, b)
