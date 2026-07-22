# Agent Instance Protocol Invocation Channels

## Description

After an agent instance is created ([Create Agent Instance](./create_agent_instance.md)) and stays running, an in-sandbox server listens on a port (e.g. sshd / WS server / HTTP server). External callers reach that port via one of frontend's three L4 passthrough channels. All three are transparent to the upper-layer protocol, route by `instance + port`, and reuse the function_proxy `tcp.tunnel`:

| Channel | Endpoint | Use case | Route carrier |
| ------- | -------- | -------- | ------------- |
| SSH | frontend `:2222` (independent listener) | Holds an SSH client; needs interactive shell / port forwarding | SSH username field |
| WebSocket | `GET /serverless/v1/ws` | Holds a WS client (browser, `websockets` lib) | URL query |
| HTTP | `ANY /serverless/v1/http` | Can only issue plain HTTP (`fetch` / `curl` / XHR) | URL query |

All three ultimately go through `resolveInstance` → `dialTunnel` (function_proxy `tcp.tunnel`) to reach the in-container service. frontend and function_proxy only move bytes end-to-end and never parse upper-layer protocol frames.

## Prerequisites

- The openYuanrong cluster is deployed and healthy; frontend is up (`yr start --master`, frontend included by default).
- An instance has been created via [Create Agent Instance](./create_agent_instance.md) and its `instance_id` obtained; the instance is `RUNNING`.
- The in-sandbox server is listening on `?port` (WS/HTTP) or the instance sshd port (SSH). A server not started / port not listening yields `502` (WS/HTTP) or connection failure (SSH).
- The function_proxy `tcp.tunnel` is published (log line `TCP tunnel listening on 127.0.1.1:22775`). The SSH channel additionally requires `ssh_enable=true` at create time (injects the platform public key into the rootfs as a read-only mount, see the switch below).

## Constraints

- **Route key**: WS/HTTP use URL query (`?instance=<id>&port=<n>`); SSH carries the route in the username field (`yr:instance:<id>:port=<n>`) since the SSH handshake has no query string. All three resolve to `instance + port`.
- **L4 passthrough**: frontend does not terminate the upper-layer protocol or parse frames. HTTP requests/responses (including streaming chunked / SSE), WS frames, and SSH channel bytes are relayed verbatim through the tunnel. Streaming response bytes flow back continuously; frontend does not buffer, assemble, or demux.
- **Auth**: WS/HTTP share `IamConfig.EnableFuncTokenAuth` (default off → takes `?tenant_id` or `default`; on → validates JWT); SSH uses `YR_FRONTEND_SSH_AUTH_ENABLE` separately (default on → validates `authorized_keys`). Cross-tenant check: the system tenant may reach any instance; otherwise the instance owner must match the caller.
- **Failure paths** (WS/HTTP): all pre-work (auth / route resolution / tunnel dial) happens in the HTTP layer before Hijack, returning a clean `4xx/502` on failure; after Hijack there is no HTTP layer left to write an error code, connection issues surface as RST/FIN from the peer.
- **Port range**: `1`–`65535`. When `port` is omitted in WS it defaults to `18092` (aligned with the AgentServer default WS port; only a "guess when omitted", it does not constrain the server to listen on this port).

## Switches and Configuration

### Deployment Switches

All three channels depend on the function_proxy `tcp.tunnel` underneath. Whether this listener and each channel are started is controlled by the following top-level switches at cluster deploy time:

| Switch | Default | Effect |
| ------ | ------- | ------ |
| `ssh_enable` | `false` | Enables the SSH channel. When on, it **also turns on `tcp.tunnel` automatically** (the SSH path depends on it) and injects the platform public key into the rootfs as a read-only mount at create time. |
| `enable_tcp_tunnel` | `false` | Enables `tcp.tunnel` standalone, **independently of `ssh_enable`**. Use this for WS/HTTP passthrough without SSH keys. |

Relationship in short:

