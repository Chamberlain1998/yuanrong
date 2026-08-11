#!/usr/bin/env python3
# coding=UTF-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
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

import logging
import sys
import time
from pathlib import Path
from typing import Optional

import click

import yr.cli.discovery as discovery
from yr.cli.config import ConfigResolver, render_user_config_template
from yr.cli.const import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_CONFIG_TEMPLATE_PATH,
    DEFAULT_VALUES_TOML,
    SESSION_JSON_PATH,
    StartMode,
)
from yr.cli.system_launcher import SystemLauncher
from yr.cli.checkpoint import CheckpointClient, get_frontend_address_from_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)-7s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)
logger = logging.getLogger(__name__)

print_logger = logging.getLogger("print")
print_logger.setLevel(logging.INFO)
print_logger.propagate = False
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(message)s"))
print_logger.addHandler(handler)


def print_version(ctx: click.Context, param: click.Parameter, value: bool) -> None:
    """Callback for --version flag.

    This is invoked by click when --version is present. It should be a no-op
    when the flag is not set or when click is just parsing help.
    """
    if not value or ctx.resilient_parsing:
        return

    from importlib.metadata import PackageNotFoundError, version

    try:
        package_version = version("openyuanrong")
    except PackageNotFoundError:
        package_version = version("openyuanrong-core")
    print_logger.info(f"yr version: {package_version}")
    ctx.exit(0)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "-c",
    "--config",
    "config_opt",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help=(
        f"Path to config.toml. If omitted, uses the default path ({DEFAULT_CONFIG_PATH})."
    ),
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Enable verbose logging (set log level to DEBUG).",
)
@click.option(
    "--version",
    is_flag=True,
    callback=print_version,
    expose_value=False,
    is_eager=True,
    help="Show version and exit",
)
@click.pass_context
def cli(ctx: click.Context, config_opt: Optional[Path], verbose: bool) -> None:
    """openYuanrong CLI.

    Tips:\n
    - Use `-h/--help` on any command to show detailed usage.\n
    - Most commands read config from `--config`.

    Example usage:\n
      - yr start --master

    Check https://pages.openeuler.openatom.cn/openyuanrong/docs/zh-cn/latest for more information.
    """
    ctx.ensure_object(dict)
    config_path = config_opt if config_opt else Path(DEFAULT_CONFIG_PATH)
    ctx.obj["config_path"] = config_path
    ctx.obj["cli_dir"] = Path(__file__).resolve().parent
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)


