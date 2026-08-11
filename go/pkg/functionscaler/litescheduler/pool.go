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
	"sync"
	"time"

	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

// InstanceStatus is the scheduling status of a local instance slot.
type InstanceStatus int

const (
	// InstanceStatusUnavailable covers fatal/evicted/exiting/deleted/new/scheduling/creating.
	InstanceStatusUnavailable InstanceStatus = iota
	// InstanceStatusRunning is schedulable, highest priority.
	InstanceStatusRunning
	// InstanceStatusSubHealth is schedulable but lower priority.
	InstanceStatusSubHealth
)

// subHealthPenalty makes subHealth rank below healthy even when its load is lower.
const subHealthPenalty = 1.0

// LiteInstance is a local slot view of an instance for LiteScheduler dispatch.
type LiteInstance struct {
	InstanceID      string
	FuncKey         string
	Capacity        int
	InUse           int
	Status          InstanceStatus
	InstanceIP      string
	InstancePort    string
	NodeIP          string
	NodePort        string
	FuncSig         string
	FunctionProxyID string
	RouteAddress    string
	AZ              string
	SessionCtxID    string
	IdleSince       time.Time
	Reclaiming      bool
}

// sessionBinding tracks a session's binding to an instance plus the idle-unbind
// timer state. It mirrors legacy's sessionRecord: when activeAllocs drops to 0,
// a timer is started; if a new acquire arrives before the timer fires, the timer
// is cancelled. On timer fire, the session→instance binding is removed.
type sessionBinding struct {
	instanceID   string
	activeAllocs int // number of outstanding allocations for this session
	timer        *time.Timer
	expiring     bool // true when the idle-unbind timer is ticking
}

// LiteFunctionPool holds local instances, session bindings and dispatcher for one funcKey.
type LiteFunctionPool struct {
	funcKey      string
	funcSpec     *types.FunctionSpecification
	instances    map[string]*LiteInstance
	sessions     map[string]*sessionBinding // sessionID -> binding
	dispatcher   Dispatcher
	sessionStore *liteSessionStore // external store coordinator; nil => all ops no-op (tests)
	sync.RWMutex
	seqCounter uint64 // for allocationID seq; protected by pool.Lock (writer-serialized)
}

// PoolStats is a read-only snapshot for Prometheus collector.
type PoolStats struct {
	FuncKey       string
	TenantID      string
	InstanceCount int
	Capacity      int
	InUse         int
	SessionCount  int
	Policy        string
}

// Dispatcher selects a target LiteInstance for a session request.
type Dispatcher interface {
	// Select picks one instance from slots; returns nil if none schedulable.
	Select(slots []*LiteInstance) *LiteInstance
	// Policy returns the dispatcher policy name.
	Policy() string
}

// mapStatus maps instance.InstanceStatus.Code to LiteInstance.Status.
func mapStatus(code int32) InstanceStatus {
	switch code {
	case int32(constant.KernelInstanceStatusRunning):
		return InstanceStatusRunning
	case int32(constant.KernelInstanceStatusSubHealth):
		return InstanceStatusSubHealth
	default:
		return InstanceStatusUnavailable
	}
}

// buildLiteInstanceFromInstance copies schedulable fields from types.Instance.
func buildLiteInstanceFromInstance(ins *types.Instance) *LiteInstance {
	if ins == nil {
		return nil
	}
	return &LiteInstance{
		InstanceID:      ins.InstanceID,
		FuncKey:         ins.FuncKey,
		Capacity:        ins.ConcurrentNum,
		Status:          mapStatus(ins.InstanceStatus.Code),
		InstanceIP:      ins.InstanceIP,
		InstancePort:    ins.InstancePort,
		NodeIP:          ins.NodeIP,
		NodePort:        ins.NodePort,
		FuncSig:         ins.FuncSig,
		FunctionProxyID: ins.FunctionProxyID,
		RouteAddress:    ins.RouteAddress,
		AZ:              ins.AZ,
		SessionCtxID:    instanceSessionCtxID(ins),
	}
}

