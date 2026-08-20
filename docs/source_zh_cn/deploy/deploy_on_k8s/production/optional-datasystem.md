# DataSystem 可选部署

生产 Chart 默认部署 DataSystem。仅使用函数或实例的 direct invoke、且不需要对象存储、KV、stream、state 等能力时，可以关闭 DataSystem。

## 配置组合

| `global.dataSystem.enabled` | `global.dataSystem.bypass` | 行为 |
| --- | --- | --- |
| `true` | `false` | 默认；invoke 经过 DataSystem，DS API 可用 |
| `true` | `true` | invoke 默认内联，DS API 仍可用 |
| `false` | `true` | 不部署 DataSystem，invoke 必须内联 |
| `false` | `false` | 非法，Helm 渲染失败 |

`enabled` 决定 DS API 是否可用；`bypass` 只决定 invoke 的默认传输路径。

## 安装或升级

在已有安装参数后增加：

```shell
helm upgrade --install openyuanrong . \
  --namespace yr --create-namespace \
  --set global.dataSystem.enabled=false \
  --set global.dataSystem.bypass=true
```

Chart 会省略 DataSystem worker、端口和专用挂载，并向控制面和 Runtime 注入：

```text
YR_DATASYSTEM_DEPLOYED=false
YR_BYPASS_DATASYSTEM=true
```

Python Driver 优先读取环境变量。off-cluster Driver 对缺失的部署能力通过 Frontend 发现；in-cluster 或进程模式 Driver 使用 DS 开启的兼容默认值，不向 FunctionProxy gRPC 地址发送 HTTP 请求。`Config.bypass_datasystem` 可显式覆盖默认 invoke 策略。旧 Frontend 返回 404 时使用兼容默认值，连接失败、超时、5xx 或响应无效时 `yr.init()` 快速失败。Runtime 不依赖 Frontend，使用 FunctionAgent/RuntimeManager 注入的环境变量。

## 检查

确认没有 DataSystem Pod，其他组件均 Ready：

```shell
kubectl -n yr get pods
kubectl -n yr get pods -o name | grep -E 'ds-worker|datasystem'
```

第二条命令应无输出。检查 Runtime 环境：

```shell
kubectl -n yr exec <runtime-pod> -- env | grep -E 'YR_DATASYSTEM_DEPLOYED|YR_BYPASS_DATASYSTEM'
```

普通函数和实例 invoke 应返回 `ObjectRefDirect`。`yr.get(ObjectRefDirect)` 和 `yr.wait` 的 direct 路径可用。

## 限制与错误

no-DS 模式不支持普通 `ObjectRef` get/wait、put、KV、stream、state/checkpoint、tensor/shared-memory、generator、`ds://` 工作目录，以及把 `ObjectRef` 作为调用参数。Python SDK 应在 1 秒内返回 `ERR_DATASYSTEM_FAILED`，消息说明 DataSystem 已关闭及不可用操作。

内联请求和响应按方向分别限制为 100 MiB 的聚合序列化大小。等于上限允许发送，超过上限返回 `ERR_PARAM_INVALID`；序列化元数据计入总量，因此业务对象应预留空间。

## 回滚

恢复默认模式：

```shell
helm upgrade openyuanrong . \
  --namespace yr \
  --set global.dataSystem.enabled=true \
  --set global.dataSystem.bypass=false
```

等待 DataSystem Pod Ready 后，再执行依赖 DS 的调用。
