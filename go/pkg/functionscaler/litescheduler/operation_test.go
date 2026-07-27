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
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/smartystreets/goconvey/convey"
	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/common/faas_common/statuscode"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

func newTestPool(t *testing.T) *LiteFunctionPool {
	p := &LiteFunctionPool{
		funcKey:    "t1/fA/v1",
		funcSpec:   &types.FunctionSpecification{FuncKey: "t1/fA/v1"},
		instances:  map[string]*LiteInstance{},
		sessions:   map[string]*sessionBinding{},
		dispatcher: &concurrencyDispatcher{},
	}
	p.instances["ins1"] = &LiteInstance{InstanceID: "ins1", FuncKey: "t1/fA/v1", Capacity: 2, InUse: 0,
		Status: InstanceStatusRunning, FuncSig: "sig"}
	p.instances["ins2"] = &LiteInstance{InstanceID: "ins2", FuncKey: "t1/fA/v1", Capacity: 2, InUse: 0,
		Status: InstanceStatusRunning, FuncSig: "sig"}
	return p
}

func TestLiteTTL(t *testing.T) {
	originalLeaseSpan := config.GlobalConfig.LeaseSpan
	defer func() { config.GlobalConfig.LeaseSpan = originalLeaseSpan }()

	tests := []struct {
		name      string
		leaseSpan int
		expected  time.Duration
	}{
		{name: "zero uses minimum", leaseSpan: 0, expected: types.MinLeaseInterval},
		{name: "below minimum uses minimum", leaseSpan: 100, expected: types.MinLeaseInterval},
		{name: "minimum is retained", leaseSpan: 500, expected: types.MinLeaseInterval},
		{name: "value above minimum is retained", leaseSpan: 1000, expected: time.Second},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			config.GlobalConfig.LeaseSpan = tt.leaseSpan
			if actual := liteTTL(); actual != tt.expected {
				t.Errorf("liteTTL() = %s, want %s", actual, tt.expected)
			}
		})
	}
}

func TestAcquireSessionSticky(t *testing.T) {
	convey.Convey("same session returns same instance", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		pool := newTestPool(t)
		ls.pools["t1/fA/v1"] = pool
		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: "sess1", SessionTTL: 30, TenantID: "t1", TraceID: "tr"}
		resp1 := ls.handleAcquire(req, time.Now())
		convey.So(resp1.ErrorCode, convey.ShouldEqual, constant.InsReqSuccessCode)
		id1 := resp1.InstanceID
		resp2 := ls.handleAcquire(req, time.Now())
		convey.So(resp2.InstanceID, convey.ShouldEqual, id1) // sticky
	})
}

func TestReleaseDecrementsInUseKeepsSticky(t *testing.T) {
	convey.Convey("release decrements InUse, keeps session binding", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		pool := newTestPool(t)
		ls.pools["t1/fA/v1"] = pool
		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: "sess1", SessionTTL: 30, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())
		allocID := resp.ThreadID
		convey.So(pool.instances["ins1"].InUse+pool.instances["ins2"].InUse, convey.ShouldEqual, 1)
		relReq := &LiteRequest{Op: "release", AllocationIDs: []string{allocID}, FuncKey: "t1/fA/v1", TraceID: "tr"}
		ls.handleRelease(relReq, time.Now())
		convey.So(pool.instances["ins1"].InUse+pool.instances["ins2"].InUse, convey.ShouldEqual, 0)
		pool.RLock()
		_, hasBinding := pool.sessions["sess1"]
		pool.RUnlock()
		convey.So(hasBinding, convey.ShouldBeTrue) // binding kept (sessionTTL=30s)
	})
}

func TestRetainDoesNotChangeConcurrency(t *testing.T) {
	convey.Convey("retain refreshes TTL, no InUse change", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		pool := newTestPool(t)
		ls.pools["t1/fA/v1"] = pool
		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: "sess1", SessionTTL: 30, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())
		allocID := resp.ThreadID
		oldExpire := ls.allocations[allocID].ExpireAt
		time.Sleep(5 * time.Millisecond)
		retReq := &LiteRequest{Op: "retain", AllocationIDs: []string{allocID}, FuncKey: "t1/fA/v1", TraceID: "tr"}
		ls.handleRetain(retReq, time.Now())
		convey.So(ls.allocations[allocID].ExpireAt.After(oldExpire), convey.ShouldBeTrue)
		convey.So(pool.instances["ins1"].InUse+pool.instances["ins2"].InUse, convey.ShouldEqual, 1) // unchanged
	})
}

func TestReleaseUnknownAllocationReturnsNotFound(t *testing.T) {
	convey.Convey("release unknown allocID -> InstanceNotFound", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		relReq := &LiteRequest{Op: "release", AllocationIDs: []string{"lite:dead:ins:thread:1"}, FuncKey: "t1/fA/v1", TraceID: "tr"}
		resp := ls.handleRelease(relReq, time.Now())
		convey.So(resp.ErrorCode, convey.ShouldEqual, statuscode.InstanceNotFoundErrCode)
	})
}

