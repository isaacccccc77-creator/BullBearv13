"""
Tickveil web backend.

The maths lives in the modules at the repository root (scoring, verdict,
quant, deals) and is shared with the Streamlit app, so a fix to a formula
lands in both. This package only fetches data, calls those modules and
turns the results into JSON.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
