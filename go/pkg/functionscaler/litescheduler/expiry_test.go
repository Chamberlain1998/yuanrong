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
	"errors"
	"runtime"
	"sync"
	"testing"
	"time"

	"github.com/smartystreets/goconvey/convey"
	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/common/faas_common/statuscode"
	"yuanrong.org/kernel/pkg/common/faas_common/timewheel"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

type expiryWaitResult struct {
	readyList []string
	err       error
}

type scriptedExpiryWheel struct {
	results  chan expiryWaitResult
	stopCh   chan struct{}
	stopOnce sync.Once
}

func newScriptedExpiryWheel() *scriptedExpiryWheel {
	return &scriptedExpiryWheel{
		results: make(chan expiryWaitResult, 1),
		stopCh:  make(chan struct{}),
	}
}

func (w *scriptedExpiryWheel) Wait() ([]string, error) {
	select {
	case result := <-w.results:
		return result.readyList, result.err
	case <-w.stopCh:
		return nil, nil
	}
}

func (w *scriptedExpiryWheel) AddTask(string, time.Duration, int) error    { return nil }
func (w *scriptedExpiryWheel) DelTask(string) error                        { return nil }
func (w *scriptedExpiryWheel) UpdateTask(string, time.Duration, int) error { return nil }
func (w *scriptedExpiryWheel) Stop() {
	w.stopOnce.Do(func() { close(w.stopCh) })
}

// newExpiryTestScheduler constructs a LiteScheduler with a real expiryWheel
// whose perimeter (pace*slots) is small enough to accept a short test TTL.
// It also starts the processExpiryEvents loop. The caller must close stopCh.
func newExpiryTestScheduler(t *testing.T) (*LiteScheduler, chan struct{}) {
	t.Helper()
	stopCh := make(chan struct{})
	// pace=2ms, slots=2 => perimeter=4ms, accepts any TTL >= 4ms.
	ls := &LiteScheduler{
		pools:       make(map[string]*LiteFunctionPool),
		allocations: make(map[string]*Allocation),
		stopCh:      stopCh,
		expiryWheel: timewheel.NewSimpleTimeWheel(2*time.Millisecond, 2),
	}
	ls.pools["t1/fA/v1"] = newTestPool(t)
	go ls.processExpiryEvents()
	return ls, stopCh
}

// TestExpiryAutoReapsAfterTTL verifies that an allocation whose TTL elapses
// without a retain or release is automatically reaped by the background expiry
// scanner, and InUse is decremented.
func TestExpiryAutoReapsAfterTTL(t *testing.T) {
	convey.Convey("expired allocation is auto-reaped and InUse decremented", t, func() {
		orig := config.GlobalConfig.LiteScheduler
		defer func() { config.GlobalConfig.LiteScheduler = orig }()
		config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true}
		config.GlobalConfig.LeaseSpan = 50 // 50ms TTL

		ls, stopCh := newExpiryTestScheduler(t)
		defer close(stopCh)

		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1",
			SessionID: "sess1", SessionTTL: 1, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())
		convey.So(resp.ErrorCode, convey.ShouldEqual, constant.InsReqSuccessCode)
		allocID := resp.ThreadID

		pool := ls.pools["t1/fA/v1"]
		convey.So(pool.currentInUse(), convey.ShouldEqual, 1)

		// Wait for the TTL to elapse and the scanner to reap the allocation.
		deadline := time.Now().Add(3 * time.Second)
		for time.Now().Before(deadline) {
			ls.allocMu.RLock()
			_, exists := ls.allocations[allocID]
			ls.allocMu.RUnlock()
			if !exists {
				break
			}
			time.Sleep(20 * time.Millisecond)
		}

		convey.So(ls.allocations, convey.ShouldNotContainKey, allocID)
		convey.So(pool.currentInUse(), convey.ShouldEqual, 0)
		// After reap, the session binding enters idle-unbind countdown (sessionTTL=1s).
		// Wait for the unbind timer to fire, then the session binding should be gone.
		unbindDeadline := time.Now().Add(3 * time.Second)
		for time.Now().Before(unbindDeadline) {
			pool.RLock()
			_, hasSession := pool.sessions["sess1"]
			pool.RUnlock()
			if !hasSession {
				break
			}
			time.Sleep(50 * time.Millisecond)
		}
		pool.RLock()
		_, hasSession := pool.sessions["sess1"]
		pool.RUnlock()
		convey.So(hasSession, convey.ShouldBeFalse)
	})
}

