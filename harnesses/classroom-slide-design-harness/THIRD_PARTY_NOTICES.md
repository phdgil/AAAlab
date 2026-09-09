# Third-party notices

No third-party code, executable, package archive, model, font, slide deck, image or dataset is vendored.

External runtime dependencies are installed separately and retain their licenses:
- Python: Python Software Foundation license; https://www.python.org/psf/license/
- python-pptx: MIT; https://github.com/scanny/python-pptx
- Pillow: HPND/Pillow license; https://github.com/python-pillow/Pillow
- pywin32 (Windows-only optional Office integration): PSF license; https://github.com/mhammond/pywin32
- Microsoft PowerPoint: proprietary, separately installed/licensed; not bundled and not required for structural-only inspection.

The workflow is conceptually inspired by https://github.com/revfactory/harness (Apache-2.0). No runtime dependency on that plugin is introduced.

Fonts, scientific structure images, screenshots, figures and retrieved database records used in a generated lesson require their own provenance and redistribution review. This package grants no rights to those assets and contains no private course examples. PyMuPDF, RDKit and database clients are not required by the bundled audit scripts.
