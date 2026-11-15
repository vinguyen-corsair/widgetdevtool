"""Widget translations. Mirrors WidgetsUiTranslator.cpp (widgetshelpers)."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_LANGUAGE = "en"


class Translator:
    def __init__(self, index_html: Path, language: str = DEFAULT_LANGUAGE) -> None:
        self.language = language
        self.missing_keys: set[str] = set()
        self.problems: list[str] = []
        self._tables: dict[str, dict[str, str]] = {}
        self._load(Path(index_html))

    def _load(self, index_html: Path) -> None:
        # Legacy "<basename>_translation.json" first, then "translation.json" (loadTranslations).
        legacy = index_html.with_name(index_html.stem + "_translation.json")
        path = legacy if legacy.is_file() else index_html.with_name("translation.json")
        if not path.is_file():
            self.problems.append(f"translation file not found: {path}")
            return
        try:
            root = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            self.problems.append(f"cannot parse {path.name}: {exc}")
            return
        if not isinstance(root, dict) or not root:
            self.problems.append(f"{path.name} is empty or not an object")
            return
        for lang, body in root.items():
            table = body.get("translation") if isinstance(body, dict) else None
            if not isinstance(table, dict) or not table:
                continue  # languages without a non-empty "translation" object are skipped
            self._tables[lang] = {k: v for k, v in table.items() if isinstance(v, str) and v}

    def translate(self, text: str) -> str:
        found = self._tables.get(self.language, {}).get(text)
        if found is not None:
            return found
        found = self._tables.get(DEFAULT_LANGUAGE, {}).get(text)  # loadDefaultTranslation
        if found is not None:
            return found
        self.missing_keys.add(text)
        return text