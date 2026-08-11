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

from __future__ import annotations

import logging
import os
import socket
import sys
import time
from functools import partial
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ImportError:
    import tomli as tomllib

import tomli_w
from jinja2 import Environment, StrictUndefined

from yr.cli.const import DEFAULT_CONFIG_TEMPLATE_PATH, DEFAULT_VALUES_TOML, SESSIONS_DIR, StartMode
from yr.cli.utils import check_port, get_ip, get_total_memory_mb, trim_hostname

logger = logging.getLogger(__name__)
print_logger = logging.getLogger("print")


def render_toml_bool(value: Any, setting: str) -> str:
    if not isinstance(value, bool):
        raise ValueError(f"{setting} must be a boolean")
    return str(value).lower()


def create_jinja_environment(port_policy: str = "RANDOM") -> Environment:
    env = Environment(
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        newline_sequence="\n",
    )
    env.filters["check_port"] = partial(check_port, port_policy=port_policy)
    env.filters["toml_bool"] = render_toml_bool
    return env


def build_runtime_context(yr_package_path: Path) -> dict[str, Any]:
    hostname = socket.gethostname()
    pid = os.getpid()
    node_id = f"{trim_hostname()}-{pid}"
    timestamp = time.time()
    time_str = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    return {
        "yr_package_path": yr_package_path,
        "hostname": hostname,
        "pid": pid,
        "node_id": node_id,
        "cpu_millicores": os.cpu_count() * 1000,
        "memory_num_mb": get_total_memory_mb(),
        "ip": get_ip(),
        "timestamp": timestamp,
        "time": time_str,
        "deploy_path": Path(f"{SESSIONS_DIR}/{time_str}"),
        "cwd": Path.cwd(),
        "ld_library_path": os.environ.get("LD_LIBRARY_PATH", ""),
        "python_path": os.environ.get("PYTHONPATH", ""),
    }


def _get_template_error_lineno(exc: Exception) -> Optional[int]:
    lineno = getattr(exc, "lineno", None)
    if lineno is not None:
        return lineno

    traceback = exc.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename == "<template>":
            lineno = traceback.tb_lineno
        traceback = traceback.tb_next
    return lineno


def render_user_config_template(template_path: Path, cli_dir: Path) -> str:
    template_path = Path(template_path)
    try:
        template_text = template_path.read_text()
    except OSError as exc:
        raise ValueError(f"Failed to read template '{template_path}': {exc}") from exc

    runtime_context = build_runtime_context(Path(cli_dir).parent)
    jinja_env = create_jinja_environment()
    jinja_env.globals.update(runtime_context)
    jinja_env.globals["env"] = dict(os.environ)

    try:
        rendered_text = jinja_env.from_string(template_text).render()
    except Exception as exc:
        lineno = _get_template_error_lineno(exc)
        location = f" at line {lineno}" if lineno is not None else ""
        raise ValueError(
            f"Failed to render template '{template_path}'{location}: {exc}"
        ) from exc

    try:
        tomllib.loads(rendered_text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"Invalid TOML rendered from template '{template_path}': {exc}"
        ) from exc
    return rendered_text


def normalize_deploy_path(deploy_path: str | Path, cwd: Path) -> Path:
    path = Path(deploy_path).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve(strict=False)


