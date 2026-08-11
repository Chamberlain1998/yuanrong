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

// Package httpserver -
package httpserver

import (
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"strconv"
	"sync/atomic"
	"time"

	"github.com/valyala/fasthttp"
	"go.uber.org/zap"

	"yuanrong.org/kernel/runtime/libruntime/api"

	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/common/faas_common/localauth"
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	"yuanrong.org/kernel/pkg/common/faas_common/statuscode"
	commonTls "yuanrong.org/kernel/pkg/common/faas_common/tls"
	"yuanrong.org/kernel/pkg/functionscaler"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/pkg/functionscaler/litescheduler"
	"yuanrong.org/kernel/pkg/functionscaler/selfregister"
	"yuanrong.org/kernel/pkg/functionscaler/sessioncontextmanager"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

var isShutDown atomic.Bool = atomic.Bool{}

const (
	defaultReadBufferSize     = 1 * 1024
	defaultMaxRequestBodySize = 1 * 1024 * 1024
	defaultServerTimeout      = 900 * time.Second
	invokePath                = "/invoke"
	scaleHintPath             = "/scalehint"
	sessionContextIdlePath    = "/sessioncontext/idle"
	sessionContextForkPath    = "/sessioncontext/fork"
	sessionContextDeletePath  = "/sessioncontext/delete"
)

// SetShutDownStatus -
func SetShutDownStatus() {
	isShutDown.Store(true)
}

// StartHTTPServer -
func StartHTTPServer(errChan chan<- error) (*fasthttp.Server, error) {
	fastServer := &fasthttp.Server{
		Handler:            route,
		TLSConfig:          getTLSConfig(),
		ReadBufferSize:     defaultReadBufferSize,
		ReadTimeout:        defaultServerTimeout,
		WriteTimeout:       defaultServerTimeout,
		MaxRequestBodySize: defaultMaxRequestBodySize,
	}
	if config.GlobalConfig.HTTPSConfig != nil && config.GlobalConfig.HTTPSConfig.HTTPSEnable {
		if err := commonTls.InitTLSConfig(*config.GlobalConfig.HTTPSConfig); err != nil {
			return nil, fmt.Errorf("init HTTPS config error: %s", err.Error())
		}
	}
	go func() {
		err := startServer(fastServer)
		if err != nil {
			log.GetLogger().Errorf("failed to start http server, err %s", err.Error())
		}
		errChan <- err
	}()
	return fastServer, nil
}

func getTLSConfig() *tls.Config {
	if config.GlobalConfig.HTTPSConfig == nil || !config.GlobalConfig.HTTPSConfig.HTTPSEnable {
		return nil
	}
	tlsConfig := commonTls.GetClientTLSConfig()
	if tlsConfig != nil {
		tlsConfig.NextProtos = []string{"http/1.1"}
	}
	return tlsConfig
}

func startServer(httpServer *fasthttp.Server) error {
	podIP := os.Getenv("POD_IP")
	if net.ParseIP(podIP) == nil {
		log.GetLogger().Errorf("failed to get pod ip, pod ip is %s", podIP)
		return errors.New("failed to get pod ip")
	}
	serverAddr := fmt.Sprintf("%s:%s", podIP, selfregister.GetFaaSSchedulerHttpPort())
	if config.GlobalConfig.HTTPSConfig != nil && config.GlobalConfig.HTTPSConfig.HTTPSEnable {
		log.GetLogger().Infof("start to listen the https request on addr: %s", serverAddr)
		if err := fastHTTPListenAndServeTLS(serverAddr, httpServer); err != nil {
			log.GetLogger().Errorf("failed to start the HTTPS server: %s", err.Error())
			return err
		}
		return nil
	}
	log.GetLogger().Infof("start to listen the http request on addr: %s", serverAddr)
	err := httpServer.ListenAndServe(serverAddr)
	if err != nil {
		log.GetLogger().Errorf("failed to start the HTTP server: %s", err.Error())
		return err
	}
	return nil
}

func fastHTTPListenAndServeTLS(addr string, server *fasthttp.Server) error {
	listener, err := net.Listen("tcp4", addr)
	if err != nil {
		return err
	}
	if server == nil || server.TLSConfig == nil {
		return errors.New("server or tls config is nil")
	}
	tlsListener := tls.NewListener(listener, server.TLSConfig)
	if err = server.Serve(tlsListener); err != nil {
		return err
	}
	return nil
}

func route(ctx *fasthttp.RequestCtx) {
	err := auth(ctx)
	if err != nil {
		ctx.SetStatusCode(http.StatusUnauthorized)
		log.GetLogger().Errorf("failed to check auth, error: %s", err.Error())
		return
	}
	path := string(ctx.Path())
	switch path {
	case invokePath:
		invokeHandler(ctx)
	case scaleHintPath:
		scaleHintHandler(ctx)
	case sessionContextIdlePath:
		sessionContextIdleHandler(ctx)
	case sessionContextForkPath:
		sessionContextForkHandler(ctx)
	case sessionContextDeletePath:
		sessionContextDeleteHandler(ctx)
	default:
		ctx.SetStatusCode(http.StatusInternalServerError)
		log.GetLogger().Errorf("unsupported http request path %s", path)
	}
	return
}

func sessionContextIdleHandler(ctx *fasthttp.RequestCtx) {
	var report types.IdleReport
	if err := json.Unmarshal(ctx.Request.Body(), &report); err != nil {
		writeSessionContextManagerError(ctx, http.StatusBadRequest, "INVALID_REQUEST", err.Error())
		return
	}
	fs := functionscaler.GetGlobalScheduler()
	if fs == nil || fs.SessionContextManager == nil {
		writeSessionContextManagerError(ctx, http.StatusServiceUnavailable, "MANAGER_UNAVAILABLE",
			"SessionContext manager is unavailable")
		return
	}
	for _, item := range report.Instances {
		ownerID, owned := selfregister.GlobalSchedulerProxy.CheckFuncOwner(item.FuncKey)
		if !owned {
			writeSessionContextManagerError(ctx, http.StatusConflict, "NOT_FUNCTION_OWNER", ownerID)
			return
		}
	}
	if err := fs.SessionContextManager.HandleIdle(report); err != nil {
		writeSessionContextManagerError(ctx, http.StatusInternalServerError, "IDLE_REPORT_FAILED", err.Error())
		return
	}
	ctx.SetStatusCode(http.StatusNoContent)
}

func sessionContextForkHandler(ctx *fasthttp.RequestCtx) {
	var request sessioncontextmanager.ForkRequest
	if err := json.Unmarshal(ctx.Request.Body(), &request); err != nil {
		writeSessionContextManagerError(ctx, http.StatusBadRequest, "INVALID_REQUEST", err.Error())
		return
	}
	if ownerID, owned := selfregister.GlobalSchedulerProxy.CheckFuncOwner(request.FuncKey); !owned {
		writeSessionContextManagerError(ctx, http.StatusConflict, "NOT_FUNCTION_OWNER", ownerID)
		return
	}
	fs := functionscaler.GetGlobalScheduler()
	if fs == nil || fs.SessionContextManager == nil {
		writeSessionContextManagerError(ctx, http.StatusServiceUnavailable, "MANAGER_UNAVAILABLE",
			"SessionContext manager is unavailable")
		return
	}
	response, err := fs.SessionContextManager.Fork(request)
	if err != nil {
		writeManagerOperationError(ctx, err)
		return
	}
	body, _ := json.Marshal(response)
	ctx.SetStatusCode(http.StatusCreated)
	ctx.Response.SetBodyRaw(body)
}

func sessionContextDeleteHandler(ctx *fasthttp.RequestCtx) {
	var request sessioncontextmanager.DeleteRequest
	if err := json.Unmarshal(ctx.Request.Body(), &request); err != nil {
		writeSessionContextManagerError(ctx, http.StatusBadRequest, "INVALID_REQUEST", err.Error())
		return
	}
	if ownerID, owned := selfregister.GlobalSchedulerProxy.CheckFuncOwner(request.FuncKey); !owned {
		writeSessionContextManagerError(ctx, http.StatusConflict, "NOT_FUNCTION_OWNER", ownerID)
		return
	}
	fs := functionscaler.GetGlobalScheduler()
	if fs == nil || fs.SessionContextManager == nil {
		writeSessionContextManagerError(ctx, http.StatusServiceUnavailable, "MANAGER_UNAVAILABLE",
			"SessionContext manager is unavailable")
		return
	}
	if err := fs.SessionContextManager.Delete(request); err != nil {
		writeManagerOperationError(ctx, err)
		return
	}
	ctx.SetStatusCode(http.StatusNoContent)
}

func writeManagerOperationError(ctx *fasthttp.RequestCtx, err error) {
	var managerErr *sessioncontextmanager.Error
	if errors.As(err, &managerErr) {
		status := http.StatusConflict
		switch managerErr.Code {
		case "FUNCTION_NOT_FOUND", "TURN_NOT_FOUND":
			status = http.StatusNotFound
		case "TURN_NOT_COMPLETED", "INVALID_SESSION_CONTEXT":
			status = http.StatusBadRequest
		case "INSTANCE_STOP_TIMEOUT":
			status = http.StatusServiceUnavailable
		}
		writeSessionContextManagerError(ctx, status, managerErr.Code, managerErr.Message)
		return
	}
	writeSessionContextManagerError(ctx, http.StatusInternalServerError, "INTERNAL_ERROR", err.Error())
}

func writeSessionContextManagerError(ctx *fasthttp.RequestCtx, status int, code, message string) {
	body, _ := json.Marshal(sessioncontextmanager.Error{Code: code, Message: message})
	ctx.SetStatusCode(status)
	ctx.Response.SetBodyRaw(body)
}

func auth(ctx *fasthttp.RequestCtx) error {
	if !config.GlobalConfig.AuthenticationEnable {
		return nil
	}
	sign := string(ctx.Request.Header.Peek(constant.HeaderAuthorization))
	timestamp := string(ctx.Request.Header.Peek(constant.HeaderAuthTimestamp))
	return localauth.AuthCheckLocally(config.GlobalConfig.LocalAuth.AKey, config.GlobalConfig.LocalAuth.SKey, sign,
		timestamp, config.GlobalConfig.LocalAuth.Duration)
}

func invokeHandler(ctx *fasthttp.RequestCtx) {
	traceID := string(ctx.Request.Header.Peek(constant.HeaderTraceID))
	traceParent := string(ctx.Request.Header.Peek(constant.HeaderTraceParent))
	logger := log.GetLogger()
	traceField := zap.String("traceID", traceID)
	if isShutDown.Load() {
		ctx.SetStatusCode(http.StatusOK)
		ctx.Response.Header.Set(constant.HeaderInnerCode, strconv.Itoa(statuscode.ErrFinalized))
		logger.Error("scheduler is in shutdown phase", traceField)
		return
	}
	var args []api.Arg
	err := json.Unmarshal(ctx.Request.Body(), &args)
	if err != nil {
		ctx.SetStatusCode(http.StatusInternalServerError)
		logger.Error("unmarshal request body failed", traceField, zap.Error(err))
		return
	}
	if functionscaler.GetGlobalScheduler() == nil {
		ctx.SetStatusCode(http.StatusInternalServerError)
		logger.Error("scheduler is nil", traceField)
		return
	}
	respBody, err := functionscaler.GetGlobalScheduler().ProcessInstanceRequestLibruntimeWithTraceParent(
		args, traceID, traceParent)
	if err != nil {
		ctx.SetStatusCode(http.StatusInternalServerError)
		logger.Error("marshal response body failed", traceField, zap.Error(err))
		return
	}
	ctx.SetStatusCode(http.StatusOK)
	// respBody is freshly allocated by json.Marshal and is not mutated after
	// this point, so ownership can be handed to fasthttp without another copy.
	ctx.Response.SetBodyRaw(respBody)
}

// scaleHintHandler receives cross-scheduler scale-up hints (LiteScheduler cold
// start). Auth runs in route() before dispatch, same as /invoke. Success is
// answered 202 immediately; a non-owner rejection is answered 200 with a
// ScaleHintResponse body carrying the non-owner error code + the owner
// InstanceID so the sender can reroute, mirroring the legacy acquire
// convention.
func scaleHintHandler(ctx *fasthttp.RequestCtx) {
	traceID := string(ctx.Request.Header.Peek(constant.HeaderTraceID))
	logger := log.GetLogger().With(zap.String("traceID", traceID))
	if isShutDown.Load() {
		ctx.SetStatusCode(http.StatusOK)
		ctx.Response.Header.Set(constant.HeaderInnerCode, strconv.Itoa(statuscode.ErrFinalized))
		logger.Errorf("reject scaleHint: scheduler is in shutdown phase")
		return
	}
	var hint litescheduler.ScaleHint
	if err := json.Unmarshal(ctx.Request.Body(), &hint); err != nil {
		ctx.SetStatusCode(http.StatusBadRequest)
		logger.Errorf("unmarshal scaleHint body error, err %s", err.Error())
		return
	}
	if hint.FuncKey == "" {
		ctx.SetStatusCode(http.StatusBadRequest)
		logger.Errorf("scaleHint funcKey is empty")
		return
	}
	fs := functionscaler.GetGlobalScheduler()
	if fs == nil {
		ctx.SetStatusCode(http.StatusInternalServerError)
		logger.Errorf("scheduler is nil")
		return
	}
	accepted, errCode, ownerID := fs.HandleScaleHint(&hint, traceID)
	if accepted {
		ctx.SetStatusCode(http.StatusAccepted)
		return
	}
	respBody, err := json.Marshal(litescheduler.ScaleHintResponse{ErrorCode: errCode, ErrorMessage: ownerID})
	if err != nil {
		ctx.SetStatusCode(http.StatusInternalServerError)
		logger.Errorf("marshal scaleHint response error, err %s", err.Error())
		return
	}
	ctx.SetStatusCode(http.StatusOK)
	ctx.Response.SetBodyRaw(respBody)
}
