# Paper source

`build_dc.py` typesets the working paper from `dc_blocks.json` (the paper text) using the
shared layout in `academic.py`. Tables are written in `build_dc.py`; figures are drawn from the
values reported in the text.

```bash
pip install reportlab matplotlib
python build_dc.py
```
