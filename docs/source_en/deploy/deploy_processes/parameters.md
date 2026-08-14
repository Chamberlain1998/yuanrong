# Deployment Parameters Table

This page lists the command-line options for the `yr` Python CLI and runtime parameters that can be overridden via TOML configuration file.

For configuration method, refer to the **Configuration** chapter in [Deployment Documentation](production/deploy.md).

## Command-Line Options

### Global Options

| Option | Description | Default Value |
|------|------|--------|
| `-c, --config PATH` | Specify `config.toml` path; if file does not exist, command exits with error. When not specified, attempts to read `/etc/yuanrong/config.toml`, uses built-in defaults if not exists. | `/etc/yuanrong/config.toml` |
| `-v, --verbose` | Enable DEBUG level log output. | Off |
| `--version` | Print version and exit. | None |
| `-h, --help` | Display help information and exit. | None|

### yr start

| Option | Description | Default Value |
|------|------|--------|
| `--master` | Start in master node mode. When not specified, starts in worker node (agent) mode. Master node starts by default: `etcd`, `ds_master`, `ds_worker`, `function_master`, `function_proxy`, `function_agent`; worker node starts by default: `ds_worker`, `function_proxy`, `function_agent`. | Worker node mode |
| `-s, --set KEY=VALUE` | Override configuration value from command line, can be specified repeatedly. `VALUE` must be a valid TOML literal. | None |
| `--master_address http(s)://HOST:PORT` | Only available in agent mode. Pulls service discovery information from specified `function_master` before startup, automatically generates `-s` overrides. Exits with error when used together with `--master`. When using `https://`, also need to provide TLS certificate path via `--config` or `-s`. | None |
| `--data-system-enable BOOL`, `--data_system_enable BOOL` | Enable the FunctionAgent DataSystem KV client. This does not disable ds-worker or other DataSystem consumers. | `false` |

### yr launch

| Option | Description | Default Value |
|------|------|--------|
| `COMPONENT` | Name of component to start, such as `etcd`, `ds_master`, `ds_worker`, `function_master`, `function_proxy`, `function_agent`, `frontend`, `dashboard`, `collector`, etc. | Required |
| `--inherit-env` | Inherit parent process environment variables. | Off |
| `--env-subst KEY` | Replace `{KEY}` in configuration file with environment variable `KEY` value. Can be specified repeatedly or comma-separated. | None |

### yr status / yr health

| Option | Description | Default Value |
|------|------|--------|
| `-f, --file SESSION_FILE` | Specify session file path. | `/tmp/yr_sessions/latest/session.json` |

### yr stop

| Option | Description | Default Value |
|------|------|--------|
| `--force` | Force stop: send SIGKILL to daemon and execute forceful termination on all component PIDs in session. | Off (default sends SIGTERM, wait up to 40 seconds) |
| `-f, --file SESSION_FILE` | Specify session file path. Path must exist. | `/tmp/yr_sessions/latest/session.json` |

### yr config dump

| Option | Description | Default Value |
|------|------|--------|
| `-s, --set KEY=VALUE` | Override configuration value from command line, can be specified repeatedly. | None |

## TOML Configuration Fields

The following fields can be overridden in user's `config.toml` under `[values]` or corresponding sub-tables, or temporarily overridden via `-s/--set` on command line. Default values with `{{ ... }}` indicate fields are automatically populated by CLI at startup, no manual configuration needed.

### [values] Core Fields

| Field | Default Value | Description |
|------|--------|------|
| `host_ip` | `{{ ip }}` | Node IP, automatically obtains current machine IP. |
| `deploy_path` | `{{ deploy_path }}` | Deployment directory, defaults to `/tmp/yr_sessions/<timestamp>`. |
| `cpu_num` | `{{ cpu_millicores }}` | Available CPU (millicores), uses all system CPUs by default. |
| `memory_num` | `{{ memory_num_mb }}` | Available memory (MB), collected from system by default. |
| `shared_memory_num` | `{{ memory_num_mb // 3 }}` | Data system available shared memory (MB), defaults to 1/3 of total memory. |
| `node_id` | `{{ node_id }}` | Node unique ID, format is `<hostname>-<pid>`, if manually configured please ensure global uniqueness. |
| `log_level` | `INFO` | Global log level, values: `DEBUG`, `INFO`, `WARN`, `ERROR`. |

### [values.runtime] Runtime Configuration

