# Rust Sandbox Runtime (RRT)

RRT (`rrt-runtime`) is the Rust-native runtime daemon for openYuanrong sandboxes. It runs inside each sandbox instance and provides the sandbox data-plane primitives used by the frontend, sandboxRouter, RuntimeRPC, and sandbox SDK.

External REST/WebSocket contracts are documented in [`../features/sandbox-rest-api.md`](../features/sandbox-rest-api.md). This document records the current runtime implementation and the internal design contracts.

---

## 1. Scope

RRT is a thin sandbox runtime peer. It provides:

- process execution and process lifecycle primitives;
- filesystem read/write/list/stat/remove primitives;
- persistent shell sessions;
- binary upload/download for files and directories;
- reverse tunnel Port-A/Port-B service;
- RuntimeRPC readiness, invoke, heartbeat, shutdown, and busy/idle reporting.

RRT does not load user functions, link libruntime, or own datasystem object storage. User code runs as normal processes inside the sandbox rootfs/container.

---

## 2. Runtime modes

`api/rust/rrt-daemon/src/bin/rrt-runtime.rs` selects the mode from environment variables.

| Mode | Trigger | Purpose |
|------|---------|---------|
| runtime mode | default | Production mode: RuntimeRPC worker + optional HTTP direct server + optional reverse tunnel server. |
| HTTP-only | `RRT_HTTP_ONLY=1` | Start only the RRT HTTP atomic-operation server for local/component tests. |
| tunnel-only | `RRT_TUNNEL_ONLY=1` | Start only the reverse tunnel server for TunnelClient interoperability tests. |

Runtime mode starts the enabled planes concurrently:

```text
function-proxy RuntimeRPC  <── MessageStream ──  rrt-runtime
frontend /direct           ── sandboxRouter ──►  RRT HTTP server (:50090 by default)
frontend /tunnel           ── sandboxRouter ──►  RRT tunnel WS Port-A (:8765 by default)
sandbox process            ──────────────────►  RRT tunnel HTTP Port-B (127.0.0.1:8766 by default)
```

The HTTP direct server starts when `RRT_HTTP_PORT` is set. The reverse tunnel server starts when `RRT_TUNNEL_WS_PORT` is set.

---

## 3. Source layout

```text
api/rust/rrt-daemon/
├── src/bin/rrt-runtime.rs       # mode entrypoint
├── src/runtime/
│   ├── mod.rs                   # env loading, RuntimeRPC MessageStream, reconnect, shutdown
│   ├── activity.rs              # busy/idle activity counter and signal reporting
│   ├── dispatch.rs              # RuntimeRPC sandbox_invoke action dispatch
│   ├── httpserver.rs            # /invoke, /upload, /download, healthz HTTP/1.1 server
│   ├── cmd.rs                   # process exec/start/poll/wait/kill/list/stdin
│   ├── fs.rs                    # filesystem primitives
│   ├── stream.rs                # tar/file stream helpers
│   ├── bash.rs                  # persistent shell sessions
│   ├── tunnel.rs                # reverse tunnel WS/HTTP bridge
│   ├── codec.rs                 # openYuanrong return-value serialization
│   └── pyval.rs                 # Python-compatible value representation
├── proto/posix/                 # RuntimeRPC / POSIX protobufs
└── tests/                       # component tests for process/fs/port/tunnel/rrtctl
```

---

## 4. Environment contract

### 4.1 RuntimeRPC identity and control

RRT reads RuntimeRPC identity from the same environment variables used by standard openYuanrong runtimes.