// candidateSlotsLocked returns schedulable instances; caller must hold pool.Lock.
func (p *LiteFunctionPool) candidateSlotsLocked(sessionCtxID string) []*LiteInstance {
	out := make([]*LiteInstance, 0, len(p.instances))
	for _, ins := range p.instances {
		if ins.SessionCtxID == sessionCtxID && !ins.Reclaiming &&
			(ins.Status == InstanceStatusRunning || ins.Status == InstanceStatusSubHealth) &&
			ins.InUse < ins.Capacity {
			out = append(out, ins)
		}
	}
	return out
}

func instanceSessionCtxID(ins *types.Instance) string {
	if ins == nil || ins.SessionCtxID == nil {
		return ""
	}
	return *ins.SessionCtxID
}

func sessionBindingKey(sessionID, sessionCtxID string) string {
	if sessionCtxID == "" {
		return sessionID
	}
	return types.JoinKey(sessionID, sessionCtxID)
}

// currentInUse returns the sum of InUse over all instances. It takes pool.RLock itself,
// so the caller must NOT hold pool.Lock (or pool.RLock). handleColdStart invokes this for
// the ScaleHint snapshot AFTER handleAcquire has released pool.Lock, so the RLock here is
// not nested with any caller-held lock and cannot deadlock. The RLock is mutually exclusive
// with event.go's pool.Lock writers (handleInstanceUpdate writes pool.instances), so the
// map read here is safe against concurrent writes.
func (p *LiteFunctionPool) currentInUse() int {
	p.RLock()
	defer p.RUnlock()
	n := 0
	for _, ins := range p.instances {
		n += ins.InUse
	}
	return n
}

// currentCapacity returns the sum of Capacity over all instances. See currentInUse for
// the locking contract: caller must NOT hold pool.Lock; this takes pool.RLock itself.
func (p *LiteFunctionPool) currentCapacity() int {
	p.RLock()
	defer p.RUnlock()
	n := 0
	for _, ins := range p.instances {
		n += ins.Capacity
	}
	return n
}

// instanceByID returns the instance pointer for id (nil if absent); caller manages locking.
func (p *LiteFunctionPool) instanceByID(id string) *LiteInstance { return p.instances[id] }

// Stats returns a read-only snapshot of pool state for the Prometheus collector.
// Only Running/SubHealth instances contribute to Capacity/InUse/InstanceCount,
// matching candidateSlotsLocked's schedulable definition (without the InUse<Capacity
// filter, since capacity reporting should include full instances too).
func (p *LiteFunctionPool) Stats() PoolStats {
	p.RLock()
	defer p.RUnlock()
	var capacity, inUse, instCount int
	for _, ins := range p.instances {
		if ins.Status == InstanceStatusRunning || ins.Status == InstanceStatusSubHealth {
			instCount++
			capacity += ins.Capacity
			inUse += ins.InUse
		}
	}
	policy := "unknown"
	if p.dispatcher != nil {
		policy = p.dispatcher.Policy()
	}
	return PoolStats{
		FuncKey:       p.funcKey,
		TenantID:      splitFuncKey(p.funcKey).tenantID,
		InstanceCount: instCount,
		Capacity:      capacity,
		InUse:         inUse,
		SessionCount:  len(p.sessions),
		Policy:        policy,
	}
}

// sessionTTLFor normalizes a request-provided sessionTTL (seconds) to a Duration.
// 0 means immediate unbind (timer fires instantly); positive values are used as-is.
// Negative values are rejected by handleAcquire before reaching here.
func sessionTTLFor(reqTTL int) time.Duration {
	if reqTTL <= 0 {
		return 0
	}
	return time.Duration(reqTTL) * time.Second
}

