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
	"time"

	"go.uber.org/zap"
	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	"yuanrong.org/kernel/pkg/common/faas_common/statuscode"
	commonTypes "yuanrong.org/kernel/pkg/common/faas_common/types"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/pkg/functionscaler/selfregister"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

// litePollInterval is the cadence at which waitForInstance rechecks the pool for a
// newly-arrived schedulable slot while waiting out the acquire timeout.
const litePollInterval = 50 * time.Millisecond

// liteTTL reuses FaaSScheduler lease interval (config.GlobalConfig.LeaseSpan ms).
func liteTTL() time.Duration {
	ttl := time.Duration(config.GlobalConfig.LeaseSpan) * time.Millisecond
	if ttl < types.MinLeaseInterval {
		return types.MinLeaseInterval
	}
	return ttl
}

func (ls *LiteScheduler) getPool(funcKey string) *LiteFunctionPool {
	ls.poolsMu.RLock()
	defer ls.poolsMu.RUnlock()
	return ls.pools[funcKey]
}

func (ls *LiteScheduler) handleAcquire(req *LiteRequest, startTime time.Time) *commonTypes.InstanceResponse {
	logger := log.GetLogger().With(zap.String("traceID", req.TraceID), zap.String("funcKey", req.FuncKey),
		zap.String("sessionID", req.SessionID))
	if req.SessionTTL < 0 {
		logger.Warnf("lite acquire sessionTTL invalid: %d (must be >= 0)", req.SessionTTL)
		return liteErrResp(statuscode.InstanceSessionInvalidErrCode, "sessionTTL must not be negative", startTime)
	}
	pool := ls.getPool(req.FuncKey)
	if pool == nil {
		logger.Warnf("lite acquire pool not found, func not deployed or pool not synced yet")
		return liteErrResp(statuscode.FuncMetaNotFoundErrCode, statuscode.FuncMetaNotFoundErrMsg, startTime)
	}
	pool.Lock()
	// 1. session sticky
	if binding, ok := pool.sessions[req.SessionID]; ok {
		instanceID := binding.instanceID
		if slot := pool.instances[instanceID]; slot != nil &&
			(slot.Status == InstanceStatusRunning || slot.Status == InstanceStatusSubHealth) &&
			slot.InUse < slot.Capacity {
			// Cancel any pending idle-unbind timer for this session.
			pool.cancelSessionUnbind(req.SessionID)
			logger.Debugf("lite acquire session sticky hit: instance %s (inUse %d/%d)", instanceID, slot.InUse+1, slot.Capacity)
			resp := ls.assignInstance(pool, req, slot, startTime)
			pool.Unlock()
			return resp
		}
		logger.Infof("lite acquire session sticky invalidated: instance %s absent/unhealthy/full, redispatch", instanceID)
		pool.removeSessionBinding(req.SessionID)
	}
	// 2. dispatch
	slots := pool.candidateSlotsLocked()
	chosen := pool.dispatcher.Select(slots)
	if chosen != nil {
		logger.Debugf("lite acquire dispatched: instance %s (inUse %d/%d, %d candidates)",
			chosen.InstanceID, chosen.InUse+1, chosen.Capacity, len(slots))
		resp := ls.assignInstance(pool, req, chosen, startTime)
		pool.Unlock()
		return resp
	}
	// 3. cold start: no schedulable slot. Release pool.Lock before delegating to
	//    handleColdStart/waitForInstance, which manage their own pool.Lock per poll
	//    iteration. Keeping pool.Lock here would self-deadlock (Go Mutex is
	//    non-recursive) the moment waitForInstance tries to re-acquire it.
	//    Snapshot instanceCount under Lock; reading len(pool.instances) after
	//    Unlock would race with handleInstanceUpdate's map writes.
	instanceCount := len(pool.instances)
	pool.Unlock()
	logger.Infof("lite acquire cold start: no schedulable slot (%d instances, capacity all full or unhealthy), wait for scale",
		instanceCount)
	return ls.handleColdStart(pool, req, startTime)
}