| Variable | Required | Meaning |
|----------|----------|---------|
| `POSIX_LISTEN_ADDR` | yes | function-proxy RuntimeRPC endpoint. Preferred over `YR_SERVER_ADDRESS`. |
| `YR_SERVER_ADDRESS` | fallback | RuntimeRPC endpoint fallback when `POSIX_LISTEN_ADDR` is absent. |
| `YR_RUNTIME_ID` | yes | Runtime ID used in RuntimeRPC metadata. |
| `INSTANCE_ID` | recommended | Sandbox instance ID. If absent, RRT derives it from `YR_RUNTIME_ID`. |
| `YR_JOB_ID` | optional | Job ID for logs/diagnostics. |
| `YR_FUNCTION_LIB_PATH` / `FUNCTION_LIB_PATH` / `YR_RT_WORKING_DIR` | optional | Runtime deploy/working directory metadata. |
| `YR_LOG_LEVEL` | optional | `DEBUG` enables per-request debug logs; default is `INFO`. |

RRT does not require command-line arguments in production. The executor can start it from the rootfs/bootstrap command and rely on environment variables for runtime identity.

### 4.2 HTTP and tunnel ports

Frontend owns the RRT internal port settings for sandbox creation. SDK callers do not specify these ports.

| Variable | Default owner | Meaning |
|----------|---------------|---------|
| `RRT_HTTP_PORT` | frontend create | Enables RRT HTTP direct server. Current default control port is `50090`. |
| `RRT_HTTP_TOKEN` | deployment/fronting layer | Optional static token checked by RRT HTTP server when set. |
| `RRT_TUNNEL_WS_PORT` | frontend create when tunnel is enabled | Enables reverse tunnel WS Port-A. Current default is `8765`. |
| `RRT_TUNNEL_HTTP_PORT` | frontend create when tunnel is enabled | Sandbox-local HTTP Port-B. Current default is `8766`. |

### 4.3 Isolated test modes

```bash
RRT_HTTP_ONLY=1 RRT_HTTP_PORT=50090 rrt-runtime
RRT_TUNNEL_ONLY=1 RRT_TUNNEL_WS_PORT=8765 RRT_TUNNEL_HTTP_PORT=8766 rrt-runtime
```

---

## 5. RuntimeRPC contract

RRT opens `RuntimeRpc.MessageStream` to function-proxy.

### 5.1 Metadata

The stream request must carry:

| Metadata | Value |
|----------|-------|
| `runtime_id` | `YR_RUNTIME_ID` |
| `instance_id` | `INSTANCE_ID` or derived instance ID |
| `source_id` | instance ID |
| `dst_id` | `function-proxy` |

### 5.2 Inbound messages

| Message | RRT behavior |
|---------|--------------|
| `HeartbeatReq` | Reply with `HeartbeatRsp`. |
| `CallReq{is_create=true}` | Reply success so the sandbox becomes ready. |
| `CallReq{is_create=false}` | Dispatch by function name/action and return `CallResultReq`. |
| `ShutdownReq` | Wait for in-flight activity to drain within the grace period, then reply with success or busy error. |
| `CallResultAck` / `KillRsp` | Accepted for observability/debug flow. |

`CallReq` dispatch uses a blocking worker task so long-running commands do not block the MessageStream receive loop or heartbeat responses.

### 5.3 MessageStream reconnect

RRT reconnects to the RuntimeRPC endpoint with exponential backoff from 200 ms to 5 s. After each successful stream open, it sends the current busy/idle state to resynchronize function-proxy IdleMgr.

Outbound retry policy:

- `CallResultReq` is retained and retried on stream send failure.
- busy/idle `KillReq(signal=23)` and heartbeat responses are not retried.
- other outbound runtime messages are retryable.

### 5.4 Return serialization

RuntimeRPC invoke results are returned inline through `CallResult.smallObjects`. RRT serializes simple values using the openYuanrong cross-language buffer layout:

```text
[8-byte metadata header][8-byte msgpack-size header][msgpack payload][optional cloudpickle payload]
```

For current RRT action results, the metadata header is zeroed and the payload is `rmp_serde` msgpack data.

---

## 6. HTTP direct server

The HTTP direct server is enabled by `RRT_HTTP_PORT`. Frontend exposes it through `/direct/{safeID}/...` and maps that path to sandboxRouter internally. SDKs should not expose or construct the internal `50090` port path.

### 6.1 Routes

