import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import io
import urllib.error

import rolithax_schema_sdk as sdk


def repository():
    return {
        "schemaVersion": 1, "id": "example.repository", "name": "Example", "plugins": [{
            "id": "example.panel", "name": "Example", "version": "1.0.0", "description": "Example",
            "engine": "yaml", "source": {"type": "files", "path": "plugins/example", "files": [
                {"path": name, "sha256": "0" * 64, "size": 0}
                for name in ("plugin.json", "rules.yaml", "ui.xml")
            ]}
        }]
    }


def fenced(value):
    return "# Example\n\n```rolithax-schema-repository\n" + json.dumps(value) + "\n```\n"


def v2_fence(value):
    return "```rolithax-schema-repository-v2\n" + json.dumps(value) + "\n```\n"


def modern_catalog():
    value = repository()
    shared = {"authors": ["Example"], "homepage": "https://example.org",
              "icon": {"path": "icons/project.png", "mediaType": "image/png", "size": 64, "sha256": "0" * 64}}
    value.update({"schemaVersion": 2, "description": "Example catalogue", **copy.deepcopy(shared)})
    value["plugins"][0].update({"sdkVersion": 3, "license": "MIT", **copy.deepcopy(shared)})
    return value


class RepositoryTests(unittest.TestCase):
    def test_valid_index(self):
        value = repository()
        self.assertEqual(sdk.read_repository(fenced(value))[0], value)

    def test_ambiguous_fence_rejected(self):
        with self.assertRaises(sdk.SdkError):
            sdk.read_repository(fenced(repository()) * 2)

    def test_v2_catalog_can_coexist_with_v1(self):
        modern = modern_catalog()
        self.assertEqual(sdk.read_repository(fenced(repository()) + v2_fence(modern))[0], modern)
        with self.assertRaises(sdk.SdkError):
            sdk.read_repository(v2_fence(modern) * 2)

    def test_v2_catalog_requires_icon_and_metadata(self):
        modern = modern_catalog()
        del modern["plugins"][0]["authors"]
        with self.assertRaises(sdk.SdkError):
            sdk.read_repository(v2_fence(modern))
        modern = modern_catalog()
        modern["icon"]["path"] = "icons/../image.png"
        with self.assertRaises(sdk.SdkError):
            sdk.read_repository(v2_fence(modern))

    def test_duplicate_json_key_rejected(self):
        with self.assertRaises(sdk.SdkError):
            sdk.read_json('{"engine":"yaml","engine":"lua"}')

    def test_lua_requires_archive(self):
        value = repository()
        value["plugins"][0]["engine"] = "lua"
        with self.assertRaises(sdk.SdkError):
            sdk.read_repository(fenced(value))

    def test_traversal_rejected(self):
        for value in ("../escape", "/absolute", "a/../b", "a\\b", "a//b", "a/%2e%2e/b", "C:/file"):
            with self.subTest(path=value), self.assertRaises(sdk.SdkError):
                sdk.safe_relative(value)

    def test_reject_duplicate_file_entry(self):
        value = repository()
        value["plugins"][0]["source"]["files"][-1]["path"] = "rules.yaml"
        with self.assertRaises(sdk.SdkError):
            sdk.read_repository(fenced(value))

    def test_url_has_no_credentials_or_plain_http(self):
        for url in ("http://example.com/a", "https://user:secret@example.com/a", "file:///a"):
            with self.subTest(url=url), self.assertRaises(sdk.SdkError):
                sdk.https_url(url)

    def test_hash_mismatch_rejected(self):
        with patch.object(sdk, "download", return_value=b"changed"), self.assertRaises(sdk.SdkError):
            sdk.verified_download("https://example.com/file", {"size": 7, "sha256": "0" * 64})

    def test_zip_deterministic(self):
        files = {"plugin.json": b"{}", "ui.xml": b"<panel/>", "rules.yaml": b""}
        one = sdk.zip_bytes(files)
        self.assertEqual(one, sdk.zip_bytes(dict(reversed(list(files.items())))))
        with zipfile.ZipFile(io.BytesIO(one)) as archive:
            self.assertEqual(archive.namelist(), sorted(files))
            self.assertEqual(archive.read("ui.xml"), files["ui.xml"])

    def test_init_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "keep.txt").write_text("keep")
            with self.assertRaises(sdk.SdkError):
                sdk.initialize(directory, "example.panel", "yaml")
            self.assertEqual(Path(directory, "keep.txt").read_text(), "keep")

    def test_unpinned_raw_url_rejected(self):
        with self.assertRaises(sdk.SdkError):
            sdk.pinned_readme("https://raw.githubusercontent.com/example/repo/main/README.md")

    def test_api_rate_limit_uses_atom_commit_without_mutable_files(self):
        error = urllib.error.HTTPError("https://api.github.com/", 403, "Rate limited", {}, None)
        atom = ("<feed><entry><id>tag:github.com,2008:Grit::Commit/" + "a" * 40 + "</id></entry></feed>").encode()
        with patch.object(sdk, "download", side_effect=[error, atom]) as download:
            result = sdk.pinned_readme("https://github.com/example/repository")
        self.assertEqual(result, "https://raw.githubusercontent.com/example/repository/" + "a" * 40 + "/README.md")
        self.assertEqual(download.call_args_list[-1].args[0], "https://github.com/example/repository/commits/HEAD.atom")

    def test_rate_limited_invalid_atom_does_not_use_head_files(self):
        error = urllib.error.HTTPError("https://api.github.com/", 429, "Rate limited", {}, None)
        with patch.object(sdk, "download", side_effect=[error, b"<feed/>"]), self.assertRaises(sdk.SdkError):
            sdk.pinned_readme("https://github.com/example/repository")


