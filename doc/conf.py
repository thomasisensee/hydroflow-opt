"""Sphinx configuration for the hydroflow-opt documentation."""

project = "hydroflow-opt"
copyright = "2026, Thomas Isensee"
author = "Thomas Isensee"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_rtd_theme",
]

templates_path = []
exclude_patterns = []
html_theme = "sphinx_rtd_theme"
html_static_path = []
autodoc_typehints = "description"
myst_heading_anchors = 3
