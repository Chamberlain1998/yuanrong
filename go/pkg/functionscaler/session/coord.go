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
	"context"
	"fmt"
	"sync"
	"sync/atomic"
	"time"

	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	"yuanrong.org/kernel/pkg/functionscaler/metrics"
)

// asyncQueueSize bounds the per-Coordinator async store op queue. When full, ops
// are dropped with a metric (fail-open): the local session map remains the
// runtime source of truth; the external store only backs crash recovery.
const asyncQueueSize = 1024

// asyncStoreOp is one enqueued store operation. Save carries a StoreRecord
// snapshot (value copy so later mutations to the caller's binding do not race
// with the worker); Delete only needs the key.
type asyncStoreOp struct {
	isDelete bool
	key      string
	record   StoreRecord
}

// asyncStoreQueue is a bounded async write queue drained by a single worker
// goroutine. It is the shared infrastructure behind Coordinator and is not used
// directly by schedulers. Lifted from the former per-scheduler copies which were
// byte-identical (concurrencyscheduler.sessionManager / litescheduler.liteSessionStore).
//
// Shutdown contract (see stop): cancel ctx → worker drains remaining ops →
// worker exits → stop returns. The caller may then sync-CleanRecords with the
// guarantee no in-flight or queued Save can outlive stop and re-write a
// record that was just cleaned.
//
// Orphan-record paths (both fail-open, reclaimed by 24h TTL =
// defaultRecoveryWindowSeconds in sessionstore.go):
//   - queue full: enqueue drops op via the default branch (metric + Warn);
//     a DEL dropped here leaves the external record alive even though the
//     caller already deleted the sessionMap entry. CleanRecords only iterates
//     sessionMap keys, so it cannot clean these drops.
//   - post-stop enqueue: enqueue rejects silently via the ctx check; a
//     Save/Delete issued after Stop but before CleanRecords will not reach the
//     store. Save dropping here is benign (record already stale); a Delete
//     drop leaves an orphan record — same TTL fallback.
//
// Both paths are by-design fail-open: the local sessionMap is the runtime
// source of truth, the external store only backs crash recovery; an orphan
// external record expires after its physical TTL and is never observed by
// future schedulers (their Save overwrites it on the next binding).
type asyncStoreQueue struct {
	store   Store
	queue   chan asyncStoreOp
	ctx     context.Context
	cancel  func()
	once    sync.Once
	done    chan struct{} // closed when the worker goroutine has fully exited
	started atomic.Bool   // true once the worker goroutine has been launched
	// mu serializes enqueue's ctx-check+send against stop's cancel. Without it
	// an enqueue that passed the ctx check before stop's cancel can land an op
	// in the queue after the worker has exited — orphan op (no consumer,
	// enqueued bumped, processed never catches up). Atomics can't bridge a load
	// and a channel send; only a lock can. Worker never takes mu.
	mu sync.Mutex
	// enqueued counts ops successfully pushed to the queue (drops on full do
	// not count); processed counts ops the worker has fully completed (Save or
	// Delete returned), including the drainRemaining path at shutdown. drain
	// waits for processed to catch up to a snapshot of enqueued — see drain.
	enqueued  atomic.Int64
	processed atomic.Int64
}

func newAsyncStoreQueue(store Store) *asyncStoreQueue {
	ctx, cancel := context.WithCancel(context.Background())
	return &asyncStoreQueue{
		store:  store,
		queue:  make(chan asyncStoreOp, asyncQueueSize),
		ctx:    ctx,
		cancel: cancel,
		done:   make(chan struct{}),
	}
}

