"""Widget folder inspection. Pure Python, no Qt dependency.

This is NOT a replacement for the iCUE validator. It only checks that the folder
is structurally loadable and reports obvious problems early.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "manifest.json"
INDEX_NAME = "index.html"

# Fields listed as required in the widget specification docs
# (icue-widget-builder/skills/icue-widget-builder/docs/specification.md).
DOC_REQUIRED_FIELDS = (
    "author",
    "id",
    "name",
    "description",
    "version",
    "preview_icon",
    "min_framework_version",
    "os",
    "supported_devices",
    "min_app_version",
)


@dataclass
class WidgetCheck:
    folder: Path
    manifest: dict | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def manifest_path(self) -> Path:
        return self.folder / MANIFEST_NAME

    @property
    def index_path(self) -> Path:
        return self.folder / INDEX_NAME

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def index_url(self) -> str:
        """file:// URL of index.html, so relative CSS/JS/asset paths resolve."""
        return self.index_path.resolve().as_uri()

    @property
    def widget_name(self) -> str:
        if self.manifest and isinstance(self.manifest.get("name"), str):
            return self.manifest["name"]
        return self.folder.name


def check_widget_folder(folder: str | Path) -> WidgetCheck:
    result = WidgetCheck(folder=Path(folder))

    if not result.folder.is_dir():
        result.errors.append(f"Not a folder: {result.folder}")
        return result

    _check_manifest(result)
    _check_index(result)
    return result


def _check_manifest(result: WidgetCheck) -> None:
    path = result.manifest_path
    if not path.is_file():
        result.errors.append(f"{MANIFEST_NAME} not found in {result.folder}")
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as exc:
        result.errors.append(f"{MANIFEST_NAME} is not valid UTF-8: {exc}")
        return
    except json.JSONDecodeError as exc:
        result.errors.append(
            f"{MANIFEST_NAME} is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}"
        )
        return

    if not isinstance(data, dict):
        result.errors.append(f"{MANIFEST_NAME} must contain a JSON object")
        return

    result.manifest = data

    missing = [key for key in DOC_REQUIRED_FIELDS if key not in data]
    if missing:
        result.warnings.append(
            f"{MANIFEST_NAME} lacks fields that the docs list as required: {', '.join(missing)}"
        )

    plugins = data.get("required_plugins")
    if isinstance(plugins, list) and plugins:
        result.warnings.append(
            "Declares required_plugins: " + ", ".join(map(str, plugins)) + " (only Linkprovider and Sensorsdataprovider have a mock; see console notices)"
        )


def _check_index(result: WidgetCheck) -> None:
    path = result.index_path
    if not path.is_file():
        result.errors.append(f"{INDEX_NAME} not found in {result.folder}")
    elif path.stat().st_size == 0:
        result.errors.append(f"{INDEX_NAME} is empty")