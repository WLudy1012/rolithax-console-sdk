from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import urllib.error
import zipfile

VERSION = "3.0.0"
LIMITS = {"plugin.json": 16384, "ui.xml": 65536, "rules.yaml": 65536, "business.lua": 131072}
PACKAGE_LIMIT = 524288
MODERN_LIMIT = 128 * 1024 * 1024
MEDIA_LIMITS = {"image/png": 8 * 1024 * 1024, "image/jpeg": 8 * 1024 * 1024,
                "image/webp": 8 * 1024 * 1024, "video/mp4": 64 * 1024 * 1024}
README_LIMIT = 262144
FENCE = re.compile(r"(?m)^```rolithax-schema-repository\s*\n(.*?)^```[ \t]*$", re.S)
FENCE_V2 = re.compile(r"(?m)^```rolithax-schema-repository-v2\s*\n(.*?)^```[ \t]*$", re.S)
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
    if not isinstance(value, str) or not value or any(c in value for c in '\\:%?#<>|*"') or any(ord(c) < 32 for c in value):
        raise SdkError(f"Unsafe relative path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(not part or part.endswith((".", " ")) for part in value.split("/")):
        raise SdkError(f"Unsafe relative path: {value!r}")
    return path


def inside(root, relative):
    base = Path(root).resolve()
    target = base
    for part in safe_relative(relative).parts:
        target /= part
        if target.is_symlink():
            raise SdkError(f"Symbolic links are not package files: {relative}")
    if not target.resolve().is_relative_to(base):
        raise SdkError(f"Path escapes source root: {relative}")
    return target


def file_bytes(path, maximum):
    if path.stat().st_size > maximum:
        raise SdkError(f"{path.name} exceeds {maximum} bytes")
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if len(data) > maximum:
        raise SdkError(f"{path.name} exceeds {maximum} bytes")
    return data


def definition_names(metadata, lua_rules_present):
    engine = metadata.get("engine", "yaml")
    if engine not in ("yaml", "lua"):
        raise SdkError("engine must be yaml or lua")
    names = ["plugin.json", "ui.xml", "business.lua" if engine == "lua" else "rules.yaml"]
    if engine == "lua" and lua_rules_present:
        names.append("rules.yaml")
    return names


def asset_entries(metadata):
    if metadata.get("sdkVersion") != 3:
        return []
    assets = metadata.get("assets")
    if not isinstance(assets, list) or not 1 <= len(assets) <= 64:
        raise SdkError("Expected 1 to 64 assets")
    ids, paths = set(), set()
    total = 0
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("id"), str) or not asset["id"] or asset["id"] in ids:
            raise SdkError("Invalid or duplicate asset id")
        ids.add(asset["id"])
        media_type = asset.get("mediaType")
        if not isinstance(media_type, str) or media_type not in MEDIA_LIMITS:
            raise SdkError("Invalid asset mediaType")
        check_hash_metadata(asset, MEDIA_LIMITS[media_type], minimum=1)
        total += asset["size"]
        if total > MODERN_LIMIT or ("path" in asset) == ("url" in asset):
            raise SdkError("Invalid asset source or total size")
        if "path" in asset:
            path = safe_relative(asset["path"]).as_posix()
            if not path.startswith("assets/") or path.casefold() in paths:
                raise SdkError("Invalid or duplicate package asset path")
            paths.add(path.casefold())
        else:
            https_url(asset["url"])
    return assets


def check_media(data, media_type):
    if media_type == "image/png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n")
    elif media_type == "image/jpeg":
        valid = data.startswith(b"\xff\xd8\xff")
    elif media_type == "image/webp":
        valid = data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    else:
        valid = data[4:8] == b"ftyp"
    if not valid:
        raise SdkError(f"Asset does not match its declared type: {media_type}")


def check_asset(data, asset):
    if digest(data) != {"sha256": asset["sha256"], "size": asset["size"]}:
        raise SdkError(f"SHA-256 or size mismatch for asset {asset.get('id', 'icon')}")
    check_media(data, asset["mediaType"])


