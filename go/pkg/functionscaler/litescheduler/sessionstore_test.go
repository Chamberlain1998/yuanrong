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
	"sync/atomic"
	"testing"
	"time"

	"github.com/smartystreets/goconvey/convey"
	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/functionscaler/session"
)

// mockSessionStore is an in-memory session.Store for testing. It records every
// Save/Get/Delete so tests can assert the external-store lifecycle without Redis
// or DataSystem.
type mockSessionStore struct {
	mu      sync.Mutex
	saves   map[string]session.StoreRecord
	gets    int
	deletes []string
}

func newMockSessionStore() *mockSessionStore {
	return &mockSessionStore{saves: make(map[string]session.StoreRecord)}
}

func (m *mockSessionStore) Save(key string, record session.StoreRecord) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.saves[key] = record
	return nil
}

func (m *mockSessionStore) Get(key string) (*session.StoreRecord, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.gets++
	if r, ok := m.saves[key]; ok {
		return &r, nil
	}
	return nil, nil
}

func (m *mockSessionStore) Delete(key string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.deletes = append(m.deletes, key)
	delete(m.saves, key)
	return nil
}

func (m *mockSessionStore) Backend() string { return "mock" }

func (m *mockSessionStore) deleteCount(key string) int {
	m.mu.Lock()
	defer m.mu.Unlock()
	n := 0
	for _, k := range m.deletes {
		if k == key {
			n++
		}
	}
	return n
}

// newTestLiteSessionStore builds a liteSessionStore wired to a caller-supplied
// store (bypassing newLiteSessionStore, which reads global config and would
// fail-open to a NoopStore in tests when New() fails).
func newTestLiteSessionStore(store session.Store) *liteSessionStore {
	return &liteSessionStore{coord: session.NewCoordinator(store)}
}

// poolWithMockStore returns a test pool whose sessionStore is a mock, so acquire
// lazy-recovery and Save/Delete lifecycle can be asserted.
func poolWithMockStore(t *testing.T) (*LiteFunctionPool, *mockSessionStore) {
	t.Helper()
	pool := newTestPool(t)
	mock := newMockSessionStore()
	pool.sessionStore = newTestLiteSessionStore(mock)
	return pool, mock
}

// waitForDelete polls mock.deleteCount until key appears or timeout elapses.
// Used where the idle-unbind timer fires asynchronously.
func waitForDelete(mock *mockSessionStore, key string, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if mock.deleteCount(key) > 0 {
			return true
		}
		time.Sleep(time.Millisecond)
	}
	return mock.deleteCount(key) > 0
}

func TestAcquireLazyRecoverFromStore(t *testing.T) {
	convey.Convey("acquire recovers binding from external store on local miss", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		pool, mock := poolWithMockStore(t)
		ls.pools["t1/fA/v1"] = pool
		// Simulate a scheduler crash: pool.sessions is empty but the external store
		// still holds the pre-crash binding for sess1 -> ins1.
		mock.saves["sess1"] = session.StoreRecord{InstanceID: "ins1", SessionID: "sess1", SessionTTL: 30}

		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: "sess1",
			SessionTTL: 30, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())

		convey.So(resp.ErrorCode, convey.ShouldEqual, constant.InsReqSuccessCode)
		convey.So(resp.InstanceID, convey.ShouldEqual, "ins1") // recovered, not redispatched
		pool.RLock()
		b, ok := pool.sessions["sess1"]
		pool.RUnlock()
		convey.So(ok, convey.ShouldBeTrue)
		convey.So(b.instanceID, convey.ShouldEqual, "ins1")
		convey.So(mock.gets, convey.ShouldEqual, 1) // singleflight: one Get despite miss
	})
}

func TestAcquireStoreDesignateInstanceGoneFallToDispatch(t *testing.T) {
	convey.Convey("store designate instance no longer in pool -> fail-open dispatch", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		pool, mock := poolWithMockStore(t)
		ls.pools["t1/fA/v1"] = pool
		// Store points at an instance that was removed after the crash.
		mock.saves["sess1"] = session.StoreRecord{InstanceID: "gone", SessionID: "sess1", SessionTTL: 30}

		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: "sess1",
			SessionTTL: 30, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())

		convey.So(resp.ErrorCode, convey.ShouldEqual, constant.InsReqSuccessCode)
		// Dispatched to one of the live instances (ins1/ins2), not "gone".
		convey.So(resp.InstanceID, convey.ShouldNotBeEmpty)
		convey.So(resp.InstanceID, convey.ShouldNotEqual, "gone")
	})
}