- To use the **SSH channel** → set only `ssh_enable=true` (`tcp.tunnel` comes along; no need to also set `enable_tcp_tunnel`).
- To use only the **WS/HTTP channels**, no SSH → set `enable_tcp_tunnel=true` (no SSH keys configured).
- `tcp.tunnel` listens on `127.0.1.1:22775` (`tcp_tunnel_port` is tunable, default 22775) with a concurrent connection limit of 1024 (`tcp_tunnel_max_connections` is tunable). Once ready the log prints `TCP tunnel listening on 127.0.1.1:22775`.

### SSH Channel Environment Variables

The SSH bastion is enabled by `YR_FRONTEND_SSH_ENABLE`; once on, frontend starts an independent listener on `:2222` and process-level behavior is tuned by the following environment variables:

| Environment variable | Default | Description |
| -------------------- | ------- | ----------- |
| `YR_FRONTEND_SSH_ENABLE` | `false` | Whether to enable the SSH bastion. Empty / unset is treated as off. |
| `YR_FRONTEND_SSH_ADDR` | `:2222` | SSH bastion listen address. |
| `YR_FRONTEND_SSH_AUTH_ENABLE` | `true` | Whether to validate client public keys. Off → `NoClientAuth` (any client may connect, for validation only). |
| `YR_FRONTEND_SSH_HOST_KEY` | — | Host private key file path frontend presents to clients (required when SSH is enabled). |
| `YR_FRONTEND_SSH_BACKEND_KEY` | — | Backend private key file path frontend uses to dial the instance sshd (required when SSH is enabled). |
| `YR_FRONTEND_SSH_AUTHORIZED_KEYS` | — | Client public key allowlist file path (required when `AUTH_ENABLE=true`). |
| `YR_FRONTEND_SSH_ROUTE_WAIT_TIMEOUT_SECONDS` | `10` | Seconds to wait for the instance route to be ready (RUNNING + functionProxyID + tunnel address). |
| `YR_FRONTEND_SSH_BACKEND_RETRY_ATTEMPTS` | `10` | Retry attempts after a failed dial to the instance sshd. |
| `YR_FRONTEND_SSH_BACKEND_RETRY_INTERVAL_MS` | `500` | Retry interval in milliseconds. |
| `YR_FRONTEND_SSH_MAX_CONNECTIONS` | `1024` | Concurrent connection limit. |

### SSH Key Configuration

The SSH channel involves three key sets with distinct roles:

| Key | Role | Environment variable | Generation example |
| --- | ---- | -------------------- | ------------------ |
| host key | frontend → client: the bastion's identity to the outside | `YR_FRONTEND_SSH_HOST_KEY` | `ssh-keygen -t ed25519 -f host_key -N ""` |
| backend key | frontend → instance sshd: used for the second SSH hop | `YR_FRONTEND_SSH_BACKEND_KEY` | `ssh-keygen -t ed25519 -f backend_key -N ""` |
| authorized_keys | client → frontend: the client public key allowlist | `YR_FRONTEND_SSH_AUTHORIZED_KEYS` | each client runs `ssh-keygen`, public keys aggregated into this file |

The **public key** of the backend key must be injected into the instance rootfs `authorized_keys` (done automatically by `ssh_enable=true` at create time as a read-only mount), otherwise the instance sshd rejects the frontend's backend connection. Host-key verification is intentionally disabled for the backend hop (`InsecureIgnoreHostKey`, because instance host keys are dynamic and frontend cannot know them in advance).

### WS / HTTP Channel Switch

WS and HTTP channels **start with frontend and have no independent switch** (routes `r.GET("/serverless/v1/ws")` / `r.Any("/serverless/v1/http")` are registered in `InitRoute`). Auth is governed uniformly by `IamConfig.EnableFuncTokenAuth`:

| Config | Default | WS/HTTP behavior |
| ------ | ------- | ---------------- |
| `enable_func_token_auth` | `false` | Off: takes `?tenant_id`, defaults to `default`, trusts the caller's self-reported tenant. |
| `enable_func_token_auth` | `true` | On: validates JWT; token taken from `X-Auth` header / `?token` / `iam_token` cookie / `Sec-WebSocket-Protocol` subprotocol (browsers cannot set custom headers, so the JWT is carried as a subprotocol). |

