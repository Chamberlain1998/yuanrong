# Agent 实例协议调用通道

## 功能介绍

Agent 实例创建（[创建 Agent 实例](./create_agent_instance.md)）后驻留运行，沙箱内服务端监听某端口（如 sshd / WS server / HTTP server）。外部调用方经 frontend 的三条 L4 透传通道之一桥接到该端口，三条通道对上层协议透明、以 `instance + port` 为路由键、复用 function_proxy 的 `tcp.tunnel`：

| 通道 | 端点 | 适用场景 | 路由信息载体 |
| ---- | ---- | -------- | ----------- |
| SSH | frontend `:2222`（独立监听） | 持有 SSH 客户端、需交互式 shell / 端口转发 | SSH username 字段 |
| WebSocket | `GET /serverless/v1/ws` | 持有 WS 客户端的接入方（浏览器、`websockets` 库） | URL query |
| HTTP | `ANY /serverless/v1/http` | 只能发普通 HTTP 的接入方（`fetch` / `curl` / XHR） | URL query |

三者最终都经 `resolveInstance` → `dialTunnel`（function_proxy `tcp.tunnel`）到达容器内服务，frontend 与 function_proxy 全程只搬字节、不解析上层协议帧。

## 前置条件

- openYuanrong 集群已部署并 healthy，frontend 已起（`yr start --master`，默认带 frontend）。
- 已通过 [创建 Agent 实例](./create_agent_instance.md) 创建实例并拿到 `instance_id`，实例处于 `RUNNING`。
- 沙箱内服务端在 `?port`（WS/HTTP）或实例 sshd 端口（SSH）监听。服务端未起 / 端口未监听会导致 `502`（WS/HTTP）或连接失败（SSH）。
- function_proxy 的 `tcp.tunnel` 已发布（日志 `TCP tunnel listening on 127.0.1.1:22775`）。SSH 通道额外要求创建实例时开启 `ssh_enable`（向 rootfs 注入平台公钥只读挂载，见下文开关）。

## 接口约束

- **路由键**：WS/HTTP 走 URL query（`?instance=<id>&port=<n>`），SSH 因协议握手自带 user 字段塞 username（`yr:instance:<id>:port=<n>`）。三者最终都落到 `instance + port`。
- **L4 透传**：frontend 不终止上层协议、不解析帧。HTTP 请求/响应（含流式 chunked / SSE）、WS 帧、SSH channel 字节均原样经 tunnel 往返。流式响应字节持续流回，frontend 不缓存、不组装、不拆帧。
- **鉴权**：WS/HTTP 共用 `IamConfig.EnableFuncTokenAuth`（默认关，取 `?tenant_id` 或 `default`；开则校验 JWT）；SSH 单独走 `YR_FRONTEND_SSH_AUTH_ENABLE`（默认开，校验 `authorized_keys`）。跨租户校验：system 租户可达任意 instance，其余须 instance 属主与调用方匹配。
- **失败路径**（WS/HTTP）：前置（鉴权 / 路由解析 / 拨 tunnel）全在 HTTP 层、Hijack 前完成，失败回干净 `4xx/502`；Hijack 后无 HTTP 层可写错误码，连接异常由对端 RST/FIN 体现。
- **端口范围**：`1`–`65535`。WS 省略 `port` 时默认 `18092`（与 AgentServer 默认 WS 端口对齐，仅作"省略时的猜测值"，不约束服务端必须听此口）。

## 开关与配置

### 部署开关

三通道底层都依赖 function_proxy 的 `tcp.tunnel`。部署集群时通过以下最外层开关控制是否起该 listener 与各通道：

| 开关 | 默认 | 作用 |
| ---- | ---- | ---- |
| `ssh_enable` | `false` | 开启 SSH 通道。开启后**自动连带开启 `tcp.tunnel`**（SSH 路径依赖它），并在创建实例时把平台公钥注入 rootfs 只读挂载。 |
| `enable_tcp_tunnel` | `false` | 单独开启 `tcp.tunnel`，**独立于 `ssh_enable`**。只要 WS/HTTP 透传、不需要 SSH 密钥时用它。 |

关系简述：

