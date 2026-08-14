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

package session

import (
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
)

// blockingSaveStore is a Store whose Save blocks on a release channel until the
// test releases it. Used to deterministically capture an in-flight Save at
// Stop time so we can assert Stop waits for it (not just cancel ctx).
type blockingSaveStore struct {
	mu      sync.Mutex
	saves   map[string]StoreRecord
	release chan struct{}
	started chan struct{} // signals a Save is in-flight (buffered so non-blocking send)
	savedCh chan string   // signals each completed Save (key)
}

func newBlockingSaveStore() *blockingSaveStore {
	return &blockingSaveStore{
		saves:   make(map[string]StoreRecord),
		release: make(chan struct{}),
		started: make(chan struct{}, 64),
		savedCh: make(chan string, 64),
	}
}

func (b *blockingSaveStore) Save(key string, record StoreRecord) error {
	select {
	case b.started <- struct{}{}:
	default:
	}
	<-b.release
	b.mu.Lock()
	b.saves[key] = record
	b.mu.Unlock()
	select {
	case b.savedCh <- key:
	default:
	}
	return nil
}

func (b *blockingSaveStore) Get(key string) (*StoreRecord, error) { return nil, nil }

func (b *blockingSaveStore) Delete(key string) error {
	b.mu.Lock()
	delete(b.saves, key)
	b.mu.Unlock()
	return nil
}

func (b *blockingSaveStore) Backend() string { return "blocking-save-test" }

// TestStopSafeWhenWorkerNeverStarted asserts Stop returns immediately when no
// enqueue ever fired (worker was never launched). Guards against a hang on
// the done channel.
func TestStopSafeWhenWorkerNeverStarted(t *testing.T) {
	c := NewCoordinator(newBlockingSaveStore())
	done := make(chan struct{})
	go func() { c.Stop(); close(done) }()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("Stop hung when worker was never started")
	}
}

// TestStopDrainsQueuedOps asserts Stop drains every op already in the queue
// before returning (no drops on shutdown). Without drain, a pending Save
// could outlive Stop and re-write a record that the caller's sync CleanRecords
// just deleted — the exact race the fix targets.
func TestStopDrainsQueuedOps(t *testing.T) {
	store := newBlockingSaveStore()
	c := NewCoordinator(store)
	// Enqueue several Saves. The worker's first Save blocks on release, so
	// subsequent ops queue up behind it. Distinct keys so the assertion can
	// distinguish all 5 completed Saves (the store is a map keyed by Save key;
	// a shared key would collapse to a single entry).
	for i := 0; i < 5; i++ {
		c.SaveRecord(fmt.Sprintf("k-%d", i), StoreRecord{InstanceID: "ins"})
	}
	// Wait for the worker to start processing the first op (it is now blocked
	// on release); #2-#5 are still queued behind it.
	<-store.started
	// Call Stop while #1 is in-flight and #2-#5 are queued. Stop must block
	// because the worker cannot exit until #1 completes.
	stopDone := make(chan struct{})
	go func() { c.Stop(); close(stopDone) }()
	select {
	case <-stopDone:
		t.Fatal("Stop returned before in-flight Save completed")
	case <-time.After(50 * time.Millisecond):
		// expected: Stop is blocked waiting for the worker to drain+exit
	}
	// Release the in-flight Save. The worker finishes #1, then either processes
	// #2-#5 normally or drains them via drainRemaining — either way all 5
	// must be processed before Stop returns.
	close(store.release)
	select {
	case <-stopDone:
	case <-time.After(3 * time.Second):
		t.Fatal("Stop did not return within 3s; queued ops not drained")
	}
	store.mu.Lock()
	got := len(store.saves)
	store.mu.Unlock()
	// All 5 enqueued Saves must have been recorded (drained, not dropped).
	assert.Equal(t, 5, got, "Stop must drain queued Saves, not drop them")
}

