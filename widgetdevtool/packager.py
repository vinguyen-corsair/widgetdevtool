"""Build Package (U2, two targets since U2.2): put a `.icuewidget` of the open widget into a user-chosen output folder. The source folder
(manifest, HTML/CSS/JS) is never written; every build works on a temporary copy (`legacy.stage` / `_copy_for_icue5`) that is always removed.

- iCUE 5 (`TARGET_ICUE5`): the format the installed iCUE 5 accepts today, as WidgetsXE builds it (`tools/build-widgets.ps1`: .NET ZipFile,
  CompressionLevel.Optimal, the widget folder's files at the archive root, `/` separators, no directory entries; e.g. the working
  `sensorbar-1.0.6.icuewidget`). The tool zips itself (Deflate, same layout); `icuewidget validate` is NOT run (CLI 0.5.1 rejects every legacy
  manifest for the missing `min_app_version`) and the manifest is packaged byte-identical (no `min_app_version` added). Checked instead:
  required files, manifest JSON/id/name/semver version, preview icon, modules, translation.json, local href/src assets, archive layout.
- iCUE Apps (`TARGET_ICUEAPPS`): the OFFICIAL `icuewidget` CLI (npm package icuewidget-cli) validates and packages the temporary copy; a missing
  `min_app_version` is added to that copy only (value from the dialog, default = the documented minimum), an existing one is kept. Remaining
  validation errors are shown, never fixed. The archive is then checked against the iCUE Apps manifest requirements. The CLI is never installed.

CLI facts used (icuewidget-cli 0.5.1 source, src-node/cli.js, read on GitHub; to be re-checked with `package --help` at run time):
- `icuewidget validate <dir>` / `icuewidget package <dir> [-o|--output <file>]`; errors -> stderr "Error: ..." and exit code 1.
- Without -o the package goes NEXT TO the widget folder (`<parent>/<slug>.icuewidget`) - inside WidgetsXE - so -o is always passed.
- An -o path without the `.icuewidget` extension gets it appended; the archive includes every file under the root except dot-files/dirs,
  `__pycache__` and `*.icuewidget` (src-node/zip.js) - `expected_entries` mirrors that rule to check the result.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import legacy
from .live_reload import snapshot

CLI_NAME = "icuewidget"
INSTALL_HINT = ("The iCUE widget CLI is not installed (command 'icuewidget' not found in PATH).\n"
                "Install it yourself (Node.js 18+ required), then press Build again:\n"
                "    npm install -g icuewidget-cli\n"
                "    icuewidget --version\n"
                "WidgetDevTool does not install it for you.")
TIMEOUT_S = 120
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
TOOL_DIR = Path(__file__).resolve().parents[1]

TARGET_ICUE5 = "icue5"
TARGET_ICUEAPPS = "icueapps"
TARGET_LABELS = {TARGET_ICUE5: "iCUE 5", TARGET_ICUEAPPS: "iCUE Apps"}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")  # icuewidget-cli constants.js SEMVER_RE
# Documented, not guessed: docs.elgato.com/icue/widgets (Getting Started > Prerequisites, Widget API 1.9.0).
DOCUMENTED_MIN_APP_VERSION = "5.47"
DOCUMENTED_MIN_APP_VERSION_SOURCE = ('iCUE widget docs, Getting Started > Prerequisites: "Developing widgets for iCUE requires iCUE 5.47 or '
                                     'later" (docs.elgato.com/icue/widgets, Widget API 1.9.0)')
# docs specification.md "Schema Properties" (Required = Yes) and icuewidget-cli 0.5.1 validation.js
ICUEAPPS_REQUIRED_STRINGS = ("author", "id", "name", "description", "version", "preview_icon", "min_app_version", "min_framework_version")
ICUEAPPS_PLATFORMS = {"windows"}
ICUEAPPS_DEVICE_TYPES = {"dashboard_lcd", "pump_lcd", "keyboard_lcd"}
ICUE5_METHOD = ("built-in packer, iCUE 5 format: ZIP Deflate, widget files at the archive root, '/' separators, no directory entries "
                "(same layout as WidgetsXE tools/build-widgets.ps1); icuewidget validate is not run")
ASSET_REF_RE = re.compile(r"""(?<![\w-])(?:href|src)\s*=\s*["']([^"'<>{}$+\s]+)["']""", re.I)
REMOTE_RE = re.compile(r"^[a-z][a-z0-9+.-]*:|^//", re.I)



def strip_ansi(text: str) -> str:
    return ANSI.sub("", text or "")


@dataclass
class CliInfo:
    command: list[str]          # how to start it: [node, script] when the npm shim is found, else [the executable]
    location: str               # what was found in PATH
    version: str = ""
    output_flag: str | None = None  # "--output" / "-o" when `package --help` lists it
    help_text: str = ""


@dataclass
class StepResult:
    name: str
    args: list[str]
    exit_code: int | None
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.cancelled


@dataclass
class BuildResult:
    ok: bool
    state: str                              # success | failed | cancelled | cli-missing
    message: str
    output: Path | None = None
    steps: list[StepResult] = field(default_factory=list)
    archive_check: dict | None = None
    changes: list = field(default_factory=list)  # legacy.Change applied in the temporary build copy (empty = packaged as in the source)
    diff: str = ""
    differs_from_source: list[str] = field(default_factory=list)  # archive entries that differ from the ORIGINAL source
    target: str = ""
    problems: list[str] = field(default_factory=list)  # why the build was refused / the archive is not compatible
    notes: list[str] = field(default_factory=list)  # compatibility notes (CLI warnings, min_app_version source, method)

    def log(self) -> str:
        lines = [self.message]
        if self.target:
            lines.append(f"Target: {TARGET_LABELS.get(self.target, self.target)}")
        if self.problems:
            lines.append("\nProblems:")
            lines += [f"  - {p}" for p in self.problems]
        if self.changes:
            lines.append("\nChanges in the temporary build copy (the source folder was not changed):")
            lines += [f"  - {c.file}: {c.description}" for c in self.changes]
            if self.differs_from_source:
                lines.append("  archive differs from the original source in: " + ", ".join(self.differs_from_source))
        if self.notes:
            lines.append("\nNotes:")
            lines += [f"  - {n}" for n in self.notes]
        for step in self.steps:
            lines.append(f"\n$ {' '.join(_quote(a) for a in step.args)}")
            lines.append(f"exit code: {step.exit_code}{' (TIMEOUT)' if step.timed_out else ''}{' (CANCELLED)' if step.cancelled else ''}, {step.seconds:.1f} s")
            if step.stdout.strip():
                lines.append("stdout:\n" + step.stdout.rstrip())
            if step.stderr.strip():
                lines.append("stderr:\n" + step.stderr.rstrip())
        if self.archive_check:
            lines.append("\narchive check: " + ("OK" if self.archive_check["ok"] else "PROBLEM") + f" ({self.archive_check['summary']})")
        return "\n".join(lines)


def _quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) else arg


# ------------------------------------------------------------------ CLI discovery
def find_cli(path_env: str | None = None) -> CliInfo | None:
    """`icuewidget` in PATH (Windows: icuewidget.cmd from npm). The npm shim is started as `node <node-bin/icuewidget.js>` so arguments with
    spaces/Unicode are passed without cmd.exe quoting; anything else is started directly."""
    found = shutil.which(CLI_NAME, path=path_env)
    if not found:
        return None
    exe = Path(found)
    script = exe.parent / "node_modules" / "icuewidget-cli" / "node-bin" / "icuewidget.js"
    node = shutil.which("node", path=path_env) or (str(exe.parent / "node.exe") if (exe.parent / "node.exe").exists() else None)
    if exe.suffix.lower() in (".cmd", ".bat", ".ps1") and script.is_file() and node:
        return CliInfo([node, str(script)], str(exe))
    if exe.suffix.lower() in (".cmd", ".bat"):
        return CliInfo(["cmd.exe", "/d", "/c", str(exe)], str(exe))
    return CliInfo([str(exe)], str(exe))


def run(command: list[str], args: list[str], cwd: Path | None, timeout: float, cancel: threading.Event | None = None, name: str = "") -> StepResult:
    """Run one CLI command (no shell), capture stdout/stderr as UTF-8, honour a timeout and a cancel flag (the process is killed)."""
    started = time.monotonic()
    env = dict(os.environ, NO_COLOR="1", FORCE_COLOR="0")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(command + args, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, env=env, creationflags=flags)
    except OSError as exc:
        return StepResult(name, command + args, None, "", f"cannot start: {exc}", time.monotonic() - started)
    out: list[bytes] = []
    err: list[bytes] = []
    readers = [threading.Thread(target=lambda s=s, b=b: b.append(s.read()), daemon=True) for s, b in ((proc.stdout, out), (proc.stderr, err))]
    for reader in readers:
        reader.start()
    timed_out = cancelled = False
    while proc.poll() is None:
        if cancel is not None and cancel.is_set():
            cancelled = True
        elif time.monotonic() - started > timeout:
            timed_out = True
        if cancelled or timed_out:
            _kill_tree(proc)
            break
        time.sleep(0.05)
    proc.wait(timeout=10)
    for reader in readers:
        reader.join(timeout=5)
    for stream in (proc.stdout, proc.stderr):
        stream.close()
    decode = lambda chunks: strip_ansi(b"".join(chunks).decode("utf-8", errors="replace"))  # noqa: E731
    return StepResult(name, command + args, proc.returncode, decode(out), decode(err), time.monotonic() - started, timed_out, cancelled)


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()


def probe_cli(info: CliInfo, timeout: float = 30) -> CliInfo:
    """`--version` and `package --help` (to learn how the output path is given)."""
    version = run(info.command, ["--version"], None, timeout, name="version")
    info.version = (version.stdout.strip().splitlines() or [""])[-1] if version.ok else ""
    helped = run(info.command, ["package", "--help"], None, timeout, name="package --help")
    info.help_text = helped.stdout + helped.stderr
    if re.search(r"(^|\s)--output\b", info.help_text):
        info.output_flag = "--output"
    elif re.search(r"(^|\s)-o\b", info.help_text):
        info.output_flag = "-o"
    return info


# ------------------------------------------------------------------ package root, names, output folder
def package_root(folder: str | Path) -> Path:
    """The folder that holds manifest.json + index.html of the open widget (what the CLI packages)."""
    root = Path(folder).resolve()
    if not (root / "manifest.json").is_file():
        raise ValueError(f"no manifest.json in {root}")
    return root


def package_file_name(manifest: dict, folder: Path, target: str | None = None) -> str:
    """`<last part of the manifest id (else the name, else the folder)>-<version>[-<target>].icuewidget` (the WidgetsXE convention, e.g.
    rssfeedreader-2.0.5; with a target: sensorbar-1.0.6-icue5 / sensorbar-1.0.6-icueapps)."""
    base = str(manifest.get("id") or "").split(".")[-1] or str(manifest.get("name") or "") or folder.name
    version = str(manifest.get("version") or "").strip()
    stem = BAD_NAME.sub("_", f"{base}-{version}" if version else base).strip(" .") or "widget"
    return f"{stem}-{target}.icuewidget" if target else f"{stem}.icuewidget"


def repository_root(folder: Path) -> Path | None:
    for parent in (folder, *folder.parents):
        if (parent / ".git").exists():
            return parent
    return None


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def check_output_folder(output_dir: str | Path, source_root: Path) -> str | None:
    """None when the folder can take the package; otherwise why not. Creates the folder when missing; checks writing with a temp file.
    Refused: inside the widget, next to it (the CLI default, in the widget project), inside the widget's git repository (e.g. WidgetsXE),
    inside the WidgetDevTool installation folder."""
    if not str(output_dir).strip():
        return "no output folder chosen"
    out = Path(output_dir).expanduser()
    if not out.is_absolute():
        return f"not an absolute path: {out}"
    out = out.resolve()
    source_root = source_root.resolve()
    repo = repository_root(source_root)
    for forbidden, why in ((source_root, "inside the widget source folder"), (source_root.parent, "the widget project folder (CLI default)"),
                           (TOOL_DIR, "inside the WidgetDevTool installation folder")):
        if (out == forbidden) if forbidden == source_root.parent else _inside(out, forbidden):
            return f"refused: {why}: {out}"
    if repo is not None and _inside(out, repo):
        return f"refused: inside the widget's repository ({repo}): build artifacts must not go there"
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"cannot create the folder: {exc.strerror or exc}"
    probe = out / f".wdt-write-test-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"x")
        probe.unlink()
    except OSError as exc:
        return f"no write permission: {exc.strerror or exc}"
    return None


# ------------------------------------------------------------------ archive check
def expected_entries(root: Path) -> list[str]:
    """Files the CLI puts into the archive (src-node/zip.js shouldInclude): no dot-file/dir, no __pycache__, no *.icuewidget."""
    out = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if rel.name.endswith(".icuewidget") or any(part.startswith(".") or part == "__pycache__" for part in rel.parts):
            continue
        out.append(rel.as_posix())
    return sorted(out)


def check_archive(archive: Path, root: Path) -> dict:
    """Read the produced archive (read-only) and compare it with the source: same file list, same bytes, manifest.json + index.html present."""
    try:
        with zipfile.ZipFile(archive) as zf:
            bad = zf.testzip()
            names = sorted(n for n in zf.namelist() if not n.endswith("/"))
            expected = expected_entries(root)
            differ = [n for n in names if n in expected and zf.read(n) != (root / n).read_bytes()]
    except (OSError, zipfile.BadZipFile) as exc:
        return {"ok": False, "summary": f"not a readable zip archive: {exc}", "entries": []}
    missing, extra = sorted(set(expected) - set(names)), sorted(set(names) - set(expected))
    required = [n for n in ("manifest.json", "index.html") if n not in names]
    ok = not (missing or extra or differ or required or bad)
    parts = [f"{len(names)} files"] + [f"{k}: {', '.join(v[:6])}" for k, v in (("missing", missing), ("unexpected", extra), ("content differs", differ),
                                                                              ("required absent", required)) if v]
    if bad:
        parts.append(f"CRC error in {bad}")
    return {"ok": ok, "summary": "; ".join(parts), "entries": names, "missing": missing, "extra": extra, "differ": differ}


# ------------------------------------------------------------------ target checks
def local_asset_refs(html: str) -> list[str]:
    """Local href/src values of index.html (query/fragment stripped); remote, data:, #anchors and values built in script are skipped."""
    out = []
    for match in ASSET_REF_RE.finditer(html):
        ref = re.split(r"[?#]", match.group(1), maxsplit=1)[0]
        if ref and not REMOTE_RE.match(ref) and not ref.endswith("/") and ref not in out:
            out.append(ref)
    return out


