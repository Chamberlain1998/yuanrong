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
	"go.uber.org/zap"
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
)

// registerExpiryTask adds an allocation to the expiry time wheel so that it will
// be automatically reaped if the frontend crashes or stops calling retain. The
// task fires after liteTTL; the callback (reapExpiredAllocation) decrements InUse
// and deletes the allocation, mirroring the legacy timeWheel callback path.
//
// Caller must hold NO lock; this method only touches the time wheel's internal
// sync.Map, which is goroutine-safe.
func (ls *LiteScheduler) registerExpiryTask(allocID string) {
	if ls.expiryWheel == nil {
		return
	}
	if err := ls.expiryWheel.AddTask(allocID, liteTTL(), 1); err != nil {
		log.GetLogger().With(zap.String("allocID", allocID)).
			Warnf("lite expiry wheel AddTask failed: %v (lease will not be auto-reaped)", err)
	}
}

// updateExpiryTask pushes the deadline of an allocation forward on the expiry
// wheel. Called by handleRetain after refreshing alloc.ExpireAt, mirroring the
// legacy leaseHolder.extendLease -> timeWheel.UpdateTask path.
func (ls *LiteScheduler) updateExpiryTask(allocID string) {
	if ls.expiryWheel == nil {
		return
	}
	if err := ls.expiryWheel.UpdateTask(allocID, liteTTL(), 1); err != nil {
		log.GetLogger().With(zap.String("allocID", allocID)).
			Warnf("lite expiry wheel UpdateTask failed: %v", err)
	}
}

// removeExpiryTask removes an allocation from the expiry wheel. Called by
// handleRelease and removeInstanceLocked so that an explicitly-released or
// instance-deleted allocation does not trigger a spurious expiry callback.
func (ls *LiteScheduler) removeExpiryTask(allocID string) {
	if ls.expiryWheel == nil {
		return
	}
	if err := ls.expiryWheel.DelTask(allocID); err != nil {
		log.GetLogger().With(zap.String("allocID", allocID)).
			Debugf("lite expiry wheel DelTask: %v (already removed or never added)", err)
	}
}

// processExpiryEvents is the background scan loop. It blocks on
// expiryWheel.Wait(), which returns a batch of allocation IDs whose TTL has
// elapsed. For each, reapExpiredAllocation performs the cleanup: decrement
// InUse, delete the allocation, and remove the session binding.
func (ls *LiteScheduler) processExpiryEvents() {
	logger := log.GetLogger()
	if ls.expiryWheel == nil {
		logger.Warn("lite expiry wheel is nil, expiry scan loop exiting")
		return
	}
	// Wait blocks on the time wheel's ready channel, so forward scheduler stop
	// directly to the wheel. Stop closes the ready channel and wakes Wait.
	stopForwardDone := make(chan struct{})
	go func() {
		select {
		case <-ls.stopCh:
			ls.expiryWheel.Stop()
		case <-stopForwardDone:
		}
	}()
	defer close(stopForwardDone)
	for {
		readyList, err := ls.expiryWheel.Wait()
		if err != nil {
			logger.Warnf("lite expiry wheel wait: %v", err)
		}
		if len(readyList) == 0 {
			select {
			case <-ls.stopCh:
				logger.Info("lite expiry scan loop exiting")
			default:
				logger.Warn("lite expiry wheel stopped, expiry scan loop exiting")
			}
			return
		}
		// Process every returned batch even when Wait reports backlog. Handling
		// synchronously provides bounded backpressure instead of spawning an
		// unbounded number of reap goroutines under an expiry burst.
		for _, allocID := range readyList {
			ls.reapExpiredAllocation(allocID)
		}
	}
}

// reapExpiredAllocation is the expiry callback for a single allocation. It mirrors
// the legacy leaseHolder.pollLease callback: release InUse, delete the allocation
// from the map, and remove the session binding. The time wheel task is already
// removed by the wheel itself (times=1 means fire-once), so no DelTask is needed.
func (ls *LiteScheduler) reapExpiredAllocation(allocID string) {
	logger := log.GetLogger().With(zap.String("allocID", allocID))

	ls.allocMu.Lock()
	alloc, ok := ls.allocations[allocID]
	if !ok {
		ls.allocMu.Unlock()
		return
	}
	delete(ls.allocations, allocID)
	ls.allocMu.Unlock()

	if pool := ls.getPool(alloc.FuncKey); pool != nil {
		pool.Lock()
		if slot := pool.instances[alloc.InstanceID]; slot != nil && slot.InUse > 0 {
			slot.InUse--
		}
		// Decrement InstanceSession's activeAllocs; pure SessionContext requests
		// have no sticky binding.
		bindingKey := ""
		needTimer := false
		if alloc.SessionID != "" {
			bindingKey = sessionBindingKey(alloc.SessionID, alloc.SessionCtxID)
			needTimer, _ = pool.unbindSessionOnRelease(bindingKey)
		}
		sessionTTL := alloc.SessionTTL
		pool.Unlock()
		if needTimer {
			ls.startSessionUnbindTimer(pool, bindingKey, sessionTTL)
		}
		logger.Infof("lite expiry reaped: instance %s, funcKey %s, InUse decremented",
			alloc.InstanceID, alloc.FuncKey)
	} else {
		logger.Infof("lite expiry reaped: pool gone (func %s undeployed), allocation %s cleaned",
			alloc.FuncKey, allocID)
	}
}