@cli.command(
    help="""Start openYuanrong cluster.

Runs in either master (control-plane) or agent (data-plane) mode.

Common patterns:\n
  - Start master: yr start --master\n
  - Start agent:  yr start\n
  - Override config: yr start -s 'values.log_level="DEBUG"'
""",
)
@click.option(
    "-s",
    "--set",
    "overrides",
    multiple=True,
    metavar="KEY=VALUE",
    help="""
        Override config values from command line (can be specified multiple times).
        The value must be a valid TOML literal.\n
        Examples:\n
          - Override a string: -s 'etcd.bin_path=\"/custom/etcd\"'\n
          - Override an int: -s 'values.ds_master.port=12123'\n
          - Override a table: -s 'values.ds_master={ip=\"192.0.2.9\",port=12123}'\n
          - Override a list: -s 'values.etcd.address=[{ip=\"192.0.2.9\",peer_port=32380,port=32379}]'
    """,
)
@click.option(
    "--master",
    "master_mode",
    is_flag=True,
    help="""
        Run in master mode (deploy control-plane components).\n
            - Master mode deploys etcd, ds_master, ds_worker, function_master, function_proxy and function_agent.\n
        If omitted, runs in agent mode (deploy data-plane components only).\n
            - Agent mode deploys ds_worker, function_proxy and function_agent.
    """,
)
@click.option(
    "--master_address",
    "function_master_addr",
    help="""
        Address of function_master in http(s)://host:port format for service discovery.\n
        If using https://, TLS cert paths must be provided via --config or -s in values.fs.tls
        (cert_file, key_file, ca_file, with optional base_path).
    """,
)
@click.option(
    "--function-proxy-merge-process-enable",
    "--function_proxy_merge_process_enable",
    "function_proxy_merge_process_enable",
    is_flag=True,
    help=(
        "Run function_agent/runtime_manager inside function_proxy and skip the "
        "standalone function_agent component."
    ),
)
@click.option(
    "--enable-runtime-launcher",
    "--enable_runtime_launcher",
    "enable_runtime_launcher",
    is_flag=True,
    help="Start runtime-launcher for sandbox container backend.",
)
@click.option(
    "--data-system-enable",
    "--data_system_enable",
    "data_system_enable",
    type=bool,
    default=None,
    help="Enable the FunctionAgent DataSystem KV client. Defaults to false.",
)
@click.option(
    "--port-policy",
    "--port_policy",
    "port_policy",
    type=click.Choice(["FIX", "RANDOM"], case_sensitive=False),
    default="RANDOM",
    show_default=True,
    help="Port allocation policy for default ports.",
)
@click.option(
    "--block",
    "block",
    type=bool,
    default=False,
    help="Keep yr start in the foreground after components become healthy.",
)
@click.pass_context
def start(ctx: click.Context, **kwargs) -> None:
    """Start the YuanRong system in master or agent mode."""
    overrides = kwargs["overrides"]
    master_mode = kwargs["master_mode"]
    function_master_addr = kwargs["function_master_addr"]
    function_proxy_merge_process_enable = kwargs["function_proxy_merge_process_enable"]
    enable_runtime_launcher = kwargs["enable_runtime_launcher"]
    data_system_enable = kwargs["data_system_enable"]
    port_policy = kwargs["port_policy"]
    block = kwargs["block"]
    config_path: Path = ctx.obj["config_path"]
    cli_dir: Path = ctx.obj["cli_dir"]
    mode = StartMode.MASTER if master_mode else StartMode.AGENT
    logger.info(f"Starting yr in {mode.value} mode")
    if function_master_addr:
        logger.info(f"Discovering services from function_master at {function_master_addr}...")
        try:
            overrides = discovery.resolve_overrides_from_function_master(
                config_path=config_path,
                cli_dir=cli_dir,
                mode=mode,
                overrides=overrides,
                function_master_addr=function_master_addr,
            )
            if overrides is None:
                raise ValueError("service discovery returned empty config overrides")
            logger.debug("Resolved %s config overrides from function_master", len(overrides))
        except Exception as e:
            logger.error(f"Failed to get service discovery info from function_master: {e}")
            ctx.exit(1)

    effective_overrides = list(overrides)
    if data_system_enable is not None:
        setting = "values.function_agent.data_system_enable"
        effective_overrides = [
            override
            for override in effective_overrides
            if override.partition("=")[0].strip() != setting
        ]
        effective_overrides.append(f"{setting}={str(data_system_enable).lower()}")
    if function_proxy_merge_process_enable:
        effective_overrides.extend(
            [
                f"mode.{mode.value}.function_agent=false",
                "function_proxy.args.enable_merge_process=true",
            ]
        )
    if enable_runtime_launcher:
        effective_overrides.extend(
            [
                f"mode.{mode.value}.runtime_launcher=true",
                "values.runtime_launcher.enable=true",
            ]
        )

    launcher = SystemLauncher(
        config_path,
        cli_dir,
        mode,
        overrides=tuple(effective_overrides),
        port_policy=port_policy,
    )
    launcher.load_components()
    success = launcher.start_all()
    if success and block:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass

    ctx.exit(0 if success else 1)


@cli.command(
    help="""
        Launch a single component based on config.toml. Intended for use as a container entrypoint.\n
        Notes:\n
        - This command runs one component and does not manage dependencies.\n
        - Use `yr start` if you want the full system with health checks and session tracking.
    """,
)
@click.option(
    "--inherit-env",
    "inherit_env",
    is_flag=True,
    help=("inherit environment variables from the parent process, default is False"),
)
@click.option(
    "--env-subst",
    "env_subst",
    multiple=True,
    metavar="KEY",
    help=(
        "Substitute {{KEY}} in config.toml with the value of environment variable KEY. "
        "Can be specified multiple times or as a comma-separated list, e.g. "
        "--env-subst A --env-subst B or --env-subst A,B."
    ),
)
@click.argument("component")
@click.pass_context
def launch(
    ctx: click.Context, inherit_env: bool, env_subst: tuple[str, ...], component: str
) -> None:
    from yr.cli.component.registry import LAUNCHER_CLASSES

    config_path: Path = ctx.obj["config_path"]
    cli_dir: Path = ctx.obj["cli_dir"]

    env_subst_keys: list[str] = []
    for item in env_subst:
        env_subst_keys.extend([k.strip() for k in item.split(",") if k.strip()])

    cfg = ConfigResolver(
        config_path, cli_dir, render=False, env_subst_keys=tuple(env_subst_keys)
    )
    launcher_cls = LAUNCHER_CLASSES.get(component)
    if launcher_cls is None:
        logger.error(f"Unknown component: {component}")
        ctx.exit(1)
    comp_launcher = launcher_cls(component, cfg)
    comp_launcher.exec(inherit_env)


