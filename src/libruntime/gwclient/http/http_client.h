/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2023-2025. All rights reserved.
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

#pragma once

#include <cerrno>
#include <functional>
#include <stdexcept>
#include <string>
#include <system_error>
#include <unordered_map>

#include <boost/asio/ssl.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/http.hpp>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include "absl/synchronization/mutex.h"
#include "src/dto/config.h"
#include "src/libruntime/err_type.h"
#include "src/libruntime/utils/fd_utils.h"
#include "src/libruntime/utils/utils.h"
#include "src/utility/logger/logger.h"
#include "src/utility/string_utility.h"
namespace http = boost::beast::http;
namespace ssl = boost::asio::ssl;
namespace beast = boost::beast;
namespace asio = boost::asio;
namespace YR {
namespace Libruntime {
using HttpCallbackFunction = std::function<void(const std::string &, const boost::beast::error_code &, const uint)>;
using HttpCallbackFunctionV2 = std::function<void(const std::string &, const boost::beast::error_code &, const uint,
                                                  const std::unordered_map<std::string, std::string> &)>;
const http::verb POST = http::verb::post;
const http::verb DELETE = http::verb::delete_;
const http::verb GET = http::verb::get;
const http::verb PUT = http::verb::put;
extern const int DEFAULT_HTTP_VERSION;
extern const uint HTTP_CONNECTION_ERROR_CODE;
const int CONNECTION_NO_TIMEOUT = -1;
extern const char *HTTP_CONNECTION_ERROR_MSG;
struct ConnectionParam {
    std::string ip;
    std::string port;
    int idleTime{120};
    int timeoutSec = CONNECTION_NO_TIMEOUT;
};

inline std::string GetProxyUrlFromEnv(bool forHttps)
{
    if (!Config::Instance().YR_ENABLE_HTTP_PROXY()) {
        return "";
    }
    if (forHttps) {
        for (const char *key : {"https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"}) {
            const std::string val = YR::GetEnvValue(key);
            if (!val.empty()) {
                return val;
            }
        }
    } else {
        for (const char *key : {"http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"}) {
            const std::string val = YR::GetEnvValue(key);
            if (!val.empty()) {
                return val;
            }
        }
    }
    return "";
}

struct ProxyEndpoint {
    std::string host;
    std::string port = "80";
    std::string auth;
};

const size_t URL_PERCENT_HEX_LEN = 2;
const int URL_HEX_NIBBLE_BITS = 4;
const size_t URL_SCHEME_SEPARATOR_LEN = 3;

inline std::string UrlDecodePercent(const std::string &in)
{
    auto hexVal = [](char c) -> int {
        if (c >= '0' && c <= '9') {
            return c - '0';
        }
        if (c >= 'a' && c <= 'f') {
            return c - 'a' + 10;
        }
        if (c >= 'A' && c <= 'F') {
            return c - 'A' + 10;
        }
        return -1;
    };
    std::string out;
    out.reserve(in.size());
    for (size_t i = 0; i < in.size(); ++i) {
        if (in[i] == '%' && i + URL_PERCENT_HEX_LEN < in.size()) {
            const int hi = hexVal(in[i + 1]);
            const int lo = hexVal(in[i + URL_PERCENT_HEX_LEN]);
            if (hi >= 0 && lo >= 0) {
                out.push_back(static_cast<char>((hi << URL_HEX_NIBBLE_BITS) | lo));
                i += URL_PERCENT_HEX_LEN;
                continue;
            }
        }
        out.push_back(in[i]);
    }
    return out;
}

inline ProxyEndpoint ParseProxyUrl(std::string url)
{
    if (const auto pos = url.find("://"); pos != std::string::npos) {
        url = url.substr(pos + URL_SCHEME_SEPARATOR_LEN);
    }
    ProxyEndpoint proxy;
    if (const auto pos = url.rfind('@'); pos != std::string::npos && pos > 0) {
        const std::string userinfo = UrlDecodePercent(url.substr(0, pos));
        std::string encoded = YR::utility::EncodedToString(userinfo);
        while (!encoded.empty() && encoded.back() == '\0') {
            encoded.pop_back();
        }
        proxy.auth = "Basic " + encoded;
        url = url.substr(pos + 1);
    }
    proxy.host = url;
    if (const auto pos = url.rfind(':'); pos != std::string::npos) {
        proxy.host = url.substr(0, pos);
        proxy.port = url.substr(pos + 1);
    }
    if (const auto pos = proxy.port.find('/'); pos != std::string::npos) {
        proxy.port = proxy.port.substr(0, pos);
    }
    return proxy;
}

inline void ConnectWithOptionalProxy(beast::tcp_stream &stream, asio::ip::tcp::resolver &resolver,
                                     const ConnectionParam &param, bool forHttps,
                                     beast::flat_buffer *connectPrefix = nullptr)
{
    const std::string proxyUrl = GetProxyUrlFromEnv(forHttps);
    if (param.timeoutSec != CONNECTION_NO_TIMEOUT) {
        stream.expires_after(std::chrono::seconds(param.timeoutSec));
    }

    try {
        const auto connect = [&stream](const asio::ip::tcp::resolver::results_type &endpoints) {
            stream.connect(endpoints);
            if (!SetCloseOnExec(stream.socket().native_handle())) {
                const int error = errno;
                throw std::system_error(error, std::generic_category(), "failed to set socket close-on-exec");
            }
        };

        const std::string target = param.ip + ":" + param.port;
        if (proxyUrl.empty()) {
            connect(resolver.resolve(param.ip, param.port));
        } else {
            const auto proxy = ParseProxyUrl(proxyUrl);
            YRLOG_INFO("HTTP CONNECT {} via proxy {}:{}", target, proxy.host, proxy.port);
            connect(resolver.resolve(proxy.host, proxy.port));

            http::request<http::empty_body> req{http::verb::connect, target, 11};
            req.set(http::field::host, target);
            req.set(http::field::connection, "keep-alive");
            req.set(http::field::proxy_connection, "keep-alive");
            if (!proxy.auth.empty()) {
                req.set(http::field::proxy_authorization, proxy.auth);
            }
            http::write(stream, req);

            beast::flat_buffer buffer;
            http::response_parser<http::empty_body> parser;
            http::read_header(stream, buffer, parser);
            if (parser.get().result() != http::status::ok) {
                throw std::runtime_error("HTTP CONNECT rejected, status " +
                                         std::to_string(parser.get().result_int()));
            }
            // Header 之后的字节（含 TLS 首包）留在 buffer 中，勿当作 HTTP body 消费掉。
            if (connectPrefix != nullptr) {
                *connectPrefix = std::move(buffer);
            }
        }

        if (param.timeoutSec != CONNECTION_NO_TIMEOUT) {
            stream.expires_never();
        }
    } catch (...) {
        beast::error_code closeError;
        stream.socket().close(closeError);
        throw;
    }
}

class HttpClient {
public:
    virtual ~HttpClient() = default;
    virtual ErrorInfo Init(const ConnectionParam &param) = 0;
    virtual void SubmitInvokeRequest(const http::verb &method, const std::string &target,
                                     const std::unordered_map<std::string, std::string> &headers,
                                     const std::string &body, const std::shared_ptr<std::string> requestId,
                                     const HttpCallbackFunction &receiver) = 0;
    virtual void SubmitInvokeRequest(const http::verb &method, const std::string &target,
                                     const std::unordered_map<std::string, std::string> &headers,
                                     const std::string &body, const std::shared_ptr<std::string> requestId,
                                     const HttpCallbackFunctionV2 &receiver)
    {
        YRLOG_DEBUG("the implementation of SubmitInvokeRequest() function is empty");
    }