- 用 **SSH 通道** → 只需开 `ssh_enable=true`（`tcp.tunnel` 随之带上，无需另设 `enable_tcp_tunnel`）。
- 只用 **WS/HTTP 通道**、不要 SSH → 开 `enable_tcp_tunnel=true`（不配 SSH 密钥）。
- `tcp.tunnel` 监听 `127.0.1.1:22775`（`tcp_tunnel_port` 可调，默认 22775），并发上限 1024（`tcp_tunnel_max_connections` 可调）。就绪后日志打印 `TCP tunnel listening on 127.0.1.1:22775`。

### SSH 通道环境变量

SSH bastion 由 `YR_FRONTEND_SSH_ENABLE` 启用，开启后 frontend 在 `:2222` 起独立监听，进程级行为由以下环境变量调节：

| 环境变量 | 默认值 | 说明 |
| -------- | ------ | ---- |
| `YR_FRONTEND_SSH_ENABLE` | `false` | 是否启用 SSH bastion。空 / 不设视为关。 |
| `YR_FRONTEND_SSH_ADDR` | `:2222` | SSH bastion 监听地址。 |
| `YR_FRONTEND_SSH_AUTH_ENABLE` | `true` | 是否校验客户端公钥。关则 `NoClientAuth`（任意客户端可连，仅用于验证）。 |
| `YR_FRONTEND_SSH_HOST_KEY` | — | frontend 对客户端的 host 私钥文件路径（SSH 启用时必填）。 |
| `YR_FRONTEND_SSH_BACKEND_KEY` | — | frontend 连实例 sshd 用的后端私钥文件路径（SSH 启用时必填）。 |
| `YR_FRONTEND_SSH_AUTHORIZED_KEYS` | — | 客户端公钥白名单文件路径（`AUTH_ENABLE=true` 时必填）。 |
| `YR_FRONTEND_SSH_ROUTE_WAIT_TIMEOUT_SECONDS` | `10` | 等待实例路由就绪（RUNNING + functionProxyID + tunnel 地址）的秒数。 |
| `YR_FRONTEND_SSH_BACKEND_RETRY_ATTEMPTS` | `10` | 拨实例 sshd 失败的重试次数。 |
| `YR_FRONTEND_SSH_BACKEND_RETRY_INTERVAL_MS` | `500` | 重试间隔毫秒。 |
| `YR_FRONTEND_SSH_MAX_CONNECTIONS` | `1024` | 并发连接上限。 |

### SSH 密钥配置

SSH 通道涉及三组密钥，角色分明：

| 密钥 | 角色 | 环境变量 | 生成示例 |
| ---- | ---- | -------- | -------- |
| host key | frontend → 客户端：bastion 对外身份 | `YR_FRONTEND_SSH_HOST_KEY` | `ssh-keygen -t ed25519 -f host_key -N ""` |
| backend key | frontend → 实例 sshd：第二段 SSH 握手用 | `YR_FRONTEND_SSH_BACKEND_KEY` | `ssh-keygen -t ed25519 -f backend_key -N ""` |
| authorized_keys | 客户端 → frontend：客户端公钥白名单 | `YR_FRONTEND_SSH_AUTHORIZED_KEYS` | 各客户端 `ssh-keygen` 后公钥汇总到此文件 |

backend key 的**公钥**须注入实例 rootfs 的 `authorized_keys`（由 `ssh_enable=true` 在创建实例时自动挂载只读），实例 sshd 才会接受 frontend 的后端连接。host key 校验对后端跳禁用（`InsecureIgnoreHostKey`，因为实例 host key 动态、前端无法预知）。

### WS / HTTP 通道开关

WS 与 HTTP 通道**随 frontend 起，无独立开关**（路由 `r.GET("/serverless/v1/ws")` / `r.Any("/serverless/v1/http")` 在 `InitRoute` 注册）。鉴权由 `IamConfig.EnableFuncTokenAuth` 统一控制：

| 配置 | 默认 | WS/HTTP 行为 |
| ---- | ---- | ----------- |
| `enable_func_token_auth` | `false` | 关：取 `?tenant_id`，缺省 `default`，信任调用方自报租户。 |
| `enable_func_token_auth` | `true` | 开：校验 JWT，token 取自 `X-Auth` header / `?token` / `iam_token` cookie / `Sec-WebSocket-Protocol` 子协议（浏览器无法设自定义 header，JWT 经 subprotocol 携带）。 |