@cli.command(help="Show components health status")
@click.option(
    "-f",
    "--file",
    "session_file",
    type=click.Path(dir_okay=False, path_type=Path, exists=True),
    help=(
        f"Path to session file (default: {SESSION_JSON_PATH}). "
        "This file is created when `yr start` succeeds and is used by status/stop."
    ),
)
@click.pass_context
def health(ctx: click.Context, session_file: Optional[str]) -> None:
    config_path: Path = ctx.obj["config_path"]
    cli_dir: Path = ctx.obj["cli_dir"]

    launcher = SystemLauncher(
        config_path, cli_dir, session_file=session_file, render=None
    )
    ok = launcher.health()
    ctx.exit(0 if ok else 1)


@cli.command(help="Show system status")
@click.option(
    "-f",
    "--file",
    "session_file",
    type=click.Path(dir_okay=False, path_type=Path, exists=True),
    help=(
        f"Path to session file (default: {SESSION_JSON_PATH}). "
        "Master mode can additionally query the global scheduler for resources."
    ),
)
@click.pass_context
def status(ctx: click.Context, session_file: Optional[str]) -> None:
    config_path: Path = ctx.obj["config_path"]
    cli_dir: Path = ctx.obj["cli_dir"]

    launcher = SystemLauncher(
        config_path, cli_dir, session_file=session_file, render=None
    )
    ok = launcher.status()
    ctx.exit(0 if ok else 1)


@cli.command(help="Stop system components")
@click.option(
    "--force",
    is_flag=True,
    help="Force stop components (SIGKILL instead of SIGTERM).",
)
@click.option(
    "-f",
    "--file",
    "session_file",
    type=click.Path(dir_okay=False, path_type=Path, exists=True),
    help=(
        f"Path to session file (default: {SESSION_JSON_PATH}). "
        "Use this when your session file is stored in a non-default location."
    ),
)
@click.pass_context
def stop(ctx: click.Context, force: bool, session_file: Optional[str]) -> None:
    config_path: Path = ctx.obj["config_path"]
    cli_dir: Path = ctx.obj["cli_dir"]
    logger.info("Stopping yr system components...")
    launcher = SystemLauncher(
        config_path, cli_dir, session_file=session_file, render=None
    )
    if force:
        logger.warning("Force stopping components...")
        daemon_ok = launcher.stop_daemon_from_session(force)
        components_ok = launcher.stop_components_from_session(force)
        ok = daemon_ok and components_ok
    else:
        ok = launcher.stop_daemon_from_session()
    ctx.exit(0 if ok else 1)


@cli.group(help="Config related commands")
@click.pass_context
def config(ctx: click.Context) -> None:
    pass


@config.command(name="dump", help="Dump merged config")
@click.option(
    "-s",
    "--set",
    "overrides",
    multiple=True,
    metavar="KEY=VALUE",
    help=(
        "Override config values from command line. Can be specified multiple times, "
        "e.g. -s etcd.bin_path=/custom/path -s etcd.health_check.enable=false"
    ),
)
@click.pass_context
def config_dump(ctx: click.Context, overrides: tuple[str, ...]) -> None:
    import tomli_w

    config_path: Path = ctx.obj["config_path"]
    cli_dir: Path = ctx.obj["cli_dir"]

    cfg = ConfigResolver(config_path, cli_dir, overrides=overrides)
    print_logger.info(tomli_w.dumps(cfg.rendered_config))


@config.command(name="template", help="Print config template")
@click.pass_context
def config_template(ctx: click.Context) -> None:
    config_path: Path = ctx.obj["config_path"]
    cli_dir: Path = ctx.obj["cli_dir"]

    cfg = ConfigResolver(config_path, cli_dir)
    values_path = cli_dir.parent / DEFAULT_VALUES_TOML
    template_path = cli_dir.parent / DEFAULT_CONFIG_TEMPLATE_PATH
    cfg.print_default_config(values_path, template_path)