| Field | Default Value | Description |
|------|--------|------|
| `default_write_mode` | ``NONE_L2_CACHE`` | Default DataSystem write mode for Agent Session internal state. Supported values are ``NONE_L2_CACHE``, ``WRITE_THROUGH_L2_CACHE``, ``WRITE_BACK_L2_CACHE``, and ``NONE_L2_CACHE_EVICT``, or their numeric forms ``0``, ``1``, ``2``, and ``3``. An invalid value falls back to ``NONE_L2_CACHE``. The value is passed to runtimes as `YR_DATASYSTEM_DEFAULT_WRITE_MODE`. |

### [values.fs.log] Function System Logs

| Field | Default Value | Description |
|------|--------|------|
| `level` | `{{ values.log_level }}` | Function system log level. |
| `path` | `{{ values.deploy_path }}/logs/function_system` | Log directory. |
| `rolling_max_size` | `40` | Maximum single file size (MB). |
| `rolling_max_files` | `10` | Rolling retained file count. |
| `rolling_retention_days` | `30` | Log retention days. |
| `compress_enable` | `false` | Whether to compress historical logs. |
| `also_log_to_stderr` | `false` | Whether to output to stderr simultaneously. |

### [values.fs.tls] Function System TLS

| Field | Default Value | Description |
|------|--------|------|
| `enable` | `false` | Whether to enable function system TLS. |
| `base_path` | `""` | Certificate root directory, certificate files with relative paths are resolved based on this directory. |
| `ca_file` | `ca.crt` | CA certificate file name. |
| `cert_file` | `module.crt` | Certificate file name. |
| `key_file` | `module.key` | Private key file name. |

### [values.fs.metrics] Metrics Configuration

| Field | Default Value | Description |
|------|--------|------|
| `metrics_config` | `""` | metrics configuration JSON string. |
| `metrics_config_file` | `{{ values.yr_package_path }}/functionsystem/config/metrics/metrics_config.json` | metrics configuration file path. |

### [values.ds.curve] Data System Security

| Field | Default Value | Description |
|------|--------|------|
| `enable` | `false` | Whether to enable Curve/ZMQ security capabilities. |
| `base_path` | `""` | Curve key directory. |
| `cache_storage_auth_type` | `Noauth` | Cache authentication method, values: `Noauth`, `ZMQ`, `AK/SK`. |
| `cache_storage_auth_enable` | `false` | Whether to enable cache authentication. |

### [values.etcd] etcd Configuration

| Field | Default Value | Description |
|------|--------|------|
| `enable_multi_master` | `false` | Whether to enable multi-master mode (multiple etcd nodes). |
| `auth_type` | `Noauth` | etcd authentication type, values: `Noauth`, `TLS`. |
| `table_prefix` | `""` | etcd key prefix, used to isolate data when multiple clusters share the same etcd, configured value cannot contain spaces. |

#### [[values.etcd.address]] etcd Node Address

| Field | Default Value | Description |
|------|--------|------|
| `ip` | `{{ values.host_ip }}` | etcd node IP. |
| `port` | `{{ 32379\|check_port() }}` | etcd client port. |
| `peer_port` | `{{ 32380\|check_port() }}` | etcd peer port. |

#### [values.etcd.auth] etcd TLS Certificate

| Field | Default Value | Description |
|------|--------|------|
| `base_path` | `""` | Certificate root directory. |
| `ca_file` | `ca.crt` | CA file name. |
| `cert_file` | `server.crt` | Server certificate file name. |
| `key_file` | `server.key` | Server private key file name. |
| `client_cert_file` | `client.crt` | Client certificate file name. |
| `client_key_file` | `client.key` | Client private key file name. |

### Component Addresses and Ports

| Configuration Section | Field | Default Value | Description |
|--------|------|--------|------|
| `[values.ds_master]` | `ip` | `{{ values.host_ip }}` | ds_master address. |
| `[values.ds_master]` | `port` | `{{ 12123\|check_port() }}` | ds_master port. |
| `[values.ds_worker]` | `ip` | `{{ values.host_ip }}` | ds_worker address. |
| `[values.ds_worker]` | `port` | `{{ 31501\|check_port() }}` | ds_worker port. |
| `[values.function_master]` | `ip` | `{{ values.host_ip }}` | function_master address. |
| `[values.function_master]` | `global_scheduler_port` | `{{ 22770\|check_port() }}` | global scheduler port, `yr status` and `--master_address` use this port. |
| `[values.function_proxy]` | `ip` | `{{ values.host_ip }}` | function_proxy address. |
| `[values.function_proxy]` | `port` | `{{ 22772\|check_port() }}` | function_proxy HTTP port. |
| `[values.function_proxy]` | `grpc_listen_port` | `{{ 22773\|check_port() }}` | function_proxy gRPC port. |
| `[values.function_proxy]` | `tcp_tunnel_port` | ``{{ 22775\|check_port() }}`` | Generic TCP tunnel listen port used by Frontend SSH and wsproxy WebSocket passthrough. |
| `[values.function_proxy]` | `tcp_tunnel_max_connections` | ``1024`` | Maximum number of concurrent TCP tunnel connections. |
| `[values.function_agent]` | `ip` | `{{ values.host_ip }}` | function_agent address. |
| `[values.function_agent]` | `port` | `{{ 58866\|check_port() }}` | function_agent port. |
| `[values.function_agent]` | `data_system_enable` | `false` | Enable the FunctionAgent DataSystem KV client. Set this field to `true` when functions require the DataSystem KV client, such as a `ds://` working directory. |