def source_data(metadata, files):
    engine = metadata.get("engine", "yaml")
    if engine == "lua" and files.get("rules.yaml", b"").decode("utf-8-sig").strip():
        raise SdkError("Lua packages cannot contain YAML business rules")
    total = sum(map(len, files.values())) + sum(asset["size"] for asset in asset_entries(metadata) if "url" in asset)
    if total > (MODERN_LIMIT if metadata.get("sdkVersion") == 3 else PACKAGE_LIMIT):
        raise SdkError("Plugin definitions and assets exceed package budget")
    return {"manifest": files["plugin.json"].decode("utf-8-sig"),
            "ui": files["ui.xml"].decode("utf-8-sig"),
            "rules": files.get("rules.yaml", b"").decode("utf-8-sig"),
            "lua": files.get("business.lua", b"").decode("utf-8-sig")}


def load_package(directory):
    root = Path(directory)
    manifest = file_bytes(inside(root, "plugin.json"), LIMITS["plugin.json"])
    metadata = read_json(manifest.decode("utf-8-sig"))
    if not isinstance(metadata, dict):
        raise SdkError("plugin.json must be an object")
    names = definition_names(metadata, (root / "rules.yaml").exists())
    files = {name: file_bytes(inside(root, name), LIMITS[name]) for name in names}
    for asset in asset_entries(metadata):
        if "path" in asset:
            data = file_bytes(inside(root, asset["path"]), asset["size"])
            check_asset(data, asset)
            files[asset["path"]] = data
    return metadata, files, source_data(metadata, files)


def validate(directory, executable=None):
    metadata, files, source = load_package(directory)
    inspected = native([{"op": "inspect", "source": source}], executable)[0]
    if inspected.get("id") != metadata.get("id"):
        raise SdkError("Native parser returned a different plugin identity")
    return inspected, files, source


def read_package_zip(package):
    with zipfile.ZipFile(package) as archive:
        items = {}
        collision_keys = set()
        for entry in archive.infolist():
            name = safe_relative(entry.filename).as_posix()
            kind = (entry.external_attr >> 16) & 0o170000
            if entry.is_dir() or entry.flag_bits & 1 or kind not in (0, 0o100000) or name.casefold() in collision_keys or len(items) >= 68:
                raise SdkError("ZIP contains an invalid or duplicate entry")
            collision_keys.add(name.casefold())
            items[name] = entry

        def read_entry(name, limit):
            entry = items.get(name)
            if entry is None or entry.file_size > limit:
                raise SdkError(f"ZIP entry missing or too large: {name}")
            with archive.open(entry) as source:
                data = source.read(limit + 1)
            if len(data) != entry.file_size or len(data) > limit:
                raise SdkError(f"Unexpected ZIP entry length: {name}")
            return data

        manifest = read_entry("plugin.json", LIMITS["plugin.json"])
        metadata = read_json(manifest.decode("utf-8-sig"))
        if not isinstance(metadata, dict):
            raise SdkError("plugin.json must be an object")
        if metadata.get("sdkVersion") != 3 and package.stat().st_size > PACKAGE_LIMIT:
            raise SdkError("SDK 1/2 ZIP exceeds 512 KiB")
        names = definition_names(metadata, "rules.yaml" in items)
        assets = [asset for asset in asset_entries(metadata) if "path" in asset]
        if set(items) != set(names) | {asset["path"] for asset in assets} or sum(entry.file_size for entry in items.values()) > MODERN_LIMIT:
            raise SdkError("ZIP entry set or total size differs from the manifest")
        files = {name: read_entry(name, LIMITS[name]) for name in names}
        for asset in assets:
            data = read_entry(asset["path"], asset["size"])
            check_asset(data, asset)
            files[asset["path"]] = data
    return metadata, files, source_data(metadata, files)