// TestSessionTTLNegativeReturnsError verifies that a negative sessionTTL is
// rejected at acquire time with InstanceSessionInvalidErrCode, matching
// legacy's CheckInstanceSessionValid behavior.
func TestSessionTTLNegativeReturnsError(t *testing.T) {
	convey.Convey("negative sessionTTL returns error", t, func() {
		orig := config.GlobalConfig.LiteScheduler
		defer func() { config.GlobalConfig.LiteScheduler = orig }()
		config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true}
		config.GlobalConfig.LeaseSpan = 5000

		ls := &LiteScheduler{
			pools:       make(map[string]*LiteFunctionPool),
			allocations: make(map[string]*Allocation),
		}
		ls.pools["t1/fA/v1"] = newTestPool(t)

		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1",
			SessionID: "sess1", SessionTTL: -1, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())
		convey.So(resp.ErrorCode, convey.ShouldEqual, statuscode.InstanceSessionInvalidErrCode)
	})
}

// TestSessionTTLZeroImmediateUnbind verifies that sessionTTL=0 causes immediate
// session unbind after release (no sticky retention).
func TestSessionTTLZeroImmediateUnbind(t *testing.T) {
	convey.Convey("sessionTTL=0 unbinds immediately after release", t, func() {
		orig := config.GlobalConfig.LiteScheduler
		defer func() { config.GlobalConfig.LiteScheduler = orig }()
		config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true}
		config.GlobalConfig.LeaseSpan = 5000

		ls := &LiteScheduler{
			pools:       make(map[string]*LiteFunctionPool),
			allocations: make(map[string]*Allocation),
		}
		ls.pools["t1/fA/v1"] = newTestPool(t)

		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1",
			SessionID: "sess1", SessionTTL: 0, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())
		allocID := resp.ThreadID

		pool := ls.pools["t1/fA/v1"]
		pool.RLock()
		_, hasBinding := pool.sessions["sess1"]
		pool.RUnlock()
		convey.So(hasBinding, convey.ShouldBeTrue)

		// Release → session should be unbound almost immediately (sessionTTL=0).
		relReq := &LiteRequest{Op: "release",
			AllocationIDs: []string{allocID}, FuncKey: "t1/fA/v1", TraceID: "tr"}
		ls.handleRelease(relReq, time.Now())

		// Give the timer goroutine a brief moment to fire (0s timer fires instantly
		// but the goroutine needs to be scheduled).
		time.Sleep(200 * time.Millisecond)

		pool.RLock()
		_, hasBinding = pool.sessions["sess1"]
		pool.RUnlock()
		convey.So(hasBinding, convey.ShouldBeFalse)
	})
}

// TestRetainUpdatesExpiryWheel verifies that retain pushes the expiry deadline
// forward, so the allocation is NOT reaped while retains keep arriving.
func TestRetainUpdatesExpiryWheel(t *testing.T) {
	convey.Convey("retain keeps allocation alive past original TTL", t, func() {
		orig := config.GlobalConfig.LiteScheduler
		defer func() { config.GlobalConfig.LiteScheduler = orig }()
		config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true}
		config.GlobalConfig.LeaseSpan = 50 // 50ms TTL

		ls, stopCh := newExpiryTestScheduler(t)
		defer close(stopCh)

		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1",
			SessionID: "sess1", TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())
		convey.So(resp.ErrorCode, convey.ShouldEqual, constant.InsReqSuccessCode)
		allocID := resp.ThreadID

		pool := ls.pools["t1/fA/v1"]

		// Retain every 30ms for ~200ms. Each retain pushes the deadline forward
		// by 50ms, so the allocation must survive the entire retain loop.
		stopRetain := make(chan struct{})
		var wg sync.WaitGroup
		wg.Add(1)
		go func() {
			defer wg.Done()
			ticker := time.NewTicker(30 * time.Millisecond)
			defer ticker.Stop()
			for {
				select {
				case <-stopRetain:
					return
				case <-ticker.C:
					retReq := &LiteRequest{Op: "retain",
						AllocationIDs: []string{allocID}, TraceID: "tr"}
					ls.handleRetain(retReq, time.Now())
				}
			}
		}()

		// After 200ms of retaining, the alloc should still exist.
		time.Sleep(200 * time.Millisecond)
		close(stopRetain)
		wg.Wait()

		ls.allocMu.RLock()
		_, exists := ls.allocations[allocID]
		ls.allocMu.RUnlock()
		convey.So(exists, convey.ShouldBeTrue)
		convey.So(pool.currentInUse(), convey.ShouldEqual, 1)

		// Stop retaining; now the allocation should expire within ~TTL.
		deadline := time.Now().Add(3 * time.Second)
		for time.Now().Before(deadline) {
			ls.allocMu.RLock()
			_, exists = ls.allocations[allocID]
			ls.allocMu.RUnlock()
			if !exists {
				break
			}
			time.Sleep(20 * time.Millisecond)
		}
		convey.So(ls.allocations, convey.ShouldNotContainKey, allocID)
		convey.So(pool.currentInUse(), convey.ShouldEqual, 0)
	})
}