def _check_file(root: Path, relative: str, label: str, included: set[str], problems: list[str]) -> None:
    resolved = (root / relative).resolve()
    if not _inside(resolved, root):
        problems.append(f"{label} must stay inside the widget folder: {relative}")
    elif not resolved.is_file():
        problems.append(f"{label} does not exist: {relative}")
    elif resolved.relative_to(root).as_posix() not in included:
        problems.append(f"{label} would not be packaged (dot-file/folder): {relative}")


def check_icue5_source(root: Path) -> list[str]:
    """What the iCUE 5 build needs (min_app_version is NOT required): manifest.json (JSON object, id/name/semver version), index.html,
    preview icon, manifest modules, translation.json readable, every local href/src of index.html packaged."""
    root = root.resolve()
    problems = [f"required file missing: {name}" for name in ("manifest.json", "index.html") if not (root / name).is_file()]
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return problems + [f"manifest.json is missing or not valid JSON: {exc}"]
    if not isinstance(manifest, dict):
        return problems + ["manifest.json must contain a JSON object"]
    for key in ("id", "name", "version"):
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            problems.append(f"manifest.{key} must be a non-empty string")
    version = manifest.get("version")
    if isinstance(version, str) and version.strip() and not SEMVER_RE.match(version):
        problems.append(f"manifest.version {version!r} is not a semantic version (for example 1.0.6)")
    included = set(expected_entries(root))
    if manifest.get("preview_icon"):
        _check_file(root, str(manifest["preview_icon"]), "manifest.preview_icon", included, problems)
    else:
        problems.append("manifest.preview_icon is missing")
    for module in manifest.get("modules") or []:
        _check_file(root, str(module), "manifest.modules", included, problems)
    if (root / "translation.json").is_file():
        try:
            json.loads((root / "translation.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            problems.append(f"translation.json is not valid JSON: {exc}")
    if (root / "index.html").is_file():
        html = (root / "index.html").read_text(encoding="utf-8", errors="replace")
        for ref in local_asset_refs(html):
            _check_file(root, ref, f"index.html asset {ref}", included, problems)
    return problems


def write_icue5_archive(root: Path, output: Path) -> list[str]:
    """ZIP Deflate of the package files at the archive root (the layout of WidgetsXE tools/build-widgets.ps1 and of the working packages)."""
    entries = expected_entries(root)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, strict_timestamps=False) as zf:
        for name in entries:
            zf.write(root / name, name)
    return entries


def check_icue5_layout(archive: Path, source_root: Path) -> list[str]:
    """iCUE 5 archive layout: Deflate, files only, '/' relative names, manifest.json + index.html at the root, manifest byte-identical to the
    source (no min_app_version added)."""
    problems = []
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        names = {i.filename for i in infos}
        problems += [f"directory entry: {i.filename}" for i in infos if i.is_dir()]
        problems += [f"not Deflate-compressed: {i.filename}" for i in infos if not i.is_dir() and i.compress_type != zipfile.ZIP_DEFLATED]
        problems += [f"bad entry name: {n}" for n in names if "\\" in n or n.startswith("/") or ".." in n.split("/")]
        problems += [f"{n} is not at the archive root" for n in ("manifest.json", "index.html") if n not in names]
        if "manifest.json" in names and zf.read("manifest.json") != (source_root / "manifest.json").read_bytes():
            problems.append("manifest.json in the archive differs from the source manifest")
    return problems


def min_app_version_status(manifest: dict) -> tuple[str, str]:
    """('valid' | 'invalid' | 'missing', value) of manifest.min_app_version."""
    value = manifest.get("min_app_version")
    text = value.strip() if isinstance(value, str) else ("" if value is None else str(value))
    if not text:
        return "missing", ""
    return ("valid" if isinstance(value, str) and legacy.APP_VERSION_RE.match(text) else "invalid"), text


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(p) for p in value.split("."))