// enqueue pushes an op to the bounded queue; on full it drops + emits a metric
// (fail-open). The worker is started lazily on first enqueue via Once.
//
// Stop 后不再 enqueue：mu 内检查 ctx，已 cancel 则直接返回（fail-open 默默丢弃是预期
// 行为，不计 metric——区别于队列满的丢弃路径）。mu 让 check+send 与 stop 的 cancel
// 原子化，闭合原先 "check 通过 → stop 排空+退出 → enqueue send 落入无人消费队列"
// 的 TOCTOU。once.Do 在 mu 外（其内部已同步，不必持锁启动 goroutine）。
func (q *asyncStoreQueue) enqueue(op asyncStoreOp) {
	q.once.Do(func() {
		q.started.Store(true)
		go q.loop()
	})
	q.mu.Lock()
	defer q.mu.Unlock()
	select {
	case <-q.ctx.Done():
		return
	default:
	}
	select {
	case q.queue <- op:
		q.enqueued.Add(1)
	default:
		opType := "save"
		if op.isDelete {
			opType = "delete"
		}
		metrics.OnSessionStoreOpDropped(opType)
		log.GetLogger().Warnf("session store async queue full, dropping %s, key=%s", opType, op.key)
	}
}

// loop serially consumes the queue executing Save/Delete. Storage I/O happens
// here, never under any caller's session lock. On ctx cancel it drains any
// ops already queued at shutdown time (so a pending Save cannot survive the
// subsequent sync CleanRecords and re-write a record that was just cleaned),
// then exits. New post-cancel enqueues are rejected by enqueue's ctx check.
func (q *asyncStoreQueue) loop() {
	defer close(q.done)
	for {
		select {
		case <-q.ctx.Done():
			q.drainRemaining()
			return
		case op := <-q.queue:
			q.process(op)
		}
	}
}

// drainRemaining processes every op still buffered in the queue at shutdown.
// Non-blocking: each receive is guarded by default so the loop exits once the
// queue empties. New enqueues are already rejected by enqueue's ctx check, so
// this terminates deterministically.
func (q *asyncStoreQueue) drainRemaining() {
	for {
		select {
		case op := <-q.queue:
			q.process(op)
		default:
			return
		}
	}
}

// process executes one store op and records latency/result metrics. The
// processed counter is bumped AFTER Save/Delete returns so drain can wait for
// full completion (not just dequeue) — that is what closes the race where
// len(queue)==0 but the worker is still mid-Save/Delete.
func (q *asyncStoreQueue) process(op asyncStoreOp) {
	opType := "save"
	if op.isDelete {
		opType = "delete"
	}
	start := time.Now()
	var err error
	if op.isDelete {
		err = q.store.Delete(op.key)
	} else {
		err = q.store.Save(op.key, op.record)
	}
	metrics.OnSessionStoreOp(opType, time.Since(start), err)
	if err != nil {
		log.GetLogger().Warnf("session store %s failed, key=%s, err=%s", opType, op.key, err.Error())
	}
	q.processed.Add(1)
}

// drain blocks until every op enqueued so far has been fully processed by the
// worker, or timeout elapses. Test-only: lets assertions deterministically
// observe save/delete counts after enqueueing.
//
// Wait condition is processed >= snapshot(enqueued), NOT len(queue)==0. The
// latter has a race: the worker can have dequeued the last op (so len==0)
// while still mid-process() — an immediate post-drain assertion then sees
// counts one short (the flake in sessionmanager_test / sessionstore_test).
// processed is bumped only after Save/Delete returns, so drain returning
// guarantees every enqueued op has reached the store. Drops (queue full) do
// not increment enqueued, so drain never waits for an op that won't be
// processed. New enqueues during drain are out of contract (test calls are
// sequential: enqueue, then drain, then assert).
func (q *asyncStoreQueue) drain(timeout time.Duration) {
	target := q.enqueued.Load()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if q.processed.Load() >= target {
			return
		}
		time.Sleep(time.Millisecond)
	}
}

// stop cancels the ctx under mu (so enqueue's check+send cannot straddle the
// cancel — closes the enqueue↔stop TOCTOU), then blocks until the worker has
// drained remaining queued ops and fully exited. Drain-then-wait guarantees
// no in-flight or queued Save can outlive stop and race the caller's
// subsequent sync CleanRecords (which would otherwise delete a key, then have
// a late Save re-write the record — leaving a stale binding alive up to the
// recovery window). Safe when the worker was never launched (no enqueue ever
// fired): started is false, so no wait happens.
func (q *asyncStoreQueue) stop() {
	q.mu.Lock()
	q.cancel()
	q.mu.Unlock()
	if q.started.Load() {
		<-q.done
	}
}

