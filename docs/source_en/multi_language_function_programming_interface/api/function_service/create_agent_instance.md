# Create Agent Instance

## Description

This API is used to create a resident agent instance in the openYuanrong cluster. An agent instance carries user business logic, stays alive after creation until explicitly killed.

Container metadata supports two sources:

| Mode | Metadata source | Use case |
| ---- | --------------- | -------- |
| inline | Carried directly by `runtime_spec` in the create request | Single-step creation, bypasses meta_service |
| registered | Registered funcMeta, associated via `urn` in create | Metadata reuse, multiple creates of the same function |

Both modes coexist: a request with `runtime_spec` uses inline, with `urn` uses registered; when both are present, inline takes precedence.

## Prerequisites

- The openYuanrong cluster is deployed and healthy; start the required components per mode:
  - inline mode (no function registration, bypasses meta_service): `yr start --master -s 'mode.master.frontend=true'`
  - registered mode (requires function registration first): `yr start --master -s 'mode.master.frontend=true' -s 'mode.master.meta_service=true'`
- **docker / supervisor services are prepared by the user**: openYuanrong does not manage the docker daemon or supervisor process. For `sandbox_type=docker` the host must have a working docker daemon (image pulled or pullable); for `sandbox_type=supervisor` the host must have a supervisor process. Unavailable services cause create to fail (e.g. `no Docker image specified`, container start failure).
- **Image requirements**: the docker executor inserts `yr_runtime_main.py` as the startup command for python runtime; the image must install the `openyuanrong` sdk whl package (which brings `yr_runtime_main.py`, faas_executor, yr runtime) into the image's default python site-packages.
- **workspace and UID alignment** (when `workspace` is provided): `workspace` is a directory on the host; the system automatically bind mounts it to `/home/<rootfs.user>` inside the container (`/workspace` when `rootfs.user` is empty) — the user only provides the host path, no need to specify the in-container mount point. Bind mount validates permissions by numeric UID (not by username); the UID of `rootfs.user` inside the image must match the owner UID of the host workspace directory, otherwise the container process gets `Permission denied` reading the workspace. For example, `rootfs.user=agentos`:

  ```bash
  # Host workspace owner UID (assume /home/snuser/workspaceA owned by snuser, UID=1002)
  stat -c '%u' /home/snuser/workspaceA   # 1002

  # agentos UID inside the image (must match)
  docker run --rm yr-docker-runtime:v0 -c 'id -u agentos'   # must be 1002; otherwise align in the image Dockerfile
  ```

  If the image UID differs from the host, options: ① image Dockerfile `useradd -u <hostUID> agentos` (recommended, once for all); ② `chown -R <containerUID> /home/snuser/workspaceA` to change host dir owner for testing; ③ `chmod -R 755` to relax permissions (testing only).

## Constraints

- `namespace`, `name` are required.
- inline mode requires `runtime_spec`, with both `runtime` and `rootfs.imageurl` non-empty.
- **registered mode requires registering the function first, then create**: first call [Register Function](./register_function.md) (`POST /serverless/v1/functions`, `kind` set to `agent`) to register the agent function, confirm the registration succeeded (response contains `functionVersionUrn`), then call create with that `functionVersionUrn` as `urn`. registered mode must carry `urn` pointing to that registered `kind=agent` function; an `urn` pointing to an unregistered or non-existent function returns 500 `failed to create agent`.
- `workspace` is optional: when non-empty it is bind mounted to `/home/<rootfs.user>` (`/workspace` when user is empty); when empty no mount is added. bind mount `source` (both `workspace` and `mounts[].source`) must be an absolute host path; `/`, `/etc`, `/proc`, `/sys`, `/dev`, `/boot`, `docker.sock`, and paths containing `..` are rejected. `mounts[].target` (in-container path) is not validated; the caller must ensure it does not overwrite sensitive in-container paths (e.g. `/etc/passwd`, `/proc`, `/sys`, `/dev`).
- **Auth**: `/api/agent` goes through frontend's global `GlobalJWTAuthMiddleware`, consistent with other cluster REST APIs. When `enable_func_token_auth` is off it is allowed by default (caller trusted); when on a valid JWT must be carried.

## URI

`POST /api/agent`

## Request Parameters

### Request Header Parameters

| **Parameter** | **Required** | **Type** | **Description** |
| ----------- | ---------- | ---------- | ----------- |
| Content-Type | Yes | string | Message body type. `application/json` recommended. |
| tenantId | No | string | Tenant ID. In inline mode funcKey is composed from tenantID, default `default`. |

### Request Body Parameters

#### Common Parameters (both modes)

