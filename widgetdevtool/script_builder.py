r"""Build the iCUE data script injected into every widget page.

The templates in vendored/ are extracted verbatim from HtmlDataProvider.cpp
(see vendored/SOURCE.txt). The substitution below mirrors HtmlDataProvider::updateScript and
::allPluginsCode, including the toQmlString() value formatting quirks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

VENDORED = Path(__file__).resolve().parent / "vendored"
# HtmlDataProvider::setData removes these keys before declaring globals.
EXCLUDED_KEYS = {"url", "typeId", "isPreview"}
DEFAULT_WEBCHANNEL_ID = "backend"  # HtmlDataProvider::m_webChannelId default

_NUMERIC = re.compile(r"^\s*[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?\s*$")
_PLUGIN_INTRO = "if (!window.plugins) { window.plugins = {}; }"


def _template(name: str) -> str:
    return (VENDORED / name).read_text(encoding="utf-8")


def to_qml_string(value) -> str:
    r"""Port of toQmlString() (HtmlDataProvider.cpp).

    Anything convertible to a double is emitted RAW (so the string "12" becomes the number 12 and
    booleans become true/false); other strings are quoted with only \ and " escaped; lists, maps
    and null become "". Faithful on purpose: widgets see exactly these initial values in iCUE.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, str):
        if _NUMERIC.match(value):
            return value
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return '""'


@dataclass(frozen=True)
class PluginDecl:
    module_name: str  # e.g. widgetbuilder.linkprovider
    component: str    # e.g. Url
    version: str      # e.g. 1.0

    @property
    def channel_id(self) -> str:
        """Capitalised last dotted segment of the module name ("Linkprovider")."""
        last = self.module_name.split(".")[-1]
        return last[:1].upper() + last[1:]


def parse_required_plugins(manifest: dict | None) -> list[PluginDecl]:
    """RequiredPluginsParser (ManifestParser.cpp): only 'module:component:version' entries count."""
    result: list[PluginDecl] = []
    entries = (manifest or {}).get("required_plugins", [])
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, str) and len(entry.split(":")) == 3:
            module, component, version = entry.split(":")
            result.append(PluginDecl(module, component, version))
    return result


def build_data_script(
    data: dict, unique_id: str, plugin_channel_ids: list[str], web_channel_id: str = DEFAULT_WEBCHANNEL_ID
) -> str:
    keys = [k for k in data if k not in EXCLUDED_KEYS]
    declarations = "".join(f"window.{key} = {to_qml_string(data.get(key))};\n" for key in keys)

    plugin_vars = ""
    plugin_init = _PLUGIN_INTRO
    for channel_id in plugin_channel_ids:  # allPluginsCode()
        plugin_vars += _template("plugin_variables.js.tmpl").replace("%1", channel_id) + "\n"
        plugin_init += _template("plugin_init.js.tmpl").replace("%1", channel_id) + "\n"

    script = _template("icue_data_script.js.tmpl")
    script = script.replace("SCRIPT_VARIABLES_DECLARATION", declarations)
    script = script.replace("SCRIPT_PLUGINS_VARIABLES_DECLARATION", plugin_vars)
    script = script.replace("SCRIPT_PLUGINS_INITIALIZATION", plugin_init)
    script = script.replace("SCRIPT_WEBCHANNEL_ID", web_channel_id)
    script = script.replace("SCRIPT_UNIQUE_ID", to_qml_string(unique_id))
    return script