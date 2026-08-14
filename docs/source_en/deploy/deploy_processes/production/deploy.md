# Deploy openYuanrong

This section will introduce configurations for deploying openYuanrong on hosts in common scenarios. For detailed configuration parameter descriptions, please refer to [Deployment Parameters Table](../parameters.md).

## Deploy Using Command-Line Tool yr

`yr` is a Python command-line tool used for deploying openYuanrong on hosts, providing configuration rendering, component orchestration, health checks, status query and stop capabilities.

The deployment command is `yr start`. When executing this command, CLI will:

- Merge default configuration, custom configuration and command-line `yr -s` configuration and render templates. Among them, configuration priority from high to low is: command-line `yr -s` configuration, custom configuration and default configuration. Default configuration is defined in `yr/cli/values.toml` and `yr/cli/config.toml.jinja` files under openYuanrong installation path, can be viewed through command `yr config template`.
- Create deployment directory `/tmp/yr_sessions/<timestamp>/`, update symbolic link `/tmp/yr_sessions/latest` to point to this directory.
- Start components in dependency order and execute health checks.
- Generate session file `/tmp/yr_sessions/latest/session.json` for `status` / `health` / `stop` use.
- Generate master node information file `/tmp/yr_sessions/yr_current_master_info` for yr SDK parsing.

### Set Deployment Parameters Through Custom Configuration File

Refer to the following steps to set deployment parameters through custom configuration file:

1. Execute `yr config template` to view default configuration and built-in template structure.
2. Create custom configuration file `config.toml`, override configurations to be modified (no need to copy entire template).
3. Use `yr -c ${YOUR_FILE_PATH}/config.toml start --master` to specify path or `yr start --master` to read custom configuration from default path `/etc/yuanrong/config.toml` to start.

:::{Note}

When executing `yr start --master` or `yr start -c --master`, CLI will attempt to read custom configuration file from default path `/etc/yuanrong/config.toml`, only use built-in default values when it does not exist.

:::

#### Configure [mode.*] to Control Component Start/Stop

`yr start --master` is used to start master node, `yr start` is used to start worker node. Components started by default for master and worker nodes are as follows:

- Master node: `etcd`, `ds_master`, `ds_worker`, `function_master`, `function_proxy`, `function_agent`
- Worker node: `ds_worker`, `function_proxy`, `function_agent`

Additional components can be enabled through `[mode.master]` or `[mode.agent]`. For example, enabling `frontend`, `dashboard` and `collector` components on master node is configured as follows:

```toml
[mode.master]
frontend = true
dashboard = true
collector = true
```

#### Configure [values.*] to Override Runtime Parameters

`values` mainly carries runtime parameters (IP, port, TLS path, log path, etc.), and is used to render component configuration templates. Configuration for enabling function system and etcd TLS is as follows:

```toml
[values.fs.tls]
enable = true
base_path = "/yuanrong/myssl/cert/yr"
ca_file = "ca.crt"
cert_file = "module.crt"
key_file = "module.key"

[values.etcd]
auth_type = "TLS"

[values.etcd.auth]
base_path = "/yuanrong/myssl/cert/etcd"
ca_file = "ca.crt"
cert_file = "server.crt"
key_file = "server.key"
client_cert_file = "client.crt"
client_key_file = "client.key"
```

#### Configure Frontend SSH and the TCP Tunnel

Frontend SSH depends on the function proxy TCP tunnel. Enabling SSH requires a frontend server private key, an authorized keys file for external clients, a private key for frontend-to-instance access, and a directory containing the instance-side `authorized_keys`:

```toml
[values.frontend]
ssh_enable = true
ssh_auth_enable = true
ssh_address = ":2222"
ssh_host_key = "/etc/yuanrong/ssh/frontend_host_key"
ssh_authorized_keys = "/etc/yuanrong/ssh/frontend_authorized_keys"
ssh_backend_key = "/etc/yuanrong/ssh/backend_key"
ssh_backend_public_key_dir = "/etc/yuanrong/ssh/backend"
ssh_max_connections = 1024

[values.function_proxy]
tcp_tunnel_port = 22775
tcp_tunnel_max_connections = 1024
```

The `ssh_backend_public_key_dir` directory must contain a readable `authorized_keys`. The default SSH address, ``:2222``, binds all interfaces; restrict it with a firewall or reverse proxy in production.

