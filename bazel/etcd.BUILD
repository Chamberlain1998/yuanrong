# BUILD file for etcd source

genrule(
    name = "bin",
    srcs = glob(
        ["**/*"],
        exclude = [
            # These are compatibility symlinks into integration-test Bazel
            # subpackages. The production binaries ignore *_test.go, while
            # Bazel cannot use a source symlink that crosses a package boundary.
            "client/v2/example_keys_test.go",
            "client/v3/example_*_test.go",
            "client/v3/concurrency/example_*_test.go",
        ],
    ),
    outs = ["etcd", "etcdctl", "etcdutl"],
    cmd = """
        set -e
        ETCD_DIR="$$(pwd)/external/etcd_source"
        OUTPUT_DIR="$$(pwd)/$(@D)"
        CACHE_ROOT="$$(pwd)/go_cache"
        export GOTOOLCHAIN=local
        export GOPATH="$${GOPATH:-$$CACHE_ROOT}"
        export GOMODCACHE="$${GOMODCACHE:-$$GOPATH/pkg/mod}"
        export GOCACHE="$${GOCACHE:-$$CACHE_ROOT/cache}"
        export GOFLAGS="$${GOFLAGS:+$$GOFLAGS }-buildvcs=false"
        export HOME="$$(pwd)"
        mkdir -p "$$GOMODCACHE" "$$GOCACHE"
        cd "$$ETCD_DIR"
        bash build.sh
        cp -f "$$ETCD_DIR/bin/etcd" "$$OUTPUT_DIR/"
        cp -f "$$ETCD_DIR/bin/etcdctl" "$$OUTPUT_DIR/"
        cp -f "$$ETCD_DIR/bin/etcdutl" "$$OUTPUT_DIR/"
    """,
    visibility = ["//visibility:public"],
)