### [values.frontend] Frontend SSH

| Field | Default Value | Description |
|------|--------|------|
| `ssh_enable` | ``false`` | Enable the frontend SSH service. Enabling SSH also enables the function proxy TCP tunnel. |
| `enable_tcp_tunnel` | ``false`` | Enable the TCP tunnel without enabling SSH, for example for wsproxy WebSocket passthrough. This setting does not require SSH keys. |
| `ssh_auth_enable` | ``true`` | Authenticate external SSH clients with `ssh_authorized_keys`. Disable this only in isolated test environments; disabling it does not enable password login. |
| `ssh_address` | ``:2222`` | Frontend SSH listen address. The default binds all interfaces; restrict exposure with a firewall or reverse proxy. |
| `ssh_host_key` | ``""`` | Frontend SSH server private key file. It must be readable when SSH is enabled. |
| `ssh_authorized_keys` | ``""`` | Authorized keys file for external SSH clients. It must be readable when SSH public-key authentication is enabled. |
| `ssh_backend_key` | ``""`` | Private key used by frontend to access the instance sshd. It must be readable when SSH is enabled. |
| `ssh_max_connections` | ``1024`` | Maximum number of concurrent frontend SSH connections. |
| `ssh_backend_public_key_dir` | ``""`` | Host directory containing a readable `authorized_keys`. When SSH is enabled, the directory is mounted read-only at ``/run/openyuanrong/ssh`` in Agent Sandboxes. |

:::{note}
When ``ssh_enable=true``, the TCP tunnel required by SSH is enabled even if ``enable_tcp_tunnel=false``.
:::

### [values.dashboard] Dashboard

| Field | Default Value | Description |
|------|--------|------|
| `ip` | `{{ values.host_ip }}` | Dashboard address. |
| `port` | `{{ 9080\|check_port() }}` | Dashboard HTTP port. |
| `grpc_port` | `{{ 9081\|check_port() }}` | Dashboard gRPC port. |
| `[values.dashboard.auth] enable` | `false` | Whether to enable Dashboard TLS. |
| `[values.dashboard.auth] cert_file` | `""` | Dashboard TLS certificate path (absolute path). |
| `[values.dashboard.auth] key_file` | `""` | Dashboard TLS private key path (absolute path). |
| `[values.dashboard.prometheus] address` | `""` | Prometheus address, format `ip:port`. |

### [mode.*] Component Start/Stop Control

Additional components can be enabled on top of default components via `[mode.master]` or `[mode.agent]`.

| Field | Type | Description |
|------|------|------|
| `frontend` | `bool` | Whether to start frontend component. |
| `dashboard` | `bool` | Whether to start dashboard component. |
| `collector` | `bool` | Whether to start collector component. |
| `meta_service` | `bool` | Whether to start meta service component. |
| `function_scheduler` | `bool` | Whether to start function_scheduler component (multi-master scenario). |

### [COMPONENT.args] Component Startup Parameters

Component command-line parameters can be overridden via `[COMPONENT.args]`, supported component names are the same as component names listed in `[mode.*]` and `etcd`, `ds_master`, `ds_worker`, `function_master`, `function_proxy`, `function_agent`.

Common override examples:

```toml
[function_master.args]
runtime_recover_enable = true
litebus_thread_num = 25
system_timeout = 2000000

[function_proxy.args]
enable_metrics = false
litebus_thread_num = 25
runtime_recover_enable = true

[ds_worker.args]
heartbeat_interval_ms = 120000
node_dead_timeout_s = 120
```

### [COMPONENT.env] Component Environment Variables

Environment variables can be injected into component processes via `[COMPONENT.env]`. Merge rules:

- Based on current shell environment variables.
- Then apply variables in `[COMPONENT.env]` (component configuration takes priority to override when same name).
- If `LD_LIBRARY_PATH` / `LD_PRELOAD` exist in both shell and component configuration, concatenate as `shell_value:config_value`.
