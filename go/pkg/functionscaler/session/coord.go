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
type asyncStoreQueue struct {
	store  Store
	queue  chan asyncStoreOp
	ctx    context.Context
	cancel func()
	once   sync.Once
}

func newAsyncStoreQueue(store Store) *asyncStoreQueue {
	ctx, cancel := context.WithCancel(context.Background())
	return &asyncStoreQueue{
		store:  store,
		queue:  make(chan asyncStoreOp, asyncQueueSize),
		ctx:    ctx,
		cancel: cancel,
	}
}

// enqueue pushes an op to the bounded queue; on full it drops + emits a metric
// (fail-open). The worker is started lazily on first enqueue via Once.
//
// Stop 后不再 enqueue：worker 已退出，op 入 channel 也无消费者，会静默堆积无人处理。
// 此处通过非阻塞 ctx 检查直接返回，让调用方对 Stop 后的 Save/Delete 不抱期望（fail-open
// 默默丢弃是预期行为，不计 metric——区别于队列满的丢弃路径）。
func (q *asyncStoreQueue) enqueue(op asyncStoreOp) {
	select {
	case <-q.ctx.Done():
		return
	default:
	}
	q.once.Do(func() { go q.loop() })
	select {
	case q.queue <- op:
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
// here, never under any caller's session lock. Exits when ctx is cancelled.
func (q *asyncStoreQueue) loop() {
	for {
		select {
		case <-q.ctx.Done():
			return
		case op := <-q.queue:
			q.process(op)
		}
	}
}

// process executes one store op and records latency/result metrics.
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
}

// drain blocks until the queue is empty or timeout elapses. Test-only: lets
// assertions deterministically observe save/delete counts after enqueueing.
func (q *asyncStoreQueue) drain(timeout time.Duration) {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if len(q.queue) == 0 {
			return
		}
		time.Sleep(time.Millisecond)
	}
}

func (q *asyncStoreQueue) stop() { q.cancel() }

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
// Stop so no ops remain in the async queue. Sync (not async) because the worker
// is already stopped and would drop enqueued ops; destroy is a cold path that
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

// Stop cancels the async worker. Call on destroy before CleanRecords so in-flight
// ops cannot race the sync cleanup. nil-safe.
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
