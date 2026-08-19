# 创建 Agent 实例

## 功能介绍

该 API 用于 openYuanrong 集群，创建常驻 agent 实例。agent 实例承载用户业务逻辑，创建后驻留直到显式销毁。

容器元信息支持两种来源：

| 模式 | 元信息来源 | 适用场景 |
| ---- | ---------- | -------- |
| inline | create 请求体 `runtime_spec` 直接带入 | 单步创建，bypass meta_service |
| registered | 注册的 funcMeta，create 传 `urn` 关联 | 元信息复用，多处创建同一函数 |

两者共存，请求体带 `runtime_spec` 走 inline，带 `urn` 走 registered，两者都有以 inline 为准。

## 前置条件

- openYuanrong 集群已部署并 healthy，按模式启动对应组件：
  - inline 模式（不注册函数，bypass meta_service）：`yr start --master -s 'mode.master.frontend=true'`
  - registered 模式（需先注册函数）：`yr start --master -s 'mode.master.frontend=true' -s 'mode.master.meta_service=true'`
- **docker / supervisor 服务由用户自行准备**：openYuanrong 不代管 docker daemon 或 supervisor 进程。`sandbox_type=docker` 时宿主须有可用的 docker daemon（镜像已 pull 或可拉取）；`sandbox_type=supervisor` 时宿主须有 supervisor 进程。服务不可用会导致 create 失败（如 `no Docker image specified`、容器拉起失败）。
- **镜像要求**：docker executor 对 python runtime 启动时插入 `yr_runtime_main.py` 作启动命令，镜像须安装 `openyuanrong` sdk whl 包（自带 `yr_runtime_main.py`、faas_executor、yr runtime），装在镜像默认 python 的 site-packages 下。
- **workspace 与 UID 对齐**（提供 `workspace` 时）：`workspace` 是宿主机上的目录，系统自动 bind mount 到容器内 `/home/<rootfs.user>`（`rootfs.user` 为空时落 `/workspace`），用户只需提供宿主路径，无需指定容器内挂载点。bind mount 按数字 UID 校验权限（不认用户名），容器内 `rootfs.user` 的 UID 必须与宿主 workspace 目录属主的 UID 一致，否则容器进程读 workspace 会 `Permission denied`。例如 `rootfs.user=agentos`：

  ```bash
  # 宿主 workspace 属主 UID（假设 /home/snuser/workspaceA 由 snuser 持有，UID=1002）
  stat -c '%u' /home/snuser/workspaceA   # 1002

  # 容器镜像内 agentos 的 UID（需要一致）
  docker run --rm yr-docker-runtime:v0 -c 'id -u agentos'   # 须为 1002，否则需在镜像 Dockerfile 对齐
  ```

  若镜像内 UID 与宿主不一致，调整方式：① 镜像 Dockerfile `useradd -u <宿主UID> agentos`（推荐，一劳永逸）；② 自验阶段 `chown -R <容器UID> /home/snuser/workspaceA` 改宿主目录属主；③ `chmod -R 755` 放宽权限（仅验证用）。

## 接口约束

- `namespace`、`name` 必填。
- inline 模式必须带 `runtime_spec`，且 `runtime` 与 `rootfs.imageurl` 都非空。
- **registered 模式必须先注册函数再 create**：先调 [注册函数](./register_function.md)（`POST /serverless/v1/functions`，`kind` 填 `agent`）注册 agent 函数，确认注册成功（响应含 `functionVersionUrn`）后，将该 `functionVersionUrn` 作为本接口的 `urn` 调 create。registered 模式必须带 `urn`，指向该已注册的 `kind=agent` 函数；`urn` 指向未注册或不存在的函数时返回 500 `failed to create agent`。
- `workspace` 可选：非空时 bind mount 到 `/home/<rootfs.user>`（`user` 空时落 `/workspace`），为空则不挂载。bind mount 的 `source`（含 `workspace` 与 `mounts[].source`）须为宿主机绝对路径，经安全校验拒绝 `/`、`/etc`、`/proc`、`/sys`、`/dev`、`/boot`、`docker.sock`、含 `..` 的路径。`mounts[].target`（容器内路径）不做校验，由调用方自行确保不覆盖容器内敏感路径（如 `/etc/passwd`、`/proc`、`/sys`、`/dev`）。
- **鉴权**：`/api/agent` 经 frontend 全局 `GlobalJWTAuthMiddleware` 中间件，与集群其它 REST 接口一致。`enable_func_token_auth` 关时默认放行（信任调用方），开时须携带有效 JWT。

