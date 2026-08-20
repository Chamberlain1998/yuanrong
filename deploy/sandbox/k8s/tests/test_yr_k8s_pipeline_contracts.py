# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import pathlib
import subprocess
import tempfile
import unittest

from test_yr_k8s_layout import (
    BASH_BIN,
    PYTHON_BIN,
    ROOT,
    emit_dynamic_pipeline,
    index_pipeline_steps,
    pipeline_step_container,
)


class YrK8sPipelineContractsTests(unittest.TestCase):
    def test_python314_buildkite_execution_contract(self):
        packager = "registry.example.com/openyuanrong/sandbox-packager:test"
        bootstrap = emit_dynamic_pipeline(
            ENABLE_PYTHON314_BUILDER_BOOTSTRAP="true",
            SANDBOX_PACKAGER_IMAGE=packager,
        )
        product = emit_dynamic_pipeline(
            ENABLE_PYTHON314_BUILDER_BOOTSTRAP="false",
            SANDBOX_PACKAGER_IMAGE=packager,
            ENABLE_MACOS_SDK="true",
            ENABLE_LINUX_ARM="true",
            ENABLE_RUNTIME_X86="true",
            ENABLE_RUNTIME_ARM="true",
            ENABLE_SANDBOX_PACKAGE="true",
            ENABLE_SANDBOX_K8S_TEST="false",
            ENABLE_TEST_PYPI_PUBLISH="false",
            ENABLE_RUST_FUNCTIONSYSTEM_ST="false",
        )
        amd64_cp314_product = emit_dynamic_pipeline(
            ENABLE_PYTHON314_BUILDER_BOOTSTRAP="false",
            ENABLE_MACOS_SDK="true",
            ENABLE_LINUX_ARM="false",
            ENABLE_RUNTIME_X86="true",
            ENABLE_RUNTIME_ARM="false",
            ENABLE_SANDBOX_PACKAGE="true",
            ENABLE_SANDBOX_MANIFEST="false",
            ENABLE_SANDBOX_K8S_TEST="false",
            ENABLE_TEST_PYPI_PUBLISH="false",
            ENABLE_RUST_FUNCTIONSYSTEM_ST="false",
            SDK_PYTHON_VERSIONS="python3.14",
            SANDBOX_RUNTIME_IMAGE_PYTHON_VERSIONS="python3.14",
        )
        bootstrap_steps = index_pipeline_steps(bootstrap)
        product_steps = index_pipeline_steps(product)
        amd64_cp314_steps = index_pipeline_steps(amd64_cp314_product)
        bootstrap_keys = {
            "build-python314-builder-amd64",
            "build-python314-builder-arm64",
            "publish-python314-builder-manifest",
        }
        self.assertEqual(set(bootstrap_steps), bootstrap_keys)
        self.assertTrue(bootstrap_keys.isdisjoint(product_steps))
        self.assertIn(
            "build-sdk-amd64-cp314",
            amd64_cp314_steps["publish-sandbox-release-amd64"]["depends_on"],
        )
        self.assertNotIn(
            "build-sdk-amd64-cp311",
            amd64_cp314_steps["publish-sandbox-release-amd64"]["depends_on"],
        )
        self.assertIn("build-sdk-macos-arm64-cp314", amd64_cp314_steps)
        self.assertFalse(any("arm64" in key and "macos" not in key for key in amd64_cp314_steps))
        self.assertNotIn("publish-sandbox-manifest", amd64_cp314_steps)
        self.assertEqual(
            set(bootstrap_steps["publish-python314-builder-manifest"]["depends_on"]),
            bootstrap_keys - {"publish-python314-builder-manifest"},
        )
        for key in {
            "build-python314-builder-amd64",
            "publish-python314-builder-manifest",
        }:
            with self.subTest(bootstrap_executor=key):
                step = bootstrap_steps[key]
                self.assertEqual(pipeline_step_container(step)["image"], packager)

        standard_base = (
            "swr.cn-southwest-2.myhuaweicloud.com/yuanrong-dev/"
            "compile-ubuntu2004:v20260428_cmake33110"
        )
        existing_rust_builder = (
            "swr.cn-southwest-2.myhuaweicloud.com/yuanrong-dev/"
            "compile-ubuntu2004-rust:v20260507_x86_64"
        )
        self.assertEqual(
            bootstrap_steps["build-python314-builder-amd64"]["env"]["PYTHON314_BUILDER_BASE_IMAGE"],
            standard_base,
        )
        self.assertEqual(
            bootstrap_steps["build-python314-builder-arm64"]["env"]["PYTHON314_BUILDER_BASE_IMAGE"],
            standard_base,
        )
        self.assertEqual(
            pipeline_step_container(bootstrap_steps["build-python314-builder-arm64"])["image"],
            standard_base,
        )
        python314_builder = standard_base.replace(
            ":v20260428_cmake33110", ":v20260717_py3146_obs"
        )
        self.assertEqual(
            pipeline_step_container(product_steps["build-all-amd64"])["image"],
            python314_builder,
        )
        self.assertEqual(
            pipeline_step_container(product_steps["build-sdk-amd64-cp314"])["image"],
            python314_builder,
        )
        self.assertEqual(
            pipeline_step_container(product_steps["build-rrt-amd64"])["image"],
            existing_rust_builder,
        )

        amd64_docker_step_keys = {
            "publish-sandbox-release-amd64",
            "publish-sandbox-manifest",
            *{
                f"publish-runtime-amd64-{suffix}"
                for suffix in ("cp39", "cp310", "cp311", "cp312", "cp313", "cp314")
            },
        }
        for key in amd64_docker_step_keys:
            with self.subTest(product_executor=key):
                step = product_steps[key]
                container = pipeline_step_container(step)
                self.assertEqual(container["image"], packager)
                secret_names = {entry["name"] for entry in container["env"]}
                self.assertTrue(
                    {"SWR_USERNAME", "SWR_PASSWORD", "SWR_DOCKER_CONFIG_JSON"}.issubset(secret_names)
                )

        for key in {
            "publish-sandbox-release-arm64",
            *{f"publish-runtime-arm64-{suffix}" for suffix in ("cp39", "cp310", "cp311", "cp312", "cp313", "cp314")},
        }:
            with self.subTest(arm64_product_step=key):
                step = product_steps[key]
                self.assertEqual(pipeline_step_container(step)["image"], python314_builder)
                self.assertEqual(step["agents"]["linux_arch"], "arm64")
                self.assertEqual(
                    step["plugins"][0]["kubernetes"]["podSpec"]["nodeSelector"]["kubernetes.io/arch"],
                    "arm64",
                )
        bootstrap_arm = bootstrap_steps["build-python314-builder-arm64"]
        self.assertEqual(bootstrap_arm["agents"]["linux_arch"], "arm64")
        self.assertEqual(
            bootstrap_arm["plugins"][0]["kubernetes"]["podSpec"]["nodeSelector"]["kubernetes.io/arch"],
            "arm64",
        )

        cp314_sdk_keys = {
            "build-sdk-amd64-cp314",
            "build-sdk-arm64-cp314",
            "build-sdk-macos-arm64-cp314",
        }
        self.assertTrue(cp314_sdk_keys.issubset(product_steps))
        self.assertIn(
            "build-sdk-amd64-cp314",
            product_steps["publish-runtime-amd64-cp314"]["depends_on"],
        )
        self.assertIn(
            "build-sdk-arm64-cp314",
            product_steps["publish-runtime-arm64-cp314"]["depends_on"],
        )
        manifest_dependencies = set(product_steps["publish-sandbox-manifest"]["depends_on"])
        self.assertTrue(cp314_sdk_keys.issubset(manifest_dependencies))
        self.assertTrue(
            {"publish-runtime-amd64-cp314", "publish-runtime-arm64-cp314"}.issubset(
                manifest_dependencies
            )
        )

        repo = ROOT.parents[2]
        packager_dockerfile = (repo / "ci/sandbox-packager/Dockerfile").read_text()
        helper = repo / ".buildkite/docker_job_helpers.sh"
        manifest_script = (repo / ".buildkite/package_sandbox_manifest.sh").read_text()
        release_script = (repo / ".buildkite/package_sandbox_release.sh").read_text()
        sdk_verifier = (repo / ".buildkite/verify_python314_sdk_wheel.sh").read_text()
        builder_script = (repo / ".buildkite/build_python314_builder_image.sh").read_text()
        self.assertIn("ARG TARGETARCH", packager_dockerfile)
        self.assertIn('arm64) HELM_ARCH="arm64"; KUBECTL_ARCH="arm64"', packager_dockerfile)
        self.assertTrue(helper.is_file())
        helper_text = helper.read_text()
        self.assertIn("overlay2", helper_text)
        self.assertIn("vfs", helper_text)
        self.assertIn("Docker daemon failed", helper_text)
        self.assertIn("verify_image_manifest.py", manifest_script)
        self.assertIn("require_cp314_sdk_records", manifest_script)
        self.assertIn("image-manifest-evidence.tsv", manifest_script)
        self.assertIn("EXPECTED_SDK_VERSION", release_script)
        self.assertIn('installed_version == expected_version', release_script)
        self.assertIn('wheel_listing="$(unzip -l "${wheel}")"', sdk_verifier)
        self.assertNotIn('unzip -l "${wheel}" |', sdk_verifier)
        self.assertIn('if [ "${VARIANT}" = compile ]; then', builder_script)

    def test_image_manifest_validator_rejects_wrong_platform_and_duplicates(self):
        verifier = ROOT.parents[2] / ".buildkite/verify_image_manifest.py"
        self.assertTrue(verifier.is_file())
        digest_amd64 = "sha256:" + "a" * 64
        digest_arm64 = "sha256:" + "b" * 64
        final_digest = "sha256:" + "c" * 64
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = pathlib.Path(tmpdir)
            source = tmp / "source.json"
            source.write_text(
                json.dumps(
                    {
                        "Descriptor": {
                            "digest": digest_amd64,
                            "platform": {"os": "linux", "architecture": "amd64"},
                        }
                    }
                )
            )
            evidence = tmp / "evidence.tsv"
            source_args = [
                str(PYTHON_BIN),
                str(verifier),
                "source",
                "--input",
                str(source),
                "--image",
                "registry.example.com/yr-runtime:test-amd64",
                "--evidence",
                str(evidence),
            ]
            subprocess.run(
                [*source_args, "--expected-platform", "linux/amd64"],
                check=True,
                capture_output=True,
                text=True,
            )
            wrong_source = subprocess.run(
                [*source_args, "--expected-platform", "linux/arm64"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(wrong_source.returncode, 0)

            final = tmp / "final.json"
            final.write_text(
                json.dumps(
                    {
                        "manifests": [
                            {
                                "digest": digest_amd64,
                                "platform": {"os": "linux", "architecture": "amd64"},
                            },
                            {
                                "digest": digest_arm64,
                                "platform": {"os": "linux", "architecture": "arm64"},
                            },
                        ]
                    }
                )
            )
            final_args = [
                str(PYTHON_BIN),
                str(verifier),
                "final",
                "--input",
                str(final),
                "--image",
                "registry.example.com/yr-runtime:test",
                "--digest",
                final_digest,
                "--expected-platform",
                "linux/amd64",
                "--expected-platform",
                "linux/arm64",
                "--evidence",
                str(evidence),
            ]
            subprocess.run(final_args, check=True, capture_output=True, text=True)
            duplicate = json.loads(final.read_text())
            duplicate["manifests"][1]["platform"]["architecture"] = "amd64"
            final.write_text(json.dumps(duplicate))
            wrong_final = subprocess.run(final_args, check=False, capture_output=True, text=True)
            self.assertNotEqual(wrong_final.returncode, 0)
            evidence_text = evidence.read_text()
            self.assertIn(digest_amd64, evidence_text)
            self.assertIn(final_digest, evidence_text)
            self.assertIn("linux/amd64,linux/arm64", evidence_text)

    def test_manifest_publish_requires_cp314_metadata_before_registry_mutation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = pathlib.Path(tmpdir)
            docker_log = tmp / "docker.log"
            fake_docker = tmp / "docker"
            fake_docker.write_text(
                "#!/usr/bin/env bash\n"
                'printf "%s\\n" "$*" >>"${DOCKER_LOG}"\n'
                "exit 0\n"
            )
            fake_docker.chmod(0o755)
            fake_agent = tmp / "buildkite-agent"
            fake_agent.write_text("#!/usr/bin/env bash\nexit 0\n")
            fake_agent.chmod(0o755)
            env = dict(os.environ)
            env.update(
                {
                    "PATH": f"{tmp}:{env['PATH']}",
                    "DOCKER_BIN": str(fake_docker),
                    "DOCKER_LOG": str(docker_log),
                    "SANDBOX_ARTIFACT_DIR": str(tmp / "artifacts"),
                    "BUILDKITE_STEP_KEY": "publish-sandbox-manifest",
                }
            )
            result = subprocess.run(
                [str(BASH_BIN), ".buildkite/package_sandbox_manifest.sh"],
                cwd=ROOT.parents[2],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Required Python 3.14 SDK metadata is missing or empty", result.stderr)
            self.assertFalse(docker_log.exists(), "registry mutation must not begin without cp314 records")

    def test_push_images_falls_back_when_platform_push_is_unsupported(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            docker_log = pathlib.Path(tmpdir) / "docker.log"
            fake_docker = pathlib.Path(tmpdir) / "docker"
            fake_docker.write_text(
                "#!/usr/bin/env bash\n"
                "echo \"$*\" >> \"${DOCKER_LOG}\"\n"
                "if [ \"$1\" = push ] && [ \"${2:-}\" = --help ]; then\n"
                "  echo 'Usage: docker push NAME[:TAG]'\n"
                "  exit 0\n"
                "fi\n"
                "if [ \"$1\" = image ] && [ \"${2:-}\" = inspect ]; then exit 0; fi\n"
                "if [ \"$1\" = push ] && [ \"${2:-}\" = --platform ]; then exit 42; fi\n"
                "exit 0\n"
            )
            fake_docker.chmod(0o755)
            result = subprocess.run(
                [str(ROOT / "push-images-swr.sh")],
                cwd=ROOT.parents[2],
                check=True,
                capture_output=True,
                text=True,
                env={
                    "PATH": f"{tmpdir}:/usr/bin:/bin",
                    "DOCKER_BIN": str(fake_docker),
                    "DOCKER_LOG": str(docker_log),
                    "YR_K8S_REGISTRY_REPO": "registry.example.com/openyuanrong",
                    "YR_K8S_IMAGE_TAG": "test-tag",
                    "YR_K8S_IMAGE_PLATFORM": "linux/arm64",
                    "YR_K8S_IMAGE_CACHE": "1",
                    "YR_K8S_IMAGE_CACHE_TAG": "cache-arm64",
                },
            )

            log_text = docker_log.read_text()
            self.assertNotIn("push --platform", log_text)
            self.assertIn("push registry.example.com/openyuanrong/yr-base:test-tag", log_text)
            self.assertIn("push registry.example.com/openyuanrong/yr-base:cache-arm64", log_text)
            self.assertIn("without platform flag", result.stderr)


if __name__ == "__main__":
    unittest.main()
