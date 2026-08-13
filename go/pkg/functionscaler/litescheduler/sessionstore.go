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
	"os"
	"time"

	"yuanrong.org/kernel/pkg/functionscaler/session"
)

// liteSessionStore is litescheduler's thin wrapper over session.Coordinator.
//
// The Coordinator (in the session package) owns all store-coordination machinery:
// bounded async write queue + single worker, singleflight read, sync destroy
// cleanup, and the store factory. This wrapper only adds:
//   - the binding→StoreRecord mapping specific to litescheduler's sessionBinding
//     (just instanceID + sessionTTL; no concurrency-scheduler thread-level fields);
//   - nil-safety so a pool constructed without a store (e.g. tests) needs no nil
//     checks at the dozens of call sites in operation.go/event.go.
//
// All real work delegates to the Coordinator.
//
// funcCacheKey 统一传 funcSpec.FuncKey，与 concurrencyscheduler 共用同一物理 key
// 格式（faasscheduler:session:<funcKeyHash>:<clusterHash>:<sessionKeyHash>），不再用
// "lite" 做 instanceType 隔离——该维度与路由层重复，去掉后两 scheduler 对同一函数的
// session 记录可互相懒恢复。
type liteSessionStore struct {
	coord       *session.Coordinator
	schedulerID string
}

// newLiteSessionStore builds a store + Coordinator for one pool. store falls
// back to NoopStore only when New() fails (init failure fail-open); backend
// 合法性由 config 加载期校验兜底，正常路径不会为空。
// schedulerID 在创建时缓存 POD_IP，避免每次 Save 都调 os.Getenv（内部有锁）。
func newLiteSessionStore(funcKey string) *liteSessionStore {
	return &liteSessionStore{
		coord:       session.NewCoordinator(session.MakeStore(funcKey)),
		schedulerID: os.Getenv("POD_IP"),
	}
}

// saveSessionToStore maps litescheduler's binding fields to a StoreRecord and
// enqueues an async Save. sessionKey is the logical cache key (used as the
// external store key); sessionID/sessionCtxID are the raw values stored in the
// record for diagnostics. sessionTTL is the business session TTL (seconds)
// recorded for idle-unbind; it is restored on lazy recovery so the recovered
// binding reuses the original TTL rather than the new request's TTL.
// nil-safe: no-op when the pool has no sessionStore (e.g. tests).
func (s *liteSessionStore) saveSessionToStore(sessionKey, instanceID string, req *LiteRequest) {
	if s == nil {
		return
	}
	s.coord.SaveRecord(sessionKey, session.StoreRecord{
		InstanceID:   instanceID,
		SchedulerID:  s.schedulerID,
		SessionID:    req.SessionID,
		SessionCtxID: req.SessionCtxID,
		SessionTTL:   req.SessionTTL,
		Concurrency:  req.Concurrency,
	})
}

// deleteSessionFromStore enqueues an async Delete. nil-safe.
func (s *liteSessionStore) deleteSessionFromStore(sessionID string) {
	if s == nil {
		return
	}
	s.coord.DeleteRecord(sessionID)
}

// getSessionFromStore queries the external store on local miss (singleflight
// deduped). Returns (nil,nil) on miss; (nil,err) on storage error (caller
// fail-opens as a fresh session). Must NOT be called under pool.Lock. nil-safe.
func (s *liteSessionStore) getSessionFromStore(sessionID string) (*session.StoreRecord, error) {
	if s == nil {
		return nil, nil
	}
	return s.coord.GetRecord(sessionID)
}

// cleanExternalRecords synchronously deletes a list of session records. Used on
// pool destroy (deletePool) AFTER stop so no in-flight or queued ops remain to
// race the sync cleanup. nil-safe.
func (s *liteSessionStore) cleanExternalRecords(sessionIDs []string) {
	if s == nil {
		return
	}
	s.coord.CleanRecords(sessionIDs)
}

// stop synchronously shuts down the async worker: cancels ctx, drains queued
// ops, and blocks until the worker goroutine has fully exited. Called on pool
// destroy before cleanExternalRecords so no in-flight Save can outlive stop
// and re-write a record that cleanExternalRecords just deleted. nil-safe.
func (s *liteSessionStore) stop() {
	if s == nil {
		return
	}
	s.coord.Stop()
}

// drainAsyncQueue blocks until the async queue is empty or timeout. Test-only.
// nil-safe.
func (s *liteSessionStore) drainAsyncQueue(timeout time.Duration) {
	if s == nil {
		return
	}
	s.coord.Drain(timeout)
}