// TestStopWaitsForInFlightOp asserts Stop blocks while a Save is mid-flight
// and only returns after it completes. This is the core guarantee: an
// in-flight Save cannot outlive Stop and race the caller's sync CleanRecords.
func TestStopWaitsForInFlightOp(t *testing.T) {
	store := newBlockingSaveStore()
	c := NewCoordinator(store)
	c.SaveRecord("k", StoreRecord{InstanceID: "ins"})
	<-store.started // worker is now mid-Save, blocked on release
	// Stop must block while the in-flight Save is still running.
	stopDone := make(chan struct{})
	go func() { c.Stop(); close(stopDone) }()
	select {
	case <-stopDone:
		t.Fatal("Stop returned before in-flight Save completed")
	case <-time.After(50 * time.Millisecond):
		// expected: Stop is blocked waiting for the worker to drain+exit.
	}
	// Release the in-flight Save; Stop must now return promptly.
	close(store.release)
	select {
	case <-stopDone:
	case <-time.After(time.Second):
		t.Fatal("Stop did not return after in-flight Save completed")
	}
	select {
	case <-store.savedCh:
	default:
		t.Fatal("in-flight Save was not recorded before Stop returned")
	}
}

// TestStopRejectsPostStopEnqueue asserts enqueue is a no-op after Stop returns
// (ctx cancelled). Without this, a late enqueue could land an op in a queue
// with no consumer and silently pile up.
func TestStopRejectsPostStopEnqueue(t *testing.T) {
	store := newBlockingSaveStore()
	c := NewCoordinator(store)
	c.SaveRecord("k1", StoreRecord{InstanceID: "ins1"})
	<-store.started
	close(store.release)
	c.Stop()
	// After Stop, SaveRecord must be a dropped no-op (worker has exited).
	c.SaveRecord("k2", StoreRecord{InstanceID: "ins2"})
	store.mu.Lock()
	_, hasK2 := store.saves["k2"]
	store.mu.Unlock()
	assert.False(t, hasK2, "enqueue after Stop must be dropped, not processed")
}

// blockingDeleteStore mirrors blockingSaveStore for Delete ops: Delete blocks
// on a release channel until the test releases it.
type blockingDeleteStore struct {
	release chan struct{}
	started chan struct{}
}

func newBlockingDeleteStore() *blockingDeleteStore {
	return &blockingDeleteStore{
		release: make(chan struct{}),
		started: make(chan struct{}, 64),
	}
}

func (s *blockingDeleteStore) Save(key string, record StoreRecord) error { return nil }
func (s *blockingDeleteStore) Get(key string) (*StoreRecord, error)      { return nil, nil }

func (s *blockingDeleteStore) Delete(key string) error {
	select {
	case s.started <- struct{}{}:
	default:
	}
	<-s.release
	return nil
}

func (s *blockingDeleteStore) Backend() string { return "blocking-delete-test" }

// TestStopWaitsForInFlightDelete mirrors the Save case for Delete ops.
func TestStopWaitsForInFlightDelete(t *testing.T) {
	store := newBlockingDeleteStore()
	c := NewCoordinator(store)
	c.DeleteRecord("k")
	<-store.started
	stopDone := make(chan struct{})
	go func() { c.Stop(); close(stopDone) }()
	select {
	case <-stopDone:
		t.Fatal("Stop returned before in-flight Delete completed")
	case <-time.After(50 * time.Millisecond):
	}
	close(store.release)
	select {
	case <-stopDone:
	case <-time.After(time.Second):
		t.Fatal("Stop did not return after in-flight Delete completed")
	}
}

// slowCountingStore is a Store whose Save/Delete sleep briefly before recording
// the op. Used to deterministically expose the drain() race: the worker has
// dequeued the last op (so len(queue)==0) but is still mid-Save/Delete when an
// assertion runs. Without the counter-based drain, the assertion would see the
// count one short.
type slowCountingStore struct {
	mu        sync.Mutex
	saves     int
	deletes   int
	saveDelay time.Duration
	delDelay  time.Duration
}

func newSlowCountingStore(saveDelay, delDelay time.Duration) *slowCountingStore {
	return &slowCountingStore{saveDelay: saveDelay, delDelay: delDelay}
}

func (s *slowCountingStore) Save(key string, record StoreRecord) error {
	time.Sleep(s.saveDelay)
	s.mu.Lock()
	s.saves++
	s.mu.Unlock()
	return nil
}

func (s *slowCountingStore) Delete(key string) error {
	time.Sleep(s.delDelay)
	s.mu.Lock()
	s.deletes++
	s.mu.Unlock()
	return nil
}

func (s *slowCountingStore) Get(key string) (*StoreRecord, error) { return nil, nil }
func (s *slowCountingStore) Backend() string                      { return "slow-counting-test" }

func (s *slowCountingStore) saveCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.saves
}

func (s *slowCountingStore) deleteCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.deletes
}