> **Security warning**: `enable_func_token_auth` is off by default. When off, `?tenant_id` is self-reported by the caller and fully trusted, and the system tenant (ID `0`) may reach any instance. Thus under the default configuration any unauthenticated caller can impersonate the system tenant by carrying `tenant_id=0` in the query and access any instance in the cluster — cross-tenant isolation does not hold. **Production deployments must enable `enable_func_token_auth=true`** so the caller's real tenant is decided by the JWT `sub`.

## URI

### WebSocket

`GET /serverless/v1/ws?instance=<instance_id>&port=<port>&tenant_id=<tenant_id>(&token=<jwt>)`

### HTTP

`<METHOD> /serverless/v1/http?instance=<instance_id>&port=<port>&tenant_id=<tenant_id>(&token=<jwt>)`

`METHOD` is arbitrary (GET / POST / PUT / DELETE …); the passthrough channel does not constrain the upper-layer method. `port` defaults to `18092` when omitted.

### SSH

`ssh -p 2222 'yr:instance:<instance_id>:port=<port>'@<frontend_host>`

The username uses the fixed prefix `yr:instance:`, followed by `<instance_id>` and optional `port=<n>` key-value pairs, segments separated by `:`. Each segment is `url.PathUnescape`-decoded by the server — percent-encode only when the instanceID or an option value itself contains URL-reserved characters like `:` (e.g. `:` → `%3A`); a plain UUID (e.g. `e836df1a-...`) must be passed verbatim, not encoded, otherwise it is treated as a different string and routing fails. When `port` is omitted it is determined by the instance `rootfs.ports` (the first sshd port).

## Request Parameters

### WS / HTTP Query Parameters

| **Parameter** | **Required** | **Type** | **Description** |
| ------------- | ------------ | -------- | --------------- |
| instance | Yes | string | Target instance ID (UUID, from the create response). |
| port | No | int | In-sandbox server listen port, `1`–`65535`. Defaults to `18092` when omitted. |
| tenant_id | Recommended when auth off | string | Caller tenant. Taken when auth is off (defaults to `default`); ignored when auth is on (decided by JWT `sub`). |
| token | Required when auth on | string | JWT. Must be provided when auth is on (also via `X-Auth` header / cookie / subprotocol). |

### SSH Username Parameters

| **Segment** | **Required** | **Description** |
| ----------- | ------------ | --------------- |
| `yr` | Yes | Fixed route prefix. |
| `instance` | Yes | Fixed, indicates instance-based routing. |
| `<instance_id>` | Yes | Target instance UUID. |
| `port=<n>` | No | In-container sshd port. When omitted, determined by the instance `rootfs.ports`. |

## Response Parameters

### WS / HTTP

The response body is produced by the **in-sandbox server** (not frontend). frontend relays the status line + headers + body verbatim, including streaming chunked / SSE — no assembly, no buffering. Failure paths (before Hijack) return:

| **HTTP status** | **Trigger** |
| --------------- | ----------- |
| 400 | Missing `instance` parameter / invalid `port`. |
| 401 | Auth failure (no token / invalid JWT). |
| 403 | Cross-tenant (caller is not system and does not own the instance). |
| 502 | Instance does not exist / not `RUNNING` / `dialTunnel` failed (port not listening / server not started). |

### SSH

After the SSH handshake succeeds, an interactive session or port forwarding is entered; bytes are relayed bidirectionally. On handshake / route / backend dial failure the connection is refused and the client sees an SSH disconnect (not an HTTP status). Common failures: `public key ... is not authorized` (client public key not in `authorized_keys`), `wait for instance ... route` (instance not RUNNING or route not ready), backend sshd not started (`port` wrong / `ssh_enable` off so the public key was not injected).

## Examples

