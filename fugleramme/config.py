"""Where the bundled data sits.

Tesserae: Fugleramme's config.py holds the frame's paths, ports and network
defaults; the widget keeps only the root the label list, the body masses and the
alias map are read from. The artwork and the faces are fetched at runtime into
the widget's data folder (glue.assets), which points `fonts.FONTS_DIR` and the
`images_dir` the modes read at it.
"""

from __future__ import annotations

from pathlib import Path

# The widget folder: fugleramme/config.py -> one parent up.
REPO_ROOT = Path(__file__).resolve().parents[1]
