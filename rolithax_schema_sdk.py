from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import urllib.error
import zipfile

VERSION = "2.0.0"
LIMITS = {"plugin.json": 16384, "ui.xml": 65536, "rules.yaml": 65536, "business.lua": 131072}
PACKAGE_LIMIT = 524288
README_LIMIT = 262144
FENCE = re.compile(r"(?m)^```rolithax-schema-repository\s*\n(.*?)^```[ \t]*$", re.S)
SYMBOL = re.compile(r"[a-z][a-z0-9-]{0,63}(?:\.[a-z][a-z0-9-]{0,63})+")


class SdkError(Exception):
    pass


def read_json(text: str):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise SdkError(f"Duplicate JSON key: {key}")
            value[key] = item
        return value
    try:
        return json.loads(text, object_pairs_hook=unique)
    except (ValueError, TypeError) as error:
        raise SdkError(f"Invalid JSON: {error}") from error


def native_binary(explicit=None):
    bundled = Path(__file__).parent / "bin" / ("rolithax-schema.exe" if os.name == "nt" else "rolithax-schema")
    candidate = explicit or os.environ.get("ROLITHAX_SCHEMA_CLI") or (bundled if bundled.is_file() else None) or shutil.which("rolithax-schema")
    if not candidate:
        raise SdkError("Native parser missing. Build Launcher native/schema, then set ROLITHAX_SCHEMA_CLI to rolithax-schema[.exe].")
    return str(candidate)


def native(requests, executable=None):
    payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in requests)
    try:
        result = subprocess.run([native_binary(executable)], input=payload, capture_output=True,
                                encoding="utf-8", errors="strict", timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SdkError(f"Native parser failed: {error}") from error
    if result.returncode:
        raise SdkError(f"Native parser exited {result.returncode}: {result.stderr.strip()[:2000]}")
    lines = result.stdout.splitlines()
    if len(lines) != len(requests):
        raise SdkError("Native parser protocol error: response count mismatch")
    values = []
    for line in lines:
        response = read_json(line)
        if not isinstance(response, dict) or "ok" not in response:
            raise SdkError("Native parser protocol error: missing ok")
        if not response["ok"]:
            error = response.get("error", {})
            raise SdkError(f"{error.get('file', 'plugin')}:{error.get('line', 0)}: {error.get('message', 'native validation failed')}")
        values.append(response.get("data"))
    return values


def safe_relative(value):
    if not isinstance(value, str) or not value or any(c in value for c in "\\:%?#") or any(ord(c) < 32 for c in value):
        raise SdkError(f"Unsafe relative path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise SdkError(f"Unsafe relative path: {value!r}")
    return path


def inside(root, relative):
    base = Path(root).resolve()
    target = base.joinpath(*safe_relative(relative).parts)
    if not target.resolve().is_relative_to(base):
        raise SdkError(f"Path escapes source root: {relative}")
    if target.is_symlink():
        raise SdkError(f"Symbolic links are not package files: {relative}")
    return target


def file_bytes(path, maximum):
    if path.stat().st_size > maximum:
        raise SdkError(f"{path.name} exceeds {maximum} bytes")
    return path.read_bytes()


def load_package(directory):
    root = Path(directory)
    manifest = file_bytes(inside(root, "plugin.json"), LIMITS["plugin.json"])
    metadata = read_json(manifest.decode("utf-8-sig"))
    engine = metadata.get("engine", "yaml")
    if engine not in ("yaml", "lua"):
        raise SdkError("engine must be yaml or lua")
    names = ["plugin.json", "ui.xml", "business.lua" if engine == "lua" else "rules.yaml"]
    if engine == "lua" and (root / "rules.yaml").exists():
        names.append("rules.yaml")
    files = {name: file_bytes(inside(root, name), LIMITS[name]) for name in names}
    if engine == "lua" and files.get("rules.yaml", b"").decode("utf-8-sig").strip():
        raise SdkError("Lua packages cannot contain YAML business rules")
    source = {"manifest": files["plugin.json"].decode("utf-8-sig"),
              "ui": files["ui.xml"].decode("utf-8-sig"),
              "rules": files.get("rules.yaml", b"").decode("utf-8-sig"),
              "lua": files.get("business.lua", b"").decode("utf-8-sig")}
    return metadata, files, source


def validate(directory, executable=None):
    metadata, files, source = load_package(directory)
    inspected = native([{"op": "inspect", "source": source}], executable)[0]
    if inspected.get("id") != metadata.get("id"):
        raise SdkError("Native parser returned a different plugin identity")
    return inspected, files, source


def replay(directory, logs, expected=None, executable=None):
    _, _, source = load_package(directory)
    text = file_bytes(Path(logs), 16 * 1024 * 1024).decode("utf-8-sig")
    lines = text.splitlines()
    if len(lines) > 100000:
        raise SdkError("Fixture contains more than 100000 lines")
    requests = [{"op": "reload", "source": source, "allowedCommands": []}]
    requests += [{"op": "log", "text": line} for line in lines]
    requests += [{"op": "snapshot"}]
    snapshot = native(requests, executable)[-1]
    if expected:
        wanted = read_json(Path(expected).read_text(encoding="utf-8-sig"))
        mismatches = {key: {"expected": value, "actual": snapshot["values"].get(key)}
                      for key, value in wanted.items() if snapshot["values"].get(key) != value}
        if mismatches:
            raise SdkError("Replay mismatch: " + json.dumps(mismatches, ensure_ascii=False))
    return snapshot


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name])
    data = buffer.getvalue()
    if len(data) > PACKAGE_LIMIT:
        raise SdkError("ZIP exceeds 512 KiB")
    return data


