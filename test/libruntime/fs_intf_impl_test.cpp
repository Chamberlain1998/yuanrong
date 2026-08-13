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
#include <boost/asio/ssl.hpp>
#include <boost/beast/http.hpp>

#define private public
#define protected public
#include "src/libruntime/fsclient/fs_intf_impl.h"
#include "src/libruntime/invoke_spec.h"
#include "mock/mock_fs_intf_manager.h"
#include "mock/mock_fs_intf_rw.h"
using namespace testing;
using namespace YR::utility;
using namespace YR::Libruntime;

namespace YR {
namespace test {

constexpr const char *FUNCTION_PROXY_ID = "function-proxy";

bool SetEnv(const std::string &k, const std::string &v)
{
    const char *name = k.c_str();
    const char *value = v.c_str();
    int overwrite = 1;
    if (setenv(name, value, overwrite) != 0) {
        std::cerr << "Failed to set environment variable." << std::endl;
        return false;
    }
    return true;
}

bool UnsetEnv(const std::string &k)
{
    if (unsetenv(k.c_str()) != 0) {
        std::cerr << "Failed to unset environment variable." << std::endl;
        return false;
    }
    return true;
}

class FSIntfImplTest : public testing::Test {
public:
    FSIntfImplTest(){};
    ~FSIntfImplTest(){};
    void SetUp() override
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
        InitLog(g_logParam);
        SetEnv("REQUEST_ACK_ACC_MAX_SEC", "11");
        Libruntime::Config::c = Libruntime::Config();
        FSIntfHandlers handlers;
        fsIntfImpl_ = std::make_shared<FSIntfImpl>(Config::Instance().HOST_IP(), 0, handlers, true, nullptr,
                                                   std::make_shared<ClientsManager>(), false);
    }
    void TearDown() override
    {
        UnsetEnv("REQUEST_ACK_ACC_MAX_SEC");
        fsIntfImpl_->Stop();
        fsIntfImpl_.reset();
    }
    std::shared_ptr<FSIntfImpl> fsIntfImpl_;
};

TEST_F(FSIntfImplTest, Test_when_retry_timeout_should_execute_callback)
{
    EXPECT_EQ(Libruntime::Config::Instance().REQUEST_ACK_ACC_MAX_SEC(), 11);
    std::string requestId = "requestId";
    auto promise = std::make_shared<std::promise<ErrorInfo>>();
    auto future = promise->get_future();
    auto respCallback = [promise](const StreamingMessage &createResp, ErrorInfo status,
                                  std::function<void(bool)> needErase) -> bool {
        promise->set_value(status);
        return true;
    };
    auto notifyCallback = [](const NotifyRequest &req, const ErrorInfo &err) {};
    auto wr = std::make_shared<WiredRequest>(respCallback, notifyCallback, fsIntfImpl_->timerWorker);
    wr = fsIntfImpl_->SaveWiredRequest(requestId, wr);
    std::atomic<size_t> retryTimes{0};
    auto retry = [&retryTimes]() { std::cout << "retryTimes: " << retryTimes++ << std::endl; };
    wr->SetupRetry(retry, std::bind(&FSIntfImpl::NeedRepeat, fsIntfImpl_.get(), requestId));
    EXPECT_EQ(future.get().Code(), ERR_REQUEST_BETWEEN_RUNTIME_BUS);
    EXPECT_EQ(retryTimes, 5);
}

TEST_F(FSIntfImplTest, Test_resend)
{
    std::string requestId = "requestId";
    auto respCallback = [](const StreamingMessage &createResp, ErrorInfo status,
                           std::function<void(bool)> needErase) -> bool {
        needErase(false);
        return false;
    };
    auto notifyCallback = [](const NotifyRequest &req, const ErrorInfo &err) {};
    auto wr = std::make_shared<WiredRequest>(respCallback, notifyCallback, fsIntfImpl_->timerWorker);
    wr = fsIntfImpl_->SaveWiredRequest(requestId, wr);
    std::atomic<size_t> retryTimes{0};
    auto retry = [&retryTimes]() { std::cout << "retryTimes: " << retryTimes++ << std::endl; };
    wr->SetupRetry(retry, std::bind(&FSIntfImpl::NeedRepeat, fsIntfImpl_.get(), requestId));
    EXPECT_EQ(retryTimes, 0);
    fsIntfImpl_->ResendRequests("function-proxy");
    EXPECT_EQ(retryTimes, 1);
    fsIntfImpl_->ClearAllWiredRequests();
    fsIntfImpl_->ResendRequests("function-proxy");
    EXPECT_EQ(retryTimes, 1);
}

TEST_F(FSIntfImplTest, Test_CreateAsync)
{
    auto spec = std::make_shared<InvokeSpec>();
    auto rspHandler = [](const CreateResponse &rsp) -> void { return; };
    auto notifyHandler = [](const NotifyRequest &req) -> void { return; };
    EXPECT_NO_THROW(fsIntfImpl_->CreateAsync(spec->requestCreate, rspHandler, notifyHandler));
    EXPECT_NO_THROW(fsIntfImpl_->ClearAllWiredRequests());
}

TEST_F(FSIntfImplTest, InterruptedCallResultUsesMessageWithoutReturnObjectIDs)
{
    auto req = std::make_shared<CallMessageSpec>();
    req->Mutable().set_requestid("interrupt-request");
    req->Mutable().set_senderid("function-proxy");

    const std::string response = R"({"body":{"message":"Interrupted Success"}})";
    auto result = fsIntfImpl_->BuildInterruptedCallResult(req, response);

    EXPECT_EQ(result->Immutable().code(), common::ERR_NONE);
    EXPECT_EQ(result->Immutable().message(), response);
    EXPECT_EQ(result->Immutable().smallobjects_size(), 0);
}

TEST_F(FSIntfImplTest, InterruptedCallResultUsesSmallObjectWithReturnObjectIDs)
{
    auto req = std::make_shared<CallMessageSpec>();
    req->Mutable().set_requestid("interrupt-request");
    req->Mutable().set_senderid("function-proxy");
    req->Mutable().add_returnobjectids("return-object");

    const std::string response = R"({"body":{"message":"Interrupted Success"}})";
    auto result = fsIntfImpl_->BuildInterruptedCallResult(req, response);

    ASSERT_EQ(result->Immutable().smallobjects_size(), 1);
    EXPECT_TRUE(result->Immutable().message().empty());
    EXPECT_EQ(result->Immutable().smallobjects(0).id(), "return-object");
    EXPECT_EQ(result->Immutable().smallobjects(0).value().substr(MetaDataLen), response);
}

TEST_F(FSIntfImplTest, Test_NeedResendReq)
{
    auto streamMsg1 = std::make_shared<StreamingMessage>();
    CreateResponse resp1;
    resp1.set_code(::common::ErrorCode::ERR_REQUEST_BETWEEN_RUNTIME_BUS);
    streamMsg1->mutable_creatersp()->CopyFrom(resp1);
    streamMsg1->set_messageid("messageId");
    EXPECT_EQ(fsIntfImpl_->NeedResendReq(streamMsg1), true);

    auto streamMsg2 = std::make_shared<StreamingMessage>();
    InvokeResponse resp2;
    resp2.set_code(::common::ErrorCode::ERR_REQUEST_BETWEEN_RUNTIME_BUS);
    streamMsg2->mutable_invokersp()->CopyFrom(resp2);
    streamMsg2->set_messageid("messageId");
    EXPECT_EQ(fsIntfImpl_->NeedResendReq(streamMsg2), true);

    auto streamMsg3 = std::make_shared<StreamingMessage>();
    ExitResponse resp3;
    streamMsg3->mutable_exitrsp()->CopyFrom(resp3);
    streamMsg3->set_messageid("messageId");
    EXPECT_EQ(fsIntfImpl_->NeedResendReq(streamMsg3), false);
}

TEST_F(FSIntfImplTest, After_Receive_Repeated_ShutdownReq_ShutdownHandler_Should_Be_Executed_Only_Once)
{
    FSIntfHandlers handlers;
    handlers.call = [](const std::shared_ptr<CallMessageSpec> &req) -> CallResponse {
        CallResponse resp;
        return resp;
    };
    handlers.checkpoint = [](const CheckpointRequest &req) -> CheckpointResponse {
        CheckpointResponse resp;
        return resp;
    };
    handlers.recover = [](const RecoverRequest &req) -> RecoverResponse {
        RecoverResponse resp;
        return resp;
    };

    handlers.signal = [](const SignalRequest &req) -> SignalResponse {
        SignalResponse resp;
        return resp;
    };

    handlers.shutdown = [](const ShutdownRequest &req) -> ShutdownResponse {
        ShutdownResponse resp;
        resp.set_message("be executed");
        return resp;
    };
    fsIntfImpl_ = std::make_shared<FSIntfImpl>(Config::Instance().HOST_IP(), 0, handlers, true, nullptr,
                                               std::make_shared<ClientsManager>(), false);
    ShutdownRequest req;

    std::promise<bool> pOne;
    auto fOne = pOne.get_future();

    std::promise<bool> pTwo;
    auto fTwo = pTwo.get_future();
    ShutdownCallBack callback = [&pOne, &pTwo](const ShutdownResponse &resp) {
        std::cout << "start to execute shutdown callback " << resp.message() << std::endl;
        if (!resp.message().empty()) {
            pOne.set_value(true);
            std::cout << "message is not empty" << std::endl;
        } else {
            pTwo.set_value(true);
            std::cout << "message is empty" << std::endl;
        }
    };
    fsIntfImpl_->HandleShutdownRequest(req, callback);
    fsIntfImpl_->HandleShutdownRequest(req, callback);
    ASSERT_TRUE(fOne.get());
    ASSERT_TRUE(fTwo.get());
}

TEST_F(FSIntfImplTest, CheckpointShouldNotTreatPendingInitCallAsBusy)
{
    std::atomic<bool> checkpointCalled{false};
    FSIntfHandlers handlers;
    handlers.checkpoint = [&checkpointCalled](const CheckpointRequest &req) -> CheckpointResponse {
        checkpointCalled = true;
        CheckpointResponse resp;
        resp.set_code(common::ERR_NONE);
        resp.set_state("state");
        return resp;
    };
    fsIntfImpl_ = std::make_shared<FSIntfImpl>(Config::Instance().HOST_IP(), 0, handlers, true, nullptr,
                                               std::make_shared<ClientsManager>(), false);
    fsIntfImpl_->checkpointRecoverExecutor.Init(1);
    ASSERT_TRUE(fsIntfImpl_->status.SetInitializing());
    ASSERT_TRUE(fsIntfImpl_->AddProcessingRequestId("initcall-request-id"));

    std::promise<CheckpointResponse> promise;
    auto future = promise.get_future();
    CheckpointRequest req;
    fsIntfImpl_->HandleCheckpointRequest(req, [&promise](const CheckpointResponse &resp) {
        promise.set_value(resp);
    });

    auto resp = future.get();
    EXPECT_TRUE(checkpointCalled);
    EXPECT_EQ(resp.code(), common::ERR_NONE);
    EXPECT_EQ(resp.state(), "state");
    EXPECT_TRUE(fsIntfImpl_->DeleteProcessingRequestId("initcall-request-id"));
}

TEST_F(FSIntfImplTest, TestRemoveInsRtIntf)
{
    Config::Instance().RUNTIME_DIRECT_CONNECTION_ENABLE() = true;
    FSIntfHandlers handlers;
    fsIntfImpl_ = std::make_shared<FSIntfImpl>(Config::Instance().HOST_IP(), 0, handlers, true, nullptr,
                                               std::make_shared<ClientsManager>(), false);
    ASSERT_NO_THROW(fsIntfImpl_->RemoveInsRtIntf("fakeInsId"));
}

TEST_F(FSIntfImplTest, Test_when_receive_repeated_call_request_should_return_call_response)
{
    Config::Instance().RUNTIME_DIRECT_CONNECTION_ENABLE() = true;
    FSIntfHandlers handlers;
    handlers.init = [](const std::shared_ptr<CallMessageSpec> &req) -> CallResponse {
        CallResponse resp;
        return resp;
    };
    fsIntfImpl_ = std::make_shared<FSIntfImpl>(Config::Instance().HOST_IP(), 0, handlers, true, nullptr,
                                               std::make_shared<ClientsManager>(), false);
    auto message = std::make_shared<StreamingMessage>();
    auto requestId = YR::utility::IDGenerator::GenRequestId();
    CallRequest callReq;
    callReq.set_requestid(requestId);
    callReq.set_iscreate(true);
    message->mutable_callreq()->CopyFrom(callReq);
    message->set_messageid(YR::utility::IDGenerator::GenMessageId(requestId, 0));
    auto req = std::make_shared<CallMessageSpec>(message);
    std::promise<bool> pOne;
    auto fOne = pOne.get_future();
    std::mutex mtx;
    int num = 0;
    int want = 2;
    auto callback = [&](const CallResponse &resp) {
        std::lock_guard<std::mutex> lockGuard(mtx);
        if (resp.code() == common::ERR_NONE) {
            num++;
        }
        if (num == want) {
            pOne.set_value(true);
        }
    };
    fsIntfImpl_->callReceiver.Init(1);
    fsIntfImpl_->HandleCallRequest(req, callback);
    fsIntfImpl_->HandleCallRequest(req, callback);
    ASSERT_TRUE(fOne.get());
}

TEST_F(FSIntfImplTest, ResendRequestWithRetryTest)
{
    auto promise = std::make_shared<std::promise<int>>();
    auto anotherPromise = std::make_shared<std::promise<int>>();
    auto future = promise->get_future();
    auto anotherFuture = anotherPromise->get_future();
    std::atomic<int> num{0};
    std::atomic<int> count{2};
    auto wiredReq = std::make_shared<WiredRequest>();
    std::function<void()> retry = [&num, &anotherPromise]() -> void {
        num++;
        if (num == 4) {
            anotherPromise->set_value(0);
        }
        YRLOG_DEBUG("current num is {}", num.load());
    };
    wiredReq->retryHdlr = std::move(retry);
    std::function<bool()> needRetryHdlr = [&count, &promise, &anotherPromise]() -> bool {
        if (count > 0) {
            count--;
            return true;
        }
        promise->set_value(0);
        return false;
    };
    wiredReq->needRetryHdlr = std::move(needRetryHdlr);
    auto timer = std::make_shared<TimerWorker>();
    wiredReq->timerWorkerWeak = timer->weak_from_this();
    wiredReq->ResendRequestWithRetry();
    wiredReq->ResendRequestWithRetry();
    wiredReq->retryIntervalSec = 1;

    ASSERT_EQ(num, 2);
    future.get();
    anotherFuture.get();
    ASSERT_EQ(num, 4);
    ASSERT_EQ(count, 0);
    if (wiredReq->timer_) {
        wiredReq->timer_->cancel();
        wiredReq->timer_.reset();
    }
}

TEST_F(FSIntfImplTest, DirectlyCallWithRetry)
{
    Config::Instance().RUNTIME_DIRECT_CONNECTION_ENABLE() = true;
    WiredRequest::requestACKTimeout = 1;
    FSIntfHandlers handlers;
    auto mockFsIntfMgr = std::make_shared<MockFSIntfManager>();
    fsIntfImpl_ = std::make_shared<FSIntfImpl>(Config::Instance().HOST_IP(), 0, handlers, true, nullptr,
                                               std::make_shared<ClientsManager>(), false);
    fsIntfImpl_->fsInrfMgr = mockFsIntfMgr;
    fsIntfImpl_->noitfyExecutor.Init(1);
    auto mockFsIntfRW = std::make_shared<MockFSIntfReaderWriter>();
    EXPECT_CALL(*mockFsIntfMgr, Get).WillRepeatedly(Return(mockFsIntfRW));
    // mock first directly return communicate err
    EXPECT_CALL(*mockFsIntfRW, Write)
        .WillOnce(Invoke([](const std::shared_ptr<StreamingMessage> &msg, std::function<void(bool, ErrorInfo)> callback,
                            std::function<void(bool)> preWrite) {
            callback(true, ErrorInfo(ERR_INNER_COMMUNICATION, "posix stream is closed"));
        }))
        .WillOnce(Invoke([](const std::shared_ptr<StreamingMessage> &msg, std::function<void(bool, ErrorInfo)> callback,
                            std::function<void(bool)> preWrite) {
            // retry to failure
            callback(false, ErrorInfo(ERR_INSTANCE_EXITED, "posix stream is closed"));
        }));
    NotificationUtility notified;
    auto promise = std::make_shared<std::promise<NotifyRequest>>();
    auto notifyHandler = [promise](const NotifyRequest &req, const ErrorInfo &err) -> void { promise->set_value(req); };

    auto reqId = YR::utility::IDGenerator::GenRequestId();
    InvokeRequest req;
    req.set_requestid(reqId);
    auto messageSpec = std::make_shared<InvokeMessageSpec>(std::move(req));
    fsIntfImpl_->InvokeAsync(messageSpec, notifyHandler, 5);
    auto rsp = promise->get_future().get();
    EXPECT_EQ(rsp.code(), common::ErrorCode(ERR_INSTANCE_EXITED));
    EXPECT_EQ(rsp.requestid(), reqId);
    auto wr = fsIntfImpl_->GetWiredRequest(reqId, false);
    EXPECT_EQ(wr, nullptr);
}

TEST_F(FSIntfImplTest, DirectlyCallResultWithRetry)
{
    Config::Instance().RUNTIME_DIRECT_CONNECTION_ENABLE() = true;
    WiredRequest::requestACKTimeout = 1;
    FSIntfHandlers handlers;
    auto mockFsIntfMgr = std::make_shared<MockFSIntfManager>();
    fsIntfImpl_ = std::make_shared<FSIntfImpl>(Config::Instance().HOST_IP(), 0, handlers, true, nullptr,
                                               std::make_shared<ClientsManager>(), false);
    fsIntfImpl_->fsInrfMgr = mockFsIntfMgr;
    auto mockFsIntfRW = std::make_shared<MockFSIntfReaderWriter>();
    EXPECT_CALL(*mockFsIntfMgr, Get).WillRepeatedly(Return(mockFsIntfRW));

    auto reqId = YR::utility::IDGenerator::GenRequestId();
    // mock first directly return communicate err
    EXPECT_CALL(*mockFsIntfRW, Write)
        .WillOnce(Invoke([reqId, weakFs(std::weak_ptr<FSIntfImpl>(fsIntfImpl_))](
                             const std::shared_ptr<StreamingMessage> &msg,
                             std::function<void(bool, ErrorInfo)> callback, std::function<void(bool)> preWrite) {
            callback(true, ErrorInfo(ERR_INNER_COMMUNICATION, "posix stream is closed"));
            if (auto ptr = weakFs.lock(); ptr) {
                auto wr = ptr->GetWiredRequest(reqId, false);
                EXPECT_NE(wr, nullptr);
            }
        }));
    auto promise = std::make_shared<std::promise<CallResultAck>>();
    auto ackHandler = [promise](const CallResultAck &req) -> void { promise->set_value(req); };
    CallResult req;
    req.set_requestid(reqId);
    auto obj = req.add_smallobjects();
    obj->set_id(reqId);
    auto messageSpec = std::make_shared<CallResultMessageSpec>();
    messageSpec->Mutable() = std::move(req);
    fsIntfImpl_->CallResultAsync(messageSpec, ackHandler);
    auto wr = fsIntfImpl_->GetWiredRequest(reqId, false);
    EXPECT_NE(wr, nullptr);
}

TEST_F(FSIntfImplTest, KillAsyncShouldFillMissingRoutingInfoFromHandlers)
{
    FSIntfHandlers handlers;
    handlers.getInstanceRoute = [](const std::string &instanceId) { return instanceId == "instance-1" ? "route-1" : ""; };
    handlers.getInstanceProxyID = [](const std::string &instanceId) {
        return instanceId == "instance-1" ? "proxy-1" : "";
    };
    auto mockFsIntfMgr = std::make_shared<MockFSIntfManager>();
    fsIntfImpl_ = std::make_shared<FSIntfImpl>(Config::Instance().HOST_IP(), 0, handlers, true, nullptr,
                                               std::make_shared<ClientsManager>(), false);
    fsIntfImpl_->fsInrfMgr = mockFsIntfMgr;
    auto mockFsIntfRW = std::make_shared<MockFSIntfReaderWriter>();
    mockFsIntfMgr->systemIntf = mockFsIntfRW;

    EXPECT_CALL(*mockFsIntfRW, Write)
        .WillOnce(Invoke([](const std::shared_ptr<StreamingMessage> &msg,
                            std::function<void(bool, ErrorInfo)> callback, std::function<void(bool)> preWrite) {
            ASSERT_TRUE(msg->has_killreq());
            EXPECT_EQ(msg->killreq().routeaddress(), "route-1");
            EXPECT_EQ(msg->killreq().proxyid(), "proxy-1");
            callback(false, ErrorInfo());
        }));

    KillRequest req;
    req.set_requestid(YR::utility::IDGenerator::GenRequestId());
    req.set_instanceid("instance-1");
    req.set_signal(libruntime::Signal::KillInstance);
    fsIntfImpl_->KillAsync(req, [](const KillResponse &, const ErrorInfo &) {});
}

TEST_F(FSIntfImplTest, TestIsHealth)
{
    EXPECT_NE(fsIntfImpl_->IsHealth(), true);
    fsIntfImpl_->fsInrfMgr = std::make_shared<FSIntfManager>(fsIntfImpl_->clientsMgr);
    EXPECT_NE(fsIntfImpl_->IsHealth(), true);
    auto mockFsIntf = std::make_shared<MockFSIntfReaderWriter>();
    fsIntfImpl_->fsInrfMgr->systemIntf = mockFsIntf;
    EXPECT_CALL(*mockFsIntf, IsHealth).WillRepeatedly(Return(true));
    EXPECT_TRUE(fsIntfImpl_->IsHealth());
}

TEST_F(FSIntfImplTest, TestNotifyDisconnected)
{
    EXPECT_NO_THROW(fsIntfImpl_->NotifyDisconnected("no_function-proxy"));
    auto wr = std::make_shared<WiredRequest>();
    wr->dstInstanceID = "instanceId";
    wr->reqId_ = "reqId";
    wr->notifyCallback = [](const NotifyRequest &req, const ErrorInfo &err) {std::cout << "hello, world" << std::endl;};
    fsIntfImpl_->wiredRequests.emplace(wr->reqId_, wr);
    auto mockFsIntf = std::make_shared<MockFSIntfReaderWriter>();
    fsIntfImpl_->fsInrfMgr->Emplace("instanceId", mockFsIntf);
    EXPECT_CALL(*mockFsIntf, Available).WillRepeatedly(Return(false));
    fsIntfImpl_->NotifyDisconnected("function-proxy");
    EXPECT_TRUE(fsIntfImpl_->wiredRequests.find("reqId") == fsIntfImpl_->wiredRequests.end());
}

TEST_F(FSIntfImplTest, TestNeedRepeat)
{
    auto wr = std::make_shared<WiredRequest>();
    fsIntfImpl_->wiredRequests["reqId"] = wr;
    wr->reqId_ = "reqId";
    wr->retryCount = 0;
    wr->remainTimeoutSec = 1;
    wr->retryIntervalSec = 10;
    wr->callback = [](const StreamingMessage &createResp, ErrorInfo status, std::function<void(bool)>){};
    EXPECT_FALSE(fsIntfImpl_->NeedRepeat("reqId"));
    wr->ackReceived = true;
    EXPECT_FALSE(fsIntfImpl_->NeedRepeat("reqId"));
}

TEST_F(FSIntfImplTest, TestUpdateRetryInterval)
{
    std::string reqId = "non_existing_request_id";
    auto result = fsIntfImpl_->UpdateRetryInterval(reqId);
    EXPECT_EQ(result.first, nullptr);
    EXPECT_TRUE(result.second);

    reqId = "existing_request_id";
    auto wr1 = std::make_shared<WiredRequest>();
    wr1->retryCount = 0;
    wr1->remainTimeoutSec = 0;
    wr1->retryIntervalSec = 1;
    fsIntfImpl_->wiredRequests[reqId] = wr1;
    result = fsIntfImpl_->UpdateRetryInterval(reqId);
    EXPECT_EQ(result.first, wr1);
    EXPECT_TRUE(result.second);
    EXPECT_EQ(fsIntfImpl_->wiredRequests.find(reqId), fsIntfImpl_->wiredRequests.end());

    reqId = "existing_request_id_1";
    auto wr2 = std::make_shared<WiredRequest>();
    wr2->retryCount = 0;
    wr2->remainTimeoutSec = 10;
    wr2->retryIntervalSec = 1;
    wr2->exponentialBackoff = true;
    fsIntfImpl_->wiredRequests[reqId] = wr2;

    result = fsIntfImpl_->UpdateRetryInterval(reqId);
    EXPECT_EQ(result.first, wr2);
    EXPECT_FALSE(result.second);
    EXPECT_EQ(result.first->retryCount, 1);
    EXPECT_EQ(result.first->remainTimeoutSec, 9);
    EXPECT_EQ(result.first->retryIntervalSec, 2);

    reqId = "existing_request_id_2";
    auto wr3 = std::make_shared<WiredRequest>();
    wr3->retryCount = 0;
    wr3->remainTimeoutSec = 1;
    wr3->retryIntervalSec = 10;
    wr3->exponentialBackoff = false;
    fsIntfImpl_->wiredRequests[reqId] = wr3;

    result = fsIntfImpl_->UpdateRetryInterval(reqId);
    EXPECT_EQ(result.first, wr3);
    EXPECT_TRUE(result.second);
    EXPECT_EQ(result.first->retryCount, 1);
    EXPECT_EQ(result.first->remainTimeoutSec, -9);
    EXPECT_EQ(result.first->retryIntervalSec, 10);
}

TEST_F(FSIntfImplTest, EventAsyncWithRetry)
{
    FSIntfHandlers handlers;
    auto mockFsIntfMgr = std::make_shared<MockFSIntfManager>();
    fsIntfImpl_ = std::make_shared<FSIntfImpl>(Config::Instance().HOST_IP(), 0, handlers, true, nullptr,
                                               std::make_shared<ClientsManager>(), false);
    fsIntfImpl_->fsInrfMgr = mockFsIntfMgr;
    auto mockFsIntfRW = std::make_shared<MockFSIntfReaderWriter>();
    auto mockNewFsIntfRW = std::make_shared<MockFSIntfReaderWriter>();
    EXPECT_CALL(*mockFsIntfMgr, TryGetEventIntfs).WillRepeatedly(Return(mockFsIntfRW));
    EXPECT_CALL(*mockFsIntfMgr, NewEventIntfClient)
        .WillRepeatedly(Invoke([mockNewFsIntfRW](const std::string &, const std::string &,
                                                const std::string &, const ReaderWriterClientOption &option,
                                                ProtocolType) {
            EXPECT_TRUE(option.streamRole.empty());
            return mockNewFsIntfRW;
        }));
    EXPECT_CALL(*mockFsIntfMgr, EmplaceEventIntfs).WillRepeatedly(Return(true));
    EXPECT_CALL(*mockFsIntfRW, Available).WillRepeatedly(Return(false));
    EXPECT_CALL(*mockNewFsIntfRW, Available).WillRepeatedly(Return(true));

    auto req = std::make_shared<EventMessageSpec>();
    std::string reqId = "testRequestId_event";
    std::string instanceId = "test_instanceId";
    req->Mutable().set_requestid(reqId);
    req->Mutable().set_message("test_event_message");
    req->Mutable().set_instanceid(instanceId);

    EXPECT_CALL(*mockNewFsIntfRW, Write)
            .WillOnce(Invoke([](const std::shared_ptr<StreamingMessage> &msg,
                                std::function<void(bool, ErrorInfo)> callback, std::function<void(bool)> preWrite) {
                callback(true, ErrorInfo(ERR_INNER_COMMUNICATION, "posix stream is closed"));
            }));

    fsIntfImpl_->UpdateEventServerInfo(Config::Instance().HOST_IP(), 1111, instanceId);
    fsIntfImpl_->EventAsync(req);
    auto wr = fsIntfImpl_->GetWiredRequest(reqId, true);
    EXPECT_EQ(wr, nullptr);
}

TEST_F(FSIntfImplTest, FunctionProxyEventRouteUsesLocalProxyEventStream)
{
    auto mockFsIntfMgr = std::make_shared<MockFSIntfManager>();
    auto mockEventIntf = std::make_shared<MockFSIntfReaderWriter>();
    fsIntfImpl_->fsInrfMgr = mockFsIntfMgr;
    fsIntfImpl_->fsIp = "127.0.0.9";
    fsIntfImpl_->fsPort = 19090;

    EXPECT_CALL(*mockFsIntfMgr, TryGetEventIntfs(FUNCTION_PROXY_ID)).WillRepeatedly(Return(nullptr));
    EXPECT_CALL(*mockFsIntfMgr, NewEventIntfClient)
        .WillOnce(Invoke([mockEventIntf](const std::string &, const std::string &dstInstance,
                                        const std::string &, const ReaderWriterClientOption &option,
                                        ProtocolType) {
            EXPECT_EQ(dstInstance, FUNCTION_PROXY_ID);
            EXPECT_EQ(option.ip, "127.0.0.9");
            EXPECT_EQ(option.port, 19090);
            EXPECT_EQ(option.streamRole, "event");
            return mockEventIntf;
        }));
    EXPECT_CALL(*mockEventIntf, Start()).WillOnce(Return(ErrorInfo()));
    EXPECT_CALL(*mockFsIntfMgr,
                EmplaceEventIntfs(FUNCTION_PROXY_ID, std::static_pointer_cast<FSIntfReaderWriter>(mockEventIntf)))
        .WillOnce(Return(true));

    EXPECT_EQ(fsIntfImpl_->NewOrGetEventIntfClient(FUNCTION_PROXY_ID), mockEventIntf);
}

TEST_F(FSIntfImplTest, LegacyEventRouteReplacesUnavailableClientAtSameAddress)
{
    const std::string instanceID = "legacy-frontend";
    auto mockFsIntfMgr = std::make_shared<MockFSIntfManager>();
    auto unavailableIntf = std::make_shared<MockFSIntfReaderWriter>();
    auto replacementIntf = std::make_shared<MockFSIntfReaderWriter>();
    fsIntfImpl_->fsInrfMgr = mockFsIntfMgr;
    fsIntfImpl_->UpdateEventServerInfo("127.0.0.8", 18080, instanceID);

    EXPECT_CALL(*mockFsIntfMgr, TryGetEventIntfs(instanceID)).WillOnce(Return(unavailableIntf));
    EXPECT_CALL(*unavailableIntf, Available()).WillOnce(Return(false));
    EXPECT_CALL(*unavailableIntf, IsSameDstAddr).Times(0);
    EXPECT_CALL(*mockFsIntfMgr, NewEventIntfClient)
        .WillOnce(Invoke([replacementIntf, &instanceID](const std::string &, const std::string &dstInstance,
                                                        const std::string &, const ReaderWriterClientOption &option,
                                                        ProtocolType) {
            EXPECT_EQ(dstInstance, instanceID);
            EXPECT_EQ(option.ip, "127.0.0.8");
            EXPECT_EQ(option.port, 18080);
            return replacementIntf;
        }));
    EXPECT_CALL(*replacementIntf, Start()).WillOnce(Return(ErrorInfo()));
    EXPECT_CALL(*mockFsIntfMgr,
                EmplaceEventIntfs(instanceID, std::static_pointer_cast<FSIntfReaderWriter>(replacementIntf)))
        .WillOnce(Return(true));

    EXPECT_EQ(fsIntfImpl_->NewOrGetEventIntfClient(instanceID), replacementIntf);
}

TEST_F(FSIntfImplTest, FunctionProxyEventRouteReplacesUnavailableClientAtSameAddress)
{
    auto mockFsIntfMgr = std::make_shared<MockFSIntfManager>();
    auto unavailableIntf = std::make_shared<MockFSIntfReaderWriter>();
    auto replacementIntf = std::make_shared<MockFSIntfReaderWriter>();
    fsIntfImpl_->fsInrfMgr = mockFsIntfMgr;
    fsIntfImpl_->fsIp = "127.0.0.9";
    fsIntfImpl_->fsPort = 19090;

    EXPECT_CALL(*mockFsIntfMgr, TryGetEventIntfs(FUNCTION_PROXY_ID))
        .WillRepeatedly(Return(unavailableIntf));
    EXPECT_CALL(*unavailableIntf, Available()).WillRepeatedly(Return(false));
    EXPECT_CALL(*unavailableIntf, IsSameDstAddr).Times(0);
    EXPECT_CALL(*mockFsIntfMgr, NewEventIntfClient)
        .WillOnce(Invoke([replacementIntf](const std::string &, const std::string &dstInstance,
                                           const std::string &, const ReaderWriterClientOption &option,
                                           ProtocolType) {
            EXPECT_EQ(dstInstance, FUNCTION_PROXY_ID);
            EXPECT_EQ(option.ip, "127.0.0.9");
            EXPECT_EQ(option.port, 19090);
            EXPECT_EQ(option.streamRole, "event");
            return replacementIntf;
        }));
    EXPECT_CALL(*replacementIntf, Start()).WillOnce(Return(ErrorInfo()));
    EXPECT_CALL(
        *mockFsIntfMgr,
        EmplaceEventIntfs(FUNCTION_PROXY_ID, std::static_pointer_cast<FSIntfReaderWriter>(replacementIntf)))
        .WillOnce(Return(true));

    EXPECT_EQ(fsIntfImpl_->NewOrGetEventIntfClient(FUNCTION_PROXY_ID), replacementIntf);
}

TEST_F(FSIntfImplTest, EventChannelConsumesCallResultAck)
{
    auto handler = fsIntfImpl_->eventMsgHdlrs.find(StreamingMessage::kCallResultAck);
    ASSERT_NE(handler, fsIntfImpl_->eventMsgHdlrs.end());

    auto ack = std::make_shared<StreamingMessage>();
    ack->mutable_callresultack()->set_code(common::ERR_NONE);
    EXPECT_NO_THROW(handler->second(FUNCTION_PROXY_ID, ack));
}

}  // namespace test
}  // namespace YR
