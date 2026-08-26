# YuanRong CI — Helm Deployment

One-click deployment of the CI infrastructure:

- **bazel-remote** cache (hostPath on the target cluster node or scheduler-selected node)
- **build-cache aging** DaemonSet for node-local `/mnt/paas/build-cache`
- **agent-stack-k8s** (Buildkite agent for K8s, upstream chart)
- **gitcode-webhook-relay** (optional GitCode -> Buildkite API bridge)
- **Secrets** templates (obs-credentials, swr-credentials, gitcode-webhook-relay-auth)

## Prerequisites

1. `kubectl` access to the CCE cluster
2. `helm` ≥ 3.10
3. Required Secrets already exist. Use
   `deploy/helm/yuanrong-ci/examples/runtime-secrets.example.yaml` as the
   deployment checklist; fill the blank values before applying it.
   - `obs-credentials` in `default` for artifact/chart upload
   - `swr-credentials` in `default` for sandbox image push jobs, or an existing
     `swr-pull-secret` Docker config secret with push permission
   - `swr-pull-secret` in `default` for Buildkite job `imagePullSecrets`
   - `swr-pull-secret` in `build-tools` for yuanrong-ci pod `imagePullSecrets`
   - `sandbox-target-kubeconfig` in `default` for Test K8S deployment,
     mounted by the agent-stack podSpec patch at `/var/run/yr-k8s/target/kubeconfig`
   - `test-pypi-credentials` in `default` for publishing pre-release `openyuanrong-sandbox` wheels to TestPyPI
   - `pypi-credentials` in `default` for publishing stable release `openyuanrong-sandbox` wheels to PyPI
   - `gitcode-webhook-relay-auth` in `build-tools` for webhook relay API/auth
4. bazel-remote image mirrored to SWR (see NOTES.txt)
5. Buildkite pipeline repository configured as
   `https://gitcode.com/openeuler/yuanrong.git` for merge-triggered builds.
   The relay also passes this repository as `BUILDKITE_REPO` so checkout keeps
   using upstream even if the pipeline UI repository drifts.

## Install

```bash
# 1. Deploy yuanrong-ci (bazel-remote + namespace)
helm upgrade --install yuanrong-ci deploy/helm/yuanrong-ci/ \
  -n default \
  --create-namespace

# 2. Deploy Linux amd64 agent-stack-k8s (upstream chart)
helm upgrade --install agent-stack-k8s oci://ghcr.io/buildkite/helm/agent-stack-k8s \
  --version 0.40.0 \
  -f deploy/helm/agent-stack-k8s-values.yaml \
  --set agentToken="${BUILDKITE_AGENT_TOKEN}" \
  -n default

# 3. Deploy Linux arm64 agent-stack-k8s (optional, same default queue)
helm upgrade --install agent-stack-k8s-arm64 oci://ghcr.io/buildkite/helm/agent-stack-k8s \
  --version 0.40.0 \
  -f deploy/helm/agent-stack-k8s-arm64-values.yaml \
  --set agentToken="${BUILDKITE_AGENT_TOKEN}" \
  -n default
```

## Key knobs in values.yaml

| Key | Default | Description |
|-----|---------|-------------|
| `global.buildNode` | `""` | Optional node name for pinning bazel-remote |
| `bazelRemote.maxSizeGb` | `150` | Max cache size |
| `bazelRemote.hostPath` | `/mnt/paas` | Host directory for cache |
| `bazelRemote.image` | SWR mirror | Must be accessible without Docker Hub |
| `cacheAging.enabled` | `true` | Run one cache-aging pod on each node |
| `cacheAging.paths` | `go-cache`, `opensource` under `/cache/build-cache` | Cache roots aged under the mounted hostPath; bazel-remote cache is intentionally excluded |
| `cacheAging.retentionDays` | `7` | Delete build-cache files older than this |
| `cacheAging.pressureMinAgeHours` | `24` | Minimum file age eligible for high-watermark pressure pruning |
| `cacheAging.highWatermarkPercent` | `85` | Start pressure pruning when `/mnt/paas` reaches this usage |
| `cacheAging.lowWatermarkPercent` | `75` | Stop pressure pruning after usage drops to this level |
| `agentStack.targetKubeconfig.secretName` | `sandbox-target-kubeconfig` | Secret mounted into Buildkite job pods for fixed target K8S deploy kubeconfig |
| `secrets.targetKubeconfig.create` | `false` | Let this chart create the target kubeconfig Secret from values |
| `secrets.testPypiCredentials.create` | `false` | Let this chart create the TestPyPI token Secret from values |
| `secrets.pypiCredentials.create` | `false` | Let this chart create the PyPI token Secret from values |

## Runtime Secret checklist

The Buildkite Kubernetes plugin references image and registry Secrets directly
from job pod specs. The target K8S kubeconfig mount is part of the
agent-stack-k8s podSpec patch recorded in this chart and mirrored in
`deploy/helm/agent-stack-k8s-values.yaml`. Missing required Secrets can leave
jobs stuck in `Pending`/`Init` before the build command starts.

