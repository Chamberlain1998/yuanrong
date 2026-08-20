# DataSystem 可选部署

## 问题与边界

Kubernetes 集群可以不部署 DataSystem，但此时函数参数和返回值必须通过 FunctionSystem 消息内联传输。本特性不改变编译依赖，也不覆盖进程部署模式。

部署能力和调用传输是两个独立概念：

| 环境变量 | 含义 | 默认值 |
| --- | --- | --- |
| `YR_DATASYSTEM_DEPLOYED` | DataSystem 是否已部署且可用 | `true` |
| `YR_BYPASS_DATASYSTEM` | 普通 invoke 是否默认使用内联传输 | `false` |

有效组合如下：

| `YR_DATASYSTEM_DEPLOYED` | `YR_BYPASS_DATASYSTEM` | 结果 |
| --- | --- | --- |
| `true` | `false` | 兼容默认模式；普通 invoke 经过 DS，DS API 可用 |
| `true` | `true` | 普通 invoke 默认内联，DS API 仍可用 |
| `false` | `true` | no-DS 模式；invoke 内联，DS API 快速失败 |
| `false` | `false` | 非法；Helm、SDK 和 RuntimeManager 拒绝该组合 |

`YR_DATASYSTEM_DEPLOYED` 独立决定 DS API 是否可用。bypass 只选择 invoke 的传输路径，不能据此判断 DS 是否部署。

进程部署中的 `DATA_SYSTEM_ENABLE` 是另一项已有配置，只控制 FunctionAgent 是否初始化
DataSystem KVClient（例如支持 `ds://` 工作目录），默认 `false`。进程部署仍然部署 DataSystem，
因此固定使用 `YR_DATASYSTEM_DEPLOYED=true`；修改 `DATA_SYSTEM_ENABLE` 不会改变 invoke、put/get
或其他 DS API 的可用性。普通 `yr start` 不提供 no-DS capability values；Sandbox Chart 通过
组件环境覆盖传递 Helm 的可信部署状态，避免把集群能力暴露成进程部署用户配置。

## Helm 入口

生产和 sandbox Chart 使用同一组 values：

```yaml
global:
  dataSystem:
    enabled: false
    bypass: true
```

Chart 负责以下操作：

- 校验 `enabled=false` 时 `bypass` 必须为 `true`；
- 不渲染 DataSystem worker 及其端口、地址、健康检查和专用挂载；
- 向 Frontend、FunctionProxy、FunctionAgent、RuntimeManager、master 和系统函数注入两个标准布尔字符串；
- 禁用 FunctionProxy、FunctionAgent 和 state storage 对 DataSystem 的连接与启动依赖。

默认值保持 `enabled=true`、`bypass=false`，已有 values 无需迁移。

## 能力传播

### Driver

Python Driver 初始化时按字段独立解析能力：

1. `YR_DATASYSTEM_DEPLOYED` 优先读取进程环境变量，off-cluster Driver 缺失时查询 Frontend；
2. invoke 策略按 `Config.bypass_datasystem`、`YR_BYPASS_DATASYSTEM`、Frontend 的顺序解析；
3. 仅当 off-cluster Driver 仍有字段未知时请求匿名只读接口 `GET /serverless/v1/capabilities`；in-cluster/process Driver 的 `server_address` 是 FunctionProxy gRPC 地址，不发起 HTTP 能力探测；
4. 旧 Frontend 返回 404 时对未知字段使用兼容默认值 `true/false`；连接失败、超时、5xx 或响应格式错误时初始化快速失败，避免误判 no-DS 集群。

能力请求不携带用户凭据，TLS 默认校验服务端证书，超时不超过 1 秒。Frontend 返回部署状态，不执行协议协商或状态修改。

### Runtime

FunctionAgent 和 RuntimeManager 把同名环境变量注入 Runtime。Runtime 优先读取环境变量且不请求 Frontend，因此由 FunctionProxy 拉起、无法访问 Frontend 的 Runtime 仍能确定能力。

