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

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[2]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def python_builder_section(text: str) -> str:
    return (ROOT.parent / "images" / "Dockerfile.base").read_text()


class AioLayoutTests(unittest.TestCase):
    def test_runtime_image_is_split_out(self):
        runtime_dockerfile = ROOT / "Dockerfile.runtime"
        self.assertTrue(runtime_dockerfile.exists(), "Dockerfile.runtime should exist")
        runtime_text = runtime_dockerfile.read_text()
        base_text = python_builder_section(runtime_text)
        self.assertIn("ARG BASE_IMAGE=yr-base", runtime_text)
        self.assertIn("FROM ${BASE_IMAGE}", runtime_text)
        self.assertIn("openyuanrong_sdk", runtime_text)
        self.assertIn("ca-certificates", base_text)
        self.assertIn("python3-pip", base_text)
        self.assertIn("unzip", base_text)
        self.assertIn("uv==0.11.11", base_text)
        self.assertIn("mirrors.huaweicloud.com/repository/pypi/simple", base_text)
        self.assertIn('uv python install "${PYTHON_VERSION}"', base_text)
        self.assertIn('if ! uv python install "${PYTHON_VERSION}"', base_text)
        self.assertIn('Python-${PYTHON_VERSION}.tgz', base_text)
        self.assertIn('test "${python_version}" = "${PYTHON_VERSION}"', base_text)
        self.assertIn(
            'ln -sf "${python_bin_dir}/python${PYTHON_MAJOR_MINOR}" /usr/local/bin/python',
            base_text,
        )
        self.assertNotIn('ln -sf "${python_bin_dir}/python" /usr/local/bin/python', base_text)
        self.assertIn("COPY openyuanrong_sdk*.whl", runtime_text)
        self.assertIn("COPY openyuanrong-*.whl", runtime_text)
        self.assertIn('link.symlink_to(runtime)', runtime_text)
        self.assertIn("test -L /openyuanrong/runtime/service", runtime_text)
        self.assertIn("test -e /openyuanrong/runtime/service", runtime_text)
        self.assertIn('ln -sf "${python_bin_dir}/yrcli" /usr/local/bin/yrcli', runtime_text)
        self.assertNotIn("COPY pkg/", runtime_text)
        self.assertNotIn("runtime-launcher", runtime_text)
        self.assertNotIn(" AS python-builder", runtime_text)
        self.assertNotIn(" AS python-builder", base_text)
        self.assertIn("build-essential", base_text)

    def test_runtime_image_includes_libcurl(self):
        base_text = (ROOT.parent / "images" / "Dockerfile.base").read_text()
        self.assertIn("libcurl4", base_text)

    def test_main_image_is_split_out(self):
        main_dockerfile = ROOT / "Dockerfile.aio-yr"
        self.assertTrue(main_dockerfile.exists(), "Dockerfile.aio-yr should exist")
        main_text = main_dockerfile.read_text()
        self.assertIn("ARG CONTROLPLANE_IMAGE=yr-controlplane", main_text)
        self.assertIn("FROM ${CONTROLPLANE_IMAGE}", main_text)
        self.assertIn("dockerd", main_text)
        self.assertIn("ca-certificates", python_builder_section(main_text))
        self.assertNotIn("COPY openyuanrong_sdk*.whl", main_text)
        self.assertNotIn("COPY openyuanrong-*.whl", main_text)
        self.assertIn("COPY runtime-launcher /openyuanrong/runtime-launcher", main_text)
        self.assertIn("COPY --from=deploy services.yaml", main_text)
        self.assertNotIn("COPY pkg/", main_text)
        self.assertNotIn("openclaw", main_text)
        self.assertNotIn("claude-code", main_text)
        self.assertNotIn(" AS python-builder", main_text)

    def test_services_use_runtime_image(self):
        services_text = read_text("services.yaml")
        self.assertIn('imageurl: "aio-yr-runtime:latest"', services_text)
        self.assertIn("rrt:\n      cpu: 0\n      memory: 0\n      runtime: python3.9", services_text)
        self.assertIn('root: "/opt/uv/python"', services_text)
        self.assertNotIn("runtime: rust", services_text)
        self.assertNotIn("export RRT_HTTP_PORT=", services_text)
        self.assertNotIn("export RRT_TUNNEL_WS_PORT=", services_text)

    def test_traefik_routes_http_to_frontend(self):
        dynamic_text = read_text("traefik/dynamic.yml")
        traefik_text = read_text("traefik/traefik.yml")
        self.assertIn("web:", traefik_text)
        self.assertIn('__AIO_NODE_IP__:22770/global-scheduler/traefik/config', traefik_text)
        self.assertIn("http:", traefik_text)
        self.assertNotIn("etcd:", traefik_text)
        self.assertIn("frontend-root", dynamic_text)
        self.assertIn("PathPrefix(`/global-scheduler`)", dynamic_text)
        self.assertIn('url: "http://__AIO_NODE_IP__:8889"', dynamic_text)
        self.assertNotIn("tunnel-router", dynamic_text)
        self.assertNotIn("redirect-to-https", dynamic_text)
        self.assertNotIn("tls:", dynamic_text)

    def test_supervisord_entrypoint_starts_dockerd_before_supervisord(self):
        entrypoint_text = read_text("supervisord-entrypoint.sh")
        self.assertIn('AIO_NODE_IP="$(hostname -i | awk \'{print $1}\')"', entrypoint_text)
        self.assertIn("dockerd --host", entrypoint_text)
        self.assertIn("docker info", entrypoint_text)
        self.assertIn("docker load", entrypoint_text)
        self.assertIn(
            'sed -i "s/__AIO_NODE_IP__/${AIO_NODE_IP}/g" '
            "/openyuanrong/traefik/dynamic.yml /openyuanrong/traefik/traefik.yml",
            entrypoint_text,
        )
        self.assertIn("exec /usr/bin/supervisord", entrypoint_text)

    def test_supervisord_runs_yuanrong_in_blocking_mode(self):
        supervisord_text = read_text("supervisord.conf")
        self.assertIn("[program:yuanrong-master]", supervisord_text)
        self.assertIn("command=/usr/local/bin/start-yuanrong.sh", supervisord_text)
        self.assertNotIn("[program:seed-traefik-etcd]", supervisord_text)
        self.assertIn("autorestart=true", supervisord_text)

    def test_start_yuanrong_script_uses_container_ip(self):
        script_text = read_text("start-yuanrong.sh")
        self.assertIn('AIO_NODE_IP="$(hostname -i | awk \'{print $1}\')"', script_text)
        self.assertIn("--block true", script_text)
        self.assertIn('-s "values.host_ip=\\"${AIO_NODE_IP}\\""', script_text)
        self.assertIn("-s 'mode.master.frontend=true'", script_text)
        self.assertIn("-s 'values.frontend.port=8889'", script_text)
        self.assertIn(
            '-s "values.frontend.sandbox_router_enable_jwt=${ENABLE_TOKEN}"',
            script_text,
        )
        self.assertIn(
            '-s "values.frontend.sandbox_router_validate_iam=${ENABLE_TOKEN}"',
            script_text,
        )
        self.assertIn("-s 'values.function_scheduler.lease_port=8890'", script_text)
        self.assertIn("-s 'mode.master.meta_service=true'", script_text)
        self.assertIn("function_master.args.enable_traefik_provider=true", script_text)
        self.assertIn('function_master.args.traefik_http_entry_point="web"', script_text)
        self.assertIn("function_master.args.traefik_enable_tls=false", script_text)
        self.assertIn("function_master.args.traefik_forward_timeout_ms=3000", script_text)
        self.assertNotIn("--enable_traefik_provider", script_text)
        self.assertNotIn("--enable_traefik_registry true", script_text)
        self.assertNotIn("--faas_frontend_http_port", script_text)
        self.assertNotIn("--port_policy", script_text)
        self.assertNotIn('-a "${AIO_NODE_IP}"', script_text)
        self.assertNotIn("-a 127.0.0.1", script_text)

    def test_images_do_not_install_traefik_etcd_seed(self):
        self.assertNotIn("seed-traefik-etcd", read_text("Dockerfile"))
        self.assertNotIn("seed-traefik-etcd", read_text("Dockerfile.aio-yr"))
        self.assertFalse((ROOT / "seed-traefik-etcd.sh").exists())

    def test_supervisord_entrypoint_retries_vfs_when_dockerd_exits_early(self):
        entrypoint_lines = read_text("supervisord-entrypoint.sh").splitlines()
        kill_check_index = next(
            i for i, line in enumerate(entrypoint_lines) if "if ! kill -0" in line
        )
        retry_index = next(
            i for i, line in enumerate(entrypoint_lines) if "retrying with vfs" in line
        )

        intervening = entrypoint_lines[kill_check_index:retry_index]
        self.assertNotIn("        exit 1", intervening)
        self.assertIn("        break", intervening)

    def test_dockerd_entrypoint_falls_back_to_vfs(self):
        dockerd_text = read_text("dockerd-entrypoint.sh")
        self.assertIn("overlay2", dockerd_text)
        self.assertIn("vfs", dockerd_text)
        self.assertIn("start_dockerd", dockerd_text)

    def test_readme_uses_privileged_dind_instructions(self):
        readme_text = read_text("README.md")
        self.assertIn("--privileged", readme_text)
        self.assertNotIn("/var/run/docker.sock", readme_text)
        self.assertIn("Dockerfile.aio-yr", readme_text)
        self.assertIn("Dockerfile.runtime", readme_text)
        self.assertIn("build-images.sh", readme_text)

    def test_build_script_uses_output_as_docker_context(self):
        script_text = read_text("build-images.sh")
        self.assertIn('"${OUTPUT_DIR}"', script_text)
        self.assertIn('--build-context deploy="${SCRIPT_DIR}"', script_text)
        self.assertIn('DEPLOY_CONTEXT_DIR="${OUTPUT_DIR}/.yr-k8s-deploy"', script_text)
        self.assertIn("stage_controlplane_context", script_text)
        self.assertIn('k8s/bin/start-master.sh', script_text)
        self.assertIn('k8s/bin/start-frontend.sh', script_text)
        self.assertIn("yr-base", script_text)
        self.assertIn("yr-runtime", script_text)
        self.assertIn("yr-controlplane", script_text)
        self.assertNotIn('"${REPO_ROOT}"', script_text)
        self.assertNotIn("/pkg/", script_text)

    def test_run_script_builds_and_starts_aio(self):
        script_path = ROOT / "run.sh"
        self.assertTrue(script_path.exists(), "run.sh should exist")
        script_text = script_path.read_text()
        self.assertNotIn("make all", script_text)
        self.assertNotIn("make image", script_text)
        self.assertIn("docker compose", script_text)
        self.assertIn("docker-compose.yml", script_text)
        self.assertNotIn("docker run", script_text)
        self.assertIn('AIO_CONTAINER_NAME:-aio-yr', script_text)

    def test_aio_compose_matches_runtime_requirements(self):
        compose_path = ROOT / "docker-compose.yml"
        self.assertTrue(compose_path.exists(), "docker-compose.yml should exist")
        compose_text = compose_path.read_text()
        self.assertIn("container_name: ${AIO_CONTAINER_NAME:-aio-yr}", compose_text)
        self.assertIn("image: ${AIO_IMAGE_NAME:-aio-yr:latest}", compose_text)
        self.assertIn('published: "${AIO_PORT:-38888}"', compose_text)
        self.assertIn("target: 8888", compose_text)
        self.assertIn("privileged: true", compose_text)
        self.assertIn("cgroup: host", compose_text)

    def test_host_sdk_script_uses_frontend_address_with_in_cluster_false(self):
        script_path = ROOT / "create-sandbox-host.sh"
        self.assertTrue(script_path.exists(), "create-sandbox-host.sh should exist")
        script_text = script_path.read_text()
        self.assertIn('server_address = os.environ["YR_HOST_SERVER_ADDRESS"]', script_text)
        self.assertIn("cfg.in_cluster = False", script_text)
        self.assertIn('YR_HOST_SERVER_ADDRESS:-127.0.0.1:38888', script_text)
        self.assertIn("openyuanrong_sdk-0.7.0.dev0-cp39-cp39-manylinux_2_34_x86_64.whl", script_text)

    def test_port_forward_verification_script_uses_detached_sandbox(self):
        script_path = ROOT / "verify-port-forward-host.sh"
        self.assertTrue(script_path.exists(), "verify-port-forward-host.sh should exist")
        script_text = script_path.read_text()
        self.assertIn('opt.custom_extensions["lifecycle"] = "detached"', script_text)
        self.assertIn("opt.port_forwardings = [PortForwarding(port=port)]", script_text)
        self.assertIn('["curl", "-sk", "-o", "-", "-w", "\\nHTTP_STATUS:%{http_code}\\n", url]', script_text)
        self.assertIn('yr.kill_instance(instance_id)', script_text)

    def test_rrt_runtime_does_not_expose_removed_stream_actions(self):
        runtime_dir = REPO_ROOT / "api/rust/rrt-daemon/src/runtime"
        dispatch_text = (runtime_dir / "dispatch.rs").read_text()
        module_text = (runtime_dir / "mod.rs").read_text()
        self.assertFalse((runtime_dir / "stream.rs").exists())
        self.assertNotIn("sandbox_stream_", dispatch_text)
        self.assertNotIn("mod stream;", module_text)

    def test_webterminal_uses_direct_sandbox_create_flow(self):
        webterm_text = (
            REPO_ROOT / "frontend/pkg/frontend/webui/webterm.go"
        ).read_text()
        self.assertIn("extractSandboxCreateInstanceId", webterm_text)
        self.assertIn("fetch('%s/api/sandbox/create'", webterm_text)
        self.assertIn("parseTenantFromJWT(token) || tenant", webterm_text)

    def test_runtime_launcher_ports_field_uses_posix_proto_source_of_truth(self):
        legacy_launcher_proto = (
            REPO_ROOT
            / "functionsystem/runtime-launcher/api/proto/runtime/v1/runtime_launcher.proto"
        )
        interface_proto = (
            REPO_ROOT / "functionsystem/proto/posix/runtime_launcher_interface.proto"
        ).read_text()
        self.assertFalse(legacy_launcher_proto.exists())
        self.assertIn("repeated string ports = 11;", interface_proto)
        self.assertIn("string trace_id = 10;", interface_proto)


if __name__ == "__main__":
    unittest.main()