| Method | RRT path | Purpose |
|--------|----------|---------|
| `GET` | `/healthz` | Health probe. |
| `POST` | `/invoke` | JSON action invoke: `{action,args,requestId?}`. |
| `POST` | `/upload?path=<abs>&type=file|tar` | Binary file upload or tar directory upload. |
| `GET` | `/upload/status?path=<abs>&uploadId=<id>` | Resumable file upload status. |
| `POST` | `/upload/commit?path=<abs>&uploadId=<id>&totalSize=<n>` | Commit a resumable file upload. |
| `GET` | `/download?path=<abs>&type=file|tar` | Binary file download or tar directory download. |

### 6.2 Invoke reliability

`/invoke` supports request de-duplication by request ID:

- `X-YR-Request-ID` header or JSON body `requestId` is used as the de-dup key.
- The owner request executes the action and stores the completed response in a short-lived cache.
- Concurrent or repeated requests with the same ID wait for or replay the cached response.

### 6.3 Binary transfer

File upload/download use raw HTTP bodies, not JSON/base64.

- File upload supports direct full upload and resumable chunk upload with `uploadId` + `offset` + `commit`.
- File download supports `Range: bytes=<offset>-` and returns `206 Partial Content` with `Content-Range`.
- Directory upload/download uses tar streams.
- `Content-Length` and `Transfer-Encoding: chunked` are both supported.

---

## 7. Action model

RuntimeRPC `sandbox_invoke` and HTTP `/invoke` share the same action implementation.

Primary action groups:

| Group | Actions |
|-------|---------|
| Process | `process.exec`, `process.start`, `process.poll`, `process.wait`, `process.kill`, `process.list`, `process.send_stdin` |
| Filesystem | `file.read`, `file.write`, `file.write_chunk`, `file.read_chunk`, `file.list`, `file.exists`, `file.remove`, `file.stat`, `file.mkdir` |
| Shell | `shell.create`, `shell.run`, `shell.poll`, `shell.delete` |

Public SDKs should use the canonical action names documented in `sandbox-rest-api.md`.

---

## 8. Reverse tunnel

When the create request enables tunnel support, frontend injects tunnel ports and returns tunnel metadata in the create response.

```json
{
  "tunnel": {
    "url": "/tunnel/<safeID>",
    "path": "/tunnel/<safeID>",
    "wsPath": "/tunnel/<safeID>",
    "proxyUrl": "http://127.0.0.1:8766",
    "proxyPort": 8766
  }
}
```

Runtime topology:

```text
Local upstream service
       ▲
       │ Python TunnelClient over ws(s)://<gateway>/tunnel/<safeID>
       ▼
RRT tunnel WS Port-A (:8765) ── RRT tunnel HTTP Port-B (127.0.0.1:8766)
       ▲
       │ sandbox code calls http://127.0.0.1:8766/...
       ▼
Sandbox process
```

Tunnel frames are WebSocket text frames containing JSON. HTTP and WebSocket payload bodies are base64 encoded in frame fields such as `body` or `data`.

The tunnel route is authorized by the sandbox create action and frontend route policy. The tunnel server itself does not receive the platform JWT.

---

## 9. Busy/idle and graceful shutdown

RRT maintains one global activity counter across:

- RuntimeRPC call handling;
- HTTP direct requests;
- reverse tunnel WS connections.

An `ActiveGuard` increments the counter on entry and decrements it on drop. RRT reports activity state to function-proxy only when the counter crosses the zero boundary:

| Transition | Message |
|------------|---------|
| `0 -> 1` | `KillRequest{signal=23,payload="busy"}` |
| `1 -> 0` | debounced `KillRequest{signal=23,payload="idle"}` |

Function-proxy maps signal `23` (`RRT_IDLE_REPORT_SIGNAL`) to IdleMgr traffic reporting. RRT does not own the idle timeout timer.

On `ShutdownReq`, RRT waits up to the requested grace period for in-flight activity to finish:

- if drained, reply success;
- if still busy, reply `ErrInstanceBusy` with the active request count;
- RRT does not exit its own process in response to graceful shutdown.

---