func (ls *LiteScheduler) assignInstance(pool *LiteFunctionPool, req *LiteRequest,
	slot *LiteInstance, startTime time.Time) *commonTypes.InstanceResponse {
	// seqCounter is incremented under pool.Lock (writer-serialized); a plain
	// ++ is enough. atomic here would be misleading and is unnecessary.
	pool.seqCounter++
	seq := int(pool.seqCounter)
	allocID := genAllocationID(req.SessionID, slot.InstanceID, seq)
	slot.InUse++
	pool.bindSessionOnAcquire(req.SessionID, slot.InstanceID)
	alloc := &Allocation{
		AllocationID: allocID, SessionID: req.SessionID, SessionTTL: req.SessionTTL,
		TenantID:   req.TenantID,
		InstanceID: slot.InstanceID, FuncKey: req.FuncKey,
		ExpireAt: time.Now().Add(liteTTL()), CreatedAt: time.Now(),
	}
	ls.allocMu.Lock()
	ls.allocations[allocID] = alloc
	ls.allocMu.Unlock()
	// Register the lease on the expiry wheel so that a frontend crash or a
	// forgotten release is automatically reaped.
	ls.registerExpiryTask(allocID)
	if ls.metrics != nil {
		policy := "unknown"
		if pool.dispatcher != nil {
			policy = pool.dispatcher.Policy()
		}
		ls.metrics.incAcquire(req.FuncKey, req.TenantID, policy, "success")
	}
	return liteSuccessResp(slot, allocID, req.FuncKey, startTime)
}

func (ls *LiteScheduler) handleRelease(req *LiteRequest, startTime time.Time) *commonTypes.InstanceResponse {
	logger := log.GetLogger().With(zap.String("traceID", req.TraceID), zap.String("allocID", req.AllocationIDs[0]))
	ls.allocMu.Lock()
	alloc, ok := ls.allocations[req.AllocationIDs[0]]
	if !ok {
		ls.allocMu.Unlock()
		logger.Warnf("lite release allocation not found, lease expired or already released")
		return liteErrResp(statuscode.InstanceNotFoundErrCode, statuscode.InstanceNotFoundErrMsg, startTime)
	}
	delete(ls.allocations, req.AllocationIDs[0])
	ls.allocMu.Unlock()
	// Cancel the auto-reap timer since the client explicitly released.
	ls.removeExpiryTask(req.AllocationIDs[0])
	pool := ls.getPool(alloc.FuncKey)
	// Capture the slot pointer inside the pool.Lock block. After Unlock,
	// reading pool.instances[...] without the lock would race with concurrent
	// writers (concurrent map read/write is fatal). Also, pool can be nil
	// when the function has been undeployed and a late release arrives;
	// keep slot nil in that case and let liteSuccessResp's nil branch handle it.
	var slot *LiteInstance
	// Snapshot inUse/Capacity under pool.Lock; reading slot fields after Unlock
	// would race with concurrent acquire's slot.InUse++ (assignInstance).
	var logInUse, logCap = -1, -1
	var needUnbindTimer bool
	var unbindSessionID string
	if pool != nil {
		pool.Lock()
		if s := pool.instances[alloc.InstanceID]; s != nil && s.InUse > 0 {
			s.InUse--
			logInUse = s.InUse
			logCap = s.Capacity
		} else if s != nil {
			logInUse = s.InUse
			logCap = s.Capacity
		}
		// Decrement session's activeAllocs; if zero, mark for idle-unbind timer.
		needUnbindTimer, _ = pool.unbindSessionOnRelease(alloc.SessionID)
		if needUnbindTimer {
			unbindSessionID = alloc.SessionID
		}
		slot = pool.instances[alloc.InstanceID]
		pool.Unlock()
		logger.Debugf("lite release decremented: instance %s (inUse %d/%d)", alloc.InstanceID, logInUse, logCap)
		// Start the idle-unbind timer OUTSIDE pool.Lock to avoid blocking.
		if needUnbindTimer {
			ls.startSessionUnbindTimer(pool, unbindSessionID, alloc.SessionTTL)
		}
	} else {
		logger.Infof("lite release pool gone (func undeployed), allocation %s cleaned, instance inUse untouched", alloc.AllocationID)
	}
	if ls.metrics != nil {
		policy := "unknown"
		if pool != nil && pool.dispatcher != nil {
			policy = pool.dispatcher.Policy()
		}
		ls.metrics.incRelease(alloc.FuncKey, alloc.TenantID, policy, "success")
	}
	return liteSuccessResp(slot, alloc.AllocationID, alloc.FuncKey, startTime)
}