def digest(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def pack(directory, output, executable=None):
    metadata, files, _ = validate(directory, executable)
    data = zip_bytes(files)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(data)
    return {"id": metadata["id"], "version": metadata["version"], "file": str(output), **digest(data)}


def read_repository(text):
    if len(text.encode("utf-8")) > README_LIMIT:
        raise SdkError("README exceeds 256 KiB")
    matches = list(FENCE.finditer(text))
    if len(matches) != 1:
        raise SdkError("README must contain exactly one rolithax-schema-repository JSON fence")
    document = read_json(matches[0].group(1))
    if not isinstance(document, dict) or document.get("schemaVersion") != 1:
        raise SdkError("Repository schemaVersion must be 1")
    if not SYMBOL.fullmatch(str(document.get("id", ""))) or not document.get("name"):
        raise SdkError("Repository requires a namespaced id and name")
    plugins = document.get("plugins")
    if not isinstance(plugins, list) or len(plugins) > 256:
        raise SdkError("Repository plugins must be an array with at most 256 entries")
    seen = set()
    for item in plugins:
        if not isinstance(item, dict):
            raise SdkError("Repository plugin must be an object")
        for key in ("id", "version", "name", "description", "engine", "source"):
            if key not in item:
                raise SdkError(f"Repository plugin missing {key}")
        if not SYMBOL.fullmatch(str(item["id"])) or not isinstance(item["version"], str) or not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", item["version"]):
            raise SdkError("Invalid plugin identity/version")
        if item["id"] in seen:
            raise SdkError(f"Duplicate plugin id: {item['id']}")
        seen.add(item["id"])
        source = item["source"]
        if not isinstance(source, dict):
            raise SdkError("Plugin source must be an object")
        if source.get("type") == "files" and item["engine"] == "yaml":
            safe_relative(source.get("path"))
            entries = source.get("files", [])
            if not isinstance(entries, list) or len(entries) != 3 or not all(isinstance(entry, dict) and isinstance(entry.get("path"), str) for entry in entries) or {entry["path"] for entry in entries} != {"plugin.json", "ui.xml", "rules.yaml"}:
                raise SdkError("YAML file sources require exactly plugin.json, ui.xml and rules.yaml")
            for entry in entries:
                check_hash_metadata(entry, LIMITS[entry["path"]])
        elif source.get("type") == "package" and item["engine"] in ("yaml", "lua"):
            https_url(source.get("url", ""))
            check_hash_metadata(source, PACKAGE_LIMIT)
        else:
            raise SdkError("Lua plugins require a prebuilt package source; YAML supports files or package")
    return document, matches[0]


def check_hash_metadata(entry, maximum):
    if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
        raise SdkError("Expected lowercase SHA-256 digest")
    size = entry.get("size")
    if type(size) is not int or size < 0 or size > maximum:
        raise SdkError(f"Invalid source size; maximum {maximum}")


def https_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise SdkError("Download URL must be HTTPS without credentials or fragment")
    return url


def source_index(directory, check=False, executable=None):
    path = Path(directory) / "README.md"
    text = path.read_text(encoding="utf-8-sig")
    document, match = read_repository(text)
    updated = copy.deepcopy(document)
    for item in updated["plugins"]:
        source = item["source"]
        if source["type"] == "files":
            location = inside(directory, source["path"])
            metadata, files, _ = validate(location, executable)
            if any(metadata.get(key) != item[key] for key in ("id", "version", "engine")):
                raise SdkError(f"README identity differs from {source['path']}/plugin.json")
            source["files"] = [{"path": name, **digest(data)} for name, data in sorted(files.items())]
    if check:
        if updated != document:
            raise SdkError("README file hashes/sizes are outdated; run source-index without --check")
    else:
        replacement = "```rolithax-schema-repository\n" + json.dumps(updated, ensure_ascii=False, indent=2) + "\n```"
        path.write_text(text[:match.start()] + replacement + text[match.end():], encoding="utf-8", newline="\n")
    return updated


class HttpsRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url, maximum):
    request = urllib.request.Request(https_url(url), headers={"User-Agent": "Rolithax-Schema-SDK/" + VERSION})
    with urllib.request.build_opener(HttpsRedirect()).open(request, timeout=30) as response:
        https_url(response.url)
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise SdkError("Download exceeds declared limit")
    return data


def verified_download(url, entry):
    data = download(url, entry["size"])
    if digest(data) != {"sha256": entry["sha256"], "size": entry["size"]}:
        raise SdkError(f"SHA-256 or size mismatch: {url}")
    return data