If only wsproxy WebSocket passthrough is required, enable the TCP tunnel independently without configuring SSH keys:

```toml
[values.frontend]
enable_tcp_tunnel = true
```

#### Configure [component.*] to Override Component Startup Parameters

In addition to `mode.*` and `values.*`, component-level configurations can also be directly overridden (such as `etcd`, `ds_master`, `function_master`, `function_proxy`). These configurations will directly affect component process startup parameters, environment variables and health check behaviors.

Commonly overridable sub-tables include:

- `[COMPONENT.args]`: Component command-line parameters
- `[COMPONENT.env]`: Component environment variables
- `[COMPONENT.health_check]`: Health check configuration

Among them, component environment variables will merge environment variables configured in current shell and variables in `[COMPONENT.env]` (usually component configuration has higher priority and will override same-name shell variables). If `LD_LIBRARY_PATH` / `LD_PRELOAD` exist in both shell and component configuration, they will be concatenated as:
  `shell_value:config_value`.

For example, overriding some `function_master` startup parameters and appending library path configuration is as follows:

```toml
[function_master.args]
runtime_recover_enable = true
litebus_thread_num = 25
system_timeout = 2000000

[function_master.env]
LD_LIBRARY_PATH = "/opt/yr/lib"
```

Execute deployment using the following command, function_master component environment variable `LD_LIBRARY_PATH` value will be configured as `/opt/custom/lib:/opt/yr/lib`.

```sh
export LD_LIBRARY_PATH=/opt/custom/lib
yr -c ${YOUR_FILE_PATH}/config.toml start --master
```

#### Configuration Example

Enable frontend component on master node, override `function_proxy` and `ds_worker` startup parameters configuration is as follows:

```toml
[mode.master]
frontend = true

[values]
log_level = "DEBUG"

[values.function_proxy]
ip = "10.88.0.4"
port = "22772"
grpc_listen_port = "22773"

[function_proxy.args]
enable_metrics = false
litebus_thread_num = 25
runtime_recover_enable = true

[ds_worker.args]
heartbeat_interval_ms = 120000
node_dead_timeout_s = 120
```

Execute when deploying master node: `yr -c ${YOUR_FILE_PATH}/config.toml start --master`. If you need to temporarily override without changing custom configuration file, you can overlay `-s/--set` (e.g., `-s 'function_proxy.args.enable_metrics=true'`).

### Set Deployment Parameters Through `yr -s`

During deployment, you can set deployment parameters through `-s/--set`, it will override parameters in default configuration and custom configuration. `-s` can be specified repeatedly, each entry must be a legal TOML assignment statement (`KEY=VALUE`).

Examples:

- Override master address and port:

    `yr start --master -s 'values.ds_master.ip="10.88.0.9"' -s 'values.ds_master.port="12123"'`

- Override array table (etcd address list):

    `yr start --master -s 'values.etcd.address=[{ip="10.88.0.9",peer_port="32380",port="32379"}]'`

### Deploy Using Default Configuration

Deploy master node:

```bash
yr start --master
```

After master node starts successfully, it will print recommended commands for worker nodes to join the cluster and minimum configuration snippets, execute the command on worker node host.

```text
yr start --master_address http://x.x.x.x:xxxx
```

Master node address information can also be obtained through `cluster_info` field in `/tmp/yr_sessions/latest/session.json` file.

### Specify Node Available Resources During Deployment

You can configure total CPU and memory on openYuanrong master and worker nodes respectively. Part of memory is used for function stack, part is used for storing data objects.

- `values.cpu_num`: CPU total configuration parameter. Master node openYuanrong components default occupy 1 millicore (unit: 1/1000 core). If you hope master node does not run distributed tasks, only for management and scheduling, you can configure `values.cpu_num=1`.
- `values.memory_num`: Memory total configuration parameter.
- `values.shared_memory_num`: Memory amount for storing data objects (unit: MB). If application scenario has more data object storage needs, you can appropriately increase it.

openYuanrong also supports heterogeneous computing resources. By configuring `function_agent.args.npu_collection_mode` and `function_agent.args.gpu_collection_enable` parameters, openYuanrong will automatically collect NPU and GPU resources on nodes. In addition, you can also configure `function_agent.args.custom_resources` to define custom resources.