def check(target, executable=None):
    target = Path(target)
    if target.is_dir():
        return validate(target, executable)[0]
    if target.stat().st_size > MODERN_LIMIT:
        raise SdkError("ZIP exceeds 128 MiB")
    metadata, _, source = read_package_zip(target)
    inspected = native([{"op": "inspect", "source": source}], executable)[0]
    if inspected.get("id") != metadata.get("id"):
        raise SdkError("Native parser returned a different plugin identity")
    return inspected


def replay(directory, logs, expected=None, executable=None, events=None):
    _, _, source = load_package(directory)
    text = file_bytes(Path(logs), 16 * 1024 * 1024).decode("utf-8-sig")
    lines = text.splitlines()
    if len(lines) > 100000:
        raise SdkError("Fixture contains more than 100000 lines")
    requests = [{"op": "reload", "source": source, "allowedCommands": []}]
    requests += [{"op": "log", "text": line} for line in lines]
    if events:
        results = read_json(file_bytes(Path(events), 256 * 1024).decode("utf-8-sig"))
        if not isinstance(results, list) or len(results) > 256:
            raise SdkError("Event fixture must be an array with at most 256 entries")
        for entry in results:
            if not isinstance(entry, dict) or set(entry) != {"eventType", "result"} or entry["eventType"] not in ("preflight", "action") or not isinstance(entry["result"], dict):
                raise SdkError("Invalid result event in fixture")
            requests.append({"op": "event", "expectedRevision": 1, **entry})
    requests += [{"op": "snapshot"}]
    snapshot = native(requests, executable)[-1]
    if expected:
        wanted = read_json(Path(expected).read_text(encoding="utf-8-sig"))
        mismatches = {key: {"expected": value, "actual": snapshot["values"].get(key)}
                      for key, value in wanted.items() if snapshot["values"].get(key) != value}
        if mismatches:
            raise SdkError("Replay mismatch: " + json.dumps(mismatches, ensure_ascii=False))
    return snapshot


def zip_bytes(files, maximum=PACKAGE_LIMIT):
    if sum(map(len, files.values())) > maximum:
        raise SdkError(f"Uncompressed package exceeds {maximum} bytes")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED if name.startswith("assets/") else zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, files[name])
    data = buffer.getvalue()
    if len(data) > maximum:
        raise SdkError(f"ZIP exceeds {maximum} bytes")
    return data


