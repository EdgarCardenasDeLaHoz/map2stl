# Sphinx configuration for strm2stl Python API reference
import os
import sys

# Add project roots to sys.path so autodoc can import modules
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_code_root = os.path.abspath(os.path.join(_project_root, '..'))
for _p in (_code_root, _project_root):
    if _p not in sys.path:
        sys.path.insert(0, _p)

project = '3D Maps — Python API Reference'
author = 'strm2stl'
release = '1.0.0'

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.autosummary',
    'sphinx.ext.viewcode',
    'sphinx.ext.napoleon',
    'myst_parser',
]

# MyST for Markdown support
source_suffix = {
    '.rst': 'restructuredtext',
    '.md': 'markdown',
}

templates_path = ['_templates']
exclude_patterns = ['_build']

# Theme
html_theme = 'sphinx_rtd_theme'
html_theme_options = {
    'navigation_depth': 3,
    'collapse_navigation': False,
}

# Autodoc settings
autodoc_default_options = {
    'members': True,
    'undoc-members': False,
    'show-inheritance': True,
    'member-order': 'bysource',
}
autodoc_mock_imports = [
    'cv2', 'PIL', 'rasterio', 'shapely', 'ee', 'osgeo',
    'joblib', 'h5py', 'trimesh',
]

# Napoleon for Google/NumPy-style docstrings
napoleon_google_docstring = True
napoleon_numpy_docstring = True
