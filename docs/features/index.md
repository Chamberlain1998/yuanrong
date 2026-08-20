# 特性总览

本文档描述 YuanRong 相对于上游版本的新增特性。

## 目录

- [yrcli 命令行工具](./yrcli.md) - 函数部署、调用、管理
- [异步调用](./async-invocation.md) - 异步请求、短 URL、结果查询
- [Oneshot 函数](./oneshot.md) - FaaS 调度策略
- [Quota 配额管理系统](./quota.md) - 租户级资源配额管理
- [IAM 认证与授权](./iam-auth.md) - Keycloak/Casdoor 集成
- [Snapshot 与 Checkpoint](./snapshot-checkpoint.md) - 函数快照与实例恢复
- [WebTerminal](./webterminal.md) - WebSocket 终端
- [Sandbox RESTful API](./sandbox-rest-api.md) - HTTP/WS 接口、action 词表、tunnel 协议、SDK 用法
- [Sandbox Create SSE/Timeout Design](./sandbox-create-sse-timeout.md) - aligned create semantics, SSE result delivery, and timeout handling
- [Rust Sandbox Runtime (rrt)](../rust-sandbox-runtime/README.md) - rrt-runtime 架构、接入、构建、部署、契约
- [可观测性](./observability.md) - OpenTelemetry、Prometheus、Loki、Tempo
- [Traefik 路由重构](./traefik-routing.md) - HTTP 路径路由
- [Sandbox 外部认证](./iam-auth.md#sandbox-外部认证) - Sandbox 与 IAM 集成
- [DataSystem 可选部署](./datasystem-optional-deployment.md) - no-DS 部署、能力传播和 API 可用性
- [Direct invoke](./direct-invoke.md) - 内联调用、引用语义和载荷限制

## 新增特性汇总

| 特性模块 | 状态 | 涉及组件 |
| -------- | ---- | -------- |
| yrcli 命令行工具 | GA | yuanrong |
| 异步调用 | GA | yuanrong, frontend |
| Oneshot 函数 | GA | functionsystem |
| WebTerminal | GA | frontend, functionsystem |
| Sandbox API / Rust Runtime (rrt) | GA | yuanrong, frontend, functionsystem |
| OpenTelemetry | GA | functionsystem |
| Prometheus/Loki/Tempo (日志/告警) | GA | functionsystem, frontend |
| Traefik HTTP 路由 | GA | functionsystem |
| Quota 配额管理 | Beta (TODO: 与 IAM 对接) | functionsystem |
| IAM 认证与授权 | Beta (TODO: 与函数系统对接) | functionsystem, frontend |
| Sandbox 外部认证 | Beta | frontend |
| Snapshot/Checkpoint | Beta | functionsystem, yuanrong |
| DataSystem 可选部署 | Beta | yuanrong, frontend, functionsystem |
| Direct invoke | Beta | yuanrong, functionsystem |

## 待办事项 (TODO)

| 功能 | 状态 | 说明 |
| ---- | ---- | ---- |
| Quota 与 IAM 对接 | 进行中 | 实现配额查询和同步机制 |
| IAM 与函数系统对接 | 进行中 | 验证完整认证流程 |

## 升级说明

### Quota 配置迁移

旧版本用户如需启用 Quota 功能：

```yaml
# 新增 quota_config_file 参数
quota:
  config_file: /etc/yuanrong/quota.json
```

### IAM 配置

```yaml
iam:
  enabled: true
  keycloak:
    endpoint: "${KEYCLOAK_ENDPOINT}"
  jwt:
    secret: "${JWT_SECRET}"
```

### Traefik 路由

旧 TCP 路由配置需要迁移到新的 HTTP 路径路由格式。