// TestReleaseCancelsExpiryTask verifies that an explicit release removes the
// expiry task, so the scanner does not try to reap an already-released allocation.
func TestReleaseCancelsExpiryTask(t *testing.T) {
	convey.Convey("explicit release cancels expiry, no double-reap", t, func() {
		orig := config.GlobalConfig.LiteScheduler
		defer func() { config.GlobalConfig.LiteScheduler = orig }()
		config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true}
		config.GlobalConfig.LeaseSpan = 50 // 50ms TTL

		ls, stopCh := newExpiryTestScheduler(t)
		defer close(stopCh)

		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1",
			SessionID: "sess1", TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())
		allocID := resp.ThreadID

		pool := ls.pools["t1/fA/v1"]
		convey.So(pool.currentInUse(), convey.ShouldEqual, 1)

		// Explicitly release
		relReq := &LiteRequest{Op: "release",
			AllocationIDs: []string{allocID}, FuncKey: "t1/fA/v1", TraceID: "tr"}
		ls.handleRelease(relReq, time.Now())

		// Wait beyond the original TTL; nothing should panic or error.
		time.Sleep(150 * time.Millisecond)

		convey.So(ls.allocations, convey.ShouldNotContainKey, allocID)
		convey.So(pool.currentInUse(), convey.ShouldEqual, 0)
	})
}

// TestExpiryWheelNilDoesNotPanic verifies that a LiteScheduler without an
// expiryWheel (e.g. in some test harnesses that construct LiteScheduler
// directly without calling New) does not panic on register/update/remove.
func TestExpiryWheelNilDoesNotPanic(t *testing.T) {
	convey.Convey("nil expiryWheel does not panic", t, func() {
		ls := &LiteScheduler{
			pools:       make(map[string]*LiteFunctionPool),
			allocations: make(map[string]*Allocation),
			// expiryWheel is nil
		}
		convey.So(func() { ls.registerExpiryTask("alloc1") }, convey.ShouldNotPanic)
		convey.So(func() { ls.updateExpiryTask("alloc1") }, convey.ShouldNotPanic)
		convey.So(func() { ls.removeExpiryTask("alloc1") }, convey.ShouldNotPanic)
	})
}

// TestSessionIdleUnbindAfterRelease verifies that a session whose all allocations
// are released gets its binding removed after sessionTTL elapses (no new acquire
// arrives in between). Mirrors legacy's startUnbindInstanceSession → timer fire.
func TestSessionIdleUnbindAfterRelease(t *testing.T) {
	convey.Convey("session binding removed after idle sessionTTL", t, func() {
		orig := config.GlobalConfig.LiteScheduler
		defer func() { config.GlobalConfig.LiteScheduler = orig }()
		config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true}
		config.GlobalConfig.LeaseSpan = 5000 // 5s lease TTL; won't fire during test

		ls := &LiteScheduler{
			pools:       make(map[string]*LiteFunctionPool),
			allocations: make(map[string]*Allocation),
		}
		ls.pools["t1/fA/v1"] = newTestPool(t)

		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1",
			SessionID: "sess1", SessionTTL: 1, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())
		allocID := resp.ThreadID

		pool := ls.pools["t1/fA/v1"]
		pool.RLock()
		_, hasBinding := pool.sessions["sess1"]
		pool.RUnlock()
		convey.So(hasBinding, convey.ShouldBeTrue)

		// Release the allocation → session enters idle-unbind countdown (1s).
		relReq := &LiteRequest{Op: "release",
			AllocationIDs: []string{allocID}, FuncKey: "t1/fA/v1", TraceID: "tr"}
		ls.handleRelease(relReq, time.Now())

		// Wait for the idle-unbind timer to fire (sessionTTL=1s).
		deadline := time.Now().Add(3 * time.Second)
		for time.Now().Before(deadline) {
			pool.RLock()
			_, hasBinding = pool.sessions["sess1"]
			pool.RUnlock()
			if !hasBinding {
				break
			}
			time.Sleep(50 * time.Millisecond)
		}
		pool.RLock()
		_, hasBinding = pool.sessions["sess1"]
		pool.RUnlock()
		convey.So(hasBinding, convey.ShouldBeFalse)
	})
}

