# Local Build Pipeline

`scripts/local-build.sh` is the supported entry point for local release and UT
builds. It keeps build state outside every worktree and runs compilation in the
standard Docker compile image.

## Quick Start

```bash
scripts/local-build.sh init
scripts/local-build.sh prime
scripts/local-build.sh release --3vm-ready
scripts/local-build.sh ut functionsystem --suite InstanceProxyTest --case Create
scripts/local-build.sh ut yuanrong --target //test/foo:foo_test
scripts/local-build.sh stats
# optional public server seed (downloaded into isolated staging)
scripts/local-build.sh seed ds_tmp
scripts/local-build.sh seed metrics
scripts/local-build.sh seed vendor
```

The default cache is derived from Git's common directory, so the primary
checkout and ordinary nested/external `git worktree` checkouts automatically
share `<workspace>/.yr-cache/yuanrong-local-build-profile`. Set
`--cache-root` or `LOCAL_BUILD_CACHE_ROOT` to choose a different host location.
`prime` is the explicit cold-cache operation; it may compile all dependencies
on its first run.

For another worktree, invoke the shared runner with both `--repo` and the
same explicit `--cache-root`; it mounts that worktree as `/workspace` while
retaining the shared cache root:

```bash
git -C /path/to/another-yuanrong-worktree submodule update --init --recursive
python3 /path/to/yuanrong/scripts/local_build.py \
  --repo /path/to/another-yuanrong-worktree \
  --cache-root /path/to/.yr-cache/yuanrong-local-build-profile \
  status
```

`--repo` deliberately does not copy or patch build scripts into a target
worktree. The target must contain the same committed local-build integration
revision:
the cache wrapper, the Makefile cache/Bazel switches, YuanRong's Bazel cache
environment support, and FunctionSystem's `bazel_local_cache_root` support.
This is checked before a Docker build starts. Rebase or cherry-pick the
local-build integration first; an older branch must not silently share a
cache while running a different build protocol. The current protocol is
`LOCAL_BUILD_PROTOCOL=2`; it also requires the fixed DataSystem 9.9.9 baseline
path, so an earlier local-cache implementation is deliberately incompatible.
The shared cache is outside the worktree, but these adapters are source-level
build semantics and must be versioned with the branch. This prevents a cache
from being used with an incompatible Makefile, Bazel invocation, or
FunctionSystem executor. The Docker runner, queue, cache data and run records
remain worktree-independent.

For local development, DataSystem defaults to the approved x86 baseline
`yr-datasystem-v9.9.9.tar.gz` downloaded into `server-seeds/`. It is copied
into the keyed component output and reused by FunctionSystem/YuanRong without
rebuilding DataSystem. To intentionally update DataSystem from the checked-out
submodule, run with `LOCAL_DATASYSTEM_SOURCE_BUILD=1`; after that update the
new package is published under the current identity.

## Worktrees

Every worktree is mounted as `/workspace` and the same host cache is mounted as
`/cache`. The source tree is never replaced by a symlink. FunctionSystem's
generated inputs are restored with nested Docker mounts:

```text
vendor/src       -> /workspace/functionsystem/vendor/src
vendor/output    -> /workspace/functionsystem/vendor/output
litebus/output   -> /workspace/functionsystem/common/litebus/output
logs/output      -> /workspace/functionsystem/common/logs/output
metrics/output   -> /workspace/functionsystem/common/metrics/output
common Bazel distdir -> /workspace/functionsystem/thirdparty/runtime_deps
thirdparty       -> /workspace/thirdparty
DataSystem CMake _deps -> /workspace/datasystem/build/_deps
```

DataSystem's generated top-level `build/` directory remains worktree-local,
but its FetchContent download/source layer is shared as
`<cache-root>/common/datasystem-fetchcontent`. This avoids downloading brpc and
other third-party sources again when switching worktrees. Builds are serialized
by the host queue, so the shared CMake `_deps` directory is never mutated by two
Docker compilers at once.

The cache identity includes dependency-list hashes, recursive submodule/gitlink
state, the local-build adapter digest, platform, architecture, Bazel major
version and compile image ID. The adapter digest covers the versioned Makefile,
YuanRong build scripts and FunctionSystem Bazel integration. Therefore a
partially cherry-picked or locally edited adapter uses a separate input and
package-output namespace even when it reports the same protocol number. The
adapter digest does not split vendor/download trees: their identities use the
actual dependency manifests, submodule state and toolchain, so compatible
worktrees continue to reuse those expensive inputs. A
nonmatching `WORKSPACE`, `VendorList.csv`, `tools/openSource.txt`, submodule pin,
adapter, platform or image is refused or isolated instead of silently borrowing
an old dependency tree. Run `status` before a build to see the selected identity
and missing inputs.

The runner itself is protocol-controlled rather than content-hashed: a change
to Docker mounts, command construction or cache paths must increment
`LOCAL_BUILD_PROTOCOL`; operational changes such as queue recovery, logging or
the default UT concurrency do not invalidate build inputs.

## Profiles And Queue

Release and UT share download caches (`common/`), but have separate Bazel
output/action caches, Cargo targets and Go build caches under `profiles/`.
Changing a branch or worktree does not select a different cache path.

