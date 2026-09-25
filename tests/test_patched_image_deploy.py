"""No Docker daemon or private instance files are modified by these unit tests."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load("hermes_image_builder_test", ROOT / "scripts/build-hermes-patched-image.py")
rollout = load("hermes_image_rollout_test", ROOT / "scripts/rollout-hermes-patched-image.py")


class GatewayPatchTests(unittest.TestCase):
    def setUp(self):
        self.patch = builder.read_patch()
        self.source = b"# harmless file header\n" + self.patch.before + b"\n# harmless footer\n"

    def test_tracked_patch_is_restricted_to_two_gateway_cap_changes(self):
        self.assertEqual(self.patch.digest, builder.sha(builder.PATCH.read_bytes()))
        changed = builder.patch_source(self.source, self.patch)
        self.assertEqual(changed, self.source.replace(self.patch.before, self.patch.after))
        self.assertEqual(changed.count(b"262144"), 1)
        self.assertEqual(changed.count(b"256KB"), 1)
        self.assertEqual(changed.count(b"harmless"), 2)

    def test_rejects_gateway_hunk_ambiguity_or_already_patched(self):
        for source in [self.source + self.patch.before, self.source.replace(self.patch.before, self.patch.after),
                       self.source.replace(self.patch.before, b"")]:
            with self.subTest(source=source[:35]), self.assertRaises(builder.PreflightError):
                builder.patch_source(source, self.patch)

    def test_rejects_patch_with_new_gateway_change(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "patch"
            path.write_bytes(builder.PATCH.read_bytes().replace(b"262144", b"524288"))
            with self.assertRaises(builder.PreflightError):
                builder.read_patch(path)

    def test_rejects_modification_beneath_gateway_file(self):
        item = {"Name": "/hermes-test-hermes-1", "Mounts": [{"Type": "bind", "Destination": "/opt/data"}]}
        with mock.patch.object(builder, "run", return_value=f"C {builder.SOURCE}\n"), \
             mock.patch.object(builder, "cp_source") as docker_cp, self.assertRaises(builder.PreflightError):
            builder.verify_container(item, self.patch)
        docker_cp.assert_not_called()

    def test_rejects_unexpected_source_permissions_and_mount(self):
        item = {"Name": "/hermes-test-hermes-1", "Mounts": [{"Type": "bind", "Destination": "/opt/data"},
                                                               {"Type": "bind", "Destination": "/opt/hermes"}]}
        with mock.patch.object(builder, "run", return_value=""), self.assertRaises(builder.PreflightError):
            builder.verify_container(item, self.patch)
        item["Mounts"].pop()
        mode = tarfile.TarInfo(builder.SOURCE)
        mode.uid = mode.gid = 0
        mode.mode = 0o666
        with mock.patch.object(builder, "run", return_value=""), \
             mock.patch.object(builder, "cp_source", return_value=(self.source, mode)), \
             self.assertRaises(builder.PreflightError):
            builder.verify_container(item, self.patch)

    def test_cp_source_rejects_symlink_tar(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:") as archive:
            entry = tarfile.TarInfo(builder.SOURCE)
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/tmp/rogue"
            archive.addfile(entry)
        result = subprocess.CompletedProcess([], 0, stdout=buffer.getvalue(), stderr=b"")
        with mock.patch.object(builder.subprocess, "run", return_value=result), \
             self.assertRaises(builder.PreflightError):
            builder.cp_source("dummy")

    def test_plan_groups_only_identical_sources_per_immutable_image(self):
        def container(project, name, image):
            return {"Image": image, "Name": name, "State": {"Running": True},
                    "Config": {"Labels": {"com.docker.compose.service": "hermes", "com.docker.compose.project": project}},
                    "Mounts": [{"Type": "bind", "Destination": "/opt/data"}]}
        base = "sha256:" + "a" * 64
        containers = [container("hermes-alpha", "/alpha", base), container("hermes-beta", "/beta", base)]
        image = {base: {"Config": {"Labels": {"org.opencontainers.image.revision": "1" * 40}}}}
        with mock.patch.object(builder, "fleet_containers", return_value=containers), \
             mock.patch.object(builder, "inspect_images", return_value=image), \
             mock.patch.object(builder, "verify_base_source") as base_verify, \
             mock.patch.object(builder, "verify_container", side_effect=[(self.source, b"new"), (self.source, b"new")]):
            patch, entries, sources = builder.plan()
        self.assertEqual(len(entries), 2)
        self.assertEqual(sources, {base: self.source})
        base_verify.assert_called_once_with(base, self.source)
        with mock.patch.object(builder, "fleet_containers", return_value=containers), \
             mock.patch.object(builder, "inspect_images", return_value=image), \
             mock.patch.object(builder, "verify_base_source"), \
             mock.patch.object(builder, "verify_container", side_effect=[(self.source, b"new"), (self.source+b"drift", b"new")]), \
             self.assertRaises(builder.PreflightError):
            builder.plan()

    def test_build_refuses_existing_tag_before_docker_build(self):
        image = "sha256:" + "a" * 64
        entry = {"base_image": image}
        with mock.patch.object(builder, "run", return_value="existing image"), \
             mock.patch.object(builder, "verify_built_image") as verify, \
             self.assertRaises(builder.PreflightError):
            builder.build(self.patch, [entry], {image: self.source})
        verify.assert_not_called()

    def test_rollout_read_only_validates_control(self):
        with tempfile.TemporaryDirectory() as dirname:
            root = Path(dirname)
            instance = root / "instances" / "alpha"
            instance.mkdir(parents=True)
            (instance / "control.env").write_text(
                "COMPOSE_PROJECT_NAME=hermes-alpha\nHERMES_IMAGE=nousresearch/hermes-agent:latest\n"
                f"HERMES_DATA_DIR={instance}/hermes-data\n"
            )
            with mock.patch.object(rollout, "ROOT", root):
                info = rollout.validate_control({"project": "hermes-alpha"})
                self.assertEqual(info["old_image"], "nousresearch/hermes-agent:latest")
                (instance / "control.env").write_text("COMPOSE_PROJECT_NAME=hermes-alpha\nHERMES_IMAGE=rogue:latest\n")
                with self.assertRaises(rollout.layer.PreflightError):
                    rollout.validate_control({"project": "hermes-alpha"})
                (instance / "control.env").unlink()
                (instance / "control.env").symlink_to(root / "other")
                with self.assertRaises(rollout.layer.PreflightError):
                    rollout.validate_control({"project": "hermes-alpha"})


if __name__ == "__main__":
    unittest.main()
