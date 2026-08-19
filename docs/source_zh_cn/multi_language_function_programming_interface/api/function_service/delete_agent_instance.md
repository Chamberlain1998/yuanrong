# 删除 Agent 实例

## 功能介绍

该 API 用于 openYuanrong 集群，删除 agent 实例。按 `instance_id` 直达，两种创建模式（inline / registered）删除一致。

## 接口约束

- `instanceId` 必须为创建 agent 实例返回的 `instance_id`（UUID）。请求 `DELETE /api/agent/`（末段为空）由路由层返回 404，handler 内对空 `instanceId` 的 400 判定为防御逻辑。
- 不存在或已删除的 `instanceId` 返回 404 而非 200/204——这是有意行为，避免重试将"已删除"误判为成功。对幂等性敏感的客户端应将 404 视为终态（资源不存在）而非失败。
- `instanceId` 非法格式（非 UUID）由路由匹配决定，一般返回 404。
- 鉴权遵循 frontend 全局 `GlobalJWTAuthMiddleware`，与其它函数服务 REST API 一致。`enable_func_token_auth` 开时须携带有效 JWT（见 [Agent 实例协议调用通道](./agent_invoke_channels.md) 鉴权说明）。

## URI

`DELETE /api/agent/:instanceId`

## 请求参数

### 请求 Path 参数

| **参数** | **是否必选** | **参数类型** | **描述** |
| -------- | ---------- | ---------- | ----------- |
| instanceId | 是 | string | 实例 ID（创建时返回的 UUID）。 |

## 响应参数

| **名称** | **类型** | **描述** |
| -------- | -------- | -------- |
| code | int | 状态码，`200` 表示成功。成功时 HTTP 状态码为 `200`。 |
| status | String | 状态。`deleted` 表示已删除。 |
| message | String | 失败时返回错误信息。 |

## 示例

```bash
curl -X DELETE http://{frontend}:8888/api/agent/0b6c6322-6533-4901-8000-00000000bb0b
# 开启鉴权时携带：-H "X-Auth: <jwt>"
```

> 示例为内网明文调用、鉴权关闭场景；生产环境建议 `https://` 并开启 `enable_func_token_auth`，请求携带有效 JWT。

响应：

```json
{"code":200,"status":"deleted"}
```

## 错误码

| **HTTP 状态** | **描述** |
| -------- | -------- |
| 200 | 成功（OK）。实例已删除。 |
| 401 | 未认证（Unauthorized）。`enable_func_token_auth` 开启时未携带或 JWT 无效，由全局 `GlobalJWTAuthMiddleware` 在进入 handler 前返回。 |
| 403 | 禁止（Forbidden）。JWT 有效但调用方无权限。 |
| 404 | 未找到（Not Found）。`instanceId` 不存在或已删除；`DELETE /api/agent/`（末段为空）亦由路由层返回 404。 |
| 500 | 内部服务器错误（Internal Server Error）。`message` 形如 `failed to delete agent: <原因>`：容器停止/删除出错等删除失败。 |
