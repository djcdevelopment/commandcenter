"""Request sizing (ADR-0050): expected output class for a call, from its shape.

``size_request`` is what the door and the execution service consult (gated by
``HEARTH_SIZER``); ``size_heuristic`` is the pure in-process sizer; the
loopback encoder service lives in ``tools/sizer/serve.py``.
"""
from hearth.sizer.client import (DEFAULT_SIZER_URL, DEFAULT_TIMEOUT_MS, MODES, SIZER_ENV, SIZER_TIMEOUT_ENV,
                                 SIZER_URL_ENV, size_request, sizer_label, sizer_mode)
from hearth.sizer.heuristic import (BIN_EDGE, BIN_ORDER, BINS, FAMILY_BASE_BIN, LONG_BINS, bin_for_tokens,
                                    media_class, size_heuristic)

__all__ = ["BINS", "BIN_ORDER", "BIN_EDGE", "LONG_BINS", "FAMILY_BASE_BIN", "bin_for_tokens", "media_class",
           "size_heuristic", "SIZER_ENV", "SIZER_URL_ENV", "SIZER_TIMEOUT_ENV", "DEFAULT_SIZER_URL",
           "DEFAULT_TIMEOUT_MS", "MODES", "sizer_mode", "size_request", "sizer_label"]