def digest(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def pack(directory, output, executable=None):
    metadata, files, _ = validate(directory, executable)
    data = zip_bytes(files, MODERN_LIMIT if metadata.get("sdkVersion") == 3 else PACKAGE_LIMIT)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(data)
    return {"id": metadata["id"], "version": metadata["version"], "file": str(output), **digest(data)}


def read_repository(text):
    if len(text.encode("utf-8")) > README_LIMIT:
        raise SdkError("README exceeds 256 KiB")
    legacy, modern = list(FENCE.finditer(text)), list(FENCE_V2.finditer(text))
    if len(legacy) > 1 or len(modern) > 1 or not (legacy or modern):
        raise SdkError("README must have at most one fence of each catalog version")
    match = (modern or legacy)[0]
    return read_catalog(match, 2 if modern else 1), match


def repository_metadata(item, *, is_plugin):
    fields = ("name", "description", "homepage", "license") if is_plugin else ("name", "description", "homepage")
    for field in fields:
        if not isinstance(item.get(field), str) or not item[field].strip() or len(item[field].encode("utf-8")) > 512:
            raise SdkError(f"Catalog metadata is missing or invalid: {field}")
    authors = item.get("authors")
    if not isinstance(authors, list) or not 1 <= len(authors) <= 16 or not all(isinstance(author, str) and author.strip() and len(author.encode("utf-8")) <= 128 for author in authors):
        raise SdkError("Catalog requires 1 to 16 author names")
    https_url(item["homepage"])
    icon = item.get("icon")
    if not isinstance(icon, dict) or icon.get("mediaType") not in ("image/png", "image/jpeg", "image/webp") or ("path" in icon) == ("url" in icon):
        raise SdkError("Catalog icon must declare a supported image and one source")
    check_hash_metadata(icon, MEDIA_LIMITS[icon["mediaType"]], minimum=1)
    if "path" in icon:
        safe_relative(icon["path"])
    else:
        https_url(icon["url"])


def read_catalog(match, version):
    document = read_json(match.group(1))
    if not isinstance(document, dict) or type(document.get("schemaVersion")) is not int or document["schemaVersion"] != version:
        raise SdkError(f"Repository schemaVersion must be {version}")
    if not SYMBOL.fullmatch(str(document.get("id", ""))) or not document.get("name"):
        raise SdkError("Repository requires a namespaced id and name")
    if version == 2:
        repository_metadata(document, is_plugin=False)
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
        sdk_version = 2
        if version == 2:
            repository_metadata(item, is_plugin=True)
            sdk_version = item.get("sdkVersion")
            if type(sdk_version) is not int or sdk_version not in (1, 2, 3):
                raise SdkError("V2 catalog requires sdkVersion 1, 2 or 3")
        source = item["source"]
        if not isinstance(source, dict):
            raise SdkError("Plugin source must be an object")
        if source.get("type") == "files" and item["engine"] == "yaml":
            safe_relative(source.get("path"))
            entries = source.get("files", [])
            names = {"plugin.json", "ui.xml", "rules.yaml"}
            if not isinstance(entries, list) or len(entries) > 67 or not all(isinstance(entry, dict) and isinstance(entry.get("path"), str) for entry in entries):
                raise SdkError("Invalid YAML file list")
            paths = {entry["path"] for entry in entries}
            if len({path.casefold() for path in paths}) != len(entries) or not names.issubset(paths) or (sdk_version < 3 and names != paths):
                raise SdkError("YAML file sources require plugin.json, ui.xml and rules.yaml without duplicates")
            for entry in entries:
                path = safe_relative(entry["path"]).as_posix()
                if path not in names and (sdk_version < 3 or not path.startswith("assets/")):
                    raise SdkError("Unknown YAML package file")
                check_hash_metadata(entry, LIMITS.get(path, 64 * 1024 * 1024))
            if sum(entry["size"] for entry in entries) > (MODERN_LIMIT if sdk_version == 3 else PACKAGE_LIMIT):
                raise SdkError("Catalog file list exceeds package budget")
        elif source.get("type") == "package" and item["engine"] in ("yaml", "lua"):
            https_url(source.get("url", ""))
            check_hash_metadata(source, MODERN_LIMIT if sdk_version == 3 else PACKAGE_LIMIT)
        else:
            raise SdkError("Lua plugins require a prebuilt package source; YAML supports files or package")
    return document


def check_hash_metadata(entry, maximum, *, minimum=0):
    if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256", ""))):
        raise SdkError("Expected lowercase SHA-256 digest")
    size = entry.get("size")
    if type(size) is not int or size < minimum or size > maximum:
        raise SdkError(f"Invalid source size; maximum {maximum}")


def https_url(url):
    if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url) or "\\" in url:
        raise SdkError("Invalid HTTPS URL")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise SdkError("Download URL must be HTTPS without credentials or fragment")
    return url


def source_index(directory, check=False, executable=None):
    path = Path(directory) / "README.md"
    text = path.read_text(encoding="utf-8-sig")
    document, match = read_repository(text)
    updated = copy.deepcopy(document)
    if updated["schemaVersion"] == 2:
        for entry in [updated, *updated["plugins"]]:
            icon = entry["icon"]
            if "path" in icon:
                data = file_bytes(inside(directory, icon["path"]), MEDIA_LIMITS[icon["mediaType"]])
                check_media(data, icon["mediaType"])
                icon.update(digest(data))
    for item in updated["plugins"]:
        source = item["source"]
        if source["type"] == "files":
            location = inside(directory, source["path"])
            metadata, files, _ = validate(location, executable)
            if not catalog_identity(item, metadata):
                raise SdkError(f"README identity differs from {source['path']}/plugin.json")
            source["files"] = [{"path": name, **digest(data)} for name, data in sorted(files.items())]
    if check:
        if updated != document:
            raise SdkError("README file hashes/sizes are outdated; run source-index without --check")
    else:
        fence_name = "rolithax-schema-repository-v2" if updated["schemaVersion"] == 2 else "rolithax-schema-repository"
        replacement = f"```{fence_name}\n" + json.dumps(updated, ensure_ascii=False, indent=2) + "\n```"
        path.write_text(text[:match.start()] + replacement + text[match.end():], encoding="utf-8", newline="\n")
    return updated


