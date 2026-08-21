/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <gmock/gmock.h>
#include <gtest/gtest.h>
#include <stdlib.h>
#include <fstream>
#include <iostream>
#include <optional>
#include <utility>

#include "src/libruntime/libruntime_config.h"
#include "src/libruntime/utils/datasystem_utils.h"
#include "src/utility/logger/logger.h"

using namespace testing;
using namespace YR::Libruntime;
using namespace YR::utility;

namespace YR {
namespace test {
namespace {

class ScopedEnv {
public:
    explicit ScopedEnv(std::string name) : name_(std::move(name))
    {
        const char *value = getenv(name_.c_str());
        if (value != nullptr) {
            originalValue_ = value;
        }
    }

    ~ScopedEnv()
    {
        if (originalValue_.has_value()) {
            setenv(name_.c_str(), originalValue_->c_str(), 1);
        } else {
            unsetenv(name_.c_str());
        }
    }

    void Set(const char *value) const
    {
        setenv(name_.c_str(), value, 1);
    }

    void Unset() const
    {
        unsetenv(name_.c_str());
    }

private:
    std::string name_;
    std::optional<std::string> originalValue_;
};

}  // namespace

class LibruntimeConfigTest : public ::testing::Test {
public:
    LibruntimeConfigTest()
    {
        Mkdir("/tmp/log");
        LogParam g_logParam = {
            .logLevel = "DEBUG",
            .logDir = "/tmp/log",
            .nodeName = "test-runtime",
            .modelName = "test",
            .maxSize = 100,
            .maxFiles = 1,
            .logFileWithTime = false,
            .logBufSecs = 30,
            .maxAsyncQueueSize = 1048510,
            .asyncThreadCount = 1,
            .alsoLog2Stderr = true,
        };
    }
    ~LibruntimeConfigTest() {}
    static void SetUpTestSuite()
    {
    }

    void TearDown() override
    {
        Config::Reset();
    }
};

extern bool fileExists(const std::string &path);

TEST_F(LibruntimeConfigTest, MergeConfigTest)
{
    LibruntimeConfig config;
    LibruntimeConfig configInput;
    configInput.jobId = "jobId";
    config.MergeConfig(configInput);
    ASSERT_EQ(config.jobId, configInput.jobId);
}

TEST_F(LibruntimeConfigTest, InitFunctionGroupRunningInfoTest)
{
    LibruntimeConfig config;
    common::FunctionGroupRunningInfo runningInfo;
    runningInfo.set_devicename("devicename");
    auto serverInfo = runningInfo.add_serverlist();
    serverInfo->set_serverid("serverid");
    auto deviceInfo = serverInfo->add_devices();
    deviceInfo->set_deviceid(123456);
    config.InitFunctionGroupRunningInfo(runningInfo);
    ASSERT_EQ(config.groupRunningInfo.deviceName, "devicename");
    ASSERT_EQ(config.groupRunningInfo.serverList.size(), 1);
}

TEST_F(LibruntimeConfigTest, GetInstanceIdTest)
{
    LibruntimeConfig config;
    libruntime::FunctionMeta meta;
    meta.set_name("name");
    config.funcMeta = meta;
    auto insId = config.GetInstanceId();
    ASSERT_EQ(insId, "yr_defalut_namespace-name");
    meta.set_ns("ns");
    config.funcMeta = meta;
    insId = config.GetInstanceId();
    ASSERT_EQ(insId, "ns-name");
}

TEST_F(LibruntimeConfigTest, DataSystemDeployedTypedConfig)
{
    ScopedEnv dataSystemDeployed("YR_DATASYSTEM_DEPLOYED");
    dataSystemDeployed.Unset();
    Config::Reset();
    EXPECT_TRUE(Config::Instance().YR_DATASYSTEM_DEPLOYED());

    dataSystemDeployed.Set("false");
    Config::Reset();
    EXPECT_FALSE(Config::Instance().YR_DATASYSTEM_DEPLOYED());

    dataSystemDeployed.Set("true");
    Config::Reset();
    EXPECT_TRUE(Config::Instance().YR_DATASYSTEM_DEPLOYED());

    dataSystemDeployed.Set("invalid");
    EXPECT_THROW(Config::Reset(), std::invalid_argument);

    dataSystemDeployed.Unset();
    Config::Reset();
}

TEST_F(LibruntimeConfigTest, DataSystemDeployedEnvironmentOverridesDetectedCapability)
{
    ScopedEnv dataSystemDeployed("YR_DATASYSTEM_DEPLOYED");
    dataSystemDeployed.Unset();
    Config::Reset();
    EXPECT_FALSE(ResolveDataSystemDeployed(false));
    EXPECT_TRUE(ResolveDataSystemDeployed(true));

    dataSystemDeployed.Set("true");
    Config::Reset();
    EXPECT_TRUE(ResolveDataSystemDeployed(false));

    dataSystemDeployed.Set("false");
    Config::Reset();
    EXPECT_FALSE(ResolveDataSystemDeployed(true));

    dataSystemDeployed.Unset();
    Config::Reset();
}

TEST_F(LibruntimeConfigTest, FunctionAgentClientSwitchDoesNotChangeDeploymentCapability)
{
    ScopedEnv dataSystemClientEnabled("DATA_SYSTEM_ENABLE");
    ScopedEnv dataSystemDeployed("YR_DATASYSTEM_DEPLOYED");
    dataSystemClientEnabled.Set("false");
    dataSystemDeployed.Unset();
    Config::Reset();

    EXPECT_TRUE(ResolveDataSystemDeployed(true));

    dataSystemClientEnabled.Unset();
    Config::Reset();
}

}  // namespace test
}  // namespace YR
