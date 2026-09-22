"""Deterministic boundary between an untrusted foreman model and worker seats.

Flash may propose bounded work.  It never supplies the repository identity,
source digest, routing facts, or an acceptance decision: this package derives
those facts from Git and rejects proposals that attempt to claim them.
"""

from .compiler import (
    ForemanProposalError,
    build_source_manifest,
    compile_workboard,
    make_checkpoint,
    write_checkpoint,
)

__all__ = [
    "ForemanProposalError",
    "build_source_manifest",
    "compile_workboard",
    "make_checkpoint",
    "write_checkpoint",
]