> **安全警告**：`enable_func_token_auth` 默认关闭。关闭时 `?tenant_id` 由调用方自报、系统完全信任，且系统租户（ID 为 `0`）可访问任意实例。因此默认配置下，任何未认证调用者只需在 query 携带 `tenant_id=0` 即可冒充系统租户访问集群内任意实例，跨租户隔离不生效。**生产环境必须开启 `enable_func_token_auth=true`**，由 JWT 的 `sub` 决定调用方真实租户。

## URI

### WebSocket

`GET /serverless/v1/ws?instance=<instance_id>&port=<port>&tenant_id=<tenant_id>(&token=<jwt>)`

### HTTP

`<METHOD> /serverless/v1/http?instance=<instance_id>&port=<port>&tenant_id=<tenant_id>(&token=<jwt>)`

`METHOD` 任意（GET / POST / PUT / DELETE …），透传通道不约束上层 method。`port` 省略时默认 `18092`。

### SSH

`ssh -p 2222 'yr:instance:<instance_id>:port=<port>'@<frontend_host>`

username 格式固定前缀 `yr:instance:`，后接 `<instance_id>` 与可选 `port=<n>` 键值对，多段以 `:` 分隔。各段经 `url.PathUnescape` 解码——仅当 instanceID 或选项值本身含 `:` 等 URL 保留字符时才需 percent-encode（如 `:` 编码为 `%3A`），普通 UUID（如 `e836df1a-...`）直接原样传入，不要编码，否则会被当作不同字符串导致路由失败。省略 `port` 时由实例 `rootfs.ports` 决定（首个 sshd 端口）。

## 请求参数

### WS / HTTP Query 参数

| **参数** | **是否必选** | **参数类型** | **描述** |
| -------- | ---------- | ---------- | ----------- |
| instance | 是 | string | 目标实例 ID（UUID，来自 create 响应）。 |
| port | 否 | int | 沙箱内服务端监听端口，`1`–`65535`。省略默认 `18092`。 |
| tenant_id | 鉴权关时推荐 | string | 调用方租户。鉴权关时取此值（缺省 `default`）；鉴权开时忽略（由 JWT `sub` 决定）。 |
| token | 鉴权开时必选 | string | JWT。鉴权开时须提供（亦可通过 `X-Auth` header / cookie / subprotocol 携带）。 |

### SSH username 参数

| **段** | **是否必选** | **描述** |
| ------ | ---------- | -------- |
| `yr` | 是 | 固定路由前缀。 |
| `instance` | 是 | 固定，标识按实例路由。 |
| `<instance_id>` | 是 | 目标实例 UUID。 |
| `port=<n>` | 否 | 实例内 sshd 端口。省略时由实例 `rootfs.ports` 决定。 |

## 响应参数

### WS / HTTP

响应体由**沙箱内服务端**产生（非 frontend）。frontend 原样透回 status line + headers + body，含流式 chunked / SSE——前端不组装、不缓存。失败路径（Hijack 前）返回：

| **HTTP 状态** | **触发条件** |
| ------------- | ----------- |
| 400 | 缺 `instance` 参数 / `port` 非法。 |
| 401 | 鉴权失败（无 token / JWT 无效）。 |
| 403 | 跨租户（调用方非 system 且不持有该实例）。 |
| 502 | 实例不存在 / 非 `RUNNING` / `dialTunnel` 失败（端口未监听 / 服务端未起）。 |

### SSH

SSH 握手成功后进入交互式 session 或端口转发，字节双向透传。握手 / 路由 / 后端拨号失败时连接被拒，客户端收到 SSH 断连（非 HTTP 状态码）。常见失败：`public key ... is not authorized`（客户端公钥不在 `authorized_keys`）、`wait for instance ... route`（实例非 RUNNING 或路由未就绪）、后端 sshd 未起（`port` 不对 / `ssh_enable` 未开致公钥未注入）。

## 示例

### 前置：创建带服务端端口的实例