@config.command(name="render", help="Render a user Jinja template to TOML")
@click.option(
    "-t",
    "--template",
    "template_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to the user Jinja template.",
)
@click.option(
    "-o",
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write rendered TOML to this path instead of stdout.",
)
@click.pass_context
def config_render(
    ctx: click.Context,
    template_path: Path,
    output_path: Optional[Path],
) -> None:
    cli_dir: Path = ctx.obj["cli_dir"]
    try:
        rendered_text = render_user_config_template(template_path, cli_dir)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if output_path is None:
        click.echo(rendered_text, nl=False)
        return

    try:
        output_path.write_text(rendered_text)
    except OSError as exc:
        raise click.ClickException(
            f"Failed to write template '{template_path}' to '{output_path}': {exc}"
        ) from exc


@cli.group(help="Checkpoint management commands")
@click.option(
    "-f",
    "--file",
    "session_file",
    type=click.Path(dir_okay=False, path_type=Path, exists=True),
    help=(
        f"Path to session file (default: {SESSION_JSON_PATH}). "
        "This file is created when `yr start` succeeds."
    ),
)
@click.pass_context
def checkpoint(ctx: click.Context, session_file: Optional[str]) -> None:
    pass


def _get_checkpoint_client(
    session_file: Optional[str],
) -> tuple[Optional[CheckpointClient], Optional[str]]:
    """Get checkpoint client and return error message if failed."""
    session_path = Path(session_file) if session_file else Path(SESSION_JSON_PATH)
    addr = get_frontend_address_from_session(session_path)
    if not addr:
        return None, "Failed to get frontend address from session file"
    return CheckpointClient(addr[0], addr[1]), None


@checkpoint.command(name="list", help="List checkpoints by function key or tenant")
@click.option(
    "--tenant-id",
    "tenant_id",
    required=True,
    help="Tenant ID",
)
@click.option(
    "--function-type",
    "function_type",
    help="Function type (e.g., moduleName.className for actors)",
)
@click.option(
    "--namespace",
    "namespace",
    help="Optional namespace",
)
@click.option(
    "--by-tenant",
    "by_tenant",
    is_flag=True,
    help="List checkpoints by tenant instead of by function key",
)
@click.pass_context
def checkpoint_list(ctx: click.Context, **kwargs) -> None:
    tenant_id = kwargs["tenant_id"]
    function_type = kwargs["function_type"]
    namespace = kwargs["namespace"]
    by_tenant = kwargs["by_tenant"]
    session_file = kwargs["session_file"]
    client, err = _get_checkpoint_client(session_file)
    if err:
        print_logger.error(err)
        ctx.exit(1)

    if by_tenant:
        if function_type or namespace:
            print_logger.error(
                "Cannot specify --function-type or --namespace with --by-tenant"
            )
            ctx.exit(1)
        result = client.list_by_tenant(tenant_id)
    else:
        if not function_type:
            print_logger.error("--function-type is required when not using --by-tenant")
            ctx.exit(1)
        result = client.list_by_function_key(tenant_id, function_type, namespace)

    if result.get("code") != 0:
        print_logger.error(f"Error: {result.get('message', 'unknown error')}")
        ctx.exit(1)

    checkpoint_ids = result.get("checkpointIDs", [])
    if checkpoint_ids:
        print_logger.info(f"Found {len(checkpoint_ids)} checkpoint(s):")
        for cid in checkpoint_ids:
            print_logger.info(f"  {cid}")
    else:
        print_logger.info("No checkpoints found")
    ctx.exit(0)


@checkpoint.command(name="delete", help="Delete a checkpoint")
@click.option(
    "--checkpoint-id",
    "checkpoint_id",
    required=True,
    help="Checkpoint ID to delete",
)
@click.pass_context
def checkpoint_delete(
    ctx: click.Context,
    checkpoint_id: str,
    session_file: Optional[str],
) -> None:
    client, err = _get_checkpoint_client(session_file)
    if err:
        print_logger.error(err)
        ctx.exit(1)

    result = client.delete(checkpoint_id)

    if result.get("code") != 0:
        print_logger.error(f"Error: {result.get('message', 'unknown error')}")
        ctx.exit(1)

    print_logger.info(f"Checkpoint {checkpoint_id} deleted successfully")
    ctx.exit(0)


def main(cmdargs: Optional[list[str]] = None) -> None:
    cli.main(args=cmdargs, prog_name="yr", standalone_mode=True)


if __name__ == "__main__":
    main()
