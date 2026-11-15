r"""Extract the iCUE 5 widget runtime script templates verbatim from the iCUE source.

Re-run after pulling a new iCUE source:  .venv\Scripts\python.exe tools\vendor_icue_templates.py [path-to-HtmlDataProvider.cpp]
Reads the iCUE source only; writes into widgetdevtool/vendored/.
"""
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

DEFAULT_SRC = Path(r"D:\icue\Application\src\qml\com\corsair\cue\htmlwidgets\src\HtmlDataProvider.cpp")
OUT = Path(__file__).resolve().parents[1] / "widgetdevtool" / "vendored"


def raw_strings(text: str):
    """Yield (start_line, content) for every C++ raw string literal R"( ... )"."""
    for m in re.finditer(r'R"\((.*?)\)"', text, re.S):
        yield text.count("\n", 0, m.start()) + 1, m.group(1)


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    text = src.read_text(encoding="utf-8")

    main_tpl = re.search(r'cScriptTemplate\s*=\s*R"\((.*?)\)";', text, re.S)
    fn = text.index("HtmlDataProvider::pluginCodeFromChannelId(")
    end = text.index("HtmlDataProvider::allPluginsCode()", fn)
    plugin_tpls = [s for _, s in raw_strings(text[fn:end])]
    assert main_tpl and len(plugin_tpls) == 2, "unexpected source layout; templates not found"

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "icue_data_script.js.tmpl").write_text(main_tpl.group(1), encoding="utf-8", newline="\n")
    (OUT / "plugin_variables.js.tmpl").write_text(plugin_tpls[0], encoding="utf-8", newline="\n")
    (OUT / "plugin_init.js.tmpl").write_text(plugin_tpls[1], encoding="utf-8", newline="\n")

    try:
        repo = src.parents[7]
        commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        branch = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        commit = branch = "unknown"
    (OUT / "SOURCE.txt").write_text(
        f"Extracted {date.today()} from:\n  {src}\n  iCUE repo branch={branch} commit={commit}\n"
        "Templates: cScriptTemplate, and the two raw strings of HtmlDataProvider::pluginCodeFromChannelId.\n"
        "NOTE: this is the 5.5x development source. The installed release is 5.52.93; the templates there are UNVERIFIED to match.\n",
        encoding="utf-8",
    )
    for f in sorted(OUT.iterdir()):
        print(f"{f.name}: {f.stat().st_size} B")


if __name__ == "__main__":
    main()