All `prime`, `build`, `ut` and `release` operations use one host-side lock.
Different worktrees and components may be edited concurrently, but only one
Docker compilation is active. `status` reports the holder and queued worktrees.
UT defaults to two compiler jobs (LOCAL_BUILD_UT_JOBS=2) because a cold debug
cache can consume enough memory to destabilize WSL. Pass --jobs N for a deliberate
one-off override; release and module builds retain their normal host-derived
parallelism.

Local development is unstamped by default: FunctionSystem's generated version
header contains the stable `unstamped` marker rather than the current branch or
commit, which prevents a branch switch from invalidating dependent actions.
`release --stamp` opts into embedding Git branch/commit metadata for a release
that needs it.

Successful upstream component builds also publish package outputs under
`artifacts/components/<complete-input-identity-sha256>/`.
YuanRong-only builds consume these keyed outputs and do not rebuild DataSystem
or FunctionSystem. If they are missing, the command stops with a request to
run `prime yuanrong` first.

## Selective UT

FunctionSystem's existing monolithic test binaries are intentionally preserved;
`--suite` and `--case` filter execution while the already-built test target is
reused. YuanRong accepts one or more explicit Bazel test labels:

```bash
scripts/local-build.sh ut functionsystem --suite Foo --case Bar
scripts/local-build.sh ut yuanrong --target //test/foo:foo_test
scripts/local-build.sh ut python --nodeid api/python/yr/tests/test_x.py::TestX::test_y
```

## Artifacts And Statistics

Successful `release` runs publish immutable artifacts under
`<cache-root>/artifacts/releases/<build-id>/manifest.json`. The manifest records
source and submodule identities, toolchain/image, consumers (`aio` and/or
`3vm`), and SHA-256 checksums for every wheel/archive/binary. AIO is built from
that same locked release output only when the worktree contains the modern
`deploy/sandbox/docker/build-images.sh` flow. `release --aio` fails before
compiling on older branches with the legacy `example/aio` layout, whose fixed
wheel names and missing bootstrap assets are incompatible with current release
artifacts. `--3vm-ready` marks the manifest as a coherent deployment candidate;
it does not claim that three VMs were deployed or passed a smoke test.

Each run writes a JSON record to `<cache-root>/runs/` and a log to
`<cache-root>/logs/`. `stats` aggregates duration, queue wait, Bazel disk/remote
hits, Bazel cached test results, vendor hits/misses, Cargo compilation count, DataSystem Ninja progress
(`ninja_actions_completed`/`ninja_actions_total`), shared dependency
hits/misses, and the speedup over the previous comparable successful run.

Jenkins is not required.  CI logs show that Buildkite uses a persistent
`/mnt/paas/build-cache` volume for dependency/compiler caches and an internal
Bazel Remote Cache at `grpc://bazel-remote.build-tools.svc.cluster.local:9092`.
The local layout mirrors the useful, portable parts of that contract under
`common/` and `profiles/`; the CI host directory itself is not copied into a
worktree.  If the developer machine has network access to the same remote
cache, it can be opted into explicitly:

```bash
scripts/local-build.sh release \
  --remote-cache grpc://bazel-remote.build-tools.svc.cluster.local:9092
```

The endpoint is TCP-probed before launching Docker.  An unresolvable or
unreachable CI-only address is reported and the build continues with the local
Bazel disk/action cache, so an offline laptop does not pay Bazel remote retry
timeouts.  Remote cache hits are recorded separately in `stats`; downloaded
dependencies, vendor trees, Cargo/Go/pip caches and release artifacts remain
local and are never assumed to be interchangeable with a CI workspace.

The public archive server is a separate, explicit seed source, not Jenkins or
the Bazel Remote Cache. `seed ds_tmp` imports only absent files into
`common/datasystem-opensource`; existing files are never overwritten. `seed
metrics`, `seed datasystem` and `seed vendor` download into
`server-seeds/yr_cache-x86_64/`. The `datasystem` archive is the approved
9.9.9 local baseline and is consumed by normal local builds; the vendor
archive remains staged because its `VendorList.csv` must match the current
worktree and toolchain. Set `LOCAL_DATASYSTEM_SOURCE_BUILD=1` only when
intentionally updating DataSystem from source.
The seed operation uses the same global compile queue, so it cannot mutate the
shared DataSystem cache while another Docker build is running.

## CI Compatibility

The local-build adapters are versioned in this repository, but they are opt-in
at runtime. A normal CI job that does not set `LOCAL_CACHE_ROOT` keeps the
historical Makefile path: CMake FunctionSystem builds, the CI-provided `JOBS`
and `FUNCTIONSYSTEM_JOBS` values, the explicitly selected FunctionSystem
builder, the legacy runtime-launcher protobuf/go commands (or the component
target when the checked-out FunctionSystem has removed the legacy proto), and
the CI-selected DataSystem archive. The local Bazel default, shared profiles,
9.9.9 DataSystem baseline, and selective-test shortcuts are enabled only by the
local runner's explicit cache environment.

Checking out the repository in CI is therefore sufficient; no host-side local
runner or cache directory is required. CI can opt into the same adapters later
by setting `LOCAL_CACHE_ROOT` and `LOCAL_CACHE_PROFILE` deliberately.

These archive seeds are never counted as a Bazel action-cache hit for the
current source. `status --json` marks each seed with
`used_as_action_cache: false`; a prebuilt DataSystem/metrics package is only a
staged runtime input until its identity is verified.