| Secret | Namespace | Keys | Used by |
|-----|-----|-----|-----|
| `obs-credentials` | `default` | `AK`, `SK`, `access-key-id`, `secret-access-key` | Build artifact upload |
| `swr-credentials` | `default` | `username`, `password` | Build Image and Test K8S registry auth |
| `swr-pull-secret` | `default` | `.dockerconfigjson` | Build Image/Test K8S image pulls and Docker config fallback |
| `swr-pull-secret` | `build-tools` | `.dockerconfigjson` | Webhook relay, cache-aging, and chart-managed pod image pulls |
| `sandbox-target-kubeconfig` | `default` | `kubeconfig` | Test K8S mounted kubeconfig |
| `test-pypi-credentials` | `default` | `api-token` | Pre-release tag upload of `openyuanrong_sandbox*.whl` to TestPyPI |
| `pypi-credentials` | `default` | `api-token` | Stable release tag upload of `openyuanrong_sandbox*.whl` to PyPI |
| `gitcode-webhook-relay-auth` | `build-tools` | `buildkite-api-token`, `webhook-token`, `webhook-signature-secret` | GitCode webhook relay |

Before enabling the pipeline on a fresh cluster, fill and apply:

```bash
kubectl apply -f deploy/helm/yuanrong-ci/examples/runtime-secrets.example.yaml
kubectl get secret obs-credentials swr-credentials swr-pull-secret sandbox-target-kubeconfig test-pypi-credentials pypi-credentials -n default
kubectl get secret swr-pull-secret gitcode-webhook-relay-auth -n build-tools
```

The upstream `agent-stack-k8s` chart also creates `agent-stack-k8s-secrets`
from `--set agentToken="${BUILDKITE_AGENT_TOKEN}"`; deploy both amd64 and arm64
agent stacks with the token before triggering builds.

The Test K8S script always reads the target kubeconfig from the fixed path
`/var/run/yr-k8s/target/kubeconfig`. Keep the agent-stack podSpec patch and
`sandbox-target-kubeconfig` secret in sync instead of overriding kubeconfig
paths in pipeline YAML.

To let Helm create the kubeconfig Secret instead of applying the example
manifest, pass `--set secrets.targetKubeconfig.create=true` and provide the
kubeconfig content through a private values file or equivalent secret manager
workflow.

Tag releases publish only `openyuanrong_sandbox*.whl` by default. Pre-release
tags such as alpha, beta, rc, and dev publish to TestPyPI through
`test-pypi-credentials`; stable release tags publish to PyPI through
`pypi-credentials`. Set `PUBLISH_OPENYUANRONG_SDK_PYPI=true` explicitly when a
release must also publish `openyuanrong_sdk*.whl`.

## GitCode webhook relay

Enable the relay when direct GitCode -> Buildkite webhook delivery is not sufficient for
merge-request events or when you need branch/action filtering before triggering a build.

```bash
helm upgrade --install yuanrong-ci deploy/helm/yuanrong-ci/ \
  -n default \
  --set gitcodeWebhookRelay.enabled=true \
  --set gitcodeWebhookRelay.ingress.enabled=true \
  --set gitcodeWebhookRelay.ingress.host=ci-webhook.example.com \
  --set secrets.gitcodeWebhookRelayAuth.create=true \
  --set secrets.gitcodeWebhookRelayAuth.buildkiteApiToken="${BUILDKITE_API_TOKEN}" \
  --set secrets.gitcodeWebhookRelayAuth.webhookToken="${GITCODE_WEBHOOK_SECRET}" \
  --set secrets.gitcodeWebhookRelayAuth.webhookSignatureSecret="${GITCODE_WEBHOOK_SECRET}"
```

GitCode WebHook settings:

| Field | Value |
|-----|-----|
| URL | `https://<ingress-host>/webhook/gitcode` |
| Content-Type | `application/json` |
| Events | `Commit Event`, `Tag Push Event`, `Pull Request Event` |
| Secret | Set GitCode WebHook password to the same value as `webhook-token` and `webhook-signature-secret` |

The relay validates the GitCode token or signature, filters branches/actions, and then
calls the Buildkite REST API with the normalized `branch`, `commit`, and `env` payload.
Configure both relay secret fields with the GitCode WebHook password. The relay
accepts a valid `X-GitCode-Signature-256` first and falls back to
`X-GitCode-Token` or GitLab-compatible token headers, which keeps the install
compatible with observed GitCode delivery headers.

By default it is configured for merge-triggered CI and tag-triggered release builds:

- `push` events do not start builds
- tag push events matching `gitcodeWebhookRelay.filters.tagPatterns` start
  release builds; tag `0.7.50` builds version `0.7.50`, disables Buildkite
  Package Registry uploads, and enables PyPI upload for
  `openyuanrong_sandbox*.whl`; pre-release tags such as `0.7.50rc1` keep using
  TestPyPI
- only merge-request action `merge` is accepted; `update` events remain `update`
  and are filtered, including post-merge metadata updates such as label changes
- merge events build the **target branch commit after merge**
- repeated merge deliveries for the same MR target commit are deduplicated for
  `gitcodeWebhookRelay.filters.dedupTtlSeconds` seconds

If the cluster has no Ingress controller, expose the relay with a Service:

```bash
helm upgrade --install yuanrong-ci deploy/helm/yuanrong-ci/ \
  -n default \
  --set gitcodeWebhookRelay.enabled=true \
  --set gitcodeWebhookRelay.service.type=LoadBalancer
```

## Migrate existing cluster resources

The cluster already has manually-created resources. This chart **takes ownership**
of the `bazel-remote` deployment once installed. Existing secrets are not
re-created by default (`secrets.*.create: false`).
