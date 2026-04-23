"""Add src/ to sys.path so tests can import poc_dev_flow_agent package."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