def catalog_identity(item, metadata):
    keys = ("id", "version", "engine", "sdkVersion", "name", "description", "authors", "license", "homepage") if item.get("sdkVersion") == 3 else ("id", "version", "engine", "sdkVersion")
    if any(key in item and item[key] != metadata.get(key) for key in keys):
        return False
    if item.get("sdkVersion") == 3:
        asset = next((entry for entry in metadata["assets"] if entry["id"] == metadata["icon"]), None)
        return asset is not None and all(asset[key] == item["icon"][key] for key in ("sha256", "size", "mediaType"))
    return True


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
    if parsed.query:
        raise SdkError("Source URL cannot have a query")
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
    if parsed.hostname == "gitlab.com":
        if len(parts) < 2 or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in (".", "..", "-") for part in parts):
            raise SdkError("Invalid GitLab repository path")
        project = "/".join(parts)
        quoted = urllib.parse.quote(project, safe="")
        info = read_json(download(f"https://gitlab.com/api/v4/projects/{quoted}/repository/commits?per_page=1", README_LIMIT).decode("utf-8"))
        commit = info[0].get("id") if isinstance(info, list) and info and isinstance(info[0], dict) else None
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise SdkError("GitLab returned an invalid commit")
        return f"https://gitlab.com/{project}/-/raw/{commit}/README.md"
    if parsed.hostname == "raw.githubusercontent.com" and (len(parts) < 4 or not re.fullmatch(r"[0-9a-f]{40}", parts[2])):
        raise SdkError("GitHub raw README URL must be pinned to a commit")
    if parts[-1].lower() == "readme.md" and parsed.hostname not in ("github.com", "gitlab.com"):
        return url
    raise SdkError("Use a GitHub or GitLab repository URL or an HTTPS README URL")


