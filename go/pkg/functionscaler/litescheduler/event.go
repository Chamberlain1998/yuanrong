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
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	commonTypes "yuanrong.org/kernel/pkg/common/faas_common/types"
	"yuanrong.org/kernel/pkg/functionscaler/registry"
	"yuanrong.org/kernel/pkg/functionscaler/types"
	"yuanrong.org/kernel/pkg/functionscaler/utils"
)

const defaultChanSize = 1000

// SubscribeAndLoop registers three independent registry subscriptions and starts event loops.
// It reads ls.stopCh (populated by New) to signal the three loops to exit; the registry
// does not close subscription channels, so without stopCh the loops would leak for the
// lifetime of the process.
func (ls *LiteScheduler) SubscribeAndLoop() {
	ls.funcSpecCh = make(chan registry.SubEvent, defaultChanSize)
	ls.insSpecCh = make(chan registry.SubEvent, defaultChanSize)
	ls.schedulerCh = make(chan registry.SubEvent, defaultChanSize)
	registry.GlobalRegistry.SubscribeFuncSpec(ls.funcSpecCh)
	registry.GlobalRegistry.SubscribeInsSpec(ls.insSpecCh)
	registry.GlobalRegistry.SubscribeSchedulerProxy(ls.schedulerCh)
	go ls.processFuncSpecEvents()
	go ls.processInstanceEvents()
	go ls.processSchedulerEvents()
	go ls.processExpiryEvents()
	go ls.processSessionContextIdle()
}

func (ls *LiteScheduler) processFuncSpecEvents() {
	logger := log.GetLogger()
	for {
		select {
		case <-ls.stopCh:
			logger.Info("lite funcSpec event loop exiting")
			return
		case event, ok := <-ls.funcSpecCh:
			if !ok {
				logger.Warn("lite funcSpec channel closed, event loop exiting")
				return
			}
			funcSpec, ok := event.EventMsg.(*types.FunctionSpecification)
			if !ok {
				logger.Warnf("lite funcSpec event type assertion failed, skip")
				continue
			}
			if !ls.isFuncEnabled(funcSpec.FuncKey) {
				continue
			}
			switch event.EventType {
			case registry.SubEventTypeUpdate, registry.SubEventTypeSynced:
				ls.upsertPool(funcSpec)
			case registry.SubEventTypeDelete:
				logger.Infof("lite funcSpec delete: drop pool %s", funcSpec.FuncKey)
				ls.deletePool(funcSpec.FuncKey)
			}
		}
	}
}

func (ls *LiteScheduler) processInstanceEvents() {
	logger := log.GetLogger()
	for {
		select {
		case <-ls.stopCh:
			logger.Info("lite instance event loop exiting")
			return
		case event, ok := <-ls.insSpecCh:
			if !ok {
				logger.Warn("lite instance channel closed, event loop exiting")
				return
			}
			insSpec, ok := event.EventMsg.(*commonTypes.InstanceSpecification)
			if !ok {
				logger.Warnf("lite instance event type assertion failed, skip")
				continue
			}
			funcKey := insSpec.CreateOptions[types.FunctionKeyNote]
			if funcKey == "" {
				logger.Warnf("lite instance event missing funcKey in CreateOptions, skip")
				continue
			}
			if !ls.isFuncEnabled(funcKey) {
				continue
			}
			pool := ls.getPool(funcKey)
			if pool == nil {
				logger.Warnf("lite instance event: pool %s not found (funcSpec event not yet synced), skip", funcKey)
				continue
			}
			// Snapshot pool.funcSpec under RLock to avoid a data race with upsertPool
			// (which replaces the pointer under Lock). BuildInstanceFromInsSpec may be
			// long-running and must not hold the lock; reading the snapshotted pointer
			// is safe because upsertPool only swaps the pointer, it never mutates the
			// old *FunctionSpecification in place.
			ls.poolsMu.RLock()
			funcSpec := pool.funcSpec
			ls.poolsMu.RUnlock()
			instance := utils.BuildInstanceFromInsSpec(insSpec, funcSpec)
			switch event.EventType {
			case registry.SubEventTypeUpdate:
				ls.handleInstanceUpdate(pool, instance)
			case registry.SubEventTypeDelete:
				ls.handleInstanceDelete(pool, instance)
			}
		}
	}
}

func (ls *LiteScheduler) processSchedulerEvents() {
	logger := log.GetLogger()
	for {
		select {
		case <-ls.stopCh:
			logger.Info("lite scheduler-proxy event loop exiting")
			return
		case event, ok := <-ls.schedulerCh:
			if !ok {
				logger.Warn("lite scheduler-proxy channel closed, event loop exiting")
				return
			}
			logger.Debugf("lite scheduler observed ring change: type %v", event.EventType)
			for _, pool := range ls.Pools() {
				pool.Lock()
				for _, instance := range pool.instances {
					if instance.SessionCtxID == "" {
						continue
					}
					routingKey := sessionContextRoutingKey(pool.funcKey, instance.SessionCtxID)
					if ls.ownerProxy != nil {
						if _, owned := ls.ownerProxy.CheckHashOwner(routingKey); owned {
							continue
						}
					}
					instance.IdleSince = time.Time{}
					instance.Reclaiming = false
				}
				pool.Unlock()
			}
		}
	}
}

