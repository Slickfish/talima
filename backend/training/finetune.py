"""Phase 9 fine-tuning stub.

CUDA + unsloth ship a heavy stack that this dev environment does not have.
The contract here is the minimum needed to honour the architecture:
detect availability, raise a clear exception when unavailable, and be ready
for a future phase to swap in real SFT scaffolding without API breakage.
"""
from __future__ import annotations


class FineTuneUnavailable(RuntimeError):
    """Raised when fine-tuning is requested but CUDA + unsloth aren't available."""


def is_finetuning_available() -> bool:
    """True iff (a) torch installed, (b) torch.cuda.is_available(),
    (c) unsloth importable. Any failure path → False."""
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        if not torch.cuda.is_available():
            return False
    except Exception:
        return False
    try:
        import unsloth  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


def finetune(*args, **kwargs):
    """Run an SFT pass over recent extractions. Production-grade SFTConfig /
    SFTTrainer scaffolding lands in a future phase when a CUDA box is wired.

    For now: skip gracefully with a clear exception so callers can branch."""
    if not is_finetuning_available():
        raise FineTuneUnavailable(
            "CUDA + unsloth required — Phase 9 ships only the availability "
            "guard. Install torch with CUDA support and `pip install unsloth` "
            "to enable fine-tuning."
        )
    # When the real implementation lands, it'll go here. Until then the
    # function only succeeds on a CUDA box, where this branch is a TODO.
    raise NotImplementedError(                                  # pragma: no cover
        "Real SFT scaffolding is deferred to a follow-up phase; "
        "is_finetuning_available() returned True so the host can run it.",
    )