// startSessionUnbindTimer starts a goroutine that waits for sessionTTL and then
// removes the session→instance binding if no new acquire arrived in between.
// Mirrors legacy's startUnbindInstanceSession + unbindInstanceSession pattern.
func (ls *LiteScheduler) startSessionUnbindTimer(pool *LiteFunctionPool, sessionID string, sessionTTL int) {
	ttl := sessionTTLFor(sessionTTL)
	go func() {
		timer := time.NewTimer(ttl)
		defer timer.Stop()
		<-timer.C
		pool.Lock()
		// Re-check: a concurrent acquire may have already cancelled the timer
		// (expiring=false) or removed the binding entirely.
		binding, ok := pool.sessions[sessionID]
		if !ok || !binding.expiring {
			pool.Unlock()
			return
		}
		// Still expiring and no active allocs: remove the binding.
		if binding.activeAllocs == 0 {
			binding.stopTimer()
			delete(pool.sessions, sessionID)
			log.GetLogger().With(zap.String("sessionID", sessionID), zap.String("funcKey", pool.funcKey)).
				Infof("lite session idle-unbind: session %s unbound after TTL (func %s)", sessionID, pool.funcKey)
		}
		pool.Unlock()
	}()
}

// handleRetain refreshes a lease. Lock order is carefully designed to avoid
// deadlock with handleAcquire/assignInstance, which takes pool.Lock(Writer)
// while holding allocMu (lock order A: allocMu -> pool.Lock).
//
// To break the AB-BA cycle (retain wants allocMu -> pool.RLock, but
// pool.RLock is blocked by an outstanding pool.Lock), handleRetain NEVER
// holds allocMu while taking pool.RLock. It accesses the allocations map
// in three short critical sections; pool.RLock is taken only after
// releasing allocMu.
func (ls *LiteScheduler) handleRetain(req *LiteRequest, startTime time.Time) *commonTypes.InstanceResponse {
	logger := log.GetLogger().With(zap.String("traceID", req.TraceID), zap.String("allocID", req.AllocationIDs[0]))
	allocID := req.AllocationIDs[0]

	// (1) Look up the allocation; snapshot the keys needed for pool lookup
	//     and for the success response. Do not hold allocMu across pool.RLock.
	ls.allocMu.Lock()
	alloc, ok := ls.allocations[allocID]
	if !ok {
		ls.allocMu.Unlock()
		logger.Warnf("lite retain allocation not found, lease expired or released")
		return liteErrResp(statuscode.LeaseIDNotFoundCode, statuscode.LeaseIDNotFoundMsg, startTime)
	}
	funcKey := alloc.FuncKey
	instanceID := alloc.InstanceID
	ls.allocMu.Unlock()

	// (2) Resolve the pool outside allocMu. getPool takes poolsMu.RLock only.
	pool := ls.getPool(funcKey)
	if pool == nil {
		// Function meta is gone (undeployed). Drop the allocation so a
		// late retain cannot resurrect a dangling lease.
		ls.allocMu.Lock()
		delete(ls.allocations, allocID)
		ls.allocMu.Unlock()
		ls.removeExpiryTask(allocID)
		logger.Infof("lite retain pool gone (func %s undeployed), allocation dropped", funcKey)
		return liteErrResp(constant.LeaseExpireOrDeletedErrorCode, constant.LeaseExpireOrDeletedErrorMessage, startTime)
	}

	// (3) Read the instance slot under pool.RLock only (allocMu released).
	//     This is the point that previously deadlocked: holding allocMu here
	//     would wait behind an outstanding pool.Lock from a concurrent acquire.
	pool.RLock()
	slot := pool.instances[instanceID]
	pool.RUnlock()

	// (4) Slot gone or unhealthy: drop the allocation.
	if slot == nil || slot.Status == InstanceStatusUnavailable {
		ls.allocMu.Lock()
		delete(ls.allocations, allocID)
		ls.allocMu.Unlock()
		ls.removeExpiryTask(allocID)
		logger.Warnf("lite retain instance %s absent or unhealthy, allocation dropped", instanceID)
		return liteErrResp(statuscode.InstanceStatusAbnormalCode, constant.LeaseErrorInstanceIsAbnormalMessage, startTime)
	}

	// (5) Refresh TTL. alloc.AllocationID is captured here too; the alloc
	//     pointer might be re-fetched to avoid a stale snapshot, but the
	//     map entry is keyed by allocID which is immutable. newExpire is
	//     snapshotted under allocMu so the post-Unlock Debug read cannot race
	//     with a concurrent retain's ExpireAt write on the same alloc.
	ls.allocMu.Lock()
	alloc, ok = ls.allocations[allocID]
	if !ok {
		// Lost the lease between (1) and (5) (e.g. concurrent release).
		ls.allocMu.Unlock()
		logger.Warnf("lite retain lost lease between lookup and refresh (concurrent release)")
		return liteErrResp(statuscode.LeaseIDNotFoundCode, statuscode.LeaseIDNotFoundMsg, startTime)
	}
	alloc.ExpireAt = time.Now().Add(liteTTL())
	newExpire := alloc.ExpireAt
	ls.allocMu.Unlock()
	// Push the auto-reap deadline forward, mirroring the legacy
	// leaseHolder.extendLease -> timeWheel.
	ls.updateExpiryTask(allocID)

	if ls.metrics != nil {
		policy := "unknown"
		if pool.dispatcher != nil {
			policy = pool.dispatcher.Policy()
		}
		ls.metrics.incRetain(alloc.FuncKey, alloc.TenantID, policy, "success")
	}
	logger.Debugf("lite retain refreshed: instance %s, new expiry %s", instanceID, newExpire.Format(time.RFC3339Nano))
	return liteSuccessResp(slot, alloc.AllocationID, alloc.FuncKey, startTime)
}