def fetch(url, plugin_id, output, executable=None, asset_cache=None):
    readme = pinned_readme(url)
    document, _ = read_repository(download(readme, README_LIMIT).decode("utf-8-sig"))
    plugin = next((item for item in document["plugins"] if item["id"] == plugin_id), None)
    if plugin is None:
        raise SdkError(f"Plugin is not in repository: {plugin_id}")
    source = plugin["source"]
    with tempfile.TemporaryDirectory(prefix="rolithax-schema-") as temporary:
        stage = Path(temporary)
        if source["type"] == "files":
            base = urllib.parse.urljoin(readme, urllib.parse.quote(source["path"], safe="/") + "/")
            paths = set()
            for item in source["files"]:
                path = safe_relative(item["path"]).as_posix()
                paths.add(path)
                destination = inside(stage, path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(verified_download(urllib.parse.urljoin(base, urllib.parse.quote(path, safe="/")), item))
            manifest, loaded, payload = load_package(stage)
            if paths != set(loaded):
                raise SdkError("Package entries differ from repository metadata")
            data = zip_bytes(loaded, MODERN_LIMIT if manifest.get("sdkVersion") == 3 else PACKAGE_LIMIT)
        else:
            data = verified_download(source["url"], source)
            archive = stage / "package.zip"
            archive.write_bytes(data)
            manifest, _, payload = read_package_zip(archive)
        inspected = native([{"op": "inspect", "source": payload}], executable)[0]
        if not catalog_identity(plugin, inspected):
            raise SdkError("Package identity differs from repository metadata")
        remote = [asset for asset in asset_entries(manifest) if "url" in asset]
        if len(data) + sum(asset["size"] for asset in remote) > MODERN_LIMIT:
            raise SdkError("ZIP plus external assets exceed 128 MiB")
        stored = []
        for asset in remote:
            content = verified_download(asset["url"], asset)
            check_media(content, asset["mediaType"])
            target = stage / asset["sha256"]
            target.write_bytes(content)
            stored.append(target)
        if stored:
            cache = Path(asset_cache or (str(output) + ".assets"))
            for target in stored:
                atomic_write(cache / target.name, target.read_bytes())
    atomic_write(Path(output), data)
    result = {"file": str(output), "repositoryReadme": readme, **digest(data)}
    if stored:
        result["assetCache"] = str(cache)
    return result


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        staged = Path(temporary.name)
        try:
            temporary.write(data)
            temporary.close()
            staged.replace(path)
        finally:
            staged.unlink(missing_ok=True)


def initialize(directory, plugin_id, engine, sdk_version=3):
    target = Path(directory)
    if target.exists() and any(target.iterdir()):
        raise SdkError("init requires an empty or new directory")
    if not SYMBOL.fullmatch(plugin_id) or len(plugin_id) > 96:
        raise SdkError("Use a namespaced id, for example example.my-panel")
    template_root = Path(__file__).parent / "templates"
    source = (template_root / "sdk3" if sdk_version == 3 else template_root) / engine
    shutil.copytree(source, target, dirs_exist_ok=True)
    path = target / "plugin.json"
    manifest = read_json(path.read_text(encoding="utf-8"))
    manifest["id"] = plugin_id
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return {"directory": str(target), "engine": engine, "id": plugin_id, "sdkVersion": sdk_version}


def doctor(executable=None):
    binary = native_binary(executable)
    info = native([{"op": "version"}], binary)[0]
    if not isinstance(info, dict) or info.get("maxSdk", 0) < 3:
        raise SdkError("Native CLI does not support SDK 3")
    return {"sdk": VERSION, "native": binary, "platform": platform.system(), "architecture": platform.machine(), **info}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rolithax Schema SDK: native validation, replay, packaging and Git sources")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--native", help="Path to the shared C++ rolithax-schema CLI")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("directory")
    init.add_argument("id", nargs="?", default="example.paper-panel")
    init.add_argument("--engine", choices=["yaml", "lua"], default="yaml")
    init.add_argument("--sdk-version", type=int, choices=[2, 3], default=3)
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("directory")
    checker = commands.add_parser("check", help="Validate a project directory or plugin ZIP without network access")
    checker.add_argument("target")
    checker.add_argument("--format", choices=["text", "json"], default="text")
    commands.add_parser("doctor", help="Show CLI and native SDK compatibility")
    replay_parser = commands.add_parser("replay")
    replay_parser.add_argument("directory")
    replay_parser.add_argument("logs")
    replay_parser.add_argument("expected", nargs="?")
    replay_parser.add_argument("--events", help="JSON array of simulated preflight and confirmed-action results")
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
    fetch_parser.add_argument("--asset-cache", help="Directory for verified external media")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize(args.directory, args.id, args.engine, args.sdk_version)
        elif args.command == "validate":
            result = validate(args.directory, args.native)[0]
        elif args.command == "check":
            result = check(args.target, args.native)
            if args.format == "text":
                print(f"OK {result['id']} {result['version']} (SDK {result['sdkVersion']})")
                return 0
        elif args.command == "doctor":
            result = doctor(args.native)
        elif args.command == "replay":
            result = replay(args.directory, args.logs, args.expected, args.native, args.events)
        elif args.command == "pack":
            result = pack(args.directory, args.output, args.native)
        elif args.command == "source-index":
            result = source_index(args.directory, args.check, args.native)
        else:
            result = fetch(args.repository, args.id, args.output, args.native, args.asset_cache)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (SdkError, OSError, ValueError, KeyError, UnicodeError, zipfile.BadZipFile) as error:
        if args.command == "check" and args.format == "json":
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        else:
            print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
