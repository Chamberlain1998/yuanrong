#!/usr/bin/env python3

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


class EtcdBazelCacheTest(unittest.TestCase):
    def test_etcd_genrule_uses_writable_sandbox_cache(self):
        build_file = (ROOT / "bazel" / "etcd.BUILD").read_text(encoding="utf-8")

        self.assertIn('export GOPATH="$$(pwd)/go_cache"', build_file)
        self.assertIn('export GOMODCACHE="$$(pwd)/go_cache/mod"', build_file)
        self.assertIn('export GOCACHE="$$(pwd)/go_cache/cache"', build_file)
        self.assertNotIn('$${GOMODCACHE:-', build_file)
        self.assertNotIn('$${GOCACHE:-', build_file)


if __name__ == "__main__":
    unittest.main()
