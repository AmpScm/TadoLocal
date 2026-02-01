"""Setup configuration for Tado Local.

Configuration is primarily defined in pyproject.toml.
This file provides custom version discovery from __version__.py.
"""

from setuptools import setup
from pathlib import Path
import re

def get_version():
    """Read version from tado_local/__version__.py"""
    version_file = Path(__file__).parent / "tado_local" / "__version__.py"
    content = version_file.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]', content, re.MULTILINE)
    if match:
        return match.group(1)
    raise RuntimeError("Unable to find __version__ in tado_local/__version__.py")

# Use custom version discovery
setup(version=get_version())