class ConfigResolver:
    def __init__(
        self,
        config_path: Path,
        cli_dir: Path,
        mode: Optional[StartMode] = None,
        overrides: Optional[tuple[str, ...]] = None,
        render: Optional[bool] = True,
        env_subst_keys: Optional[tuple[str, ...]] = None,
        port_policy: str = "RANDOM",
    ) -> None:
        self.mode = mode
        self.yr_package_path = cli_dir.parent
        self.env_subst_keys = env_subst_keys
        # full wheel bundles binaries under yr/inner/; sanbao's yr/inner/ holds
        # only the legacy run_yr script, so detect by a binary dir unique to full.
        inner = self.yr_package_path / "inner"
        self.is_full_layout = (inner / "third_party").is_dir()
        self.binary_root = inner if self.is_full_layout else self.yr_package_path
        # full nests datasystem worker+lib under datasystem/service/.
        self.ds_bin = (
            self.binary_root / "datasystem" / "service"
            if self.is_full_layout
            else self.binary_root / "datasystem"
        )
        # full places faas under pattern/pattern_faas/ instead of faas/.
        self.faas_root = (
            self.binary_root / "pattern" / "pattern_faas"
            if self.is_full_layout
            else self.binary_root / "faas"
        )
        if render:
            self.runtime_context = self._build_runtime_context()
            self.jinja_env = create_jinja_environment(port_policy)
            self.jinja_env.globals.update(
                {
                    "yr_package_path": self.binary_root,
                    "ds_bin": self.ds_bin,
                    "faas_root": self.faas_root,
                    "hostname": self.runtime_context["hostname"],
                    "pid": self.runtime_context["pid"],
                    "node_id": self.runtime_context["node_id"],
                    "cpu_millicores": self.runtime_context["cpu_millicores"],
                    "memory_num_mb": self.runtime_context["memory_num_mb"],
                    "ip": self.runtime_context["ip"],
                    "timestamp": self.runtime_context["timestamp"],
                    "time": self.runtime_context["time"],
                    "deploy_path": f"{SESSIONS_DIR}/{self.runtime_context['time']}",
                    "ld_library_path": self.runtime_context["ld_library_path"],
                    "python_path": self.runtime_context["python_path"],
                },
            )
            self.rendered_config = self._load_config(config_path, overrides)
            self.runtime_context["deploy_path"] = Path(
                self.rendered_config["values"]["deploy_path"]
            )  # update deploy_path in runtime context with rendered value
        elif render is False:
            config_text = config_path.read_text()
            config_text = self._apply_env_subst(config_text)
            self.rendered_config = tomllib.loads(config_text)

    @staticmethod
    def print_default_config(values_path: Path, template_path: Path) -> None:
        with values_path.open() as f:
            values_str = f.read()
        with template_path.open() as f:
            template_str = f.read()
        print_logger.info(values_str)
        print_logger.info(template_str)

    def _load_config(self, config_path: Path, overrides: Optional[tuple[str, ...]] = None) -> dict[str, any]:
        # Load default templates
        values_path = self.yr_package_path / DEFAULT_VALUES_TOML
        template_path = self.yr_package_path / DEFAULT_CONFIG_TEMPLATE_PATH
        default_values_str = values_path.read_text()
        template_str = template_path.read_text()
        default_values = tomllib.loads(default_values_str)
        logger.debug(f"Default values: {default_values}")

        # Load user config if exists
        user_config = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
        logger.debug(f"User config from {config_path}: {user_config}" if user_config else "No user config found")

        # Apply command line overrides
        overrides_dict: dict[str, any] = {}
        if overrides:
            overrides_str = "\n".join(overrides)
            overrides_dict = tomllib.loads(overrides_str)
            logger.debug(f"User config from command line overrides: {overrides_dict}")
            # Merge overrides on top of user config (overrides win), including nested keys.
            merged_user_config = merge_config(user_config, overrides_dict, allow_unknown=True)
        else:
            merged_user_config = user_config
        logger.debug(f"Merged user config with overrides: {merged_user_config}")

        # Merge default and user values
        merged_values = merge_config(
            default_values.get("values", {}),
            merged_user_config.get("values", {}),
            allow_unknown=True,
        )
        # Keep the existing single-address behavior unless local_ip is explicit.
        if "host_ip" in merged_values and "local_ip" not in merged_user_config.get(
            "values", {}
        ):
            merged_values["local_ip"] = merged_values["host_ip"]
        logger.debug(f"Merged values: {merged_values}")

        # First pass: render root scalar values using runtime context
        rendered_root = {
            k: self.jinja_env.from_string(v).render() if isinstance(v, str) else v
            for k, v in merged_values.items()
            if not isinstance(v, dict)
        }
        logger.debug(f"Rendered root values: {rendered_root}")

        # Second pass: render full values including nested using rendered root
        full_values = {**merged_values, **rendered_root}
        values_template = tomli_w.dumps({"values": full_values})
        final_values = tomllib.loads(self.jinja_env.from_string(values_template).render(values=full_values))
        deploy_path = final_values.get("values", {}).get("deploy_path")
        if deploy_path is not None:
            final_values["values"]["deploy_path"] = str(
                normalize_deploy_path(deploy_path, self.runtime_context["cwd"])
            )
        logger.debug(f"Final rendered values: {final_values}")

        # Render config template and merge remaining user config
        rendered_config = tomllib.loads(self.jinja_env.from_string(template_str).render(**final_values))
        # Do not mutate user config; exclude `values` from the rendered-config merge.
        merged_user_config_without_values = {k: v for k, v in merged_user_config.items() if k != "values"}
        merged_config = (
            merge_config(rendered_config, merged_user_config_without_values, allow_unknown=True)
            if merged_user_config_without_values
            else rendered_config
        )

        return {"values": final_values.get("values", {}), **merged_config}

    def _build_runtime_context(self) -> dict[str, any]:
        """build runtime context, collect environment information"""
        return build_runtime_context(self.yr_package_path)

    def _apply_env_subst(self, text: str) -> str:
        if not self.env_subst_keys:
            return text
        envs = os.environ
        for key in self.env_subst_keys:
            value = envs.get(key)
            if value is None:
                raise ValueError(f"Environment variable '{key}' is not set but was requested by --env-subst.")
            logger.info(f"Substituting '{{{key}}}' in config.toml with value '{value}'.")
            text = text.replace(f"{{{key}}}", value)
        logger.debug(f"Config after environment substitution: {text}")
        return text


