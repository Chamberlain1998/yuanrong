# 部署 openYuanrong

本节将介绍常见场景下主机部署 openYuanrong 时的配置，配置参数详细说明请参考[部署参数表](../parameters.md)。

## 使用命令行工具 yr 部署

`yr` 是 Python 版命令行工具，用于主机部署 openYuanrong 时，提供配置渲染、组件编排、健康检查、状态查询与停止能力。

部署命令为 `yr start`，执行该命令 CLI 会：

- 合并默认配置、自定义配置及命令行 `yr -s` 配置并渲染模板。其中，配置优先级从高到低为：命令行 `yr -s` 配置、自定义配置和默认配置。默认配置在 openYuanrong 安装路径下的 `yr/cli/values.toml` 和 `yr/cli/config.toml.jinja` 文件中定义，可通过命令 `yr config template` 查看。 
- 创建部署目录 `/tmp/yr_sessions/<timestamp>/`，更新符号链接 `/tmp/yr_sessions/latest` 指向该目录。
- 按依赖顺序启动组件并执行健康检查。
- 生成会话文件 `/tmp/yr_sessions/latest/session.json`，供 `status` / `health` / `stop` 使用。
- 生成主节点信息文件 `/tmp/yr_sessions/yr_current_master_info`，供 yr SDK 解析。

### 通过自定义配置文件设置部署参数

参考以下步骤通过自定义配置文件设置部署参数：

1. 执行 `yr config template` 查看默认配置及内置模板结构。
2. 新建自定义配置文件 `config.toml`，覆盖要修改的配置（无需复制整份模板）。
3. 使用 `yr -c ${YOUR_FILE_PATH}/config.toml start --master` 指定路径或 `yr start --master` 从默认路径 `/etc/yuanrong/config.toml` 读取自定义配置启动。

:::{Note}

执行 `yr start --master` 或 `yr start -c --master`，CLI 都会尝试读取默认路径 `/etc/yuanrong/config.toml` 的自定义配置文件，不存在时才使用内置默认值。

:::

#### 配置 [mode.*] 控制组件启停

`yr start --master` 用于启动主节点，`yr start` 用于启动从节点。主从节点默认配置启动的组件如下：

- 主节点：`etcd`、`ds_master`、`ds_worker`、`function_master`、`function_proxy`、`function_agent`
- 从节点：`ds_worker`、`function_proxy`、`function_agent`

可通过 `[mode.master]` 或 `[mode.agent]` 打开额外组件。例如在主节点上启用 `frontend`、`dashboard` 和 `collector` 组件配置如下：

```toml
[mode.master]
frontend = true
dashboard = true
collector = true
```

#### 配置 [values.*] 覆盖运行时参数

`values` 主要承载运行时参数（IP、端口、TLS 路径、日志路径等），并用于渲染组件配置模板。开启函数系统和 etcd 的 TLS 参考配置如下：

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

#### 配置 Frontend SSH 与 TCP tunnel

Frontend SSH 依赖 function proxy 的 TCP tunnel。启用 SSH 时需分别准备 frontend 服务端私钥、外部客户端 authorized keys、frontend 访问实例 sshd 的私钥，以及包含实例侧 `authorized_keys` 的目录：

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

`ssh_backend_public_key_dir` 目录必须包含可读的 `authorized_keys`。默认 SSH 地址 ``:2222`` 监听所有网卡，生产环境应通过防火墙或反向代理限制访问。

如果只需要 wsproxy WebSocket 透传，可独立开启 TCP tunnel，无需配置 SSH 密钥：

```toml
[values.frontend]
enable_tcp_tunnel = true
```

#### 配置 [component.*] 覆盖组件启动参数

除 `mode.*` 和 `values.*` 外，还可直接覆盖组件级配置（例如 `etcd`、`ds_master`、`function_master`、`function_proxy`）。这些配置会直接影响组件进程启动参数、环境变量和健康检查行为。

常见可覆盖的子表包括：

- `[COMPONENT.args]`：组件命令行参数
- `[COMPONENT.env]`：组件环境变量
- `[COMPONENT.health_check]`：健康检查配置

其中，组件环境变量会合并当前 shell 中配置的环境变量和 `[COMPONENT.env]` 中的变量（通常组件配置优先级更高，会覆盖同名 shell 变量）。若 `LD_LIBRARY_PATH` / `LD_PRELOAD` 同时在 shell 和组件配置中存在，则会拼接为：
  `shell_value:config_value`。

例如覆盖部分 `function_master` 的启动参数并追加库路径参考配置如下：

```toml
[function_master.args]
runtime_recover_enable = true
litebus_thread_num = 25
system_timeout = 2000000

[function_master.env]
LD_LIBRARY_PATH = "/opt/yr/lib"
```

使用如下命令部署，function_master 组件环境变量 `LD_LIBRARY_PATH` 的值将配置为 `/opt/custom/lib:/opt/yr/lib`。

```sh
export LD_LIBRARY_PATH=/opt/custom/lib
yr -c ${YOUR_FILE_PATH}/config.toml start --master
```

#### 配置示例

在主节点启用 frontend 组件，覆盖 `function_proxy` 和 `ds_worker` 的启动参数配置如下：

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

部署主节点时执行：`yr -c ${YOUR_FILE_PATH}/config.toml start --master`。如需在不改自定义配置文件的情况下临时覆盖，可叠加 `-s/--set`（例如 `-s 'function_proxy.args.enable_metrics=true'`）。

