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

package concurrencyscheduler

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/assert"

	"yuanrong.org/kernel/pkg/common/faas_common/datasystemclient"
	commonTypes "yuanrong.org/kernel/pkg/common/faas_common/types"
	"yuanrong.org/kernel/pkg/functionscaler/rollout"
	"yuanrong.org/kernel/pkg/functionscaler/session"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

// fakeRedisStore is a session.Store stand-in whose Get always misses and whose
// Backend reports Redis, so getSessionFromStore falls through to the legacy
// bulk-package reader. save/delete are no-ops.
type fakeRedisStore struct{}

func (fakeRedisStore) Save(string, session.StoreRecord) error   { return nil }
func (fakeRedisStore) Get(string) (*session.StoreRecord, error) { return nil, nil }
func (fakeRedisStore) Delete(string) error                      { return nil }
func (fakeRedisStore) Backend() string                          { return session.BackendRedis }

// nonRedisBackendStore reports a DataSystem (non-Redis) backend to verify the
// legacy fallback applies uniformly regardless of primary backend.
type nonRedisBackendStore struct{}

func (nonRedisBackendStore) Save(string, session.StoreRecord) error   { return nil }
func (nonRedisBackendStore) Get(string) (*session.StoreRecord, error) { return nil, nil }
func (nonRedisBackendStore) Delete(string) error                      { return nil }
func (nonRedisBackendStore) Backend() string                          { return session.BackendDataSystem }

const testFuncKeyWithLabel = "sessioncache-myfunc-abcdef0123456789"

func newBulkTestSM(store session.Store) *sessionManager {
	return makeSessionManager(testFuncKeyWithLabel, "10.0.0.1",
		types.InstanceType("scaled"), store)
}

// withRollout toggles the global rollout canary flag around fn and restores it.
func withRollout(updating bool, fn func()) {
	rc := rollout.GetGlobalRolloutConfig()
	origUpdating := rc.IsUpdating()
	origRatio := rc.GetCurrentRatio()
	rc.SetUpdating(updating)
	defer func() {
		rc.SetUpdating(origUpdating)
		rc.CurrentRatio = origRatio
	}()
	fn()
}

// stubBulkGet replaces bulkKVGet to return canned bulk JSON, capturing the
// requested key and call count; restored via the returned restore func.
func stubBulkGet(bulk map[string][]legacySessionInDS) (reqKey *string, callCount *int, restore func()) {
	var keySeen string
	var count int
	orig := bulkKVGet
	restore = func() { bulkKVGet = orig }
	bulkKVGet = func(key string, _ *datasystemclient.Option, _ string) ([]byte, error) {
		keySeen = key
		count++
		if bulk == nil {
			return nil, nil
		}
		b, _ := json.Marshal(bulk)
		return b, nil
	}
	return &keySeen, &count, restore
}

func TestGetRecordFromLegacyBulk_Hit(t *testing.T) {
	sm := newBulkTestSM(fakeRedisStore{})
	bulk := map[string][]legacySessionInDS{
		"inst-old-1": {{
			SchedulerID: "10.0.0.99",
			InstanceSessionConfig: commonTypes.InstanceSessionConfig{
				SessionID: "sess-A", SessionTTL: 30, Concurrency: 4,
			},
		}},
		"inst-old-2": {{
			SchedulerID: "10.0.0.99",
			InstanceSessionConfig: commonTypes.InstanceSessionConfig{
				SessionID: "sess-B", SessionTTL: 60, Concurrency: 2,
			},
		}},
	}
	reqKey, _, restore := stubBulkGet(bulk)
	defer restore()
	withRollout(true, func() {
		rec, err := sm.getSessionFromStore("sess-A")
		assert.NoError(t, err)
		assert.NotNil(t, rec)
		assert.Equal(t, "inst-old-1", rec.InstanceID)
		assert.Equal(t, "sess-A", rec.SessionID)
		assert.Equal(t, 4, rec.Concurrency)
		assert.Equal(t, "10.0.0.99", rec.SchedulerID)
		assert.Equal(t, "scaled"+testFuncKeyWithLabel, *reqKey)
	})
}