def cli_warnings(text: str) -> list[str]:
    """The `! ...` entries of the CLI's Warnings panel, wrapped continuation lines joined (box-drawing characters stripped)."""
    out: list[str] = []
    current: list[str] | None = None
    for raw in strip_ansi(text).splitlines():
        content = raw.strip().strip("\u2502\u2503|").rstrip()
        if content.lstrip().startswith("! "):
            current = [content.lstrip()[2:].strip()]
            out.append("")
        elif current is not None and content.startswith("  ") and content.strip():
            current.append(content.strip())
        else:
            current = None
            continue
        out[-1] = " ".join(current)
    return list(dict.fromkeys(out))


def check_icueapps_archive(archive: Path, source_root: Path) -> tuple[list[str], list[str]]:
    """(problems, notes) of the iCUE Apps requirements, read from the manifest INSIDE the archive: required manifest strings (docs
    specification.md), min_app_version / version / min_framework_version formats, windows platform, device types, preview icon packaged,
    manifest.json + index.html at the root; and nothing but min_app_version differs from the source manifest."""
    problems, notes = [], []
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        problems += [f"{n} is not at the archive root" for n in ("manifest.json", "index.html") if n not in names]
        if "manifest.json" not in names:
            return problems, notes
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8-sig"))
        except ValueError as exc:
            return problems + [f"manifest.json in the archive is not valid JSON: {exc}"], notes
    for key in ICUEAPPS_REQUIRED_STRINGS:
        if not isinstance(manifest.get(key), str) or not manifest[key].strip():
            problems.append(f"manifest.{key} must be a non-empty string")
    status, value = min_app_version_status(manifest)
    if status == "valid":
        if _version_tuple(value) < _version_tuple(DOCUMENTED_MIN_APP_VERSION):
            notes.append(f"min_app_version {value} is below the documented minimum {DOCUMENTED_MIN_APP_VERSION} ({DOCUMENTED_MIN_APP_VERSION_SOURCE})")
    elif status == "invalid":
        problems.append(f"manifest.min_app_version {value!r} is not in iCUE version format (for example 5.47)")
    for key in ("version", "min_framework_version"):
        if isinstance(manifest.get(key), str) and manifest[key].strip() and not SEMVER_RE.match(manifest[key]):
            problems.append(f"manifest.{key} {manifest[key]!r} is not a semantic version")
    platforms = [o.get("platform") for o in manifest.get("os") or [] if isinstance(o, dict)]
    if not platforms or any(p not in ICUEAPPS_PLATFORMS for p in platforms):
        problems.append(f"manifest.os must list platform(s) from {sorted(ICUEAPPS_PLATFORMS)} (found {platforms})")
    devices = [d.get("type") for d in manifest.get("supported_devices") or [] if isinstance(d, dict)]
    if not devices or any(d not in ICUEAPPS_DEVICE_TYPES for d in devices):
        problems.append(f"manifest.supported_devices must list types from {sorted(ICUEAPPS_DEVICE_TYPES)} (found {devices})")
    icon = manifest.get("preview_icon")
    if isinstance(icon, str) and icon.strip() and icon.lstrip("./").replace("\\", "/") not in names:
        problems.append(f"preview icon not in the archive: {icon}")
    try:
        original = json.loads((source_root / "manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        original = None
    if isinstance(original, dict):
        strip = lambda m: {k: v for k, v in m.items() if k != "min_app_version"}  # noqa: E731
        if strip(original) != strip(manifest):
            problems.append("the packaged manifest differs from the source in more than min_app_version")
        if min_app_version_status(original)[0] != "missing" and original.get("min_app_version") != manifest.get("min_app_version"):
            problems.append("the existing min_app_version of the source was not kept")
    return problems, notes


# ------------------------------------------------------------------ build
def build(cli: CliInfo | None, source: str | Path, output_dir: str | Path, manifest: dict, *, target: str = TARGET_ICUEAPPS,
          min_app_version: str | None = None, legacy_mode: bool = False,
          confirm_overwrite: Callable[[Path], bool] = lambda path: False, progress: Callable[[str], None] = lambda state: None,
          cancel: threading.Event | None = None, timeout: float = TIMEOUT_S) -> BuildResult:
    """Build `<name>-<version>-<target>.icuewidget` from a temporary copy of the source: package into a hidden temp file in the output folder
    -> archive checks -> atomic replace of the target. A failure or cancel removes the temp file and leaves an existing package untouched;
    a source change during the build fails it; the temporary copy is always removed.
    iCUE 5: no CLI (see the module doc). iCUE Apps: `min_app_version` is used only when the manifest has none; `legacy_mode` also self-closes
    the void elements in <head> of the copy; then `icuewidget validate`, and only if it passed `icuewidget package`."""
    if target not in TARGET_LABELS:
        raise ValueError(f"unknown target: {target}")
    if target == TARGET_ICUEAPPS and cli is None:
        return BuildResult(False, "cli-missing", INSTALL_HINT, target=target)
    try:
        root = package_root(source)
    except ValueError as exc:
        return BuildResult(False, "failed", str(exc), target=target)
    problem = check_output_folder(output_dir, root)
    if problem:
        return BuildResult(False, "failed", f"Output folder: {problem}", target=target)
    out_dir = Path(output_dir).resolve()
    destination = out_dir / package_file_name(manifest, root, target)
    if destination.exists() and not confirm_overwrite(destination):
        return BuildResult(False, "cancelled", f"Not overwritten: {destination}", target=target)
    before = snapshot(root)  # the ORIGINAL source; taken before the temporary copy is made
    if target == TARGET_ICUE5:
        result = _build_icue5(root, out_dir, destination, progress, cancel, before)
    else:
        try:
            json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            return BuildResult(False, "failed", "manifest.json is not valid JSON: the package was NOT built.", target=target, problems=[str(exc)])
        progress("staging")
        try:
            staging = legacy.stage(root, min_app_version, self_close=legacy_mode)
        except ValueError as exc:
            return BuildResult(False, "failed", f"min_app_version for iCUE Apps: {exc}", target=target)
        except (RuntimeError, OSError) as exc:
            return BuildResult(False, "failed", f"Temporary build copy failed: {exc}", target=target)
        try:
            result = _build_icueapps(cli, root, staging, out_dir, destination, progress, cancel, timeout, before)
        finally:
            staging.cleanup()
        result.changes, result.diff = staging.changes, staging.diff
        if any(c.file == "manifest.json" for c in staging.changes):
            result.notes.insert(0, f"min_app_version {min_app_version.strip()} added to the build copy only; documented minimum: "
                                   f"{DOCUMENTED_MIN_APP_VERSION_SOURCE}")
    result.target = target
    return result


def _copy_for_icue5(root: Path) -> tuple[Path, Path]:
    """(temp dir, copied package root): the packaged files only, timestamps kept (they become the ZIP entry times)."""
    temp_dir = Path(tempfile.mkdtemp(prefix=legacy.STAGE_PREFIX))
    staged = temp_dir / root.name
    try:
        for name in expected_entries(root):
            (staged / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / name, staged / name)
        staged.mkdir(exist_ok=True)
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return temp_dir, staged


def _remove(path: Path) -> None:
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _build_icue5(root: Path, out_dir: Path, destination: Path, progress, cancel, before) -> BuildResult:
    progress("checking")
    problems = check_icue5_source(root)
    if problems:
        return BuildResult(False, "failed", "iCUE 5 check failed: the package was NOT built.", problems=problems)
    temp_dir, staged = _copy_for_icue5(root)
    partial = out_dir / f".{destination.stem}.{uuid.uuid4().hex[:8]}.partial.icuewidget"
    try:
        if cancel is not None and cancel.is_set():
            return BuildResult(False, "cancelled", "Cancelled before packaging; the existing package (if any) is unchanged.")
        progress("packaging")
        if set(legacy.compare_trees(root, staged)) & set(expected_entries(root)):
            return BuildResult(False, "failed", "The temporary copy differs from the source: the package was NOT built.")
        try:
            write_icue5_archive(staged, partial)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            return BuildResult(False, "failed", f"Packaging failed; the existing package (if any) is unchanged: {exc}")
        if cancel is not None and cancel.is_set():
            return BuildResult(False, "cancelled", "Cancelled while packaging; the existing package (if any) is unchanged.")
        if snapshot(root) != before:
            return BuildResult(False, "failed", "The source changed during the build: the package was discarded, build again.")
        check = check_archive(partial, staged)
        layout = check_icue5_layout(partial, root)
        if layout:
            check["ok"] = False
            check["summary"] += "; layout: " + "; ".join(layout[:6])
        if not check["ok"]:
            return BuildResult(False, "failed", f"The archive is not a valid iCUE 5 package: {check['summary']}", archive_check=check,
                               problems=layout)
        os.replace(partial, destination)
        return BuildResult(True, "success", f"Package created: {destination}", destination, archive_check=check,
                           notes=[f"Method: {ICUE5_METHOD}", "manifest.json packaged byte-identical to the source (min_app_version not added)"])
    finally:
        _remove(partial)
        shutil.rmtree(temp_dir, ignore_errors=True)


def _build_icueapps(cli, root: Path, staging, out_dir: Path, destination: Path, progress, cancel, timeout, before) -> BuildResult:
    package_dir = staging.root
    steps: list[StepResult] = []
    progress("validating")
    validate = run(cli.command, ["validate", str(package_dir)], out_dir, timeout, cancel, "validate")
    steps.append(validate)
    if validate.cancelled:
        return BuildResult(False, "cancelled", "Cancelled while validating.", steps=steps)
    if not validate.ok:
        return BuildResult(False, "failed", "Validation failed: the package was NOT built. Fix the reported error in the widget; the tool "
                           "changes no other metadata.", steps=steps)
    flag = cli.output_flag
    if flag is None:
        return BuildResult(False, "failed", "This CLI version does not list an output option in 'package --help': not building next to the source "
                           "(it would write into the widget project).", steps=steps)
    partial = out_dir / f".{destination.stem}.{uuid.uuid4().hex[:8]}.partial.icuewidget"
    progress("packaging")
    try:
        package = run(cli.command, ["package", str(package_dir), flag, str(partial)], out_dir, timeout, cancel, "package")
        steps.append(package)
        if package.cancelled:
            return BuildResult(False, "cancelled", "Cancelled while packaging; the existing package (if any) is unchanged.", steps=steps)
        if not package.ok or not partial.is_file():
            return BuildResult(False, "failed", "Packaging failed; the existing package (if any) is unchanged." if not package.ok
                               else f"The CLI reported success but no file was created at {partial}.", steps=steps)
        if snapshot(root) != before:
            return BuildResult(False, "failed", "The source changed during the build: the package was discarded, build again.", steps=steps)
        unexpected = sorted(set(legacy.compare_trees(root, package_dir)) - set(staging.changed_files))
        if unexpected:
            return BuildResult(False, "failed", f"The build copy changed during the build: {', '.join(unexpected[:6])}; the package was discarded.",
                               steps=steps)
        check = check_archive(partial, package_dir)  # against what the CLI packaged (the temporary copy)
        if not check["ok"]:
            return BuildResult(False, "failed", f"The archive does not match the build copy: {check['summary']}", steps=steps, archive_check=check)
        problems, notes = check_icueapps_archive(partial, root)
        notes += [f"CLI warning: {w}" for w in cli_warnings(validate.stdout + "\n" + package.stdout)]
        if problems:
            return BuildResult(False, "failed", "The package does not meet the iCUE Apps requirements: NOT kept.", steps=steps,
                               archive_check=check, problems=problems, notes=notes)
        differs = [n for n in check["entries"] if (root / n).is_file() and (root / n).read_bytes() != (package_dir / n).read_bytes()]
        os.replace(partial, destination)
        result = BuildResult(True, "success", f"Package created: {destination}", destination, steps, check, notes=notes)
        result.differs_from_source = differs
        return result
    finally:
        _remove(partial)
