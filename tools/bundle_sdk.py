import argparse
from pathlib import Path
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument("native_directory")
parser.add_argument("output")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
files = {}
for name in ("README.md", "LICENSE", "rolithax_schema_sdk.py"):
    files[name] = (root / name).read_bytes()
for directory in ("bin", "templates", "spec", "skills"):
    for path in (root / directory).rglob("*"):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_bytes()
for path in Path(args.native_directory).iterdir():
    if path.name in ("rolithax-schema", "rolithax-schema.exe", "rolithax_schema.dll", "librolithax_schema.so", "librolithax_schema.dylib"):
        files["bin/" + path.name] = path.read_bytes()
native_licenses = Path(args.native_directory) / "licenses"
if not native_licenses.is_dir():
    raise SystemExit("Native dependency licenses missing; rebuild the native runtime")
for path in native_licenses.iterdir():
    if path.is_file():
        files["licenses/native/" + path.name] = path.read_bytes()
if not any(name in files for name in ("bin/rolithax-schema", "bin/rolithax-schema.exe")):
    raise SystemExit("Native CLI missing")
if not any(name in files for name in ("bin/rolithax_schema.dll", "bin/librolithax_schema.so", "bin/librolithax_schema.dylib")):
    raise SystemExit("Native shared library missing")
with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for name, data in sorted(files.items()):
        info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o100755 if name.startswith("bin/") else 0o100644) << 16
        archive.writestr(info, data)
print(args.output)
