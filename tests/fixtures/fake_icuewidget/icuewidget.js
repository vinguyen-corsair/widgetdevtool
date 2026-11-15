// FAKE icuewidget CLI for WidgetDevTool tests ONLY. Mimics the documented interface of icuewidget-cli 0.5.1 (validate / package -o / --version /
// package --help, "Error: ..." on stderr, exit code 1). Its validation is a tiny subset; it is NOT the real validator.
const fs = require("fs"), path = require("path");
const args = process.argv.slice(2), mode = process.env.FAKE_ICUEWIDGET_MODE || "", logFile = process.env.FAKE_ICUEWIDGET_LOG;
if (logFile) fs.appendFileSync(logFile, JSON.stringify(args) + "\n", "utf8");
function fail(msg, code = 1) { console.error("Error: " + msg); process.exit(code); }
if (args[0] === "--version") { console.log("\u001b[1m0.0.0-fake\u001b[0m"); process.exit(0); }
if (args[0] === "package" && args.includes("--help")) { console.log("Usage: icuewidget package [<widget-dir>] [-o <file>]\n  -o, --output <file>  Write the package to this path."); process.exit(0); }
function validate(dir) {
  let manifest;
  try { manifest = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json"), "utf8")); } catch (e) { fail("manifest.json is missing or not valid JSON"); }
  for (const key of ["id", "name", "version"]) if (!manifest[key]) fail(`manifest.${key} is required`);
  if (typeof manifest.min_app_version !== "string" || !manifest.min_app_version.trim()) fail("manifest.min_app_version must be a non-empty string");
  if (!/^\d+\.\d+(?:\.\d+)?$/.test(manifest.min_app_version)) fail("manifest.min_app_version must use iCUE version format (for example 5.45)");
  if (!/^\d+\.\d+\.\d+$/.test(manifest.version)) fail("manifest.version must use semantic version format (for example 1.0.0)");
  if (!fs.existsSync(path.join(dir, "index.html"))) fail("index.html (entry point) is missing");
  if (manifest.preview_icon && !fs.existsSync(path.join(dir, manifest.preview_icon))) fail(`preview_icon not found: ${manifest.preview_icon}`);
  return manifest;
}
const crcTable = new Uint32Array(256).map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
const crc32 = (b) => { let c = 0xffffffff; for (const x of b) c = crcTable[(c ^ x) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
const u16 = (v) => { const b = Buffer.alloc(2); b.writeUInt16LE(v); return b; }, u32 = (v) => { const b = Buffer.alloc(4); b.writeUInt32LE(v >>> 0); return b; };
function walk(root, dir = root, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name), rel = path.relative(root, p);
    if (rel.split(path.sep).some((part) => part.startsWith(".") || part === "__pycache__") || rel.endsWith(".icuewidget")) continue;
    if (e.isDirectory()) walk(root, p, out); else out.push(p);
  }
  return out.sort();
}
function zip(root, output) {
  const chunks = [], central = []; let offset = 0;
  for (const file of walk(root)) {
    const data = fs.readFileSync(file), name = Buffer.from(path.relative(root, file).split(path.sep).join("/"), "utf8"), crc = crc32(data);
    const local = Buffer.concat([u32(0x04034b50), u16(20), u16(0x0800), u16(0), u16(0), u16(0x21), u32(crc), u32(data.length), u32(data.length), u16(name.length), u16(0), name]);
    chunks.push(local, data);
    central.push(Buffer.concat([u32(0x02014b50), u16(20), u16(20), u16(0x0800), u16(0), u16(0), u16(0x21), u32(crc), u32(data.length), u32(data.length), u16(name.length), u16(0), u16(0), u16(0), u16(0), u32(0), u32(offset), name]));
    offset += local.length + data.length;
  }
  const cd = Buffer.concat(central);
  fs.writeFileSync(output, Buffer.concat([...chunks, cd, u32(0x06054b50), u16(0), u16(0), u16(central.length), u16(central.length), u32(cd.length), u32(offset), u16(0)]));
}
const cmd = args[0], dir = path.resolve(args[1] || ".");
const warnings = () => { if (mode === "warn") console.log("\u256d Warnings \u256e\n\u2502 ! fake warning: interactive widget on pump_lcd \u2502\n\u2570\u2500\u256f"); };
if (cmd === "validate") { const m = validate(dir); console.log(`\u001b[32mWidget is valid\u001b[0m\nWidget ${m.name}\nVersion ${m.version}`); warnings(); process.exit(0); }
if (cmd === "package") {
  const i = Math.max(args.indexOf("-o"), args.indexOf("--output"));
  let out = i > 0 ? path.resolve(args[i + 1]) : path.join(path.dirname(dir), "default.icuewidget");
  if (path.extname(out) !== ".icuewidget") out += ".icuewidget";
  const m = validate(dir);
  if (mode === "slow") { const end = Date.now() + 30000; while (Date.now() < end) {} }
  if (mode === "fail") fail("simulated packaging failure", 2);
  if (mode === "touch") fs.appendFileSync(path.join(dir, "index.html"), "\n<!-- changed during the build -->");
  if (mode === "nozip") { console.log("Package created"); process.exit(0); }
  if (mode === "badzip") { fs.writeFileSync(out, "not a zip"); process.exit(0); }
  zip(dir, out);
  console.log(`Package created\nWidget ${m.name}\nOutput ${out}`);
  process.exit(0);
}
fail(`Unknown command: ${cmd}`);