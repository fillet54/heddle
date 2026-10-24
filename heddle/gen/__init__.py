"""Model-oriented code generation: IR → generation model → templates → files.

``generate`` is the whole pipeline for one parsed document. The front end
and checks have already run; generation refuses to proceed past errors.
"""

from __future__ import annotations

from pathlib import Path

from ..diagnostics import Diagnostics
from ..frontend import ParseResult
from .engine import TARGET_SETS, Output, WriteResult, render, write
from .model import GComponent, build_model

__all__ = ["TARGET_SETS", "Output", "WriteResult", "GComponent", "build_model", "generate",
           "render", "write"]


def generate(result: ParseResult, target_set: str = "cpp", namespace: str = "components",
             template_dirs: list[Path] | None = None,
             root: Path | None = None) -> tuple[list[Output], Diagnostics]:
    """Render every output of ``target_set`` for one checked document.

    Returns no outputs if the document (or the generation model) has errors.
    """
    diags = result.diagnostics
    if diags.errors:
        return [], diags
    model = build_model(result.component, diags, namespace, root=root)
    if diags.errors:
        return [], diags
    return render(model, target_set, template_dirs), diags