| **Name** | **Type** | **Required** | **Description** |
| ----------- | ---------- | ---------- | ----------- |
| namespace | String | Yes | Instance namespace. |
| name | String | Yes | Instance name (must be unique within a tenant + namespace). The `instance_id` is generated by the backend scheduler (UUID), independent of `name`. |
| workspace | String | No | Absolute host path, bind mounted to `/home/<rootfs.user>` (`/workspace` when user is empty). When empty, no mount is added. |
| env_vars | map | No | Environment variables injected into the container. Sunk via `DELEGATE_ENV_VAR`, no `func-` prefix. inline mode carries all env; registered mode carries dynamic env (static env via funcMeta.Environment). |
| mounts | array | No | Extra bind mounts. Each item see Mount. |

#### inline Mode Parameters (`runtime_spec`)

| **Name** | **Type** | **Required** | **Description** |
| ----------- | ---------- | ---------- | ----------- |
| runtime_spec | Object | inline required | inline container config. |
| runtime_spec.runtime | String | inline required | Real language, mapped to faasExecutor. Values see Runtime Types. |
| runtime_spec.sandbox_type | String | No | executor dispatch. Values: `docker`, `supervisor`; empty falls to default RuntimeExecutor. |
| runtime_spec.rootfs | Object | inline required | Container rootfs config. |
| runtime_spec.rootfs.imageurl | String | inline required | docker image reference (e.g. `yr-docker-runtime:v0`). |
| runtime_spec.rootfs.user | String | No | Container run-as user (must exist in image). Empty runs as root — **high security risk** (in-container process has maximum privileges); production use should explicitly specify a non-root user. |
| runtime_spec.rootfs.ports | array | No | Container port forwarding. Format `[<proto>:]<port>`, where `<port>` is the in-container listen port (not the host port); proto values `tcp`/`udp` (default TCP). The docker executor dynamically allocates a host port mapped to this container port; the host port is system-decided and cannot be specified. |
| runtime_spec.cpu | int | No | CPU size, unit `1/1000` cores. Default `1000`. |
| runtime_spec.memory | int | No | Memory size, unit `MB`. Default `2048`. |

#### registered Mode Parameters

| **Name** | **Type** | **Required** | **Description** |
| ----------- | ---------- | ---------- | ----------- |
| urn | String | registered required | Function URN (e.g. `sn:cn:yrk:default:function:0@myService@python-agent:$latest`), taken from the `functionVersionUrn` in the [Register Function](./register_function.md) response. Converted to funcKey via `CombineFunctionKey`; frontend reads funcMeta from funcSpecMap by funcKey and passes through. |

#### Runtime Types

| **Value** | **Mapped faasExecutor** |
| -------- | --------------------- |
| python3.6 / python3.7 / python3.8 / python3.9 / python3.10 / python3.11 | Python3.x |
| go / http / custom image | Go1.x |

> `go` is the Go language runtime; `http` means invocation via the HTTP channel (runtime still the Go executor); `custom image` means a user-provided custom image (startup command comes from the image, executor still maps to Go1.x). All three share the same Go executor.
| java8 / java11 / java17 / java21 | Java8 / Java11 / Java17 / Java21 |
| posix-custom-runtime | PosixCustom |
| others | PosixCustom (fallback) |

#### Mount

| **Name** | **Type** | **Required** | **Description** |
| ----------- | ---------- | ---------- | ----------- |
| source | String | Yes | Absolute host path. |
| target | String | Yes | In-container path. |
| readonly | boolean | No | Read-only. Default `false`. |

## Response Parameters

| **Name** | **Type** | **Description** |
| -------- | -------- | -------- |
| code | int | Status code; `200` means success. |
| instance_id | String | Instance ID (UUID). |

## Examples

### inline mode

```bash
curl -X POST http://{frontend}:8888/api/agent -H "Content-Type: application/json" -d '{
  "name": "agent-001", "namespace": "dev",
  "runtime_spec": {
    "runtime": "python3.11", "sandbox_type": "docker",
    "rootfs": {"imageurl": "yr-docker-runtime:v0", "user": "agentos", "ports": ["tcp:22"]},
    "cpu": 600, "memory": 512
  },
  "workspace": "/home/snuser/workspaceA",
  "env_vars": {"AGENT_MODE": "prod", "userid": "u-9f3a"},
  "mounts": [{"source": "/home/snuser/workspaceB", "target": "/mnt/workspaceB", "readonly": false}]
}'
```

Response:

```json
{"code":200,"instance_id":"0b6c6322-6533-4901-8000-00000000bb0b"}
```

### registered mode

registered mode is two steps: register the `kind=agent` function first, **confirm the registration succeeded** (response `code=0` and contains `functionVersionUrn`), then call create with that URN.

