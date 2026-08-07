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

import "sync"

// StoreCallGroup is inline singleflight: concurrent Do calls for the same key
// execute fn once and share the result. Equivalent to golang.org/x/sync/
// singleflight.Group but avoids introducing a new dependency.
//
// Used by Coordinator.GetRecord to dedupe concurrent same-session store lookups
// so the external store is hit at most once per in-flight key. Lifted here from
// the former per-scheduler copies in concurrencyscheduler.sessionManager and
// litescheduler.liteSessionStore, which were byte-identical.
type StoreCallGroup struct {
	mu    sync.Mutex
	calls map[string]*storeCall
}

// storeCall is one in-flight singleflight call: done closes when val/err are set.
type storeCall struct {
	done chan struct{}
	val  *StoreRecord
	err  error
}

// NewStoreCallGroup returns a fresh StoreCallGroup.
func NewStoreCallGroup() *StoreCallGroup {
	return &StoreCallGroup{calls: make(map[string]*storeCall)}
}

// Do executes fn for key; if another Do for the same key is already in flight,
// it blocks and reuses that call's result. fn runs outside StoreCallGroup's lock.
func (g *StoreCallGroup) Do(key string, fn func() (*StoreRecord, error)) (*StoreRecord, error) {
	g.mu.Lock()
	if c, ok := g.calls[key]; ok {
		g.mu.Unlock()
		<-c.done
		return c.val, c.err
	}
	c := &storeCall{done: make(chan struct{})}
	g.calls[key] = c
	g.mu.Unlock()

	c.val, c.err = fn()
	close(c.done)

	g.mu.Lock()
	delete(g.calls, key)
	g.mu.Unlock()
	return c.val, c.err
}