func TestGetRecordFromLegacyBulk_Miss(t *testing.T) {
	sm := newBulkTestSM(fakeRedisStore{})
	bulk := map[string][]legacySessionInDS{
		"inst-old-1": {{
			InstanceSessionConfig: commonTypes.InstanceSessionConfig{SessionID: "sess-X"},
		}},
	}
	_, _, restore := stubBulkGet(bulk)
	defer restore()
	withRollout(true, func() {
		rec, err := sm.getSessionFromStore("sess-not-there")
		assert.NoError(t, err)
		assert.Nil(t, rec)
	})
}

func TestGetRecordFromLegacyBulk_CtxMatch(t *testing.T) {
	sm := newBulkTestSM(fakeRedisStore{})
	bulk := map[string][]legacySessionInDS{
		"inst-old": {
			{
				SessionCtxID: "ctx-A",
				InstanceSessionConfig: commonTypes.InstanceSessionConfig{
					SessionID: "sess-C", Concurrency: 1,
				},
			},
			{
				SessionCtxID: "ctx-B",
				InstanceSessionConfig: commonTypes.InstanceSessionConfig{
					SessionID: "sess-C", Concurrency: 2,
				},
			},
		},
	}
	_, _, restore := stubBulkGet(bulk)
	defer restore()
	withRollout(true, func() {
		rec, err := sm.getSessionFromStore("sess-C\x00ctx-B")
		assert.NoError(t, err)
		assert.NotNil(t, rec)
		assert.Equal(t, "inst-old", rec.InstanceID)
		assert.Equal(t, 2, rec.Concurrency)
		assert.Equal(t, "ctx-B", rec.SessionCtxID)
	})
}

func TestGetRecordFromLegacyBulk_NotCanary_SkipsBulk(t *testing.T) {
	sm := newBulkTestSM(fakeRedisStore{})
	bulk := map[string][]legacySessionInDS{
		"inst-old": {{
			InstanceSessionConfig: commonTypes.InstanceSessionConfig{SessionID: "sess-A"},
		}},
	}
	_, calls, restore := stubBulkGet(bulk)
	defer restore()
	withRollout(false, func() {
		rec, err := sm.getSessionFromStore("sess-A")
		assert.NoError(t, err)
		assert.Nil(t, rec)
		assert.Equal(t, 0, *calls, "must not read DataSystem bulk when not in canary")
	})
}

func TestGetRecordFromLegacyBulk_DataSystemBackend_SameLogic(t *testing.T) {
	// The legacy fallback is backend-agnostic: a DataSystem per-session primary
	// must still read the old bulk-package on miss (old data lives in the
	// function-level bulk key, unreachable via per-session keys).
	sm := newBulkTestSM(nonRedisBackendStore{})
	bulk := map[string][]legacySessionInDS{
		"inst-old": {{
			InstanceSessionConfig: commonTypes.InstanceSessionConfig{SessionID: "sess-A", Concurrency: 3},
		}},
	}
	_, calls, restore := stubBulkGet(bulk)
	defer restore()
	withRollout(true, func() {
		rec, err := sm.getSessionFromStore("sess-A")
		assert.NoError(t, err)
		assert.NotNil(t, rec)
		assert.Equal(t, "inst-old", rec.InstanceID)
		assert.Equal(t, 1, *calls, "bulk must be read regardless of primary backend")
	})
}

func TestLoadLegacyBulk_CachesWithinTTL(t *testing.T) {
	sm := newBulkTestSM(fakeRedisStore{})
	bulk := map[string][]legacySessionInDS{
		"inst": {{
			InstanceSessionConfig: commonTypes.InstanceSessionConfig{SessionID: "s1"},
		}},
	}
	_, calls, restore := stubBulkGet(bulk)
	defer restore()
	withRollout(true, func() {
		_, _ = sm.getSessionFromStore("s1")
		_, _ = sm.getSessionFromStore("s1")
		_, _ = sm.getSessionFromStore("s1")
		assert.Equal(t, 1, *calls, "bulk must be read once within TTL window")
	})
}

func TestSplitSessionKey(t *testing.T) {
	id, ctx := splitSessionKey("sid")
	assert.Equal(t, "sid", id)
	assert.Equal(t, "", ctx)
	id, ctx = splitSessionKey("sid\x00ctx1")
	assert.Equal(t, "sid", id)
	assert.Equal(t, "ctx1", ctx)
}