```bash
# 1. Register the agent function (one-time; confirm code=0 and response contains functionVersionUrn)
curl -H "Content-type: application/json" -X POST http://{meta_service}:31182/serverless/v1/functions -d '{
  "name": "0@myService@python-agent", "kind": "agent", "runtime": "python3.11",
  "cpu": 600, "memory": 512, "timeout": 60,
  "storageType": "local", "codePath": "/opt/mycode/service",
  "environment": {"AGENT_MODE": "prod"},
  "sandboxType": "docker",
  "rootfs": {"type": "image", "imageurl": "yr-docker-runtime:v0", "user": "agentos", "ports": ["tcp:22"]}
}'
# After confirming registration succeeded, take functionVersionUrn as urn
export FUNCTION_VERSION_URN='sn:cn:yrk:default:function:0@myService@python-agent:$latest'

# 2. Create the agent instance (with urn; ensure docker/supervisor service is ready)
curl -X POST http://{frontend}:8888/api/agent -H "Content-Type: application/json" -d '{
  "name": "agent-001", "namespace": "dev", "urn": "'"${FUNCTION_VERSION_URN}"'",
  "workspace": "/home/snuser/workspaceA",
  "env_vars": {"userid": "u-9f3a"},
  "mounts": [{"source": "/home/snuser/workspaceB", "target": "/mnt/workspaceB", "readonly": false}]
}'
```

Response:

```json
{"code":200,"instance_id":"0b6c6322-6533-4901-8000-00000000bb0b"}
```

#### Register Function Parameters

registered mode first calls `POST /serverless/v1/functions` (see [Register Function](./register_function.md)) to register a `kind=agent` function. Parameters:

| **Name** | **Type** | **Required** | **Description** |
| -------- | ------ | ---------- | -------- |
| name | String | Yes | Function name, format `0@{service}@{funcName}` (e.g. `0@myService@python-agent`). `service` letters/digits ≤16; `funcName` lowercase letters/digits/`-` ≤127. Must be globally unique. |
| kind | String | Yes | Function category; agent must be `agent`. |
| runtime | String | Yes | Real language, mapped to faasExecutor; values see Runtime Types. |
| cpu | int | Yes | CPU size, unit `1/1000` cores. Sunk with funcMeta after registration, passed through at create to docker `CpuShares`. |
| memory | int | Yes | Memory size, unit `MB`. Sunk with funcMeta after registration, passed through at create to docker `Memory`. |
| timeout | int | No | Function invocation timeout in seconds, max `8640000`, default `900` if omitted. |
| storageType | String | No | Code package storage type. `local`: disk; `s3`: minio; `copy`: disk and copy to container path. |
| codePath | String | No | Code package local path. Effective when `storageType` is `local` or `copy`. |
| environment | map | No | Static environment variables (key-value, all string). Written to funcMeta.Environment, merged with dynamic env_vars at create and sunk; agent kind has no `func-` prefix. On key conflict dynamic env_vars take precedence over static environment. |
| sandboxType | String | No | executor dispatch. Values `docker`/`supervisor`; empty falls to default RuntimeExecutor. Written to funcMeta, passed through at create to createOptions["sandbox_type"]. |
| rootfs.type | String | No | rootfs type; for docker image use `image`. |
| rootfs.imageurl | String | No | docker image reference. Written to funcMeta.rootfs.imageurl, merged into createOptions["rootfs"] JSON at create. |
| rootfs.user | String | No | Container run-as user (must exist in image). Passed through at create to createOptions["host_user"]. |
| rootfs.ports | array | No | Port forwarding, format `[<proto>:]<port>`, proto values `tcp`/`udp` (default TCP). Passed through at create to createOptions["network"]. |

> The registered funcMeta is written to etcd `/sn/functions` and loaded into funcSpecMap by the frontend watcher. At create, `applyAgentFuncMeta` passes through runtime/sandboxType/rootfs/cpu/memory/environment; no need to repeat them in the create request body.

## Error Codes

Response body `code` field: `200` for success, `500` for failure (the `message` field carries the specific error).

| **HTTP status** | **Description** |
| -------- | -------- |
| 400 | Bad Request. `message` carries the specific cause: `either runtime_spec (inline) or urn (registered) is required` (neither `runtime_spec` nor `urn` present), `invalid request body` (missing required field), `... must be an absolute path` / `unsafe ...` (invalid workspace/mount source). |
| 500 | Internal Server Error. `message` is of the form `failed to create agent: <cause>`: `invalid function` (proxy cannot find faasExecutor funcMeta; runtime not in the mapping table or executor-meta not preloaded), `no Docker image specified` (`rootfs.imageurl` not passed through), `deploy dir is empty` (faasExecutor funcMeta code_path does not exist). In registered mode, an `urn` pointing to an unregistered or non-existent function causes a funcMeta cache miss that ultimately returns 500 via this path. |
