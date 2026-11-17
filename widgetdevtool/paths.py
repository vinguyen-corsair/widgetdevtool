r"""Default locations of the external source trees the tool reads (never writes).

Override per machine with environment variables instead of editing code:
  WIDGETDEVTOOL_ICUE_DIR     iCUE 5 source checkout  (default C:\Work\reference\icue)
  WIDGETDEVTOOL_WIDGETS_DIR  WidgetsXE checkout      (default C:\Work\widgets\WidgetsXE)
Tests that need a tree are skipped when it does not exist.
"""
import os
from pathlib import Path

ICUE_DIR_ENV = "WIDGETDEVTOOL_ICUE_DIR"
WIDGETS_DIR_ENV = "WIDGETDEVTOOL_WIDGETS_DIR"

ICUE_DIR = Path(os.environ.get(ICUE_DIR_ENV) or r"C:\Work\reference\icue")
WIDGETS_DIR = Path(os.environ.get(WIDGETS_DIR_ENV) or r"C:\Work\widgets\WidgetsXE")
