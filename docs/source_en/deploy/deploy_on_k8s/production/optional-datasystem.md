# Optional DataSystem Deployment

The production chart deploys DataSystem by default. You can disable it when workloads only use direct function or instance invocation and do not require object storage, KV, streams, state, or other DataSystem-backed APIs.

## Configuration Matrix

| `global.dataSystem.enabled` | `global.dataSystem.bypass` | Behavior |
| --- | --- | --- |
| `true` | `false` | Default; invoke uses DataSystem and DataSystem APIs are available |
| `true` | `true` | Invoke is inline by default; DataSystem APIs remain available |
| `false` | `true` | DataSystem is not deployed and invoke must be inline |
| `false` | `false` | Invalid; Helm rendering fails |

`enabled` determines DataSystem API availability. `bypass` only selects the default invoke transport.

## Install or Upgrade

Add these values to the existing installation command:

```shell
helm upgrade --install openyuanrong . \
  --namespace yr --create-namespace \
  --set global.dataSystem.enabled=false \
  --set global.dataSystem.bypass=true
```

The chart omits DataSystem workers, ports, and dedicated mounts, and injects the following values into control-plane components and runtimes:

```text
YR_DATASYSTEM_DEPLOYED=false
YR_BYPASS_DATASYSTEM=true
```

A Python Driver reads environment values first. An off-cluster Driver discovers a missing deployment capability from Frontend, while an in-cluster or process-mode Driver keeps the compatible DataSystem-enabled defaults and never sends HTTP requests to its FunctionProxy gRPC address. `Config.bypass_datasystem` explicitly overrides the default invoke transport. A 404 from an old Frontend uses the compatible DataSystem-enabled defaults; connection failures, timeouts, 5xx responses, or invalid responses make `yr.init()` fail fast. Runtimes do not depend on Frontend; they use values injected by FunctionAgent and RuntimeManager.

## Verify

Confirm that no DataSystem pod exists and all other components are Ready:

```shell
kubectl -n yr get pods
kubectl -n yr get pods -o name | grep -E 'ds-worker|datasystem'
```

The second command should produce no output. Check a Runtime environment:

```shell
kubectl -n yr exec <runtime-pod> -- env | grep -E 'YR_DATASYSTEM_DEPLOYED|YR_BYPASS_DATASYSTEM'
```

Normal function and instance invocation should return `ObjectRefDirect`. Direct `yr.get` and `yr.wait` remain available.

## Restrictions and Errors

The no-DataSystem mode does not support normal `ObjectRef` get/wait, put, KV, streams, state/checkpoint, tensor/shared-memory, generators, `ds://` working directories, or passing an `ObjectRef` as an invoke argument. The Python SDK should fail within one second with `ERR_DATASYSTEM_FAILED` and identify the unavailable operation.

Aggregate serialized request and response sizes are each limited to 100 MiB. A size equal to the limit is accepted; a larger size returns `ERR_PARAM_INVALID`. Serialization metadata counts toward the limit, so leave space below 100 MiB for application values.

## Roll Back

Restore the default mode:

```shell
helm upgrade openyuanrong . \
  --namespace yr \
  --set global.dataSystem.enabled=true \
  --set global.dataSystem.bypass=false
```

Wait for DataSystem pods to become Ready before using DataSystem-backed APIs.
