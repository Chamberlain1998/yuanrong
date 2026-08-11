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
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestPhysicalKeyDistinctForSessionCtx(t *testing.T) {
	cfg := Config{FuncCacheKey: "fc", Cluster: "c1"}
	kA := physicalKey(cfg, "s1\x00ctxA")
	kB := physicalKey(cfg, "s1\x00ctxB")
	assert.NotEqual(t, kA, kB, "different sessionCtx must hash to different physical keys")
	assert.Equal(t, kA, physicalKey(cfg, "s1\x00ctxA"), "same session key must hash to same physical key")
}

func TestPhysicalKeyAllHashedAndBounded(t *testing.T) {
	cfg := Config{FuncCacheKey: "sessioncache-faasscheduler-a1b2c3d4e5f6a7b8", Cluster: "c1"}
	k := physicalKey(cfg, "session1")
	// 固定前缀，无 '{' '}'
	assert.True(t, strings.HasPrefix(k, "faasscheduler:session:"),
		"physical key must have fixed prefix; got %s", k)
	assert.NotContains(t, k, "{", "physical key must not contain '{' (DataSystem regex rejects it)")
	assert.NotContains(t, k, "}", "physical key must not contain '}' (DataSystem regex rejects it)")
	// funcCacheKey 已哈希，原值不应出现在 key 里
	assert.NotContains(t, k, "sessioncache-faasscheduler", "funcCacheKey must be hashed, not embedded raw")
	// 长度 ≤ 255（DataSystem 限制）
	assert.LessOrEqual(t, len(k), 255, "physical key must not exceed 255; got %d", len(k))
	// 整体满足 DataSystem 正则
	assert.Regexp(t, `^[a-zA-Z0-9\-_!@#%\^\*\(\)\+\=\:;]*$`, k)
}

func TestPhysicalKeyIsolatesCluster(t *testing.T) {
	a := Config{FuncCacheKey: "fc", Cluster: "c1"}
	b := Config{FuncCacheKey: "fc", Cluster: "c2"}
	assert.NotEqual(t, physicalKey(a, "s1"), physicalKey(b, "s1"),
		"different cluster must hash to different physical keys (env isolation)")
}

func TestPhysicalKeyIsolatesFunction(t *testing.T) {
	// instanceType 维度已移除（与路由层重复），物理 key 只靠 函数/集群/session 三维
	// 隔离。不同函数必须落到不同物理 key。
	a := Config{FuncCacheKey: "funcA", Cluster: "c1"}
	b := Config{FuncCacheKey: "funcB", Cluster: "c1"}
	assert.NotEqual(t, physicalKey(a, "s1"), physicalKey(b, "s1"),
		"different function must hash to different physical keys")
}

func TestNewEmptyBackendErrors(t *testing.T) {
	// 不再支持"不配置外部存储"：空 backend 必须报错，由 config 加载期校验拦截；
	// New() 这里再做一次防御性校验，确保任何路径都不会静默降级到 NoopStore。
	_, err := New(Config{Backend: ""})
	assert.Error(t, err)
}

func TestNewNoopStoreBackendReturnsNoop(t *testing.T) {
	// NoopStore 仅在 MakeStore 的 fail-open 路径出现（New 失败兜底），不对用户配置暴露。
	// Backend() 返回 BackendNoop sentinel，用于日志/指标区分真实后端与 fail-open 兜底。
	s := NoopStore{}
	assert.Equal(t, BackendNoop, s.Backend())
	assert.NoError(t, s.Save("k", StoreRecord{}))
	r, err := s.Get("k")
	assert.NoError(t, err)
	assert.Nil(t, r)
	assert.NoError(t, s.Delete("k"))
}

func TestNewRedisStoreDoesNotCacheClient(t *testing.T) {
	// 方案 1：redisStore 构造期不缓存 client 指针，每次 op 调 redisclient.GetRedisCmd()
	// 取全局，使 ReloadRedisClient 换全局后存量 store 立即生效。构造期不检查 client 是否
	// 就绪；未初始化时 op 返回 errStoreDisabled fail-open。
	s, err := New(Config{Backend: BackendRedis, FuncCacheKey: "fc", Cluster: "c1"})
	assert.NoError(t, err)
	assert.Equal(t, BackendRedis, s.Backend())
	// 全局未初始化时 Save 应 fail-open 返回 errStoreDisabled，不 panic
	err = s.Save("k", StoreRecord{InstanceID: "i1"})
	assert.Error(t, err)
}

func TestNewUnknownBackendErrors(t *testing.T) {
	_, err := New(Config{Backend: "unknown"})
	assert.Error(t, err)
}

func TestNewDefaultsRecoveryWindow(t *testing.T) {
	// 两后端统一恢复窗口默认 24h
	assert.Equal(t, 86400, defaultRecoveryWindowSeconds, "default recovery window must be 24h")
}

func TestNewDataSystemDefaultsTTLAligned(t *testing.T) {
	// DataSystem 后端 TTLSecond=0 时默认对齐 24h（与 Redis 一致），不再是 UploadTTLSec 的 10s
	s, err := New(Config{Backend: BackendDataSystem, FuncCacheKey: "fc", Cluster: "c1"})
	assert.NoError(t, err)
	ds, ok := s.(*dataSystemStore)
	assert.True(t, ok)
	assert.Equal(t, uint32(86400), ds.cfg.DSOption.TTLSecond,
		"DataSystem TTLSecond must default to 24h recovery window when unset")
}

func TestNewDataSystemRespectsExplicitTTL(t *testing.T) {
	// 显式配置 TTLSecond 时尊重用户值
	s, err := New(Config{
		Backend:      BackendDataSystem,
		FuncCacheKey: "fc", Cluster: "c1",
		DSOption: DSOptionConfig{TTLSecond: 3600},
	})
	assert.NoError(t, err)
	ds, ok := s.(*dataSystemStore)
	assert.True(t, ok)
	assert.Equal(t, uint32(3600), ds.cfg.DSOption.TTLSecond, "explicit TTLSecond must be respected")
}
