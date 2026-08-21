# InvokeDirect 验证证据

本文只记录验证环境和结果。规范性设计见 [Direct invoke](../features/direct-invoke.md)，部署约束见 [DataSystem 可选部署](../features/datasystem-optional-deployment.md)。

## 环境

- 基础功能验证：单节点集群内和经 Frontend 的集群外 Driver。
- 载荷边界验证：Buildkite #191 镜像，北京四 no-DS 集群，Helm revision 5，2026-07-27。
- 性能数据：单节点集群内，每个数据量执行 3 轮取平均。

## 功能结果

| Case | 结果 |
| --- | --- |
| stateless `.invoke_direct()` | 返回 `ObjectRefDirect`，结果正确 |
| instance method `.invoke_direct()` | 返回 `ObjectRefDirect`，结果正确 |
| 连续 10 次 direct invoke | 全部成功 |
| 经 Frontend 的 100 KiB 至 4 MiB direct invoke | 全部成功 |
| no-DS 自动能力发现后普通 invoke | 自动走 direct，结果正确 |
| no-DS DS API | 快速返回 4299/DATASYSTEM 和具体操作名 |

## 载荷边界

| Case | 结果 |
| --- | --- |
| 参数序列化后 104857600 bytes | 成功 |
| 返回值序列化后 104857600 bytes | 成功 |
| 参数或返回值序列化后 104857601 bytes | 约 20 ms 返回 `ERR_PARAM_INVALID` |
| 99 MiB 原始参数和返回值 | 成功 |
| 100、101、120、127、128 MiB 原始参数 | 均在进入 gRPC 上限前返回产品约束错误 |
| 两个 51 MiB 或两个 64 MiB 返回值 | 按聚合大小快速失败，无卡住 |
| 60 MiB 普通 DS invoke | 成功，不受 inline 限制 |

边界按序列化后总量判断。测试中的 Python `bytes` 恰好边界原始大小分别为请求 104857550 bytes、响应 104857584 bytes，只用于复现实验，不能作为其他类型的通用上限。

## 已验证缺陷

验证曾暴露并确认修复两项缺陷：

1. FunctionProxy 转换 InvokeRequest 时遗漏 bypass 标志，导致集群外较大 direct 调用等待 DataSystem 路径而超时。
2. 超限返回值曾被截断，造成 Python 反序列化断言；当前请求和响应均在组包前按聚合大小返回 `ERR_PARAM_INVALID`，不再截断。

## 性能样本

| 载荷 | direct 延迟 (ms) | DS 延迟 (ms) | direct/DS | direct RSS (MiB) | DS RSS (MiB) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 MiB | 8.6 | 6.2 | 1.4x | 127 | 130 |
| 10 MiB | 68 | 37 | 1.8x | 231 | 259 |
| 20 MiB | 144 | 63 | 2.3x | 383 | 442 |
| 40 MiB | 375 | 155 | 2.4x | 595 | 714 |
| 60 MiB | 620 | 226 | 2.7x | 816 | 901 |
| 80 MiB | 816 | 304 | 2.7x | 1018 | 1258 |

该样本表明 direct 在大载荷下因 protobuf 编解码和消息复制而慢于共享内存 DS 路径，同时省去部分 DS buffer 和引用计数内存。它是特定环境的测量证据，不构成跨集群性能保证。

## 复现入口

测试代码位于 `test/smoke/invoke-direct/`：

```bash
cd test/smoke/invoke-direct
bash run_test.sh
```
