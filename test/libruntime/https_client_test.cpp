/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2024-2024. All rights reserved.
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
#include "src/libruntime/gwclient/http/client_manager.h"
#include "src/libruntime/gwclient/http/http_client.h"
namespace YR {
namespace test {
using namespace YR::Libruntime;

class HttpsClientTest : public ::testing::Test {
public:
    void SetUp() override
    {
        YR::utility::LogParam logParam = {
            .logLevel = "DEBUG",
            .logDir = "/tmp/log",
            .nodeName = "test-https",
            .modelName = "test",
            .maxSize = 100,
            .maxFiles = 1,
            .logFileWithTime = false,
            .logBufSecs = 30,
            .maxAsyncQueueSize = 1048510,
            .asyncThreadCount = 1,
            .alsoLog2Stderr = true,
        };
        YR::utility::InitLog(logParam);
    }
};

std::shared_ptr<LibruntimeConfig> ConstructLibruntimeConfig()
{
    std::shared_ptr<LibruntimeConfig> librtCfg = std::make_shared<LibruntimeConfig>();
    librtCfg->enableMTLS = true;
    librtCfg->verifyFilePath = "./test/data/cert/ca.crt";
    librtCfg->certificateFilePath = "./test/data/cert/client.crt";
    std::strcpy(librtCfg->privateKeyPaaswd, "test");
    librtCfg->privateKeyPath = "./test/data/cert/client.key";
    // The serverName is not verified.
    librtCfg->serverName = "test";
    return librtCfg;
}

TEST_F(HttpsClientTest, InitFailed)
{
    auto librtCfg = ConstructLibruntimeConfig();
    auto httpClient = std::make_unique<ClientManager>(librtCfg);
    auto err = httpClient->Init({"127.0.0.1", "0", 1}, 1, 1);
    ASSERT_EQ(err.OK(), false);
}
}  // namespace test
}  // namespace YR