// tryLocalStickyLocked checks the local session binding and returns the matching
// schedulable slot. Returns (slot, true) on sticky hit (and cancels any pending
// idle-unbind timer). Returns (nil, false) on miss or on an invalidated binding
// (instance absent/unhealthy/full); in the latter case the stale binding is
// removed so the caller can dispatch or lazily recover from the external store.
// Shared by handleAcquire Phase 1 (first lookup) and Phase 3 (re-check after the
// out-of-lock store.Get). Caller must hold pool.Lock.
func (p *LiteFunctionPool) tryLocalStickyLocked(bindingKey string) (*LiteInstance, bool) {
	binding, ok := p.sessions[bindingKey]
	if !ok {
		return nil, false
	}
	slot := p.instances[binding.instanceID]
	if slot != nil &&
		(slot.Status == InstanceStatusRunning || slot.Status == InstanceStatusSubHealth) &&
		slot.InUse < slot.Capacity {
		p.cancelSessionUnbind(bindingKey)
		return slot, true
	}
	// Binding exists but its instance is absent/unhealthy/full: clean the stale
	// binding so dispatch/recovery start from a clean slate.
	p.removeSessionBinding(bindingKey)
	return nil, false
}

// bindSessionOnAcquire creates or refreshes a sessionBinding for the given
// sessionID, incrementing activeAllocs. If the session was in the idle-unbind
// pending state (expiring), the timer is cancelled. Caller must hold pool.Lock.
func (p *LiteFunctionPool) bindSessionOnAcquire(sessionID, instanceID string) {
	binding, ok := p.sessions[sessionID]
	if !ok {
		binding = &sessionBinding{instanceID: instanceID}
		p.sessions[sessionID] = binding
	} else {
		// session rebind to a different instance (e.g. sticky invalidated): stop
		// any pending unbind timer and rebind.
		binding.stopTimer()
		binding.expiring = false
		binding.instanceID = instanceID
	}
	binding.activeAllocs++
}

// unbindSessionOnRelease decrements activeAllocs and, if it reaches 0, starts the
// idle-unbind timer. The caller schedules the timer outside pool.Lock.
// Caller must hold pool.Lock.
func (p *LiteFunctionPool) unbindSessionOnRelease(sessionID string) (needTimer bool, ttl time.Duration) {
	binding, ok := p.sessions[sessionID]
	if !ok {
		return false, 0
	}
	if binding.activeAllocs > 0 {
		binding.activeAllocs--
	}
	if binding.activeAllocs > 0 || binding.expiring {
		return false, 0
	}
	// All allocations released and no timer running: start idle-unbind countdown.
	binding.expiring = true
	return true, 0 // ttl filled by caller via sessionTTLFor
}

// cancelSessionUnbind cancels the idle-unbind timer if it is running.
// Called when an acquire arrives for a session whose timer is ticking.
// Caller must hold pool.Lock.
func (p *LiteFunctionPool) cancelSessionUnbind(sessionID string) {
	binding, ok := p.sessions[sessionID]
	if !ok || !binding.expiring {
		return
	}
	binding.stopTimer()
	binding.expiring = false
}

// removeSessionBinding deletes the session binding entry entirely and enqueues
// an async Delete of the external store record so a subsequent acquire on the same
// session does not recover a stale binding. Used by the timer callback, instance
// deletion cleanup and sticky-invalidated re-dispatch. Caller must hold pool.Lock;
// the async enqueue does not block on I/O. nil-safe when sessionStore is unset.
// deletePool does NOT call this (it bulk-syncs via cleanExternalRecords instead).
func (p *LiteFunctionPool) removeSessionBinding(sessionID string) {
	if binding, ok := p.sessions[sessionID]; ok {
		binding.stopTimer()
	}
	delete(p.sessions, sessionID)
	p.sessionStore.deleteSessionFromStore(sessionID)
}

// stopTimer stops the timer if set; safe to call when timer is nil.
func (b *sessionBinding) stopTimer() {
	if b.timer != nil {
		b.timer.Stop()
		b.timer = nil
	}
}