## 10. Routing, auth, and trace boundaries

| Path | Auth boundary | RRT receives |
|------|---------------|--------------|
| `/api/sandbox/v1/...` | frontend JWT middleware | RuntimeRPC action, no platform JWT in RRT. |
| `/direct/{safeID}/...` | frontend JWT middleware + internal sandboxRouter hop | HTTP request with platform credentials stripped. |
| `/tunnel/{safeID}` | frontend tunnel route policy | Tunnel WebSocket without platform JWT. |
| `/{safeID}/{port}` | sandboxRouter route policy | User-service traffic for declared ports. |

Trace behavior:

- frontend initializes or propagates `X-Trace-ID`;
- `/direct` responses include `X-Trace-ID`;
- RRT HTTP access logs include the trace ID when provided;
- RRT falls back to request ID for RuntimeRPC-side access trace labels when no trace ID is present.

RRT log format:

```text
[yyyy-mm-dd HH:MM:SS.mmm LEVEL] message
```

`INFO`/`DEBUG` logs go to stdout. `WARN`/`ERROR` logs go to stderr.

---

## 11. Build and artifacts

RRT is built in the Rust compile image, not on the host.

```bash
bash scripts/build_rrt_runtime.sh
cargo test --manifest-path api/rust/Cargo.toml -p rrt-daemon
```

The release builder selects `x86_64-unknown-linux-musl` or
`aarch64-unknown-linux-musl` from the requested architecture. Before staging an
artifact, it verifies that the ELF machine matches the target and that the
binary has neither an ELF interpreter nor shared-library dependencies. The
release artifact is packaged as the `openyuanrong-rrt` Python wheel. The wheel
contains the `rrt-runtime` binary and exposes `openyuanrong_rrt.runtime_path()`
so bootstrap commands can locate the binary without hard-coded paths.

Current CI packaging model:

- Build RRT amd64 builds `rrt-runtime` and the `openyuanrong-rrt` wheel.
- Build RRT arm64 builds the corresponding native musl runtime and wheel.
- Build Runtime images install the wheel into the runtime image.
- Build Manifest includes the RRT wheel and sandbox SDK artifacts.

---

## 12. Deployment contract

A sandbox runtime image must contain:

- `rrt-runtime` installed through `openyuanrong-rrt`;
- a shell and basic userland required by sandbox commands;
- Python only when the image needs Python user code or examples.

Functionsystem executor behavior:

- a service with custom rootfs plus bootstrap is treated as self-contained;
- sandbox and sandboxd executors skip language wrapper argument construction for self-contained bootstraps;
- `YR_LANGUAGE` follows the service runtime field;
- sandboxd only sends `template_id` when the template is known to be registered;
- custom rootfs sandbox starts do not require a warmup template.

Frontend create behavior:

- owns internal RRT port injection;
- adds RRT HTTP/tunnel ports to sandbox network ports;
- exposes control/data routes through frontend and sandboxRouter;
- applies a short default graceful shutdown budget for RRT sandboxes.

---

## 13. Verification entrypoints

Focused local checks:

```bash
# RRT unit/component tests
cd api/rust/rrt-daemon && cargo test -p rrt-daemon

# Activity transition unit tests
cd api/rust/rrt-daemon && cargo test --lib runtime::activity

# HTTP-only mode
RRT_HTTP_ONLY=1 RRT_HTTP_PORT=50090 rrt-runtime

# Tunnel-only mode
RRT_TUNNEL_ONLY=1 RRT_TUNNEL_WS_PORT=8765 RRT_TUNNEL_HTTP_PORT=8766 rrt-runtime
```

End-to-end checks live in the sandbox SDK and K8S pipeline:

- `sandbox-sdk/python/tests/test_transport_direct.py` validates SDK direct-route construction and fallback behavior.
- `sandbox-sdk/python/tests/e2e_rrt_direct.py` validates live frontend `/direct` command and file paths.
- `.buildkite/test_sandbox_k8s.sh` runs idle-timeout, direct, upload/download, and example checks against a deployed cluster.
