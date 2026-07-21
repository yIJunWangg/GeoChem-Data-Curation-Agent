# Test Fixtures

Committed binary fixtures used by the unit test suite:

- `feart-09-788349.pdf` — Ma et al., *Frontiers in Earth Science* 9:788349,
  DOI 10.3389/feart.2021.788349. Open access under CC-BY 4.0
  (https://creativecommons.org/licenses/by/4.0/). Used by
  `test_web_workbench.py` and `test_docling_vs_pdfplumber.py` for real-layout
  table discovery assertions.
- `奥陶纪地化数据_headers.json` — header field descriptions (metadata only,
  no measurement data). Used by `test_curation_flow.py`.

Not committed (generated or local-only):

- `tests/main.pdf` — was a local paper of unknown licensing; the agent upload
  test now synthesizes a minimal PDF at runtime with PyMuPDF.
- `tests/奥陶纪地化数据.xlsx` — raw research data, local only; no test
  references it.