## URI

`POST /api/agent`

## 请求参数

### 请求 Header 参数

| **参数**     | **是否必选** | **参数类型** | **描述** |
| ----------- | ---------- | ---------- | ----------- |
| Content-Type | 是 | string | 消息体类型。建议填写 `application/json`。 |
| tenantId | 否 | string | 租户 ID。inline 模式 funcKey 由 tenantID 组成，缺省 `default`。 |

### 请求 Body 参数

#### 公共参数（两种模式）

| **名称**     | **类型**  | **是否必选**  | **描述** |
| ----------- | ---------- | ---------- | ----------- |
| namespace | String | 是 | 实例命名空间。 |
| name | String | 是 | 实例名（同一租户 + namespace 下需唯一）。`instance_id` 由后端 scheduler 生成（UUID），与 `name` 无关。 |
| workspace | String | 否 | 宿主机绝对路径，bind mount 到 `/home/<rootfs.user>`（user 空时落 `/workspace`）。为空则不挂载。 |
| env_vars | map | 否 | 注入容器的环境变量。经 `DELEGATE_ENV_VAR` 下沉，无 `func-` 前缀。inline 模式含全部 env；registered 模式为动态 env（静态 env 走 funcMeta.Environment）。 |
| mounts | array | 否 | 额外 bind mount。每项见 Mount。 |

#### inline 模式参数（`runtime_spec`）

| **名称**     | **类型**  | **是否必选**  | **描述** |
| ----------- | ---------- | ---------- | ----------- |
| runtime_spec | Object | inline 必选 | inline 容器配置。 |
| runtime_spec.runtime | String | inline 必选 | 真实语言，映射 faasExecutor。取值见 Runtime 类型。 |
| runtime_spec.sandbox_type | String | 否 | executor dispatch。取值：`docker`、`supervisor`；空则落默认 RuntimeExecutor。 |
| runtime_spec.rootfs | Object | inline 必选 | 容器 rootfs 配置。 |
| runtime_spec.rootfs.imageurl | String | inline 必选 | docker 镜像引用（如 `yr-docker-runtime:v0`）。 |
| runtime_spec.rootfs.user | String | 否 | 容器 run-as 用户（镜像内须存在）。空时以 root 运行，**安全风险高**（容器内进程具备最高权限），生产环境建议显式指定非 root 用户。 |
| runtime_spec.rootfs.ports | array | 否 | 容器端口转发。格式 `[<proto>:]<port>`，`<port>` 指容器内监听端口（非宿主端口），proto 取值 `tcp`/`udp`（默认 TCP）。docker executor 动态分配宿主端口映射到该容器端口，宿主端口由系统决定，不支持指定。 |
| runtime_spec.cpu | int | 否 | CPU 大小，单位 `1/1000` 核。缺省 `1000`。 |
| runtime_spec.memory | int | 否 | 内存大小，单位 `MB`。缺省 `2048`。 |

#### registered 模式参数

| **名称**     | **类型**  | **是否必选**  | **描述** |
| ----------- | ---------- | ---------- | ----------- |
| urn | String | registered 必选 | 函数 URN（如 `sn:cn:yrk:default:function:0@myService@python-agent:$latest`），来自 [注册函数](./register_function.md) 响应的 `functionVersionUrn`。经 `CombineFunctionKey` 转 funcKey，frontend 按 funcKey 从 funcSpecMap 读 funcMeta 透传。 |