Example: Deploy master node not used for running distributed tasks.

```bash
yr start --master -s 'values.cpu_num=1'
```

Example: Deploy worker node to support collecting GPU resources and customize 5 ssd resources.

```bash
yr start --master_address http://x.x.x.x:xxxx \
-s 'function_agent.args.gpu_collection_enable=true' \
-s 'function_agent.args.custom_resources="{\"ssd\":5}"'
```

### Deploy Cluster to Support Function Services

Default configuration deployment only supports running stateless functions and stateful functions. To add support for function services, you need to start frontend, function scheduler and meta service three components when deploying master node. Worker node deployment does not need additional configuration.

```bash
yr start --master \
-s 'mode.master.frontend=true' \
-s 'mode.master.function_scheduler=true' \
-s 'mode.master.meta_service=true'
```

### Deploy Dashboard

Dashboard supports viewing logs, metrics, task running status and other data on the page. For detailed usage, refer to [Dashboard](../../../observability/dashboard.md) introduction.

Create custom configuration file `config.toml`, content as follows, configure `metrics_config_file` and `address` parameters according to actual situation.

```toml
[mode.master]
dashboard = true
collector = true
frontend = true

[mode.agent]
collector = true

[function_agent.args]
enable_metrics = true
metrics_config_file = "/home/metrics/config.json"

[values.dashboard.prometheus]
address = "10.88.0.3:9090"
```

Use the following command to deploy master node:

```bash
yr -c ${YOUR_FILE_PATH}/config.toml start --master
```

Refer to the following command to deploy worker node:

```bash
yr -c ${YOUR_FILE_PATH}/config.toml start --master_address http://x.x.x.x:xxxx
```

### Deploy Multiple Master Nodes

By default, only one master node is deployed. In high reliability scenarios, multiple master nodes can also be deployed in active-standby mode. Here takes common one-master-two-standby deployment mode, using built-in etcd as an example.

Deploy on three hosts separately using the following custom configuration file `config.toml`. Among them, replace ip with each host's ip, and specify etcd port used by each host, please ensure ports do not conflict:

```toml
[values.etcd]
enable_multi_master = true

[[values.etcd.address]]
ip = "192.168.2.2"
peer_port = "32380"
port = "32381"

[[values.etcd.address]]
ip = "192.168.2.3"
peer_port = "32380"
port = "32381"

[[values.etcd.address]]
ip = "192.168.2.4"
peer_port = "32380"
port = "32381"
```

```bash
yr -c ${YOUR_FILE_PATH}/config.toml start --master
```

:::{Note}

When using built-in etcd, for an openYuanrong cluster with N master nodes, it can tolerate at most (N-1)/2 master node failures. When the number of failed master nodes exceeds this value, the cluster cannot provide services normally.

:::

### Enable Secure Communication During Deployment

openYuanrong supports encrypted communication between internal components and between internal components and third-party component ETCD. Currently only supports configuring plaintext certificate keys, so there is risk of certificate key leakage. If you have high security key management requirements, you can implement key decryption algorithm based on openYuanrong open source code, and configure encrypted certificate keys at the same time. Other key configurations can also refer to this solution.

openYuanrong secure communication options are not enabled by default. If you need to enable them, please refer to [Secure Communication](security.md) chapter to generate related certificate keys.

## Verify Deployment Status

After deployment completes, you can execute `yr status` on master node to view cluster status. Under normal circumstances, `ReadyAgentsCount` should be consistent with actual available Agent count. If deployment fails, please check the following log files first:

- Daemon process log: `/tmp/yr_sessions/latest/logs/yr_daemon.log`
- Component log: `/tmp/yr_sessions/latest/logs/<component>_stdout.log`

```bash
Cluster Status:
  ReadyAgentsCount: 2
```

You can run [Simple Example](../../../multi_language_function_programming_interface/examples/simple-function-template.md) to further verify the deployment result.

## Delete openYuanrong Cluster

Execute the following command on all nodes to complete cluster deletion.

```shell
yr stop
```

:::{Note}

The `yr stop` command does not delete files under the deployment directory. If you modify configuration and redeploy, please clear files in that directory or specify a new directory.

:::