    virtual ErrorInfo ReInit(const std::shared_ptr<std::string> requestId)
    {
        GracefulExit();
        const int totalRetryCount = YR::Libruntime::Config::Instance().MAX_HTTP_RETRY_TIME();
        const int maxTimeoutSec = YR::Libruntime::Config::Instance().MAX_HTTP_TIMEOUT_SEC();
        int timeoutSec = YR::Libruntime::Config::Instance().INITIAL_HTTP_CONNECT_SEC();
        YRLOG_DEBUG("client start reinit. initTimeoutSec: {} maxTimeoutSec: {} totalRetryCount: {} requestId: {}",
            timeoutSec, maxTimeoutSec, totalRetryCount, *requestId);
        int retryCount = 0;
        int backoffFactor = 2;
        ErrorInfo err;
        while (retryCount < totalRetryCount) {
            connParam_.timeoutSec = timeoutSec;
            err = Init(connParam_);
            if (err.OK()) {
                YRLOG_DEBUG("client reinit success, requestId: {}", *requestId);
                connParam_.timeoutSec = CONNECTION_NO_TIMEOUT;
                return err;
            }
            retryCount++;
            if (timeoutSec != CONNECTION_NO_TIMEOUT) {
                timeoutSec = std::min(timeoutSec * backoffFactor, maxTimeoutSec);
            }
            YRLOG_DEBUG("retry count {}, requestId: {} init err: {}", retryCount, *requestId, err.Msg());
        }
        connParam_.timeoutSec = CONNECTION_NO_TIMEOUT;
        return err;
    }