#### Runtime 类型

| **取值** | **映射 faasExecutor** |
| -------- | --------------------- |
| python3.6 / python3.7 / python3.8 / python3.9 / python3.10 / python3.11 | Python3.x |
| go / http / custom image | Go1.x |

> `go` 为 Go 语言运行时；`http` 指经 HTTP 通道调用（运行时仍为 Go executor）；`custom image` 指用户自定义镜像（启动命令由镜像自带，executor 仍映射 Go1.x）。三者复用同一 Go executor。
| java8 / java11 / java17 / java21 | Java8 / Java11 / Java17 / Java21 |
| posix-custom-runtime | PosixCustom |
| 其它 | PosixCustom（fallback） |

#### Mount

| **名称**     | **类型**  | **是否必选**  | **描述** |
| ----------- | ---------- | ---------- | ----------- |
| source | String | 是 | 宿主机绝对路径。 |
| target | String | 是 | 容器内路径。 |
| readonly | boolean | 否 | 是否只读。默认 `false`。 |

## 响应参数

| **名称** | **类型** | **描述** |
| -------- | -------- | -------- |
| code | int | 状态码，`200` 表示成功。 |
| instance_id | String | 实例 ID（UUID）。 |

## 示例

### inline 模式

```bash
curl -X POST http://{frontend}:8888/api/agent -H "Content-Type: application/json" -d '{
  "name": "agent-001", "namespace": "dev",
  "runtime_spec": {
    "runtime": "python3.11", "sandbox_type": "docker",
    "rootfs": {"imageurl": "yr-docker-runtime:v0", "user": "agentos", "ports": ["tcp:22"]},
    "cpu": 600, "memory": 512
  },
  "workspace": "/home/snuser/workspaceA",
  "env_vars": {"AGENT_MODE": "prod", "userid": "u-9f3a"},
  "mounts": [{"source": "/home/snuser/workspaceB", "target": "/mnt/workspaceB", "readonly": false}]
}'
```

响应：

```json
{"code":200,"instance_id":"0b6c6322-6533-4901-8000-00000000bb0b"}
```

### registered 模式

registered 模式分两步：先注册 `kind=agent` 函数，**确认注册成功**（响应 `code=0` 且含 `functionVersionUrn`）后，再用该 URN 调 create。

```bash
# 1. 注册 agent 函数（一次性，确认 code=0 且响应含 functionVersionUrn）
curl -H "Content-type: application/json" -X POST http://{meta_service}:31182/serverless/v1/functions -d '{
  "name": "0@myService@python-agent", "kind": "agent", "runtime": "python3.11",
  "cpu": 600, "memory": 512, "timeout": 60,
  "storageType": "local", "codePath": "/opt/mycode/service",
  "environment": {"AGENT_MODE": "prod"},
  "sandboxType": "docker",
  "rootfs": {"type": "image", "imageurl": "yr-docker-runtime:v0", "user": "agentos", "ports": ["tcp:22"]}
}'
# 确认注册成功后，取 functionVersionUrn 作 urn
export FUNCTION_VERSION_URN='sn:cn:yrk:default:function:0@myService@python-agent:$latest'

# 2. 创建 agent 实例（带 urn，确认 docker/supervisor 服务已就绪）
curl -X POST http://{frontend}:8888/api/agent -H "Content-Type: application/json" -d '{
  "name": "agent-001", "namespace": "dev", "urn": "'"${FUNCTION_VERSION_URN}"'",
  "workspace": "/home/snuser/workspaceA",
  "env_vars": {"userid": "u-9f3a"},
  "mounts": [{"source": "/home/snuser/workspaceB", "target": "/mnt/workspaceB", "readonly": false}]
}'
```

响应：