// TestAcquireCancelsIdleUnbindTimer verifies that a new acquire arriving during
// the idle-unbind countdown cancels the timer and keeps the session binding.
func TestAcquireCancelsIdleUnbindTimer(t *testing.T) {
	convey.Convey("acquire during idle-unbind countdown cancels timer", t, func() {
		orig := config.GlobalConfig.LiteScheduler
		defer func() { config.GlobalConfig.LiteScheduler = orig }()
		config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true}
		config.GlobalConfig.LeaseSpan = 5000

		ls := &LiteScheduler{
			pools:       make(map[string]*LiteFunctionPool),
			allocations: make(map[string]*Allocation),
		}
		ls.pools["t1/fA/v1"] = newTestPool(t)

		// acquire → release → wait briefly → re-acquire (should cancel unbind timer)
		req1 := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1",
			SessionID: "sess1", SessionTTL: 2, TenantID: "t1", TraceID: "tr"}
		resp1 := ls.handleAcquire(req1, time.Now())
		allocID1 := resp1.ThreadID

		relReq := &LiteRequest{Op: "release",
			AllocationIDs: []string{allocID1}, FuncKey: "t1/fA/v1", TraceID: "tr"}
		ls.handleRelease(relReq, time.Now())
		pool := ls.pools["t1/fA/v1"]
		pool.RLock()
		binding := pool.sessions["sess1"]
		timerStored := binding != nil && binding.timer != nil
		expiring := binding != nil && binding.expiring
		pool.RUnlock()
		convey.So(binding, convey.ShouldNotBeNil)
		convey.So(timerStored, convey.ShouldBeTrue)
		convey.So(expiring, convey.ShouldBeTrue)

		// Wait 200ms (well within the 2s sessionTTL), then re-acquire.
		time.Sleep(200 * time.Millisecond)
		req2 := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1",
			SessionID: "sess1", SessionTTL: 2, TenantID: "t1", TraceID: "tr"}
		resp2 := ls.handleAcquire(req2, time.Now())
		convey.So(resp2.ErrorCode, convey.ShouldEqual, constant.InsReqSuccessCode)
		pool.RLock()
		binding = pool.sessions["sess1"]
		timerCleared := binding != nil && binding.timer == nil
		expiring = binding != nil && binding.expiring
		pool.RUnlock()
		convey.So(binding, convey.ShouldNotBeNil)
		convey.So(timerCleared, convey.ShouldBeTrue)
		convey.So(expiring, convey.ShouldBeFalse)

		// Wait beyond the original 2s sessionTTL; session should still be bound
		// because the timer was cancelled by the re-acquire.
		time.Sleep(3 * time.Second)
		pool.RLock()
		binding, hasBinding := pool.sessions["sess1"]
		activeAllocs := 0
		if binding != nil {
			activeAllocs = binding.activeAllocs
		}
		pool.RUnlock()
		convey.So(hasBinding, convey.ShouldBeTrue)
		convey.So(activeAllocs, convey.ShouldEqual, 1)
	})
}

