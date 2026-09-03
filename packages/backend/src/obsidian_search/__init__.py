"""obsidian-search — semantic search over an Obsidian vault.

The version is read from installed package metadata rather than hardcoded, so
it cannot drift from pyproject.toml the way the FastAPI app version did (it
advertised 0.1.0 through seven releases).
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

try:
    __version__ = _package_version("obsidian-search")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