```json
{"code":200,"instance_id":"0b6c6322-6533-4901-8000-00000000bb0b"}
```

#### 注册函数参数

registered 模式先调 `POST /serverless/v1/functions`（详见 [注册函数](./register_function.md)）注册 `kind=agent` 函数，参数如下：

| **名称** | **类型** | **是否必选** | **描述** |
| -------- | ------ | ---------- | -------- |
| name | String | 是 | 函数名，格式 `0@{service}@{funcName}`（如 `0@myService@python-agent`）。`service` 仅字母数字 ≤16 位，`funcName` 仅小写字母/数字/`-` ≤127 位。需全局唯一。 |
| kind | String | 是 | 函数类别，agent 必须填 `agent`。 |
| runtime | String | 是 | 真实语言，映射 faasExecutor；取值同 Runtime 类型。 |
| cpu | int | 是 | CPU 大小，单位 `1/1000` 核。注册后随 funcMeta 下沉，create 时透传到 docker `CpuShares`。 |
| memory | int | 是 | 内存大小，单位 `MB`。注册后随 funcMeta 下沉，create 时透传到 docker `Memory`。 |
| timeout | int | 否 | 函数调用超时秒数，最大 `8640000`，不填默认 `900`。 |
| storageType | String | 否 | 代码包存储类型。`local`：本地磁盘；`s3`：minio；`copy`：磁盘并拷贝至容器路径。 |
| codePath | String | 否 | 代码包本地路径。`storageType` 为 `local` 或 `copy` 时生效。 |
| environment | map | 否 | 静态环境变量（key-value，均 string）。写入 funcMeta.Environment，create 时与动态 env_vars 合并下沉，agent kind 无 `func-` 前缀。键冲突时动态 env_vars 覆盖静态 environment。 |
| sandboxType | String | 否 | executor dispatch。取值 `docker`/`supervisor`；空落默认 RuntimeExecutor。写入 funcMeta，create 时透传 createOptions["sandbox_type"]。 |
| rootfs.type | String | 否 | rootfs 类型，docker 镜像填 `image`。 |
| rootfs.imageurl | String | 否 | docker 镜像引用。写入 funcMeta.rootfs.imageurl，create 时合并进 createOptions["rootfs"] JSON。 |
| rootfs.user | String | 否 | 容器 run-as 用户（镜像内须存在）。create 时透传 createOptions["host_user"]。 |
| rootfs.ports | array | 否 | 端口转发，格式 `[<proto>:]<port>`，proto 取值 `tcp`/`udp`（默认 TCP）。create 时透传 createOptions["network"]。 |

> 注册的 funcMeta 经 etcd `/sn/functions` → frontend watcher 加载进 funcSpecMap。create 时 `applyAgentFuncMeta` 透传 runtime/sandboxType/rootfs/cpu/memory/environment，无需在 create 请求体重复。

## 错误码

响应体 `code` 字段：`200` 表示成功，`500` 表示失败（`message` 字段含具体错误信息）。

| **HTTP 状态** | **描述** |
| -------- | -------- |
| 400 | 错误的请求（Bad Request）。`message` 含具体原因：`either runtime_spec (inline) or urn (registered) is required`（既无 `runtime_spec` 又无 `urn`）、`invalid request body`（缺必填字段）、`... must be an absolute path` / `unsafe ...`（workspace/mount source 不合法）。 |
| 500 | 内部服务器错误（Internal Server Error）。`message` 形如 `failed to create agent: <原因>`：`invalid function`（proxy 找不到 faasExecutor funcMeta，runtime 不在映射表或 executor-meta 未预加载）、`no Docker image specified`（`rootfs.imageurl` 未透传）、`deploy dir is empty`（faasExecutor funcMeta 的 code_path 不存在）。registered 模式下 `urn` 指向未注册或不存在的函数时，funcMeta 缓存未命中，最终也经此路径返回 500。 |