@unittest.skipUnless(os.environ.get("ROLITHAX_SCHEMA_CLI"), "Native CLI is required for integration tests")
class NativeTests(unittest.TestCase):
    def test_yaml_and_lua_use_native_engine(self):
        for version in (2, 3):
            for engine in ("yaml", "lua"):
                with self.subTest(engine=engine, version=version), tempfile.TemporaryDirectory() as directory:
                    sdk.initialize(directory, "example.test-panel", engine, version)
                    info, _, _ = sdk.validate(directory)
                    self.assertEqual(info["engine"], engine)
                    self.assertEqual(info["sdkVersion"], version)
                    sdk.replay(directory, Path(directory, "fixtures/logs.txt"), Path(directory, "fixtures/expected.json"))
                    package = Path(directory, "plugin.zip")
                    sdk.pack(directory, package)
                    self.assertEqual(sdk.check(package)["id"], "example.test-panel")

    def test_sdk3_asset_check_is_offline_and_detects_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            sdk.initialize(directory, "example.test-panel", "yaml")
            asset = Path(directory, "assets/icon.png")
            with patch.object(sdk, "download", side_effect=AssertionError("network used")):
                self.assertEqual(sdk.check(directory)["sdkVersion"], 3)
                asset.write_bytes(asset.read_bytes()[:-1] + b"x")
                with self.assertRaises(sdk.SdkError):
                    sdk.check(directory)

    def test_replay_calls_sdk3_lua_result_handlers(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            sdk.initialize(target, "example.test-panel", "lua")
            script = target / "business.lua"
            original = script.read_text(encoding="utf-8")
            script.write_text(original.rstrip()[:-1] + ",\n"
                'on_preflight=function(result) return {lifecycle=result.version} end,\n'
                'on_action=function(result) return {last=result.message} end\n}', encoding="utf-8")
            events = target / "events.json"
            events.write_text(json.dumps([
                {"eventType": "preflight", "result": {"version": "1.2.3"}},
                {"eventType": "action", "result": {"message": "completed"}},
            ]), encoding="utf-8")
            snapshot = sdk.replay(target, target / "fixtures/logs.txt", events=events)
            self.assertEqual(snapshot["values"], {"lifecycle": "1.2.3", "last": "completed"})

    def test_v2_fetch_downloads_external_media_and_preserves_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, output = root / "project", root / "output.zip"
            sdk.initialize(project, "example.panel", "yaml")
            manifest_file = project / "plugin.json"
            manifest = sdk.read_json(manifest_file.read_text(encoding="utf-8"))
            content = (project / "assets/icon.png").read_bytes()
            manifest["assets"][0].pop("path")
            manifest["assets"][0]["url"] = "https://example.org/external.png"
            manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
            package = root / "original.zip"
            sdk.pack(project, package)
            original = package.read_bytes()
            catalog = modern_catalog()
            entry = catalog["plugins"][0]
            entry.update({key: manifest[key] for key in ("name", "description", "authors", "license", "homepage")})
            entry["icon"] = {key: manifest["assets"][0][key] for key in ("mediaType", "size", "sha256", "url")}
            entry["source"] = {"type": "package", "url": "https://example.org/plugin.zip", **sdk.digest(original)}
            with patch.object(sdk, "download", side_effect=[v2_fence(catalog).encode(), original, content]):
                result = sdk.fetch("https://example.org/README.md", "example.panel", output)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual((Path(result["assetCache"]) / sdk.digest(content)["sha256"]).read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
