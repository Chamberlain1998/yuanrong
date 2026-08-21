# Direct invoke

## 语义

Direct invoke 将序列化后的参数和返回值内联到 FunctionSystem 消息中，不使用 DataSystem 对象存储和引用计数。它既可用于部署了 DS 的集群，也是在 no-DS 集群中调用函数的必要路径。

Python 提供三种入口：

- `Config.bypass_datasystem=None|True|False` 设置当前进程的默认 invoke 策略；`None` 使用探测到的集群默认值。
- `InvokeOptions.bypass_datasystem=True` 为该调用显式开启 direct；`False` 不会关闭已经生效的进程默认 direct 策略。
- `.invoke_direct()` 始终为该调用开启 direct。

无 DS 时进程默认必须为 direct。`Config.bypass_datasystem=False` 与 `YR_DATASYSTEM_DEPLOYED=false` 组合会在 `yr.init()` 失败。

## 返回引用

direct 调用返回 `ObjectRefDirect`。它由 Libruntime 回调和本地 future 完成，不执行 DataSystem IncreaseRef/DecreaseRef：

```python
ref = fn.invoke_direct(payload)
value = yr.get(ref)
ready, pending = yr.wait([ref], wait_num=1, timeout=10)
```

`yr.get` 和 `yr.wait` 支持 direct 引用列表。在 DS 已部署时也支持普通 `ObjectRef` 与 `ObjectRefDirect` 的混合列表：

- direct ID 不会发送给 DataSystem；
- 结果按用户输入顺序返回；
- 整个操作共享一个 timeout，不会分别消耗两次完整超时。

no-DS 模式不能获取普通 `ObjectRef`，也不能把任何 `ObjectRef` 作为新调用参数继续传递。

## Inline 载荷限制

每次 bypass 调用按方向检查聚合序列化大小：

| 方向 | 上限 | 口径 |
| --- | ---: | --- |
| 请求 | 100 MiB（104857600 bytes） | 所有参数序列化后的总大小 |
| 响应 | 100 MiB（104857600 bytes） | 所有返回值序列化后的总大小 |

请求和响应分别计算，不相加。恰好等于限制允许发送，超过限制在进入 gRPC 传输前返回 `ERR_PARAM_INVALID`，数据不会被截断。业务对象应小于 100 MiB，因为序列化元数据也计入总量。

该产品限制低于链路的默认 gRPC 128 MiB 和 LiteBus 500 MiB 上限，为 protobuf 字段、函数元数据和协议演进预留空间。所有 bypass 调用都受此限制；经过 DS 的普通 invoke 不受 inline 限制。

## 选择建议

direct 适合不需要跨调用传递对象引用的调用。大对象经过 protobuf 编解码和消息复制，延迟通常高于 DS 共享内存路径；需要 DS API、对象复用、generator 或大对象传输时，应部署 DataSystem 并使用普通 invoke。

部署能力和 DS API 可用性见 [DataSystem 可选部署](./datasystem-optional-deployment.md)。历史验证数据见 [InvokeDirect 验证报告](../invoke-chain/invoke-direct-verification.md)。
