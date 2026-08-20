# Invoke 当前调用链

本文描述当前 Python SDK 经 Frontend 或集群内连接调用 Runtime 的数据路径。部署配置见 [DataSystem 可选部署](../features/datasystem-optional-deployment.md)，direct 契约见 [Direct invoke](../features/direct-invoke.md)。

## 路径选择

Python 进程初始化时得到两个有效值：

- `data_system_deployed` 决定 DS client 和 DS API 是否可用；
- `bypass_data_system` 决定普通 invoke 是否默认内联。

`YR_DATASYSTEM_DEPLOYED` 优先取环境变量，缺失时 Driver 查询 Frontend。invoke 策略按 `Config.bypass_datasystem`、`YR_BYPASS_DATASYSTEM`、Frontend 的顺序解析。Runtime 只使用环境变量和兼容默认值，不连接 Frontend；`.invoke_direct()` 或 `InvokeOptions.bypass_datasystem=True` 可为单次调用开启 direct。

## 公共控制链

```text
Python FunctionProxy / InstanceProxy
  -> InvokeOptions 应用有效 capability
  -> Python 参数序列化
  -> Cython fnruntime 转换 InvokeOptions 和 InvokeArg
  -> C++ Libruntime 构造 InvokeRequest
  -> 集群外 Driver 经 Frontend HTTP 转发
  -> Libruntime gRPC stream
  -> FunctionProxy 路由并保留 bypass 标志
  -> Worker InvokeAdaptor::Call
  -> 语言 Runtime 执行用户函数
  -> CallResult 沿原路径返回
```

Frontend 只转发调用。`GET /serverless/v1/capabilities` 是 Driver 初始化前的匿名只读能力发现接口，不参与每次 invoke，也不修改集群状态。

## 普通 DataSystem 路径

当 bypass 为 `false` 时：

1. Libruntime 为返回值生成对象 ID，并在本地 memory store/DataSystem 注册引用。
2. 大参数和 `ObjectRef` 参数通过 DataSystem 存取；调用消息携带对象 ID。
3. Worker 从 memory store/DataSystem 解析引用参数。
4. 返回对象根据大小进入 native buffer 或 DataSystem。
5. Caller 收到就绪通知后通过普通 `ObjectRef` get，并在生命周期结束时执行引用计数清理。

该路径要求 `YR_DATASYSTEM_DEPLOYED=true`，不受 direct 的 100 MiB inline 产品限制。

## Direct inline 路径

当 bypass 为 `true` 时：

1. SDK 返回类型选择 `ObjectRefDirect`。
2. Libruntime 在组包前按单次调用检查所有序列化参数的聚合大小。
3. 参数以 VALUE 内联到 InvokeRequest；no-DS 模式拒绝 `ObjectRef` 参数。
4. FunctionProxy 将 bypass 标志原样复制到 Worker 的 CallRequest。
5. Worker 为返回值分配 native buffer，不创建 DataSystem 对象。
6. Worker 按所有返回值的聚合序列化大小检查上限，并将内容内联到 CallResult。
7. Caller 的 Libruntime 回调直接完成本地 future；`ObjectRefDirect` 不执行 DataSystem IncreaseRef/DecreaseRef。

请求和响应的聚合序列化大小分别限制为 104857600 bytes。超过限制返回 `ERR_PARAM_INVALID`，不会截断内容。

## get 与 wait

- 全部为普通 `ObjectRef`：请求只进入 DataSystem。
- 全部为 `ObjectRefDirect`：只等待本地 future。
- 混合列表且 DS 可用：普通 ID 进入 DataSystem，direct ID 留在本地；结果按输入顺序返回，并共享一个 timeout deadline。
- no-DS 模式出现普通 `ObjectRef`：在 native DS client 前返回 `ERR_DATASYSTEM_FAILED`。

mixed wait 使用对普通 ID 的非阻塞探测和对 direct future 的有界轮询，不创建无法取消的后台等待线程。

## Runtime 启动链

```text
Helm values
  -> Pod 环境 YR_DATASYSTEM_DEPLOYED / YR_BYPASS_DATASYSTEM
  -> FunctionAgent 以可信值覆盖 Runtime 请求环境
  -> RuntimeManager 请求环境优先、进程环境兜底
  -> Runtime 进程环境
  -> Python/C++ Runtime 初始化
```

RuntimeManager 在启动入口拒绝 `false/false`，且在拒绝前不生成 Runtime ID、不分配端口、不调用 executor。no-DS 时不注入 `DATA_SYSTEM_ADDR` 或 `YR_DS_ADDRESS`。
C++ Libruntime 还会在 no-DS 时强制设置 bypass，防止调用方遗漏 invoke option 后进入 DS 路径。