def pinned_readme(url):
    parsed = urllib.parse.urlsplit(https_url(url))
    parts = parsed.path.strip("/").removesuffix(".git").split("/")
    if parsed.hostname == "github.com" and len(parts) == 2:
        owner, repo = parts
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in (".", "..") for part in parts):
            raise SdkError("Invalid GitHub repository path")
        try:
            info = read_json(download(f"https://api.github.com/repos/{owner}/{repo}/commits/HEAD", 1048576).decode("utf-8"))
            commit = info.get("sha", "")
        except urllib.error.HTTPError as error:
            if error.code not in (403, 429):
                raise
            feed = download(f"https://github.com/{owner}/{repo}/commits/HEAD.atom", README_LIMIT).decode("utf-8")
            match = re.search(r"<id>\s*tag:github\.com,2008:Grit::Commit/([a-f0-9]{40})\s*</id>", feed)
            if match is None:
                raise SdkError("GitHub Atom feed did not contain a pinned commit") from error
            commit = match.group(1)
        if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise SdkError("GitHub returned an invalid commit")
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/README.md"
    if parsed.hostname == "raw.githubusercontent.com" and len(parts) >= 4 and re.fullmatch(r"[0-9a-f]{40}", parts[2]):
        return url
    raise SdkError("Use a GitHub repository URL or a raw.githubusercontent.com README URL pinned to a 40-character commit")


def fetch(url, plugin_id, output, executable=None):
    readme = pinned_readme(url)
    document, _ = read_repository(download(readme, README_LIMIT).decode("utf-8-sig"))
    plugin = next((item for item in document["plugins"] if item["id"] == plugin_id), None)
    if plugin is None:
        raise SdkError(f"Plugin is not in repository: {plugin_id}")
    source = plugin["source"]
    if source["type"] == "files":
        base = urllib.parse.urljoin(readme, urllib.parse.quote(source["path"], safe="/") + "/")
        files = {item["path"]: verified_download(urllib.parse.urljoin(base, item["path"]), item) for item in source["files"]}
    else:
        archive = verified_download(source["url"], source)
        files = {}
        with zipfile.ZipFile(io.BytesIO(archive)) as package:
            for entry in package.infolist():
                if entry.filename not in LIMITS or entry.filename in files or entry.file_size > LIMITS[entry.filename] or entry.flag_bits & 1:
                    raise SdkError("Invalid, duplicate, encrypted, or oversized ZIP entry")
                if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise SdkError("ZIP must not contain symbolic links")
                files[entry.filename] = package.read(entry)
    with tempfile.TemporaryDirectory(prefix="rolithax-schema-") as temporary:
        for name, data in files.items():
            Path(temporary, name).write_bytes(data)
        metadata, loaded, _ = validate(temporary, executable)
        if any(metadata.get(key) != plugin[key] for key in ("id", "version", "engine")) or set(files) != set(loaded):
            raise SdkError("Package identity, engine, or entries differ from repository metadata")
        data = zip_bytes(loaded)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(data)
    return {"file": str(output), "repositoryReadme": readme, **digest(data)}


def initialize(directory, plugin_id, engine):
    target = Path(directory)
    if target.exists() and any(target.iterdir()):
        raise SdkError("init requires an empty or new directory")
    if not SYMBOL.fullmatch(plugin_id) or len(plugin_id) > 96:
        raise SdkError("Use a namespaced id, for example example.my-panel")
    source = Path(__file__).parent / "templates" / engine
    shutil.copytree(source, target, dirs_exist_ok=True)
    path = target / "plugin.json"
    manifest = read_json(path.read_text(encoding="utf-8"))
    manifest["id"] = plugin_id
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"directory": str(target), "engine": engine, "id": plugin_id}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rolithax Schema SDK: native validation, replay, packaging and Git sources")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--native", help="Path to the shared C++ rolithax-schema CLI")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("directory")
    init.add_argument("id", nargs="?", default="example.paper-panel")
    init.add_argument("--engine", choices=["yaml", "lua"], default="yaml")
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("directory")
    replay_parser = commands.add_parser("replay")
    replay_parser.add_argument("directory")
    replay_parser.add_argument("logs")
    replay_parser.add_argument("expected", nargs="?")
    pack_parser = commands.add_parser("pack")
    pack_parser.add_argument("directory")
    pack_parser.add_argument("output")
    index_parser = commands.add_parser("source-index")
    index_parser.add_argument("directory")
    index_parser.add_argument("--check", action="store_true")
    fetch_parser = commands.add_parser("fetch")
    fetch_parser.add_argument("repository")
    fetch_parser.add_argument("id")
    fetch_parser.add_argument("output")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize(args.directory, args.id, args.engine)
        elif args.command == "validate":
            result = validate(args.directory, args.native)[0]
        elif args.command == "replay":
            result = replay(args.directory, args.logs, args.expected, args.native)
        elif args.command == "pack":
            result = pack(args.directory, args.output, args.native)
        elif args.command == "source-index":
            result = source_index(args.directory, args.check, args.native)
        else:
            result = fetch(args.repository, args.id, args.output, args.native)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (SdkError, OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