### Prerequisite: create an instance with a server port

```bash
# SSH: rootfs declares the sshd port, cluster enables ssh_enable (key auto-injected)
curl -X POST http://{frontend}:8888/api/agent -H "Content-Type: application/json" -d '{
  "name": "agent-ssh", "namespace": "dev",
  "runtime_spec": {
    "runtime": "python3.11", "sandbox_type": "docker",
    "rootfs": {"imageurl": "yr-docker-runtime:v0", "user": "agentos", "ports": ["tcp:22"]}
  }
}'
# WS/HTTP: cmds starts the in-sandbox WS/HTTP server, rootfs.ports declares the listen port
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

Response (take the `instance_id`):

```json
{"code":200,"instance_id":"e836df1a-b800-4000-8000-00004f815568"}
```

### SSH Invocation

```bash
# Interactive login (instance sshd on 22, rootfs.ports declares tcp:22, ssh_enable on)
ssh -p 2222 'yr:instance:e836df1a-b800-4000-8000-00004f815568'@<frontend_host> -i ~/.ssh/client_key

# Specify the port (instance sshd listens on 2222)
ssh -p 2222 'yr:instance:e836df1a-b800-4000-8000-00004f815568:port=2222'@<frontend_host> -i ~/.ssh/client_key

# Port forwarding: forward local 8080 to in-instance 18092
ssh -p 2222 -L 8080:127.0.0.1:18092 \
  'yr:instance:e836df1a-b800-4000-8000-00004f815568'@<frontend_host> -i ~/.ssh/client_key
```

> The username contains `:`; in a shell it must be wrapped in single quotes. `-p 2222` is the frontend bastion port, not the instance sshd port. The client public key must be in `YR_FRONTEND_SSH_AUTHORIZED_KEYS`.

### WebSocket Invocation

```bash
# Auth off (default)
curl -sS -i -N \
  "http://{frontend}:8888/serverless/v1/ws?instance=e836df1a-b800-4000-8000-00004f815568&port=18092&tenant_id=default" \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ=="

# Auth on (browsers cannot set custom headers; the JWT is carried via subprotocol)
const ws = new WebSocket(
  "ws://{frontend}:8888/serverless/v1/ws?instance=<id>&port=18092",
  ["<jwt>"]
);
```

### HTTP Invocation

```bash
# Request-response round-trip
curl -sS -i -X POST \
  "http://{frontend}:8888/serverless/v1/http?instance=e836df1a-b800-4000-8000-00004f815568&port=18092&tenant_id=default" \
  -H "Content-Type: application/json" -d '{"any":"body"}'

# Streaming (SSE)
curl -sS -N \
  "http://{frontend}:8888/serverless/v1/http?instance=e836df1a-b800-4000-8000-00004f815568&port=18092&tenant_id=default&mode=sse"
```

Observed response (produced by the in-sandbox server, relayed verbatim):

```bash
HTTP/1.1 200 OK
Content-Length: 59

method=POST path=/serverless/v1/http body=hello-from-client
```

### Failure Paths

```bash
curl -sS -o /dev/null -w "%{http_code}" \
  "http://{frontend}:8888/serverless/v1/http?instance=<id>&port=18094&tenant_id=default"  # 502 port not listening
curl -sS -o /dev/null -w "%{http_code}" \
  "http://{frontend}:8888/serverless/v1/http?tenant_id=default&port=18092"                # 400 missing instance
```

## Error Codes

The `code` (HTTP status) of the WS/HTTP channels is in the "Response Parameters" table above. The SSH channel has no HTTP status; failures surface as an SSH connection refusal.

| **HTTP status** | **Description** |
| --------------- | --------------- |
| 400 | Bad request: missing `instance`, invalid `port`. |
| 401 | Unauthorized: auth on but no token / invalid JWT. |
| 403 | Forbidden: cross-tenant access and not the system tenant. |
| 502 | Bad gateway: instance does not exist / not `RUNNING` / `dialTunnel` failed (port not listening / server not started). |
