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

#include "domain_socket_client.h"

#include <system_error>

#include "src/libruntime/utils/fd_utils.h"
#include "src/utility/logger/logger.h"
#include "src/utility/platform_compat.h"

namespace YR {
namespace Libruntime {
DomainSocketClient::DomainSocketClient(std::string socketPath) : socketPath_(socketPath)
{
    running_ = true;
}

DomainSocketClient::~DomainSocketClient()
{
    Stop();
}

void DomainSocketClient::InitOnce()
{
    std::call_once(this->initFlag_, [this]() { this->initErr_ = this->DoInitOnce(); });
}

ErrorInfo DomainSocketClient::DoInitOnce()
{
#ifdef SOCK_CLOEXEC
    // Set close-on-exec atomically to avoid a fork/exec race between socket() and fcntl().
    sockfd_ = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
#else
    // macOS does not define SOCK_CLOEXEC, so set the descriptor flag immediately after creation.
    sockfd_ = socket(AF_UNIX, SOCK_STREAM, 0);
#endif
    if (sockfd_ == -1) {
        return ErrorInfo(ErrorCode::ERR_INCORRECT_INIT_USAGE, "failed to init socket.");
    }
#ifndef SOCK_CLOEXEC
    if (!SetCloseOnExec(sockfd_)) {
        close(sockfd_);
        sockfd_ = -1;
        return ErrorInfo(ErrorCode::ERR_INCORRECT_INIT_USAGE, "failed to set socket close-on-exec.");
    }
#endif
    struct sockaddr_un serverAddr;
    if (memset_s(&serverAddr, sizeof(struct sockaddr_un), 0, sizeof(struct sockaddr_un)) != 0) {
        close(sockfd_);
        sockfd_ = -1;
        return ErrorInfo(ErrorCode::ERR_INCORRECT_INIT_USAGE, "failed to set sock address.");
    }
    serverAddr.sun_family = AF_UNIX;
    if (strncpy_s(serverAddr.sun_path, sizeof(serverAddr.sun_path) - 1, socketPath_.c_str(),
                  sizeof(serverAddr.sun_path) - 1) != 0) {
        close(sockfd_);
        sockfd_ = -1;
        return ErrorInfo(ErrorCode::ERR_INCORRECT_INIT_USAGE, "failed to copy sock address.");
    }
    if (connect(sockfd_, (struct sockaddr *)&serverAddr, sizeof(struct sockaddr_un)) == -1) {
        close(sockfd_);
        sockfd_ = -1;
        return ErrorInfo(ErrorCode::ERR_INCORRECT_INIT_USAGE, "failed to connect socket.");
    }
    try {
        writeThread_ = std::thread([this] {
            YR_SET_THREAD_NAME_CURRENT("yr.uds.write");
            HandleWrite();
        });
    } catch (const std::system_error &e) {
        close(sockfd_);
        sockfd_ = -1;
        return ErrorInfo(ErrorCode::ERR_INCORRECT_INIT_USAGE,
                         std::string("failed to start socket write thread: ") + e.what());
    }
    return ErrorInfo();
}

void DomainSocketClient::Stop()
{
    {
        std::lock_guard<std::mutex> lk(mu_);
        running_ = false;
        cv_.notify_one();
    }
    if (sockfd_ >= 0) {
        close(sockfd_);
        sockfd_ = -1;
        CleanupSocket();
    }
    if (writeThread_.joinable()) {
        writeThread_.join();
    }
}

void DomainSocketClient::CleanupSocket()
{
    if (access(socketPath_.c_str(), F_OK) != -1) {
        YRLOG_INFO("Clean up socket in {}", socketPath_);
        unlink(socketPath_.c_str());
    }
}

ErrorInfo DomainSocketClient::Send(std::string msg)
{
    SOCKET_INIT_ONCE();
    std::lock_guard<std::mutex> lk(mu_);
    if (!running_) {
        return ErrorInfo(ErrorCode::ERR_INNER_SYSTEM_ERROR, "failed to send, err: socket client is not running.");
    }
    msgQueue_.push(msg);
    cv_.notify_one();
    return ErrorInfo();
}

bool DomainSocketClient::IsEmpty()
{
    std::lock_guard<std::mutex> lk(mu_);
    return msgQueue_.empty();
}

void DomainSocketClient::PopAndSendBatch()
{
    std::lock_guard<std::mutex> lk(mu_);
    while (!msgQueue_.empty()) {
        std::string msg = msgQueue_.front();
        msgQueue_.pop();
        send(sockfd_, msg.c_str(), msg.length(), 0);
    }
}

void DomainSocketClient::HandleWrite()
{
    while (running_) {
        {
            std::unique_lock<std::mutex> lk(mu_);
            cv_.wait(lk, [this] { return msgQueue_.size() != 0 || !running_; });
        }
        if (msgQueue_.empty()) {
            continue;
        }
        PopAndSendBatch();
    }
}

}  // namespace Libruntime
}  // namespace YR