C++ Libruntime 在 `YR_DATASYSTEM_DEPLOYED=false` 时再次强制 invoke 使用 bypass，避免非 Python SDK 或内部调用遗漏选项后进入 DS 路径。

FunctionSystem 对请求环境的解析优先于 RuntimeManager 进程环境。RuntimeManager 在启动边界再次拒绝 `false/false`，防止绕过 FunctionAgent 的请求创建非法 Runtime。

## 组件职责

- FunctionAgent 使用 Pod 的可信能力覆盖用户委托环境。
- RuntimeManager 只在 DS 开启时注入 `DATA_SYSTEM_ADDR` 和 `YR_DS_ADDRESS`。
- FunctionProxy 在 no-DS 模式不创建或绑定 DS client。
- FunctionMaster 不向实例 Pod 注入 DS 地址。
- `ds://` 工作目录在 no-DS 模式立即失败。
- Python SDK 和 C++ Libruntime 对 DS API 提供结构化快速失败；无 DS 的内部引用计数路径直接跳过。

## 实现落点

| 层次 | 关键文件 | 职责 |
| --- | --- | --- |
| Python SDK | `api/python/yr/datasystem_capability.py`、`config_manager.py`、`apis.py` | 能力解析、调用路径选择、DS API 保护和 direct 引用 get/wait |
| C++ Libruntime | `src/libruntime/libruntime.cpp`、`invokeadaptor/invoke_adaptor.cpp`、`objectstore/memory_store.cpp` | 跳过 DS 初始化与引用计数、native API 快速失败、100 MiB 双向边界 |
| Go/FaaS | `go/pkg/common/faas_common/utils/helper.go`、`go/pkg/functionscaler/instancepool/instance_operation_kernel.go` | 将部署默认 bypass 写入系统函数创建请求 |
| FunctionSystem | `common/datasystem_capability.h`、`runtime_manager/config/build.cpp`、`runtime_manager/manager/runtime_manager.cpp` | 传播 runtime 环境、移除 DS 地址、拒绝非法能力组合 |
| Frontend | `pkg/frontend/api/v1/capabilities.go`、`middleware/jwtauth_whitelist.go` | 提供匿名只读能力发现接口 |
| Helm | `deploy/k8s/charts/openyuanrong`、`deploy/sandbox/k8s/charts/yr-k8s` | values 校验、组件环境注入及 DS 资源裁剪 |

FunctionSystem 和 Frontend 位于独立仓库，核心仓通过 `functionsystem`、`frontend` gitlink 固定经过验证的精确提交。

## API 可用性

no-DS 模式支持函数与实例的内联调用，以及 `yr.get`/`yr.wait` 对 `ObjectRefDirect` 的本地 future 操作。

以下能力依赖 DataSystem，在 no-DS 模式不可用：普通 `ObjectRef` 的 get/wait、put、KV、stream、state/checkpoint、tensor/shared-memory、generator，以及把 `ObjectRef` 作为 invoke 参数继续传递。Python SDK 在进入 native client 前返回：

```text
code=ERR_DATASYSTEM_FAILED (4299)
module_code=DATASYSTEM
DataSystem is disabled in this cluster; <operation> is unavailable
```

调用传输、优先级、混合引用和 100 MiB 限制见 [Direct invoke](./direct-invoke.md)。生产安装、巡检和回滚见中英文 K8s 部署指南。

## 语言范围

| SDK | 自动发现 | no-DS invoke | DS API 保护 |
| --- | --- | --- | --- |
| Python | 环境变量，off-cluster Driver 可回查 Frontend | 自动 | Python 与 C++ 双层保护 |
| C++ | 环境变量 | 需显式 direct option | C++ Libruntime |
| Go | 环境变量 | 系统组件自动设置，用户可显式设置 | C++ Libruntime |
| Java | 无 Frontend 发现 | 本特性未新增自动 direct | C++ Libruntime 覆盖 native 路径 |
