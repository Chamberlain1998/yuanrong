/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
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

// Package litescheduler -
package litescheduler

import (
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"time"

	"go.uber.org/zap"
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	"yuanrong.org/kernel/pkg/common/faas_common/statuscode"
	"yuanrong.org/kernel/pkg/common/faas_common/timewheel"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/pkg/functionscaler/registry"
	"yuanrong.org/kernel/pkg/functionscaler/selfregister"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

// liteExpiryScanPace is the time wheel pace for the expiry scanner. It must be
// <= liteTTL so that an unretained lease is reaped within roughly one TTL cycle.
const liteExpiryScanPace = 5 * time.Millisecond

// liteExpiryScanSlots is the slot count for the expiry time wheel. With pace=5ms
// and slots=100, the wheel circumference is 500ms, which is well below the typical
// LeaseSpan (≥1s). This ensures retain's UpdateTask lands within one revolution.
const liteExpiryScanSlots = 100

// ScaleHintSender sends idempotent capacity hints to a Scaler backend.
// The current default implementation is noopSender (logs only). Future
// implementations include httpSender (POST hint as JSON) and grpcSender
// (stream hints over gRPC) to a remote scaler service. Swapping implementations
// requires no changes to operation.go or callers of handleColdStart.
type ScaleHintSender interface {
	Send(hint *ScaleHint) (*ScaleHintResponse, error)
}

// LiteScheduler is the session-based lightweight scheduling branch.
type LiteScheduler struct {
	pools           map[string]*LiteFunctionPool
	allocations     map[string]*Allocation
	poolsMu         sync.RWMutex
	allocMu         sync.RWMutex
	ownerProxy      *selfregister.SchedulerProxy
	funcSpecGetter  func(string) *types.FunctionSpecification
	scaleHintSender ScaleHintSender
	metrics         *LiteCollector
	stopCh          <-chan struct{}
	funcSpecCh      chan registry.SubEvent
	insSpecCh       chan registry.SubEvent
	schedulerCh     chan registry.SubEvent
	idleOnce        sync.Once
	idleHTTP        *idleReporter
	// expiryWheel is the time wheel that drives automatic lease expiry. Each
	// allocation is registered as a task; when the wheel fires, the expiry
	// callback reaps the allocation and decrements InUse. retain calls
	// expiryWheel.UpdateTask to push the deadline forward, mirroring the
	// legacy timeWheel.
	expiryWheel timewheel.TimeWheel
}

// New constructs a LiteScheduler with injected dependencies. It does NOT snapshot
// config: isFuncEnabled reads config.GlobalConfig.LiteScheduler live, which today is
// loaded once at startup (no runtime hot-load path yet). The live-read is a forward
// hook for future whitelist hot-reload support.
func New(ownerProxy *selfregister.SchedulerProxy,
	funcSpecGetter func(string) *types.FunctionSpecification,
	scaleHintSender ScaleHintSender, stopCh <-chan struct{}) *LiteScheduler {
	result := &LiteScheduler{
		pools:           make(map[string]*LiteFunctionPool),
		allocations:     make(map[string]*Allocation),
		ownerProxy:      ownerProxy,
		funcSpecGetter:  funcSpecGetter,
		scaleHintSender: scaleHintSender,
		stopCh:          stopCh,
		expiryWheel:     timewheel.NewSimpleTimeWheel(liteExpiryScanPace, liteExpiryScanSlots),
	}
	// Build the Prometheus collector WITHOUT registering it with the default
	// prometheus registry. Registration is a separate InitMetric step owned by the
	// integration layer; avoiding global registration here prevents duplicate-register
	// panics when multiple LiteSchedulers are constructed (notably in tests).
	result.metrics = NewLiteCollector(result)
	return result
}

// Pools returns a shallow snapshot of pools for the Prometheus collector.
// Returned *LiteFunctionPool pointers are shared with the scheduler; callers
// MUST treat them as read-only and must not mutate pool internals.
func (ls *LiteScheduler) Pools() map[string]*LiteFunctionPool {
	ls.poolsMu.RLock()
	defer ls.poolsMu.RUnlock()
	out := make(map[string]*LiteFunctionPool, len(ls.pools))
	for k, v := range ls.pools {
		out[k] = v
	}
	return out
}

// Metrics returns the LiteCollector for external registration with a Prometheus
// registry. May be nil if New did not construct one (should not happen in normal
// production construction, but callers must nil-check).
func (ls *LiteScheduler) Metrics() *LiteCollector {
	return ls.metrics
}

// isFuncEnabled checks the three-tier whitelist (read live from config).
func (ls *LiteScheduler) isFuncEnabled(funcKey string) bool {
	cfg := config.GlobalConfig.LiteScheduler
	if !cfg.Enable {
		return false
	}
	if cfg.EnableAllTenants {
		return true
	}
	tenantID := splitFuncKey(funcKey).tenantID
	if contains(cfg.EnabledTenants, tenantID) {
		return true
	}
	if contains(cfg.EnabledFunctions, funcKey) {
		return true
	}
	return false
}

// splitFuncKey parses "tenantID/funcName/version".
type funcKeyParts struct {
	tenantID string
	funcName string
	version  string
}

func splitFuncKey(funcKey string) funcKeyParts {
	parts := strings.SplitN(funcKey, "/", 3)
	out := funcKeyParts{tenantID: parts[0]}
	if len(parts) > 1 {
		out.funcName = parts[1]
	}
	if len(parts) > 2 {
		out.version = parts[2]
	}
	return out
}

func contains(slice []string, s string) bool {
	for _, x := range slice {
		if x == s {
			return true
		}
	}
	return false
}

// Process is the stateful entry: reverse lookup, owner check, dispatch to handlers.
// A deferred recover guards the public entry so a handler panic does not crash the
// caller (consistent with ParseRequest's recovery pattern). The caller receives an
// internal-error JSON response instead of a propagated panic.
func (ls *LiteScheduler) Process(req *LiteRequest, traceID, traceParent string,
	extraData []byte) (resp []byte, err error) {
	logger := log.GetLogger().With(zap.String("traceID", traceID), zap.String("op", string(req.Op)),
		zap.String("funcKey", req.FuncKey))
	startTime := time.Now()
	defer func() {
		if r := recover(); r != nil {
			logger.Errorf("lite Process panic recovered: %v", r)
			data, _ := json.Marshal(liteErrResp(
				statuscode.InternalErrorCode, "lite process panic recovered", startTime))
			resp = data
			err = nil
		}
	}()
	if req.NeedReverseLookup {
		if fillErr := ls.reverseLookup(req); fillErr != nil {
			// A retain carrying ReacquireData can rebuild a missing in-memory
			// allocation. Do not reject it before the retain handler gets a
			// chance to perform that recovery. Release has no recovery contract
			// and still fails immediately. Batch retain resolves every item in
			// the handler so one missing allocation does not block the rest.
			if req.Op != "retain" && req.Op != "batchRetain" {
				logger.Warnf("lite Process reverse lookup failed: code %d, %s", fillErr.code, fillErr.msg)
				data, _ := json.Marshal(liteErrResp(fillErr.code, fillErr.msg, startTime))
				return data, nil
			}
			logger.Infof("lite Process reverse lookup missed for %s, defer to retain recovery", req.Op)
		} else if !ls.isFuncEnabled(req.FuncKey) {
			logger.Warnf("lite Process func %s not enabled after reverse lookup (whitelist excluded)", req.FuncKey)
			data, _ := json.Marshal(liteErrResp(statuscode.FuncMetaNotFoundErrCode, statuscode.FuncMetaNotFoundErrMsg, startTime))
			return data, nil
		}
	}
	// owner check (acquire only; release/retain skip per spec)
	if req.Op == "acquire" && ls.ownerProxy != nil {
		routingID := req.SessionID
		if req.SessionCtxID != "" {
			routingID = req.FuncKey + "/" + req.SessionCtxID
		}
		ownerID, owned := ls.ownerProxy.CheckHashOwner(req.TenantID + "/" + routingID)
		if !owned {
			logger.Warnf("lite Process not owner of session (owner=%s), should reroute", ownerID)
			data, _ := json.Marshal(liteErrResp(statuscode.AcquireNonOwnerSchedulerErrorCode,
				ownerID, startTime))
			return data, nil
		}
	}
	var response interface{}
	switch req.Op {
	case "acquire":
		response = ls.handleAcquire(req, startTime)
	case "release":
		response = ls.handleRelease(req, startTime)
	case "retain":
		response = ls.handleRetain(req, startTime)
	case "batchRetain":
		response = ls.handleBatchRetain(req, startTime)
	default:
		logger.Errorf("lite Process unsupported operation: %s", req.Op)
		response = liteErrResp(statuscode.FuncMetaNotFoundErrCode,
			fmt.Sprintf("unsupported operation: %s", req.Op), startTime)
	}
	data, marshalErr := json.Marshal(response)
	if marshalErr != nil {
		logger.Errorf("lite Process marshal response failed: %v", marshalErr)
		return nil, marshalErr
	}
	logger.Debugf("lite Process done: op %s cost %dms", req.Op, time.Since(startTime).Milliseconds())
	return data, nil
}

type lookupErr struct {
	code int
	msg  string
}

// reverseLookup fills SessionID/TenantID/FuncKey from the allocations map. It is
// the authoritative lookup for release and a fast-path validation for retain.
// Batch retain resolves and validates each allocation independently in its
// handler; the representative fields written here are not used for recovery.
func (ls *LiteScheduler) reverseLookup(req *LiteRequest) *lookupErr {
	ls.allocMu.RLock()
	defer ls.allocMu.RUnlock()
	for _, allocID := range req.AllocationIDs {
		alloc, ok := ls.allocations[allocID]
		if !ok {
			return &lookupErr{code: statuscode.LeaseIDNotFoundCode, msg: statuscode.LeaseIDNotFoundMsg}
		}
		req.SessionID = alloc.SessionID
		req.SessionCtxID = alloc.SessionCtxID
		req.SessionTTL = alloc.SessionTTL
		req.TenantID = alloc.TenantID
		req.FuncKey = alloc.FuncKey
	}
	log.GetLogger().With(zap.String("traceID", req.TraceID)).
		Debugf("lite reverseLookup resolved: funcKey %s, tenantID %s, %d allocIDs",
			req.FuncKey, req.TenantID, len(req.AllocationIDs))
	return nil
}

// processRequest is a test-only entry that bypasses JSON serialization to surface
// the error from Process directly. Production callers use Process.
func (ls *LiteScheduler) processRequest(req *LiteRequest, traceParent string, extraData []byte) error {
	_, err := ls.Process(req, req.TraceID, traceParent, extraData)
	return err
}
