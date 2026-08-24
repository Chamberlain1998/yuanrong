/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2023-2023. All rights reserved.
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
#include <fcntl.h>
#include <pthread.h>
#include <unistd.h>
#include <boost/beast/http.hpp>
#include <atomic>
#include <future>
#define private public
#include "httpserver/async_http_server.h"
#include "src/libruntime/gwclient/http/client_manager.h"
#include "src/libruntime/gwclient/http/http_client.h"
namespace YR {
namespace test {
using namespace YR::Libruntime;

class HttpClientTest : public ::testing::Test {
public:
    HttpClientTest() {}
    ~HttpClientTest() {}

    void SetUp() override
    {
        httpServer_ = std::make_shared<AsyncHttpServer>();
        YR::utility::LogParam logParam = {
            .logLevel = "DEBUG",
            .logDir = "/tmp/log",
            .nodeName = "test-http",
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
    void TearDown() override
    {
        if (httpServer_) {
            httpServer_.reset();
        }
    }

private:
    std::shared_ptr<AsyncHttpServer> httpServer_;
    std::string ip_ = "127.0.0.1";
    unsigned short port_ = 0;
    int threadNum = 8;
};

TEST_F(HttpClientTest, InitFailed)
{
    std::shared_ptr<LibruntimeConfig> librtCfg = std::make_shared<LibruntimeConfig>();
    auto httpClient = std::make_unique<ClientManager>(librtCfg);
    auto err = httpClient->Init({"127.0.0.1", "0", 1}, 1, 1);
    ASSERT_EQ(err.OK(), false);
}

TEST_F(HttpClientTest, ConnectedTcpSocketIsCloseOnExec)
{
    ASSERT_TRUE(httpServer_->StartServer(ip_, port_, threadNum));
    port_ = httpServer_->GetListeningPort();
    ASSERT_NE(port_, 0);

    auto ioc = std::make_shared<asio::io_context>();
    asio::ip::tcp::resolver resolver(*ioc);
    beast::tcp_stream stream(*ioc);
    ConnectWithOptionalProxy(stream, resolver, {ip_, std::to_string(port_)}, false);

    const int flags = fcntl(stream.socket().native_handle(), F_GETFD);
    ASSERT_NE(flags, -1);
    EXPECT_NE(flags & FD_CLOEXEC, 0);
    httpServer_->StopServer();
}

TEST_F(HttpClientTest, SubmitTask)
{
    ASSERT_TRUE(httpServer_->StartServer(ip_, port_, threadNum));
    port_ = httpServer_->GetListeningPort();
    ASSERT_NE(port_, 0);
    std::shared_ptr<LibruntimeConfig> librtCfg = std::make_shared<LibruntimeConfig>();
    librtCfg->httpIocThreadsNum = 5;
    auto httpClient = std::make_unique<ClientManager>(librtCfg);
    auto err = httpClient->Init({ip_, std::to_string(port_)});
    ASSERT_EQ(err.OK(), true);

    std::unordered_map<std::string, std::string> headers;
    headers.emplace("type", "test");
    std::string urn = "/test";
    auto retPromise = std::make_shared<std::promise<std::string>>();
    auto future = retPromise->get_future();
    auto requestId = std::make_shared<std::string>("requestID");
    httpClient->SubmitInvokeRequest(
        GET, urn, headers, "", requestId,
        [retPromise](const std::string &result, const boost::beast::error_code &errorCode, const uint statusCode) {
            if (errorCode) {
                std::cerr << "network error, error_code: " << errorCode.message() << std::endl;
            }
            retPromise->set_value(errorCode ? "" : result);
        });
    ASSERT_EQ(future.wait_for(std::chrono::seconds(10)), std::future_status::ready);
    ASSERT_EQ("ok", future.get());
    httpServer_->StopServer();
}

TEST_F(HttpClientTest, ExpandsConnectionPoolForConcurrentRequests)
{
    constexpr uint32_t initialConnections = 1;
    constexpr uint32_t requestCount = 4;
    ASSERT_TRUE(httpServer_->StartServer(ip_, port_, threadNum));
    port_ = httpServer_->GetListeningPort();
    ASSERT_NE(port_, 0);
    auto librtCfg = std::make_shared<LibruntimeConfig>();
    librtCfg->httpIocThreadsNum = requestCount + 1;
    librtCfg->maxConnSize = requestCount;
    auto httpClient = std::make_unique<ClientManager>(librtCfg);
    auto err = httpClient->Init({ip_, std::to_string(port_)}, initialConnections, 1);
    ASSERT_TRUE(err.OK());

    std::unordered_map<std::string, std::string> headers;
    headers.emplace("type", "test");
    auto releasePromise = std::make_shared<std::promise<void>>();
    auto releaseFuture = releasePromise->get_future().share();
    auto completionPromise = std::make_shared<std::promise<void>>();
    auto completionFuture = completionPromise->get_future();
    auto completed = std::make_shared<std::atomic<uint32_t>>(0);
    auto failed = std::make_shared<std::atomic<bool>>(false);
    auto requestId = std::make_shared<std::string>("requestID");
    for (uint32_t i = 0; i < requestCount; i++) {
        httpClient->SubmitInvokeRequest(
            GET, "/test", headers, "", requestId,
            [releaseFuture, completionPromise, completed, failed](
                const std::string &result, const boost::beast::error_code &errorCode, const uint statusCode) {
                releaseFuture.wait();
                if (errorCode) {
                    failed->store(true);
                }
                if (completed->fetch_add(1) + 1 == requestCount) {
                    completionPromise->set_value();
                }
            });
    }
    boost::asio::post(*httpClient->strand_, [releasePromise]() { releasePromise->set_value(); });

    ASSERT_EQ(completionFuture.wait_for(std::chrono::seconds(10)), std::future_status::ready);
    ASSERT_FALSE(failed->load());
    ASSERT_EQ(completed->load(), requestCount);
    ASSERT_EQ(httpClient->connectedClientsCnt_, requestCount);
    httpServer_->StopServer();
}

/*case
 * @title: Server故障后发送请求失败
 * @precondition:
 * @step:  1. 启动HttpServer
 * @step:  2. 创建客户端连接
 * @step:  3. 停止HttpServer
 * @step:  5. 发送http请求
 * @expect:  1.callback执行一次
 */
TEST_F(HttpClientTest, after_httpserver_stop_request_should_return_once)
{
    ASSERT_TRUE(httpServer_->StartServer(ip_, port_, threadNum));
    port_ = httpServer_->GetListeningPort();
    ASSERT_NE(port_, 0);
    std::shared_ptr<LibruntimeConfig> librtCfg = std::make_shared<LibruntimeConfig>();
    auto httpClient = std::make_unique<ClientManager>(librtCfg);
    auto err = httpClient->Init({ip_, std::to_string(port_)});
    ASSERT_EQ(err.OK(), true);
    httpServer_->StopServer();
    std::unordered_map<std::string, std::string> headers;
    headers.emplace("type", "test");
    std::string urn = "/test";
    auto promise = std::make_shared<std::promise<int>>();
    auto future = promise->get_future();
    auto callbackCount = std::make_shared<std::atomic<int>>(0);
    auto requestId = std::make_shared<std::string>("requestID");
    auto sendMsgHandler = [&]() {
        httpClient->SubmitInvokeRequest(
            GET, urn, headers, "", requestId,
            [promise, callbackCount](const std::string &result, const boost::beast::error_code &errorCode,
                                     const uint statusCode) {
                if (errorCode) {
                    std::cerr << "network error, error_code: " << errorCode.message() << std::endl;
                } else {
                    std::cout << "request success" << std::endl;
                }
                if (callbackCount->fetch_add(1) == 0) {
                    promise->set_value(1);
                } else {
                    ADD_FAILURE() << "request callback invoked more than once";
                }
            });
    };
    sendMsgHandler();
    ASSERT_EQ(future.wait_for(std::chrono::seconds(10)), std::future_status::ready);
    ASSERT_EQ(1, future.get());
    httpClient->Stop();
    ASSERT_EQ(callbackCount->load(), 1);
}
}  // namespace test
}  // namespace YR