### 通过 `yr -s` 设置部署参数

部署时可通过 `-s/--set` 设置部署参数，它将覆盖默认配置和自定义配置中的参数。`-s` 可重复指定，每条必须是合法 TOML 赋值语句（`KEY=VALUE`）。

示例：

- 覆盖 master 地址与端口：

    `yr start --master -s 'values.ds_master.ip="10.88.0.9"' -s 'values.ds_master.port="12123"'`

- 覆盖数组表（etcd 地址列表）：

    `yr start --master -s 'values.etcd.address=[{ip="10.88.0.9",peer_port="32380",port="32379"}]'`

### 使用默认配置部署

部署主节点：

```bash
yr start --master
```

主节点启动成功后，会打印从节点加入集群的推荐命令和最小配置片段，在从节点主机上执行该命令即可。

```text
yr start --master_address http://x.x.x.x:xxxx
```

主节点的地址信息也可以通过 `/tmp/yr_sessions/latest/session.json` 文件中 `cluster_info` 字段获取。

### 部署时指定节点可用资源

您可在 openYuanrong 主从节点上分别配置 CPU 和内存总量，内存中部分用于函数堆栈，部分用于存储数据对象。

- `values.cpu_num`：CPU 总量配置参数。主节点 openYuanrong 组件默认占用 1 毫核（单位：1/1000 核），如果您希望主节点不运行分布式任务，只用于管理和调度，可配置 `values.cpu_num=1`。
- `values.memory_num`：内存总量配置参数。
- `values.shared_memory_num`：用于存储数据对象的内存量（单位：MB）。如果应用场景中有较多的数据对象存储，可适当调大。

openYuanrong 也支持异构计算资源，通过配置 `function_agent.args.npu_collection_mode` 及 `function_agent.args.gpu_collection_enable` 参数，openYuanrong 会自动采集节点上的 NPU 及 GPU 资源。此外您也可以配置 `function_agent.args.custom_resources` 定义自定义资源。

示例：部署主节点不用于运行分布式任务。

```bash
yr start --master -s 'values.cpu_num=1'
```

示例：部署从节点支持采集 GPU 资源并自定义 5 个 ssd 资源。

```bash
yr start --master_address http://x.x.x.x:xxxx \
-s 'function_agent.args.gpu_collection_enable=true' \
-s 'function_agent.args.custom_resources="{\"ssd\":5}"'
```

### 部署集群支持函数服务

默认配置部署只支持运行无状态函数和有状态函数，增加支持函数服务需要在部署主节点时启动 frontend、functon scheduler 和 meta service 三个组件，从节点部署无需新增配置。

```bash
yr start --master \
-s 'mode.master.frontend=true' \
-s 'mode.master.function_scheduler=true' \
-s 'mode.master.meta_service=true'
```

### 部署 Dashboard

Dashboard 支持在页面上查看日志、指标、任务运行状态等数据，详细使用参考 [Dashboard](../../../observability/dashboard.md) 介绍。

创建自定义配置文件 `config.toml`，内容如下，按实际情况配置 `metrics_config_file` 和 `address` 参数。

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

使用如下命令部署主节点：

```bash
yr -c ${YOUR_FILE_PATH}/config.toml start --master
```

参考如下命令部署从节点

```bash
yr -c ${YOUR_FILE_PATH}/config.toml start --master_address http://x.x.x.x:xxxx
```

### 部署多个主节点

主节点默认只部署一个，在高可靠场景下，也可按一主多备方式部署多个。这里以常见的一主两备部署方式，使用内置 etcd 为例。

在三台主机上分别使用以下自定义配置文件 `config.toml` 部署。其中，ip 替换为每台主机的 ip，并指定每台主机使用的 etcd 端口，请确保端口不冲突：

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

使用内置 etcd 时，对于一个有 N 个主节点的 openYuanrong 集群，最多能容忍 (N-1)/2 个主节点故障，当故障主节点数量超过该数值时，集群无法正常提供服务。

:::

### 部署时开启安全通信

openYuanrong 支持内部组件间及内部组件同三方组件 ETCD 间的加密通信。当前只支持配置明文证书密钥，因此存在证书密钥泄露风险。如果您有高安全的密钥管理需求，可基于 openYuanrong 开源代码自行实现密钥解密算法，同时配置加密的证书密钥，其他密钥配置也可参考该方案。

openYuanrong 默认未开启安全通信选项，如需开启请参考[安全通信](./security.md)章节生成相关证书密钥。

## 验证部署状态

部署完成后，可在主节点执行 `yr status` 查看集群状态。正常情况下，`ReadyAgentsCount` 与实际可用 Agent 数量一致。若部署失败，可优先检查如下日志文件：

- 守护进程日志：`/tmp/yr_sessions/latest/logs/yr_daemon.log`
- 组件日志：`/tmp/yr_sessions/latest/logs/<component>_stdout.log`

```bash
Cluster Status:
  ReadyAgentsCount: 2
```

可运行[简单示例](../../../multi_language_function_programming_interface/examples/simple-function-template.md)进一步验证部署结果。

## 删除 openYuanrong 集群

在所有节点执行如下命令即完成集群删除。

```shell
yr stop
```

:::{Note}

`yr stop` 命令并不会删除部署目录下的文件，如果您修改了配置重新部署，请清空该目录下的文件或指定一个新的目录。

:::
