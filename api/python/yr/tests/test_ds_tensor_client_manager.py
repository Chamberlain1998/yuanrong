#!/usr/bin/env python3
# coding=UTF-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import importlib
import os
import sys
import unittest
from unittest import mock

torch_mock = mock.MagicMock()
torch_npu_mock = mock.MagicMock()
torch_npu_module = mock.MagicMock()
torch_mock.npu = torch_npu_module
datasystem_mock = mock.MagicMock()
sys.modules['torch'] = torch_mock
sys.modules['torch_npu'] = torch_npu_mock
sys.modules['torch.npu'] = torch_npu_module
sys.modules['datasystem'] = datasystem_mock

config_manager = importlib.import_module("yr.config_manager")
capability_module = importlib.import_module("yr.datasystem_capability")
tensor_client_manager = importlib.import_module("yr.ds_tensor_client_manager")
error_type = importlib.import_module("yr.err_type")
yr_exception = importlib.import_module("yr.exception")


class TestGetTensorClient(unittest.TestCase):

    def setUp(self):
        # 清理全局状态和环境变量
        setattr(tensor_client_manager, "_global_tensor_client", None)
        if 'YR_DS_ADDRESS' in os.environ:
            del os.environ['YR_DS_ADDRESS']
        config_manager.ConfigManager().data_system_capability = capability_module.DataSystemCapability()

    def tearDown(self):
        config_manager.ConfigManager().data_system_capability = capability_module.DataSystemCapability()

    def test_disabled_datasystem_fails_before_import_or_address_checks(self):
        config_manager.ConfigManager().data_system_capability = capability_module.DataSystemCapability(
            False, True, "environment")

        with self.assertRaises(yr_exception.YRRuntimeError) as raised:
            tensor_client_manager.get_tensor_client()

        self.assertEqual(raised.exception.code, error_type.ErrorCode.ERR_DATASYSTEM_FAILED)
        self.assertEqual(raised.exception.module_code, error_type.ModuleCode.DATASYSTEM)
        self.assertIn("tensor", str(raised.exception))

    @mock.patch('yr.ds_tensor_client_manager.data_system_import', False)
    @mock.patch('yr.ds_tensor_client_manager._import_error', Exception("Mock import error"))
    def test_import_failure_raises_runtime_error(self):
        with self.assertRaises(RuntimeError) as cm:
            tensor_client_manager.get_tensor_client()
        self.assertIn("import err", str(cm.exception))

    @mock.patch('yr.ds_tensor_client_manager.data_system_import', True)
    @mock.patch('yr.config_manager.ConfigManager')
    def test_no_address_in_env_or_config_raises_error(self, mock_config_manager):
        with self.assertRaises(RuntimeError) as cm:
            tensor_client_manager.get_tensor_client()
        self.assertIn("cannot inspect data system address", str(cm.exception))

    @mock.patch('yr.ds_tensor_client_manager.os.getenv', return_value="123")
    @mock.patch('yr.ds_tensor_client_manager.log.get_logger')
    def test_get_tensor_client_success(
            self, mock_logger, mock_getenv
    ):
        with self.assertRaises(ValueError) as cm:
            tensor_client_manager.get_tensor_client()
        self.assertIn("expect 'ip:port'", str(cm.exception))

    @mock.patch('yr.ds_tensor_client_manager.data_system_import', True)
    @mock.patch('yr.ds_tensor_client_manager.os.getenv', return_value="192.168.1.10:8080")
    @mock.patch('yr.ds_tensor_client_manager.DsTensorClient')
    @mock.patch('yr.ds_tensor_client_manager.log.get_logger')
    @mock.patch('torch.npu.cunrrent_device', return_value=4)
    def test_successful_client_creation_from_env(
            self, mock_cunrrent_device, mock_logger, mock_ds_client, mock_getenv
    ):
        result = tensor_client_manager.get_tensor_client()
        result2 = tensor_client_manager.get_tensor_client()
        self.assertIs(result2, result)  # 同一个实例

    @mock.patch('yr.ds_tensor_client_manager.data_system_import', True)
    @mock.patch('yr.ds_tensor_client_manager.os.getenv', return_value="")
    @mock.patch('datasystem.DsTensorClient', return_value=1)
    @mock.patch('yr.ds_tensor_client_manager.log.get_logger')
    @mock.patch('torch.npu.current_device', return_value=4)
    def test_successful_client_creation_from_config(
            self, mock_current_device, mock_logger, mock_ds_client, mock_get_env
    ):
        config_manager.ConfigManager().ds_address = "10.0.0.5:9000"
        result = tensor_client_manager.get_tensor_client()
        result2 = tensor_client_manager.get_tensor_client()
        self.assertEqual(result, result2)


if __name__ == '__main__':
    unittest.main()