func (ls *LiteScheduler) handleBatchRetain(req *LiteRequest, startTime time.Time) *commonTypes.BatchInstanceResponse {
	logger := log.GetLogger().With(zap.String("traceID", req.TraceID))
	resp := &commonTypes.BatchInstanceResponse{
		InstanceAllocSucceed: map[string]commonTypes.InstanceAllocationSucceedInfo{},
		InstanceAllocFailed:  map[string]commonTypes.InstanceAllocationFailedInfo{},
		LeaseInterval:        int64(liteTTL().Milliseconds()),
	}
	for _, allocID := range req.AllocationIDs {
		sub := &LiteRequest{Op: "retain", AllocationIDs: []string{allocID}, TraceID: req.TraceID}
		insResp := ls.handleRetain(sub, startTime)
		if insResp.ErrorCode == constant.InsReqSuccessCode {
			resp.InstanceAllocSucceed[allocID] = commonTypes.InstanceAllocationSucceedInfo{
				FuncKey: insResp.FuncKey, FuncSig: insResp.FuncSig,
				InstanceID: insResp.InstanceID, ThreadID: allocID,
			}
		} else {
			resp.InstanceAllocFailed[allocID] = commonTypes.InstanceAllocationFailedInfo{
				ErrorCode: insResp.ErrorCode, ErrorMessage: insResp.ErrorMessage,
			}
		}
	}
	logger.Infof("lite batchRetain done: %d succeed, %d failed (of %d)",
		len(resp.InstanceAllocSucceed), len(resp.InstanceAllocFailed), len(req.AllocationIDs))
	resp.SchedulerTime = time.Since(startTime).Seconds()
	return resp
}

func liteSuccessResp(slot *LiteInstance, allocID, funcKey string, startTime time.Time) *commonTypes.InstanceResponse {
	resp := &commonTypes.InstanceResponse{
		InstanceAllocationInfo: commonTypes.InstanceAllocationInfo{
			ThreadID: allocID, LeaseInterval: int64(liteTTL().Milliseconds()),
		},
		ErrorCode:     constant.InsReqSuccessCode,
		ErrorMessage:  constant.InsReqSuccessMessage,
		SchedulerTime: time.Since(startTime).Seconds(),
	}
	if slot != nil {
		resp.InstanceAllocationInfo.FuncKey = funcKey
		resp.InstanceAllocationInfo.FuncSig = slot.FuncSig
		resp.InstanceAllocationInfo.InstanceID = slot.InstanceID
		resp.InstanceAllocationInfo.InstanceIP = slot.InstanceIP
		resp.InstanceAllocationInfo.InstancePort = slot.InstancePort
		resp.InstanceAllocationInfo.NodeIP = slot.NodeIP
		resp.InstanceAllocationInfo.NodePort = slot.NodePort
		resp.InstanceAllocationInfo.FunctionProxyID = slot.FunctionProxyID
		resp.InstanceAllocationInfo.RouteAddress = slot.RouteAddress
	}
	return resp
}

