"""Environment compat shim for the `wx` env (loaded automatically by Python).

PyTorch >= 2.6 defaults torch.load(weights_only=True); pyannote-audio 3.3.2
checkpoints (VAD + diarization) contain omegaconf objects and fail to load.
All checkpoints here are local, hash-verified files, so we restore the old
default unless a caller asks for weights_only explicitly.
"""
try:
    import functools
    import torch

    if not getattr(torch.load, "_wx_compat", False):
        _orig_load = torch.load

        @functools.wraps(_orig_load)
        def _load(*args, **kwargs):
            # lightning passes weights_only=None explicitly, so check the value
            if kwargs.get("weights_only") is None:
                kwargs["weights_only"] = False
            return _orig_load(*args, **kwargs)

        _load._wx_compat = True
        torch.load = _load
except Exception:  # torch missing or import error -> nothing to patch
    pass
