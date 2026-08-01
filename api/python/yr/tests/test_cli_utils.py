#!/usr/bin/env python3
# coding=UTF-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest

from yr.cli.utils import check_port


class TestCheckPort(unittest.TestCase):
    def test_fix_keeps_valid_port_and_rejects_invalid_ports(self):
        self.assertEqual(check_port(12345, "FIX"), 12345)
        for invalid_port in (0, 65536, "not-a-port"):
            with self.subTest(port=invalid_port):
                with self.assertRaisesRegex(ValueError, r"1\.\.65535"):
                    check_port(invalid_port, "FIX")


if __name__ == "__main__":
    unittest.main()