// liteErrResp builds an error InstanceResponse. code is int (InstanceResponse.ErrorCode is int).
func liteErrResp(code int, msg string, startTime time.Time) *commonTypes.InstanceResponse {
	return &commonTypes.InstanceResponse{
		ErrorCode:     code,
		ErrorMessage:  msg,
		SchedulerTime: time.Since(startTime).Seconds(),
	}
}

// handleColdStart emits a cold-start ScaleHint to the Scaler, then polls the pool
// (bounded by AcquireWaitTimeoutMs) for a slot the dispatcher can hand out. The hint
// is idempotent at the Scaler (deduped by FuncKey), so re-emitting on every cold-start
// acquire is safe.
//
// LOCKING: handleAcquire invokes this AFTER releasing pool.Lock (see handleAcquire step 3),
// so handleColdStart runs WITHOUT holding pool.Lock. The ScaleHint snapshot is taken via
// currentInUse()/currentCapacity(), which manage their own pool.RLock. That RLock is not
// nested with any caller-held lock (none is held here) and is mutually exclusive with
// event.go's pool.Lock writers, so the map reads are race-free. waitForInstance then takes
// pool.Lock per poll iteration on its own.
func (ls *LiteScheduler) handleColdStart(pool *LiteFunctionPool, req *LiteRequest,
	startTime time.Time) *commonTypes.InstanceResponse {
	logger := log.GetLogger().With(zap.String("traceID", req.TraceID), zap.String("funcKey", req.FuncKey),
		zap.String("sessionID", req.SessionID))
	if ls.scaleHintSender != nil {
		logger.Infof("lite cold start: emit scale hint (inUse %d, capacity %d)",
			pool.currentInUse(), pool.currentCapacity())
		ls.scaleHintSender.Send(&ScaleHint{
			FuncKey:                 req.FuncKey,
			TenantID:                req.TenantID,
			SessionID:               req.SessionID,
			Reason:                  "cold_start",
			RequestedConcurrency:    1,
			CurrentLocalConcurrency: pool.currentInUse(),
			CurrentLocalCapacity:    pool.currentCapacity(),
			SchedulerID:             selfregister.SelfInstanceID,
			TraceID:                 req.TraceID,
			RequestID:               req.TraceID,
		})
	} else {
		logger.Warnf("lite cold start: scaleHintSender is nil, cannot request scale-up")
	}
	if ls.metrics != nil {
		ls.metrics.incScaleHint(req.FuncKey, req.TenantID, "cold_start")
	}
	return ls.waitForInstance(pool, req, startTime)
}

// waitForInstance polls the pool for a schedulable slot until AcquireWaitTimeoutMs
// elapses or ls.stopCh closes. It takes pool.Lock per poll iteration (NOT nested with
// any caller-held lock). On timeout or stop it returns NoInstanceAvailable.
func (ls *LiteScheduler) waitForInstance(pool *LiteFunctionPool, req *LiteRequest,
	startTime time.Time) *commonTypes.InstanceResponse {
	logger := log.GetLogger().With(zap.String("traceID", req.TraceID), zap.String("funcKey", req.FuncKey))
	timeout := time.Duration(config.GlobalConfig.LiteScheduler.AcquireWaitTimeoutMs) * time.Millisecond
	if timeout <= 0 {
		logger.Warnf("lite waitForInstance: AcquireWaitTimeoutMs<=0, reject immediately")
		return liteErrResp(statuscode.NoInstanceAvailableErrCode,
			"no available instance", startTime)
	}
	deadline := time.Now().Add(timeout)
	ticker := time.NewTicker(litePollInterval)
	defer ticker.Stop()
	for {
		pool.Lock()
		chosen := pool.dispatcher.Select(pool.candidateSlotsLocked())
		if chosen != nil {
			logger.Debugf("lite waitForInstance: slot appeared, instance %s", chosen.InstanceID)
			resp := ls.assignInstance(pool, req, chosen, startTime)
			pool.Unlock()
			return resp
		}
		pool.Unlock()
		if time.Now().After(deadline) {
			logger.Warnf("lite waitForInstance: timed out after %dms, no instance became schedulable", timeout.Milliseconds())
			return liteErrResp(statuscode.NoInstanceAvailableErrCode,
				"no available instance", startTime)
		}
		select {
		case <-ticker.C:
		case <-ls.stopCh:
			logger.Warnf("lite waitForInstance: scheduler stopping, abort wait")
			return liteErrResp(statuscode.NoInstanceAvailableErrCode,
				"no available instance", startTime)
		}
	}
}