    virtual void Stop() {}

    virtual void GracefulExit() noexcept {}

    bool SetUnavailable()
    {
        absl::WriterMutexLock l(&mu_);
        if (isUsed_) {
            return false;
        }
        isUsed_ = true;
        return true;
    }

    void SetAvailable()
    {
        std::function<void()> releaseCallback;
        {
            absl::WriterMutexLock l(&mu_);
            isUsed_ = false;
            releaseCallback = std::move(onRelease_);
        }
        if (releaseCallback) {
            releaseCallback();
        }
    }

    void SetOnRelease(std::function<void()> callback)
    {
        absl::WriterMutexLock l(&mu_);
        onRelease_ = std::move(callback);
    }

    void ResetConnActive()
    {
        absl::WriterMutexLock l(&mu_);
        lastActiveTime_ = std::chrono::high_resolution_clock::now();
        isConnectionAlive_ = true;
    }

    void ResetConnActiveTime()
    {
        absl::WriterMutexLock l(&mu_);
        lastActiveTime_ = std::chrono::high_resolution_clock::now();
    }

    void SetConnInActive()
    {
        absl::WriterMutexLock l(&mu_);
        isConnectionAlive_ = false;
    }

    bool Available() const
    {
        absl::ReaderMutexLock l(&mu_);
        return !this->isUsed_;
    };

    bool IsConnActive() const
    {
        absl::ReaderMutexLock l(&mu_);
        auto current = std::chrono::high_resolution_clock::now();
        auto idle = std::chrono::duration_cast<std::chrono::seconds>(current - this->lastActiveTime_).count();
        return isConnectionAlive_ && idle < idleTime_;
    };

    void CheckResponseHeaderAndReset()
    {
        auto headers = resParser_->get().base();
        if (auto it = headers.find("Connection"); it != headers.end() && it->value() == "close") {
            SetConnInActive();
        }
        resParser_.reset();
        buf_.clear();
        req_.clear();
        ResetConnActiveTime();
        SetAvailable();
    };

    std::unordered_map<std::string, std::string> GetRespHeaders()
    {
        std::unordered_map<std::string, std::string> headers;
        // 1. 检查指针是否有效
        if (!resParser_) {
            YRLOG_ERROR("Response parser pointer is null.");
            return headers;
        }

        // 2. 检查是否解析完成
        // 如果还在解析中（例如流式解析未完成），get() 可能返回不完整的数据或未定义的行为
        if (!resParser_->is_done()) {
            YRLOG_ERROR("Response parsing is not yet complete.");
            return headers;
        }

        // 3. 获取解析完成的 response 对象
        // gets() 返回的是一个引用：http::response<http::string_body>&
        for (auto const& field : resParser_->get().base()) {
            headers.emplace(std::string(field.name_string()), std::string(field.value()));
        }
        return headers;
    };

protected:
    ConnectionParam connParam_;
    HttpCallbackFunction callback_;
    HttpCallbackFunctionV2 callbackV2_;
    beast::flat_buffer buf_;
    std::shared_ptr<http::response_parser<http::string_body>> resParser_;
    http::request<http::string_body> req_;
    bool isUsed_{true};
    bool isConnectionAlive_{false};
    std::chrono::time_point<std::chrono::high_resolution_clock> lastActiveTime_;
    bool retried_{false};
    int idleTime_{120};
    std::function<void()> onRelease_;
    mutable absl::Mutex mu_;
};

inline bool IsResponseSuccessful(const uint statusCode)
{
    const uint SUCCESS_CODE_MIN = 200;
    const uint SUCCESS_CODE_MAX = 299;
    return (statusCode >= SUCCESS_CODE_MIN && statusCode <= SUCCESS_CODE_MAX);
}

inline bool IsResponseServerError(const uint statusCode)
{
    const uint SUCCESS_CODE_MIN = 500;
    const uint SUCCESS_CODE_MAX = 599;
    return (statusCode >= SUCCESS_CODE_MIN && statusCode <= SUCCESS_CODE_MAX);
}

inline bool IsResponseClientError(const uint statusCode)
{
    const uint SUCCESS_CODE_MIN = 400;
    const uint SUCCESS_CODE_MAX = 499;
    return (statusCode >= SUCCESS_CODE_MIN && statusCode <= SUCCESS_CODE_MAX);
}
}  // namespace Libruntime
}  // namespace YR