func TestAcquireStoreDesignateSessionCtxMismatch(t *testing.T) {
	convey.Convey("store designate instance sessionCtx mismatch -> delete stale record and redispatch", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		pool, mock := poolWithMockStore(t)
		ls.pools["t1/fA/v1"] = pool
		// Override test pool instances to carry SessionCtxID: ins1 has ctx-A,
		// ins2 has ctx-B. The store record points at ins1 but the request carries
		// ctx-B, so ins1 is a stale recovery target.
		pool.Lock()
		pool.instances["ins1"].SessionCtxID = "ctx-A"
		pool.instances["ins2"].SessionCtxID = "ctx-B"
		pool.Unlock()
		bindingKey := sessionBindingKey("sess1", "ctx-B")
		mock.saves[bindingKey] = session.StoreRecord{
			InstanceID: "ins1", SessionID: "sess1", SessionCtxID: "ctx-B", SessionTTL: 30,
		}

		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: "sess1",
			SessionCtxID: "ctx-B", SessionTTL: 30, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())

		convey.So(resp.ErrorCode, convey.ShouldEqual, constant.InsReqSuccessCode)
		// Must dispatch to ins2 (matching ctx-B), not ins1 (stale, ctx-A).
		convey.So(resp.InstanceID, convey.ShouldEqual, "ins2")
		// Stale store record must be deleted; the new binding to ins2 overwrites
		// it via Save after the Delete.
		convey.So(waitForDelete(mock, bindingKey, 2*time.Second), convey.ShouldBeTrue)
		pool.sessionStore.drainAsyncQueue(time.Second)
		mock.mu.Lock()
		rec, _ := mock.saves[bindingKey]
		mock.mu.Unlock()
		convey.So(rec.InstanceID, convey.ShouldEqual, "ins2")
	})
}

func TestAcquireStoreMissFailOpenDispatch(t *testing.T) {
	convey.Convey("store miss fail-opens to normal dispatch", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		pool, _ := poolWithMockStore(t)
		ls.pools["t1/fA/v1"] = pool
		// store is empty -> miss

		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: "sess1",
			SessionTTL: 30, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())

		convey.So(resp.ErrorCode, convey.ShouldEqual, constant.InsReqSuccessCode)
		convey.So(resp.InstanceID, convey.ShouldNotBeEmpty)
	})
}

func TestAcquireSavesBindingToStore(t *testing.T) {
	convey.Convey("acquire persists binding to external store", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		pool, mock := poolWithMockStore(t)
		ls.pools["t1/fA/v1"] = pool

		req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: "sess1",
			SessionTTL: 30, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(req, time.Now())
		convey.So(resp.ErrorCode, convey.ShouldEqual, constant.InsReqSuccessCode)

		pool.sessionStore.drainAsyncQueue(time.Second)
		mock.mu.Lock()
		rec, saved := mock.saves["sess1"]
		mock.mu.Unlock()
		convey.So(saved, convey.ShouldBeTrue)
		convey.So(rec.InstanceID, convey.ShouldEqual, resp.InstanceID)
		convey.So(rec.SessionTTL, convey.ShouldEqual, 30)
	})
}

func TestReleaseIdleUnbindDeletesExternalRecord(t *testing.T) {
	convey.Convey("idle-unbind timer fires and deletes external record", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		pool, mock := poolWithMockStore(t)
		ls.pools["t1/fA/v1"] = pool

		// SessionTTL=0 => idle-unbind timer fires immediately after release.
		acqReq := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: "sess1",
			SessionTTL: 0, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(acqReq, time.Now())
		convey.So(resp.ErrorCode, convey.ShouldEqual, constant.InsReqSuccessCode)

		relReq := &LiteRequest{Op: "release", AllocationIDs: []string{resp.ThreadID}, TraceID: "tr"}
		ls.handleRelease(relReq, time.Now())

		// Timer fires async -> removeSessionBinding -> async Delete. Poll for it.
		convey.So(waitForDelete(mock, "sess1", 2*time.Second), convey.ShouldBeTrue)
		pool.RLock()
		_, stillBound := pool.sessions["sess1"]
		pool.RUnlock()
		convey.So(stillBound, convey.ShouldBeFalse) // local binding also removed
	})
}