// TestDrainWaitsForInFlightProcess reproduces the race the fix targets: with a
// slow store, the worker dequeues the last op (queue empty) but is still
// mid-Save when Drain returns. The post-drain assertion must observe the save
// count exactly. Run multiple iterations to amplify any residual race window.
func TestDrainWaitsForInFlightProcess(t *testing.T) {
	const iters = 50
	const opsPerIter = 8
	const drainTimeout = time.Second
	// Save delay must be long enough to span the drain poll interval (1ms) so
	// the race window is reliably hit on the buggy len(queue)==0 condition.
	const saveDelay = 5 * time.Millisecond

	store := newSlowCountingStore(saveDelay, 0)
	c := NewCoordinator(store)
	defer c.Stop()
	for i := 0; i < iters; i++ {
		for j := 0; j < opsPerIter; j++ {
			c.SaveRecord(fmt.Sprintf("k-%d-%d", i, j), StoreRecord{InstanceID: "ins"})
		}
		c.Drain(drainTimeout)
		// Drain must guarantee every enqueued Save has reached the store.
		want := (i + 1) * opsPerIter
		got := store.saveCount()
		assert.Equal(t, want, got, "iter %d: drain returned before in-flight Save completed", i)
	}
}

// TestDrainWaitsForInFlightDelete mirrors TestDrainWaitsForInFlightProcess for
// Delete ops.
func TestDrainWaitsForInFlightDelete(t *testing.T) {
	const iters = 50
	const opsPerIter = 8
	const drainTimeout = time.Second
	const delDelay = 5 * time.Millisecond

	store := newSlowCountingStore(0, delDelay)
	c := NewCoordinator(store)
	defer c.Stop()
	for i := 0; i < iters; i++ {
		for j := 0; j < opsPerIter; j++ {
			c.DeleteRecord(fmt.Sprintf("k-%d-%d", i, j))
		}
		c.Drain(drainTimeout)
		want := (i + 1) * opsPerIter
		got := store.deleteCount()
		assert.Equal(t, want, got, "iter %d: drain returned before in-flight Delete completed", i)
	}
}

// TestStopNoLeakUnderConcurrentEnqueue stresses the enqueue↔stop TOCTOU: many
// enqueues run concurrently with Stop. Without the mu fix, an enqueue that
// passed the ctx check before stop's cancel can land an op in the queue after
// the worker has exited — the op leaks in the buffered channel (no consumer,
// enqueued bumped, processed never catches up). With the fix, check+send is
// atomic w.r.t. cancel, so the queue channel is empty once Stop returns.
//
// Save delay is 0 so the worker exits within microseconds of cancel — that
// makes the TOCTOU window (between enqueue's outer ctx-check and its inner
// send) comparable to the worker-exit window, so the race is reliably hit
// across iters. A longer delay would slow the worker's exit and let enqueues
// finish BEFORE the worker leaves, hiding the race. The existing
// TestStopRejectsPostStopEnqueue is serial (Stop returns before the next
// SaveRecord) and cannot exercise this race.
func TestStopNoLeakUnderConcurrentEnqueue(t *testing.T) {
	const iters = 50
	const enqueueWorkers = 16
	const opsPerWorker = 500
	store := newSlowCountingStore(0, 0)
	for i := 0; i < iters; i++ {
		c := NewCoordinator(store)
		var enqWg sync.WaitGroup
		for w := 0; w < enqueueWorkers; w++ {
			enqWg.Add(1)
			go func(worker int) {
				defer enqWg.Done()
				for j := 0; j < opsPerWorker; j++ {
					c.SaveRecord(fmt.Sprintf("k-%d-%d-%d", i, worker, j), StoreRecord{InstanceID: "ins"})
				}
			}(w)
		}
		// Stop concurrently with the enqueue workers — peak TOCTOU exposure.
		stopDone := make(chan struct{})
		go func() { c.Stop(); close(stopDone) }()
		enqWg.Wait()
		select {
		case <-stopDone:
		case <-time.After(5 * time.Second):
			t.Fatalf("iter %d: Stop did not return within 5s", i)
		}
		// After Stop returns, the queue channel must be empty — no orphan ops
		// leaked by an enqueue that raced Stop across the old TOCTOU window.
		assert.Equal(t, 0, len(c.queue.queue),
			"iter %d: leaked ops in queue after Stop (enqueue↔stop TOCTOU)", i)
	}
}