```bash
# SSH：rootfs 声明 sshd 端口，集群开 ssh_enable（公钥自动注入）
curl -X POST http://{frontend}:8888/api/agent -H "Content-Type: application/json" -d '{
  "name": "agent-ssh", "namespace": "dev",
  "runtime_spec": {
    "runtime": "python3.11", "sandbox_type": "docker",
    "rootfs": {"imageurl": "yr-docker-runtime:v0", "user": "agentos", "ports": ["tcp:22"]}
  }
}'
# WS/HTTP：cmds 拉起沙箱内 WS/HTTP server，rootfs.ports 声明监听端口
curl -X POST http://{frontend}:8888/api/agent -H "Content-Type: application/json" -d '{
  "name": "agent-http", "namespace": "dev",
  "runtime_spec": {
    "runtime": "python3.11", "sandbox_type": "supervisor",
    "rootfs": {"imageurl": "ws-agent-runtime:latest", "user": "root", "ports": ["tcp:18092"]},
    "cmds": [["python3.11", "/home/root/http_server.py"]],
    "cpu": 2000, "memory": 4096
  }
}'
```

响应（取 `instance_id`）：

```json
{"code":200,"instance_id":"e836df1a-b800-4000-8000-00004f815568"}
```

### SSH 调用

```bash
# 交互式登录（实例 sshd 在 22，rootfs.ports 声明 tcp:22，ssh_enable 已开）
ssh -p 2222 'yr:instance:e836df1a-b800-4000-8000-00004f815568'@<frontend_host> -i ~/.ssh/client_key

# 指定端口（实例 sshd 监听 2222）
ssh -p 2222 'yr:instance:e836df1a-b800-4000-8000-00004f815568:port=2222'@<frontend_host> -i ~/.ssh/client_key

# 端口转发：把本地 8080 转发到实例内 18092
ssh -p 2222 -L 8080:127.0.0.1:18092 \
  'yr:instance:e836df1a-b800-4000-8000-00004f815568'@<frontend_host> -i ~/.ssh/client_key
```

> username 含 `:`，shell 中须用单引号包住。`-p 2222` 是 frontend bastion 端口，非实例 sshd 端口。客户端公钥须在 `YR_FRONTEND_SSH_AUTHORIZED_KEYS` 内。

### WebSocket 调用

```bash
# 鉴权关（默认）
curl -sS -i -N \
  "http://{frontend}:8888/serverless/v1/ws?instance=e836df1a-b800-4000-8000-00004f815568&port=18092&tenant_id=default" \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ=="

# 鉴权开（浏览器无法设自定义 header，JWT 经 subprotocol 携带）
const ws = new WebSocket(
  "ws://{frontend}:8888/serverless/v1/ws?instance=<id>&port=18092",
  ["<jwt>"]
);
```

### HTTP 调用

```bash
# 请求-响应往返
curl -sS -i -X POST \
  "http://{frontend}:8888/serverless/v1/http?instance=e836df1a-b800-4000-8000-00004f815568&port=18092&tenant_id=default" \
  -H "Content-Type: application/json" -d '{"any":"body"}'

# 流式（SSE）
curl -sS -N \
  "http://{frontend}:8888/serverless/v1/http?instance=e836df1a-b800-4000-8000-00004f815568&port=18092&tenant_id=default&mode=sse"
```

实测响应（沙箱内 server 产生，原样透回）：

```bash
HTTP/1.1 200 OK
Content-Length: 59

method=POST path=/serverless/v1/http body=hello-from-client
```

### 失败路径

```bash
curl -sS -o /dev/null -w "%{http_code}" \
  "http://{frontend}:8888/serverless/v1/http?instance=<id>&port=18094&tenant_id=default"  # 502 端口未监听
curl -sS -o /dev/null -w "%{http_code}" \
  "http://{frontend}:8888/serverless/v1/http?tenant_id=default&port=18092"                # 400 缺 instance
```

## 错误码

WS/HTTP 通道的 `code`（HTTP 状态码）见上文"响应参数"表。SSH 通道无 HTTP 状态码，失败表现为 SSH 连接拒绝。

| **HTTP 状态** | **描述** |
| ------------- | -------- |
| 400 | 错误的请求：缺 `instance`、`port` 非法。 |
| 401 | 未认证：鉴权开但无 token / JWT 无效。 |
| 403 | 禁止：跨租户访问且非 system 租户。 |
| 502 | 网关错误：实例不存在 / 非 `RUNNING` / `dialTunnel` 失败（端口未监听 / 服务端未起）。 |