func TestDeletePoolCleansExternalRecords(t *testing.T) {
	convey.Convey("deletePool synchronously deletes all external session records", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		pool, mock := poolWithMockStore(t)
		ls.pools["t1/fA/v1"] = pool

		for _, sid := range []string{"sess1", "sess2"} {
			req := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: sid,
				SessionTTL: 30, TenantID: "t1", TraceID: "tr"}
			convey.So(ls.handleAcquire(req, time.Now()).ErrorCode,
				convey.ShouldEqual, constant.InsReqSuccessCode)
		}
		pool.sessionStore.drainAsyncQueue(time.Second)

		ls.deletePool("t1/fA/v1")

		// cleanExternalRecords is synchronous: deletes must be observed immediately.
		convey.So(mock.deleteCount("sess1"), convey.ShouldBeGreaterThanOrEqualTo, 1)
		convey.So(mock.deleteCount("sess2"), convey.ShouldBeGreaterThanOrEqualTo, 1)
		_, exists := ls.pools["t1/fA/v1"]
		convey.So(exists, convey.ShouldBeFalse)
	})
}

func TestStickyInvalidatedDeletesStaleExternalRecord(t *testing.T) {
	convey.Convey("sticky binding invalidated by instance removal deletes stale record", t, func() {
		ls := &LiteScheduler{pools: map[string]*LiteFunctionPool{}, allocations: map[string]*Allocation{}}
		pool, mock := poolWithMockStore(t)
		ls.pools["t1/fA/v1"] = pool
		// Make ins1 the deterministic first choice. Both test instances otherwise
		// have the same concurrency score, so map iteration can select either one.
		pool.instances["ins2"].InUse = 1

		// Bind sess1 to ins1.
		acq := &LiteRequest{Op: "acquire", FuncKey: "t1/fA/v1", SessionID: "sess1",
			SessionTTL: 30, TenantID: "t1", TraceID: "tr"}
		resp := ls.handleAcquire(acq, time.Now())
		convey.So(resp.InstanceID, convey.ShouldEqual, "ins1")
		pool.sessionStore.drainAsyncQueue(time.Second)

		// Remove ins1 -> its session binding becomes stale.
		pool.Lock()
		delete(pool.instances, "ins1")
		pool.Unlock()

		// Re-acquire: Phase 1 sees stale binding -> removeSessionBinding (async Delete)
		// -> Phase 2 store still has the pre-crash record -> Phase 3 designate ins1
		// absent -> dispatch to ins2.
		resp2 := ls.handleAcquire(acq, time.Now())
		convey.So(resp2.ErrorCode, convey.ShouldEqual, constant.InsReqSuccessCode)
		convey.So(resp2.InstanceID, convey.ShouldEqual, "ins2")
		// Stale external record for sess1 must eventually be deleted.
		convey.So(waitForDelete(mock, "sess1", 2*time.Second), convey.ShouldBeTrue)
	})
}

func TestStoreCallGroupSingleflight(t *testing.T) {
	convey.Convey("concurrent same-key Do calls execute fn once and share result", t, func() {
		g := session.NewStoreCallGroup()
		var execCount int32
		var wg sync.WaitGroup
		key := "sess1"
		results := make([]*session.StoreRecord, 8)
		var errCount int32
		for i := 0; i < 8; i++ {
			wg.Add(1)
			go func(i int) {
				defer wg.Done()
				rec, err := g.Do(key, func() (*session.StoreRecord, error) {
					atomic.AddInt32(&execCount, 1)
					time.Sleep(10 * time.Millisecond) // widen the dedup window
					return &session.StoreRecord{InstanceID: "ins1"}, nil
				})
				results[i] = rec
				if err != nil {
					atomic.AddInt32(&errCount, 1)
				}
			}(i)
		}
		wg.Wait()
		convey.So(atomic.LoadInt32(&errCount), convey.ShouldEqual, 0)
		convey.So(atomic.LoadInt32(&execCount), convey.ShouldEqual, 1)
		for i := 0; i < 8; i++ {
			convey.So(results[i], convey.ShouldNotBeNil)
			convey.So(results[i].InstanceID, convey.ShouldEqual, "ins1")
		}
	})
}

func TestNilSessionStoreAllOpsNoop(t *testing.T) {
	convey.Convey("nil sessionStore does not panic and all ops are no-op", t, func() {
		pool := newTestPool(t)
		// pool.sessionStore is nil (default newTestPool)
		convey.So(func() { pool.sessionStore.saveSessionToStore("s", "s", "", "i", 1) }, convey.ShouldNotPanic)
		convey.So(func() { pool.sessionStore.deleteSessionFromStore("s") }, convey.ShouldNotPanic)
		rec, err := pool.sessionStore.getSessionFromStore("s")
		convey.So(rec, convey.ShouldBeNil)
		convey.So(err, convey.ShouldBeNil)
		convey.So(func() { pool.sessionStore.stop() }, convey.ShouldNotPanic)
		convey.So(func() { pool.sessionStore.cleanExternalRecords([]string{"s"}) }, convey.ShouldNotPanic)
	})
}
