"""Turning an uploaded file into citable pages and chunks.

The pipeline's one non-negotiable property: **text is preserved verbatim**. Citation
highlighting works by finding a cited span inside a page's stored text, so any
normalisation here — collapsing whitespace, straightening quotes, stripping hyphens —
would produce citations that open the right page and highlight nothing. That is the
silent failure the product exists to prevent, so cleanup is confined to joining what
the parser splits.
"""

from .constants import NO_TEXT_LAYER_REASON, is_accepted_filename
from .pipeline import IngestionPipeline

__all__ = ["NO_TEXT_LAYER_REASON", "IngestionPipeline", "is_accepted_filename"]