func (ls *LiteScheduler) upsertPool(funcSpec *types.FunctionSpecification) {
	logger := log.GetLogger().With(zap.String("funcKey", funcSpec.FuncKey))
	ls.poolsMu.Lock()
	defer ls.poolsMu.Unlock()
	pool, ok := ls.pools[funcSpec.FuncKey]
	if !ok {
		pool = &LiteFunctionPool{
			funcKey: funcSpec.FuncKey, funcSpec: funcSpec,
			instances: map[string]*LiteInstance{}, sessions: map[string]*sessionBinding{},
			dispatcher:   newDispatcher(funcSpec),
			sessionStore: newLiteSessionStore(funcSpec.FuncKey),
		}
		ls.pools[funcSpec.FuncKey] = pool
		logger.Infof("lite pool created: dispatcher %s", pool.dispatcher.Policy())
		return
	}
	pool.funcSpec = funcSpec
	logger.Debug("lite pool funcSpec updated")
}

func (ls *LiteScheduler) deletePool(funcKey string) {
	logger := log.GetLogger().With(zap.String("funcKey", funcKey))
	ls.poolsMu.Lock()
	pool, ok := ls.pools[funcKey]
	if ok {
		// Stop all session idle-unbind timers and snapshot the sessionID list under
		// pool.Lock, then clear the map. We do NOT call removeSessionBinding here:
		// it would enqueue async Deletes that the about-to-stop worker would drop.
		// Instead cleanExternalRecords deletes them synchronously below.
		pool.Lock()
		sessionIDs := make([]string, 0, len(pool.sessions))
		for sid, binding := range pool.sessions {
			binding.stopTimer()
			sessionIDs = append(sessionIDs, sid)
		}
		pool.sessions = make(map[string]*sessionBinding)
		pool.Unlock()
		// Stop the async worker first so no in-flight op races the sync cleanup,
		// then synchronously delete all external records (cold path, blocking OK).
		pool.sessionStore.stop()
		pool.sessionStore.cleanExternalRecords(sessionIDs)
	}
	delete(ls.pools, funcKey)
	ls.poolsMu.Unlock()
	ls.allocMu.Lock()
	for id, alloc := range ls.allocations {
		if alloc.FuncKey == funcKey {
			delete(ls.allocations, id)
			ls.removeExpiryTask(id)
		}
	}
	ls.allocMu.Unlock()
	logger.Info("lite pool deleted and its allocations purged")
}

func (ls *LiteScheduler) handleInstanceUpdate(pool *LiteFunctionPool, ins *types.Instance) {
	logger := log.GetLogger().With(zap.String("funcKey", pool.funcKey), zap.String("instanceID", ins.InstanceID))
	pool.Lock()
	defer pool.Unlock()
	switch mapStatus(ins.InstanceStatus.Code) {
	case InstanceStatusRunning, InstanceStatusSubHealth:
		next := buildLiteInstanceFromInstance(ins)
		if current := pool.instances[ins.InstanceID]; current != nil {
			next.InUse = current.InUse
			next.IdleSince = current.IdleSince
			next.Reclaiming = current.Reclaiming
		}
		pool.instances[ins.InstanceID] = next
		logger.Debugf("lite instance upserted: status %d, capacity %d", ins.InstanceStatus.Code, ins.ConcurrentNum)
	case InstanceStatusUnavailable:
		ls.removeInstanceLocked(pool, ins.InstanceID)
		logger.Infof("lite instance marked unavailable, removed: %s", ins.InstanceID)
	}
}

func (ls *LiteScheduler) handleInstanceDelete(pool *LiteFunctionPool, ins *types.Instance) {
	logger := log.GetLogger().With(zap.String("funcKey", pool.funcKey), zap.String("instanceID", ins.InstanceID))
	pool.Lock()
	defer pool.Unlock()
	ls.removeInstanceLocked(pool, ins.InstanceID)
	logger.Infof("lite instance deleted from pool")
}

func (ls *LiteScheduler) removeInstanceLocked(pool *LiteFunctionPool, instanceID string) {
	delete(pool.instances, instanceID)
	for sid, binding := range pool.sessions {
		if binding.instanceID == instanceID {
			pool.removeSessionBinding(sid)
		}
	}
	ls.allocMu.Lock()
	for allocID, alloc := range ls.allocations {
		if alloc.InstanceID == instanceID && alloc.FuncKey == pool.funcKey {
			delete(ls.allocations, allocID)
			ls.removeExpiryTask(allocID)
		}
	}
	ls.allocMu.Unlock()
}