// TestCancelledTimerCannotRemoveNewIdleGeneration verifies that a callback from
// an older idle period cannot remove the binding created by a later release.
func TestCancelledTimerCannotRemoveNewIdleGeneration(t *testing.T) {
	pool := newTestPool(t)
	ls := &LiteScheduler{
		pools:       map[string]*LiteFunctionPool{"t1/fA/v1": pool},
		allocations: map[string]*Allocation{},
	}
	firstReq := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1",
		SessionID: "sess1", SessionTTL: 1, TenantID: "t1", TraceID: "tr"}
	firstResp := ls.handleAcquire(firstReq, time.Now())
	ls.handleRelease(&LiteRequest{Op: "release", AllocationIDs: []string{firstResp.ThreadID}, TraceID: "tr"},
		time.Now())

	time.Sleep(600 * time.Millisecond)
	secondReq := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1",
		SessionID: "sess1", SessionTTL: 2, TenantID: "t1", TraceID: "tr"}
	secondResp := ls.handleAcquire(secondReq, time.Now())
	ls.handleRelease(&LiteRequest{Op: "release", AllocationIDs: []string{secondResp.ThreadID}, TraceID: "tr"},
		time.Now())

	// The first deadline has elapsed, but the second idle period still has more
	// than one second remaining. The binding must belong to the second timer.
	time.Sleep(600 * time.Millisecond)
	pool.Lock()
	binding, exists := pool.sessions["sess1"]
	if !exists || binding.timer == nil || !binding.expiring {
		pool.Unlock()
		t.Fatalf("new idle generation was removed by an old timer: exists=%v binding=%+v", exists, binding)
	}
	pool.removeSessionBinding("sess1")
	pool.Unlock()
}

// TestSessionTimerDoesNotLeakGoroutines is a regression for the OOM root cause:
// each old implementation release left one goroutine blocked on timer.C.
func TestSessionTimerDoesNotLeakGoroutines(t *testing.T) {
	const cycles = 256
	pool := newTestPool(t)
	ls := &LiteScheduler{
		pools:       map[string]*LiteFunctionPool{"t1/fA/v1": pool},
		allocations: map[string]*Allocation{},
	}
	req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1",
		SessionID: "leak-session", SessionTTL: 3600, TenantID: "t1", TraceID: "tr"}
	baseline := runtime.NumGoroutine()
	for i := 0; i < cycles; i++ {
		resp := ls.handleAcquire(req, time.Now())
		if resp.ErrorCode != constant.InsReqSuccessCode {
			t.Fatalf("cycle %d acquire failed: %d %s", i, resp.ErrorCode, resp.ErrorMessage)
		}
		ls.handleRelease(&LiteRequest{Op: "release", AllocationIDs: []string{resp.ThreadID}, TraceID: "tr"},
			time.Now())
	}
	pool.Lock()
	pool.removeSessionBinding("leak-session")
	pool.Unlock()
	runtime.Gosched()
	time.Sleep(50 * time.Millisecond)
	if delta := runtime.NumGoroutine() - baseline; delta > 20 {
		t.Fatalf("session timer goroutines grew with release cycles: delta=%d cycles=%d", delta, cycles)
	}
}

func TestProcessExpiryEventsHandlesReadyListWithError(t *testing.T) {
	pool := newTestPool(t)
	pool.instances["ins1"].InUse = 1
	wheel := newScriptedExpiryWheel()
	stopCh := make(chan struct{})
	ls := &LiteScheduler{
		pools:       map[string]*LiteFunctionPool{"t1/fA/v1": pool},
		allocations: map[string]*Allocation{},
		stopCh:      stopCh,
		expiryWheel: wheel,
	}
	const allocID = "lite:hash:ins1:thread:1"
	ls.allocations[allocID] = &Allocation{
		AllocationID: allocID, SessionID: "sess1", TenantID: "t1",
		InstanceID: "ins1", FuncKey: "t1/fA/v1",
	}
	wheel.results <- expiryWaitResult{readyList: []string{allocID}, err: errors.New("backlog warning")}
	done := make(chan struct{})
	go func() {
		ls.processExpiryEvents()
		close(done)
	}()

	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		ls.allocMu.RLock()
		_, exists := ls.allocations[allocID]
		ls.allocMu.RUnlock()
		if !exists {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	ls.allocMu.RLock()
	_, exists := ls.allocations[allocID]
	ls.allocMu.RUnlock()
	if exists {
		t.Fatal("ready allocation was discarded when Wait returned a warning")
	}
	close(stopCh)
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("expiry event loop did not stop")
	}
}

func TestProcessExpiryEventsStopsWhileWaitIsBlocked(t *testing.T) {
	stopCh := make(chan struct{})
	ls := &LiteScheduler{
		stopCh:      stopCh,
		expiryWheel: timewheel.NewSimpleTimeWheel(2*time.Millisecond, 2),
	}
	done := make(chan struct{})
	go func() {
		ls.processExpiryEvents()
		close(done)
	}()
	close(stopCh)
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("expiry event loop remained blocked in TimeWheel.Wait after stop")
	}
}