// TestReleaseWhenPoolNil covers HIGH2: a release that arrives after the
// function's pool has been removed (undeploy) must not dereference a nil
// pool when building the success response. The response still returns
// success with the allocID as ThreadID; only the instance fields are zero.
func TestReleaseWhenPoolNil(t *testing.T) {
	convey.Convey("release with pool==nil does not panic and returns success", t, func() {
		ls := &LiteScheduler{
			pools:       map[string]*LiteFunctionPool{}, // no pool for the funcKey
			allocations: map[string]*Allocation{},
		}
		alloc := &Allocation{
			AllocationID: "lite:hash:ins1:thread:1",
			SessionID:    "sess1", TenantID: "t1",
			InstanceID: "ins1", FuncKey: "t1/fA/v1",
			ExpireAt: time.Now().Add(liteTTL()), CreatedAt: time.Now(),
		}
		ls.allocations[alloc.AllocationID] = alloc
		relReq := &LiteRequest{Op: "release",
			AllocationIDs: []string{alloc.AllocationID}, FuncKey: "t1/fA/v1", TraceID: "tr"}
		// Must not panic.
		resp := ls.handleRelease(relReq, time.Now())
		convey.So(resp.ErrorCode, convey.ShouldEqual, constant.InsReqSuccessCode)
		convey.So(resp.ThreadID, convey.ShouldEqual, alloc.AllocationID)
		convey.So(resp.InstanceID, convey.ShouldEqual, "") // slot was nil
		convey.So(ls.allocations, convey.ShouldNotContainKey, alloc.AllocationID)
	})
}

// TestConcurrentAcquireRetainNoDeadlock exercises the lock-order fix in
// HIGH1. It mixes acquire / release / retain on the same pool concurrently.
// Under the race detector this catches both data races and deadlocks (a
// deadlock would hang the test past its timeout).
func TestConcurrentAcquireRetainNoDeadlock(t *testing.T) {
	convey.Convey("concurrent acquire/release/retain does not deadlock", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		ls.pools["t1/fA/v1"] = newTestPool(t)

		const n = 16
		var wg sync.WaitGroup
		wg.Add(n)
		// deadline signals timeout if any goroutine is stuck (deadlock).
		// t.Fatal must not be called from a non-test goroutine, so we capture
		// the timeout and assert on the main test goroutine after wg.Wait.
		done := make(chan struct{})
		timedOut := false
		go func() {
			select {
			case <-done:
			case <-time.After(5 * time.Second):
				timedOut = true
			}
		}()

		for i := 0; i < n; i++ {
			go func(id int) {
				defer wg.Done()
				sess := "sess" + string(rune('A'+id%8))
				req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1",
					SessionID: sess, SessionTTL: 30, TenantID: "t1", TraceID: "tr"}
				start := time.Now()
				resp := ls.handleAcquire(req, start)
				if resp.ErrorCode != constant.InsReqSuccessCode {
					return // cold-start path; nothing to retain/release
				}
				allocID := resp.ThreadID
				// retain then release in a loop to stress the lock-order paths.
				for j := 0; j < 4; j++ {
					ls.handleRetain(&LiteRequest{Op: "retain",
						AllocationIDs: []string{allocID}, TraceID: "tr"}, time.Now())
				}
				ls.handleRelease(&LiteRequest{Op: "release",
					AllocationIDs: []string{allocID}, FuncKey: "t1/fA/v1", TraceID: "tr"}, time.Now())
			}(i)
		}
		wg.Wait()
		close(done)
		if timedOut {
			t.Errorf("concurrent acquire/release/retain deadlocked")
		}
	})
}

func TestAcquireNoInstanceReturnsNoInstanceAfterTimeout(t *testing.T) {
	convey.Convey("acquire with no instances and short timeout returns NoInstanceAvailable", t, func() {
		orig := config.GlobalConfig.LiteScheduler
		defer func() { config.GlobalConfig.LiteScheduler = orig }()
		config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true, AcquireWaitTimeoutMs: 50}
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{},
			scaleHintSender: NewNoopSender(), stopCh: make(chan struct{})}
		pool := &LiteFunctionPool{funcKey: "t1/fA/v1", funcSpec: &types.FunctionSpecification{FuncKey: "t1/fA/v1"},
			instances: map[string]*LiteInstance{}, sessions: map[string]*sessionBinding{}, dispatcher: &concurrencyDispatcher{}}
		ls.pools["t1/fA/v1"] = pool
		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: "sess1", TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())
		convey.So(resp.ErrorCode, convey.ShouldEqual, statuscode.NoInstanceAvailableErrCode)
	})
}

// TestColdStartConcurrentWithInstanceUpdateNoRace exercises the CRITICAL fix: handleColdStart
// reads pool.instances via currentInUse()/currentCapacity() (pool.RLock) while handleInstanceUpdate
// writes pool.instances under pool.Lock. Under -race this must report no fatal/no data race.
func TestColdStartConcurrentWithInstanceUpdateNoRace(t *testing.T) {
	orig := config.GlobalConfig.LiteScheduler
	defer func() { config.GlobalConfig.LiteScheduler = orig }()
	config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true, AcquireWaitTimeoutMs: 100}
	ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{},
		scaleHintSender: NewNoopSender(), stopCh: make(chan struct{})}
	pool := &LiteFunctionPool{funcKey: "t1/fA/v1", funcSpec: &types.FunctionSpecification{FuncKey: "t1/fA/v1"},
		instances: map[string]*LiteInstance{}, sessions: map[string]*sessionBinding{}, dispatcher: &concurrencyDispatcher{}}
	ls.pools["t1/fA/v1"] = pool
	var wg sync.WaitGroup
	// N goroutines: handleAcquire (triggers cold-start, currentInUse reads map under RLock)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: "sess1", TenantID: "t1", TraceID: "tr"}
			ls.handleAcquire(req, time.Now()) // cold-start, no instances, times out
		}()
	}
	// M goroutines: handleInstanceUpdate (writes pool.instances under pool.Lock)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			ins := &types.Instance{InstanceID: fmt.Sprintf("ins%d", idx), FuncKey: "t1/fA/v1", ConcurrentNum: 2}
			ins.InstanceStatus.Code = int32(constant.KernelInstanceStatusRunning)
			ls.handleInstanceUpdate(pool, ins)
		}(i)
	}
	wg.Wait()
	// Under -race: no "concurrent map read/write" fatal, no data race reported.
}
