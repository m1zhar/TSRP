"""Migration test package for unittest discovery."""

import sys
import os
from pathlib import Path


BACKEND_PATH = str(Path(__file__).resolve().parents[1] / "backend")
if BACKEND_PATH not in sys.path:
    sys.path.insert(0, BACKEND_PATH)

# Provider integration tests use mocked Yahoo adapters and must opt into that
# transport explicitly; production remains disabled when this variable is absent.
os.environ["TSRP_YAHOO_FINANCE_ENABLED"] = "true"
