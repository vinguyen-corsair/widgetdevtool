r"""Legacy Compatibility packaging (U2.1) and the staging copy of the iCUE Apps build (U2.2): build a widget written for the older WidgetsXE
format with the OFFICIAL icuewidget CLI, through a temporary STAGING copy. The source folder is never written.

Verified (icuewidget-cli 0.5.1 source `src-node/validation.js`, `head-rules.js`, `xml-head.js`; real CLI runs on copies of RSS Feeder 2.0.5,
Basketball Scores 2.0.8, Smoke Clock 1.0.3):
- `manifest.min_app_version` is required (format `5.45` / `5.45.104`). None of the WidgetsXE widgets has it (also none of the 52 published
  `.icuewidget` files). The value is NOT guessed: it comes from the iCUE Apps build dialog (pre-filled with the documented minimum, editable);
  an existing value is kept as it is.
- Void elements in `<head>` (outside comments / inline script / style) must self-close: `<link ...>` -> `<link ... />`. Adding the slash does not
  change how an HTML parser (Chromium) reads the element (a void element has no content either way). Applied only when Legacy Compatibility
  is ON (`self_close=True`).
Only these two changes are applied. Everything else the CLI rejects (e.g. a `<header>` element in the body, non-ASCII in `<head>`, inline
script content that is not XML) is REPORTED, never rewritten: it would change markup, CSS selectors or behaviour.
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

APP_VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")  # constants.js APP_VERSION_RE
VOID = "area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr"  # validation.js VOID_ELEMENTS
HEAD_RE = re.compile(r"<head\b[^>]*>([\s\S]*?)</head>", re.I)
PROTECTED_RE = re.compile(r"<!--[\s\S]*?-->|<script\b[\s\S]*?</script>|<style\b[\s\S]*?</style>", re.I)
VOID_RE = re.compile(rf"<({VOID})\b[^>]*?>", re.I)
HINT_RE = re.compile(r"(?:iCUE[^\n]{0,30}?|\b)(5\.\d{2}(?:\.\d+)?)\s*(?:\+|or (?:higher|later|newer))|min_app_version\"?\s*:\s*\"(5\.\d{2}(?:\.\d+)?)\"", re.I)
TEXT_SUFFIXES = {".html", ".htm", ".md", ".txt", ".json"}
STAGE_PREFIX = "wdt_stage_"  # %TEMP%\wdt_stage_*: every build copy (iCUE 5 and iCUE Apps); always removed after the build


@dataclass
class Issue:
    rule: str           # short id
    fixable: bool       # True = one of the verified Legacy changes handles it
    detail: str
    lines: list[int] = field(default_factory=list)


@dataclass
class Change:
    file: str           # relative to the package root
    description: str


@dataclass
class Staging:
    root: Path              # the staged package root (inside a temporary folder)
    temp_dir: Path
    changes: list[Change]
    diff: str
    changed_files: list[str]

    def cleanup(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _head(html: str):
    return HEAD_RE.search(html)


def open_void_tags(html: str) -> list[tuple[int, int, str]]:
    """(start, end, tag) of every void element in <head> that does not self-close, outside comments / inline script / style (CLI rule)."""
    match = _head(html)
    if not match:
        return []
    head, base = match.group(1), match.start(1)
    protected = [(m.start(), m.end()) for m in PROTECTED_RE.finditer(head)]
    out = []
    for tag in VOID_RE.finditer(head):
        if tag.group(0).endswith("/>") or any(a <= tag.start() < b for a, b in protected):
            continue
        out.append((base + tag.start(), base + tag.end(), tag.group(0)))
    return out


def self_close_head(html: str) -> tuple[str, int]:
    """`<link ...>` -> `<link ... />` for the tags of open_void_tags; nothing else in the file changes."""
    tags = open_void_tags(html)
    pieces, pos = [], 0
    for start, end, text in tags:
        inner = html[start:end - 1].rstrip()
        pieces.append(html[pos:start] + inner + " />")
        pos = end
    pieces.append(html[pos:])
    return "".join(pieces), len(tags)


def analyze(root: str | Path) -> list[Issue]:
    """Static report of the CLI 0.5.1 rules that legacy WidgetsXE widgets break (all of them at once; the CLI stops at the first)."""
    root = Path(root)
    issues: list[Issue] = []
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    if not str(manifest.get("min_app_version") or "").strip():
        issues.append(Issue("min_app_version", True, "manifest.min_app_version is missing (required by the CLI); the user must confirm a value"))
    elif not APP_VERSION_RE.match(str(manifest["min_app_version"])):
        issues.append(Issue("min_app_version-format", False, f"manifest.min_app_version {manifest['min_app_version']!r} is not in iCUE version format"))
    for key in ("author", "id", "name", "description", "version", "preview_icon", "min_framework_version"):
        if not str(manifest.get(key) or "").strip():
            issues.append(Issue(f"manifest.{key}", False, f"manifest.{key} is missing"))
    index = root / "index.html"
    if not index.is_file():
        return issues + [Issue("index.html", False, "index.html is missing")]
    html = index.read_text(encoding="utf-8")
    match = _head(html)
    if not match:
        return issues + [Issue("head", False, "index.html has no <head>")]
    tags = open_void_tags(html)
    if tags:
        issues.append(Issue("void-elements", True, f"{len(tags)} void element(s) in <head> do not self-close", [_line(html, s) for s, _, _ in tags]))
    head_end = match.end()
    non_ascii = sorted({_line(html, m.start()) for m in re.finditer(r"[^\x00-\x7f]", html[:head_end])})
    if non_ascii:
        issues.append(Issue("non-ascii-head", False, "non-ASCII characters before </head>", non_ascii))
    after = [m for m in re.finditer(r"</?(head[a-z0-9-]*)\b", html[head_end:], re.I)]
    if after:
        issues.append(Issue("head-lookalike", False, "'<header>' (or another '<head...' text) after </head>: iCUE would cut the head there; "
                            "changing it would change markup/CSS, so it is NOT rewritten", sorted({_line(html, head_end + m.start()) for m in after})))
    titles = re.findall(r"<title\b[^>]*>([\s\S]*?)</title\s*>", match.group(1), re.I)
    if len(titles) != 1 or not titles[0].strip():
        issues.append(Issue("title", False, f"<head> must have exactly one non-empty <title> (found {len(titles)})"))
    return issues


def min_version_hints(root: str | Path) -> list[tuple[str, str, int, str]]:
    """(version, file, line, text) of explicit iCUE version statements in the widget's OWN files (package root and its project folder,
    e.g. 'iCUE 5.41+ restrictions' in a comment). Hints only: the user decides."""
    root = Path(root).resolve()
    project = root.parent.parent if root.parent.name.lower() == "widget" else root.parent
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES]
    files += [p for p in project.glob("*") if p.is_file() and p.suffix.lower() in {".md", ".txt"}]
    hints = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            for m in HINT_RE.finditer(line):
                hints.append((m.group(1) or m.group(2), str(path.relative_to(project)), number, line.strip()[:120]))
    return hints


def _add_min_app_version(text: str, value: str) -> str:
    """Insert `"min_app_version": "<value>",` right after the `"version"` line (keeps the rest of the file byte-identical); falls back to a
    JSON rewrite only when no such line exists."""
    match = re.search(r'^([ \t]*)"version"\s*:\s*"[^"]*"\s*,[ \t]*\r?\n', text, re.M)
    if match:
        newline = "\r\n" if match.group(0).endswith("\r\n") else "\n"
        insert = f'{match.group(1)}"min_app_version": "{value}",{newline}'
        return text[:match.end()] + insert + text[match.end():]
    data = json.loads(text.lstrip("\ufeff"))
    data["min_app_version"] = value
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def stage(source: str | Path, min_app_version: str | None, self_close: bool = True) -> Staging:
    """Copy the package root into a new temporary folder and apply ONLY the verified changes. The source is read, never written.
    `min_app_version` is required when the manifest has none (ValueError otherwise) and must match the iCUE version format; an existing
    non-empty value is kept unchanged. `self_close`: also self-close the open void elements in <head> (Legacy Compatibility)."""
    source = Path(source).resolve()
    manifest_text = (source / "manifest.json").read_text(encoding="utf-8-sig")
    manifest = json.loads(manifest_text)
    needs_version = not str(manifest.get("min_app_version") or "").strip()
    if needs_version and not (min_app_version and APP_VERSION_RE.match(min_app_version.strip())):
        raise ValueError("min_app_version must be confirmed by the user (format like 5.45 or 5.45.104)")
    temp_dir = Path(tempfile.mkdtemp(prefix=STAGE_PREFIX))
    staged = temp_dir / source.name
    try:
        shutil.copytree(source, staged)
        changes: list[Change] = []
        before: dict[str, str] = {}
        if needs_version:
            raw = (staged / "manifest.json").read_bytes().decode("utf-8")
            before["manifest.json"] = raw
            (staged / "manifest.json").write_bytes(_add_min_app_version(raw, min_app_version.strip()).encode("utf-8"))
            changes.append(Change("manifest.json", f'added "min_app_version": "{min_app_version.strip()}" (build copy only)'))
        index = staged / "index.html"
        if self_close and index.is_file():
            raw = index.read_bytes().decode("utf-8")
            fixed, count = self_close_head(raw)
            if count:
                before["index.html"] = raw
                index.write_bytes(fixed.encode("utf-8"))
                changes.append(Change("index.html", f"self-closed {count} void element(s) in <head> (e.g. <link ...> -> <link ... />)"))
        diff = "".join(
            "".join(difflib.unified_diff(old.splitlines(keepends=True), (staged / name).read_bytes().decode("utf-8").splitlines(keepends=True),
                                         fromfile=f"source/{name}", tofile=f"staging/{name}", n=0))
            for name, old in before.items())
        changed = compare_trees(source, staged)
        unexpected = sorted(set(changed) - set(before))
        if unexpected:
            raise RuntimeError(f"staging changed files it must not touch: {unexpected}")
        return Staging(staged, temp_dir, changes, diff, changed)
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def compare_trees(original: Path, other: Path) -> list[str]:
    """Relative paths whose bytes differ (or exist on one side only) between two folders."""
    names = {p.relative_to(original).as_posix() for p in original.rglob("*") if p.is_file()} | {
        p.relative_to(other).as_posix() for p in other.rglob("*") if p.is_file()}
    return sorted(n for n in names if not ((original / n).is_file() and (other / n).is_file() and (original / n).read_bytes() == (other / n).read_bytes()))