// Coordinator coordinates the external session store for one scheduler/pool:
// async Save/Delete (bounded queue + single worker), singleflight Get, and sync
// cleanup on destroy. It is the shared store-coordination layer used by both
// concurrencyscheduler.sessionManager and litescheduler, eliminating the
// duplicated queue/singleflight/factory code that previously lived in each.
// Callers only keep their binding→StoreRecord mapping logic.
//
// nil-safe: every method is a no-op when the receiver is nil, so a pool that
// did not construct a Coordinator (e.g. tests) needs no nil checks at call sites.
type Coordinator struct {
	store Store
	queue *asyncStoreQueue
	sf    *StoreCallGroup
}

// NewCoordinator wraps store with async-write + singleflight-read coordination.
// store may be a NoopStore (init failure fail-open) in which case all ops are
// cheap no-ops.
func NewCoordinator(store Store) *Coordinator {
	return &Coordinator{
		store: store,
		queue: newAsyncStoreQueue(store),
		sf:    NewStoreCallGroup(),
	}
}

// SaveRecord enqueues an async Save. nil-safe.
func (c *Coordinator) SaveRecord(key string, record StoreRecord) {
	if c == nil || key == "" {
		return
	}
	c.queue.enqueue(asyncStoreOp{key: key, record: record})
}

// DeleteRecord enqueues an async Delete. nil-safe.
func (c *Coordinator) DeleteRecord(key string) {
	if c == nil || key == "" {
		return
	}
	c.queue.enqueue(asyncStoreOp{isDelete: true, key: key})
}

// GetRecord queries the store on local miss, deduped by singleflight so
// concurrent same-key lookups hit the store at most once. Returns (nil,nil) on
// miss; (nil,err) on storage error (caller fail-opens as a fresh session).
// Must NOT be called under the caller's session lock (storage I/O). nil-safe:
// returns (nil,nil).
func (c *Coordinator) GetRecord(key string) (*StoreRecord, error) {
	if c == nil {
		return nil, nil
	}
	if key == "" {
		return nil, fmt.Errorf("key is empty")
	}
	return c.sf.Do(key, func() (*StoreRecord, error) {
		start := time.Now()
		rec, err := c.store.Get(key)
		metrics.OnSessionStoreOp("get", time.Since(start), err)
		return rec, err
	})
}

// CleanRecords synchronously deletes a list of records. Used on destroy AFTER
// Stop so no in-flight or queued ops remain to race the sync cleanup. Sync (not
// async) because Stop has already drained+stopped the worker, which would
// otherwise reject any post-stop enqueue; destroy is a cold path that
// tolerates blocking I/O. nil-safe.
func (c *Coordinator) CleanRecords(keys []string) {
	if c == nil {
		return
	}
	if len(keys) == 0 {
		return
	}
	for _, k := range keys {
		if err := c.store.Delete(k); err != nil {
			log.GetLogger().Warnf("clean external session record failed, key=%s, err=%s", k, err.Error())
		}
	}
	log.GetLogger().Infof("cleaned %d external session records on destroy", len(keys))
}

// Stop synchronously shuts the async worker down: cancels the ctx, drains any
// ops already queued, and blocks until the worker goroutine has fully exited.
// Call on destroy BEFORE CleanRecords so in-flight/queued Saves cannot
// outlive Stop and re-write a record that CleanRecords just deleted.
// nil-safe.
func (c *Coordinator) Stop() {
	if c == nil {
		return
	}
	c.queue.stop()
}

// Drain blocks until the async queue is empty or timeout elapses. Test-only.
// nil-safe.
func (c *Coordinator) Drain(timeout time.Duration) {
	if c == nil {
		return
	}
	c.queue.drain(timeout)
}

// Backend returns the underlying store backend (for logging/metrics). nil-safe:
// returns BackendNoop when the receiver is nil.
func (c *Coordinator) Backend() string {
	if c == nil {
		return BackendNoop
	}
	return c.store.Backend()
}
