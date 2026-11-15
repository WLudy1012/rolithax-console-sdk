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


class RepositoryTests(unittest.TestCase):
    def test_valid_index(self):
        value = repository()
        self.assertEqual(sdk.read_repository(fenced(value))[0], value)

    def test_ambiguous_fence_rejected(self):
        with self.assertRaises(sdk.SdkError):
            sdk.read_repository(fenced(repository()) * 2)

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
        for engine in ("yaml", "lua"):
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as directory:
                sdk.initialize(directory, "example.test-panel", engine)
                info, _, _ = sdk.validate(directory)
                self.assertEqual(info["engine"], engine)
                sdk.replay(directory, Path(directory, "fixtures/logs.txt"), Path(directory, "fixtures/expected.json"))
                sdk.pack(directory, Path(directory, "plugin.zip"))
                self.assertTrue(Path(directory, "plugin.zip").is_file())


if __name__ == "__main__":
    unittest.main()