def type_convert(value: str) -> any:
    value = value.strip()
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
        return int(value)

    try:
        return float(value)
    except ValueError:
        pass

    if "," in value and value.startswith("[") and value.endswith("]"):
        return [type_convert(part.strip()) for part in value[1:-1].split(",")]

    return value


def merge_dot_notation(
    dot_notation_list: list[str],
    target_dict: dict[str, any],
    convert_types: bool = True,
) -> dict[str, any]:
    """Merge dot-notation key-value pairs into a target dictionary.

    Args:
        dot_notation_list: List of strings in format ["key.path=value"]
        target_dict: Target dictionary to merge into
        convert_types: Whether to automatically convert value types

    Returns:
        Merged dictionary
    """
    for item in dot_notation_list:
        if "=" not in item:
            logger.warning(f"skip wrong format argument: {item}")
            continue

        key_path, value = item.split("=", 1)
        keys = key_path.split(".")
        processed_value = type_convert(value) if convert_types else value

        current = target_dict
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        final_key = keys[-1]
        current[final_key] = processed_value

    return target_dict


def merge_config(
    default_config: dict[str, any],
    user_config: dict[str, any],
    nested_key_path: str = "",
    allow_unknown: bool = False,
) -> dict[str, any]:
    """Recursively merge user configuration into default configuration.

    This function merges a user-provided configuration dictionary into a default
    configuration dictionary.

    By default it validates that all keys in the user config exist in the default
    config and maintains the expected structure. When `allow_unknown=True`, keys
    not present in `default_config` are accepted and merged into the result.
    """
    for key, value in user_config.items():
        if key not in default_config:
            if allow_unknown:
                default_config[key] = value
                continue
            msg = f"Invalid config key: {nested_key_path + ('.' if nested_key_path else '')}{key}"
            raise ValueError(msg)
        if isinstance(value, dict):
            if not isinstance(default_config[key], dict):
                msg = f"Invalid config key: {nested_key_path + ('.' if nested_key_path else '')}{key}, expected dict"
                raise TypeError(msg)
            default_config[key] = merge_config(
                default_config[key],
                value,
                nested_key_path + ("." if nested_key_path else "") + key,
                allow_unknown=allow_unknown,
            )
        else:
            default_config[key] = value

    return default_config
