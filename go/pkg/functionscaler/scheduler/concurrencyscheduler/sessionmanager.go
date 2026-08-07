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

// Package concurrencyscheduler -
package concurrencyscheduler

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"yuanrong.org/kernel/pkg/common/faas_common/datasystemclient"
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	commonTypes "yuanrong.org/kernel/pkg/common/faas_common/types"
	"yuanrong.org/kernel/pkg/common/uuid"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/pkg/functionscaler/rollout"
	"yuanrong.org/kernel/pkg/functionscaler/session"
	"yuanrong.org/kernel/pkg/functionscaler/types"
	"yuanrong.org/kernel/pkg/functionscaler/utils"
)

const (
	// bulkCacheTTL bounds how long a parsed legacy bulk-package is reused
	// across concurrent cache misses during canary, so the warm-up burst of
	// misses on one function issues at most one DataSystem GET per window.
	bulkCacheTTL = 2 * time.Second
)

// bulkKVGet is the DataSystem GET used by the legacy bulk-package migration
// reader. Indirected as a package var so tests can inject a fake without a
// live DataSystem deployment.
var bulkKVGet = datasystemclient.KVGetWithRetry

// legacySessionInDS mirrors the pre-reliability-redesign SessionInDS layout the
// old concurrencyscheduler wrote into the function-level DataSystem bulk-package
// (key: instanceType + funcKeyWithLabel, value: map[instanceID][]SessionInDS).
// Used only to parse legacy bulk data during an upgrade canary so the new
// scheduler inherits old session→instance bindings instead of rebinding.
type legacySessionInDS struct {
	SchedulerID                       string `json:"SchedulerID"`
	SessionCtxID                      string `json:"sessionCtxID,omitempty"`
	commonTypes.InstanceSessionConfig `json:",inline"`
}

type sessionRecord struct {
	ctx            context.Context
	timer          *time.Timer
	availThdMap    map[string]struct{}
	allocThdMap    map[string]struct{}
	overAcqThdMap  map[string]struct{}
	expiring       atomic.Value
	ttl            time.Duration
	concurrency    int
	sessionID      string
	sessionCtxID   string
	expireCancelCh chan struct{}
	expireCh       chan struct{}
	cancelFunc     func()

	insElem *instanceElement
}

func (s *sessionRecord) MarkThreadAsAvailable(threadID string) error {
	if _, ok := s.allocThdMap[threadID]; !ok {
		return fmt.Errorf("thread %s doesn't belong to session %s for function", threadID, s.sessionID)
	}
	s.availThdMap[threadID] = struct{}{}
	return nil
}

func (s *sessionRecord) PutThreadToAllocThdMap(threadID string) {
	s.allocThdMap[threadID] = struct{}{}
}

func (s *sessionRecord) GetThreadFromAvailThdMap() string {
	var (
		threadID string
	)
	for key := range s.availThdMap {
		threadID = key
		break
	}
	delete(s.availThdMap, threadID)
	return threadID
}

func (s *sessionRecord) GetOrReplaceDesignateThreadFromAvailThdMap(designateThreadID string) (string, error) {
	if designateThreadID == "" {
		return s.GetThreadFromAvailThdMap(), nil
	}
	if _, ok := s.availThdMap[designateThreadID]; ok {
		delete(s.availThdMap, designateThreadID)
		return designateThreadID, nil
	}
	if _, ok := s.allocThdMap[designateThreadID]; ok {
		return "", fmt.Errorf("designate thread %s has been acquired", designateThreadID)
	}
	threadID := s.GetThreadFromAvailThdMap()
	log.GetLogger().Debugf("threadID %s has been replaced by %s", threadID, designateThreadID)
	delete(s.allocThdMap, threadID)
	s.allocThdMap[designateThreadID] = struct{}{}
	return designateThreadID, nil
}

// sessionManager 维护本地 session 与实例/线程的绑定关系。
// 外部存储（SessionStore）仅作为崩溃后懒恢复的索引：本地命中时不访问外部存储；
// 本地 miss 时按 session cache key 查询外部记录。详见 faasscheduler Session 可靠性优化设计。
//
// 异步写入/单飞读/同步清理等存储协调逻辑已上移到 session.Coordinator，本结构只保留
// sessionRecord→StoreRecord 的映射（concurrencyscheduler 的线程级绑定字段与 litescheduler
// 不同，故映射各自保留）。详见 session.Coordinator 注释。
type sessionManager struct {
	currentSchedulerID string
	sessionMap         map[string]*sessionRecord
	funcKeyWithLabel   string
	currentNode        string
	instanceType       types.InstanceType
	coord              *session.Coordinator
	isFuncOwner        bool
	// bulkCache/bulkLoadedAt/bulkMu back the legacy bulk-package migration reader
	// (getRecordFromLegacyBulk). Only touched during a scheduler-upgrade canary
	// when the primary backend is Redis. Per-function (one sessionManager per
	// function), so the cache is naturally scoped to the function's bulk key.
	bulkMu       sync.Mutex
	bulkCache    map[string][]legacySessionInDS
	bulkLoadedAt time.Time
	*sync.RWMutex
}

func makeSessionManager(funcKeyWithLabel string, currentNode string, instanceType types.InstanceType,
	store session.Store) *sessionManager {
	return &sessionManager{
		funcKeyWithLabel:   funcKeyWithLabel,
		currentSchedulerID: os.Getenv("POD_IP"),
		sessionMap:         make(map[string]*sessionRecord, utils.DefaultMapSize),
		currentNode:        currentNode,
		instanceType:       instanceType,
		coord:              session.NewCoordinator(store),
		RWMutex:            &sync.RWMutex{},
	}
}

func (sm *sessionManager) setFuncOwner(isFuncOwner bool) {
	sm.Lock()
	defer sm.Unlock()
	sm.isFuncOwner = isFuncOwner
}

func (sm *sessionManager) getSession(sessionID string) (*sessionRecord, bool) {
	sm.RLock()
	defer sm.RUnlock()
	record, ok := sm.sessionMap[sessionID]
	// if inselem is nil, this is invalid session
	if ok && record.insElem == nil {
		return nil, false
	}
	return record, ok
}

// addSession 写入本地 map 后异步写外部存储。本地 map 写在 sm 锁内（快），外部 Save
// 由 Coordinator 的 worker goroutine 锁外执行，不阻塞 bcs 锁。外部写入失败 fail-open。
func (sm *sessionManager) addSession(sessionID string, sessionRecord *sessionRecord) {
	sm.Lock()
	sm.sessionMap[sessionID] = sessionRecord
	sm.Unlock()
	sm.saveSessionToStore(sessionID, sessionRecord)
}

// delSession 删除本地 map 后异步删外部记录。本地删除在 sm 锁内（快），外部 Delete
// 由 Coordinator 的 worker 异步执行。删除失败 fail-open。
func (sm *sessionManager) delSession(sessionID string) {
	sm.Lock()
	delete(sm.sessionMap, sessionID)
	sm.Unlock()
	sm.deleteSessionFromStore(sessionID)
}

// getSessionFromStore 本地 miss 后按 session cache key 查询外部记录（singleflight 去重）。
// 返回 (nil,nil) 表示外部 miss；返回 error 表示存储异常，调用方 fail-open 按新 session 处理。
// 不在 bcs 锁内调用（存储 I/O）。
//
// 升级灰度期数据迁移：主后端干净 miss（无记录无错误）且处于灰度（rollout.IsUpdating）
// 时，回退读取旧 scheduler 写入 DataSystem 的函数级 bulk-package，继承旧 session→instance
// 绑定，不丢亲和性。命中后由恢复路径 addSession→saveSessionToStore 异步写入主后端（新
// per-session key），完成 DataSystem-bulk→主后端 单向迁移。主后端无关 redis 还是 datasystem
// 都是这套逻辑；主后端为 Noop（未配外存）时 Save 为 no-op，仅恢复本地 sessionMap 亲和性。
// 读取次序：主后端先查（已迁移的 fresh 记录优先，避免旧 bulk 里的 stale instance 覆盖新
// 绑定），miss 再查 bulk。
func (sm *sessionManager) getSessionFromStore(sessionKey string) (*session.StoreRecord, error) {
	rec, err := sm.coord.GetRecord(sessionKey)
	if rec != nil || err != nil {
		return rec, err
	}
	// Primary clean miss: during canary the new scheduler may inherit bindings
	// the old scheduler left in the DataSystem function-level bulk-package. Same
	// logic regardless of primary backend (Redis/DataSystem/Noop): Noop just
	// means the async Save on recovery is a no-op, recovering local affinity only.
	if rollout.GetGlobalRolloutConfig().IsUpdating() {
		return sm.getRecordFromLegacyBulk(sessionKey)
	}
	return nil, nil
}

// getRecordFromLegacyBulk reads the old scheduler's function-level DataSystem
// bulk-package and extracts the binding for sessionKey. Returns (nil,nil) when
// the session is absent (caller fail-opens as a fresh session). The parsed
// bulk is cached for bulkCacheTTL so a burst of concurrent misses on one
// function issues at most one DataSystem GET per window.
func (sm *sessionManager) getRecordFromLegacyBulk(sessionKey string) (*session.StoreRecord, error) {
	bulk, err := sm.loadLegacyBulk()
	if err != nil || len(bulk) == 0 {
		return nil, err
	}
	sessionID, sessionCtxID := splitSessionKey(sessionKey)
	for instanceID, sessions := range bulk {
		for i := range sessions {
			s := &sessions[i]
			if s.SessionID != sessionID {
				continue
			}
			// When the session key carries a context id, require it to match too,
			// so a ctx-scoped session does not recover against a sibling ctx.
			if sessionCtxID != "" && s.SessionCtxID != sessionCtxID {
				continue
			}
			return &session.StoreRecord{
				InstanceID:   instanceID,
				SchedulerID:  s.SchedulerID,
				SessionID:    s.SessionID,
				SessionCtxID: s.SessionCtxID,
				SessionTTL:   s.SessionTTL,
				Concurrency:  s.Concurrency,
			}, nil
		}
	}
	return nil, nil
}

// loadLegacyBulk reads and parses the old scheduler's function-level
// bulk-package from DataSystem, caching the result for bulkCacheTTL. The
// legacy key is instanceType + funcKeyWithLabel — identical to what the old
// scheduler wrote: funcKeyWithLabel holds makeSessionCacheKey's output, which
// is unchanged across the reliability redesign (verified against the pre-
// redesign sessionManager).
func (sm *sessionManager) loadLegacyBulk() (map[string][]legacySessionInDS, error) {
	sm.bulkMu.Lock()
	defer sm.bulkMu.Unlock()
	if sm.bulkCache != nil && time.Since(sm.bulkLoadedAt) < bulkCacheTTL {
		return sm.bulkCache, nil
	}
	key := string(sm.instanceType) + sm.funcKeyWithLabel
	opt := &datasystemclient.Option{
		TenantID: "0",
		NodeIP:   sm.currentNode,
		Cluster:  config.GlobalConfig.DataSystemConfig.CurrentCluster,
	}
	resp, err := bulkKVGet(key, opt, uuid.New().String())
	if err != nil {
		log.GetLogger().Warnf("legacy bulk-package get failed, key=%s, err=%s", key, err.Error())
		return nil, err
	}
	bulk := make(map[string][]legacySessionInDS)
	if len(resp) == 0 {
		sm.bulkCache = bulk
		sm.bulkLoadedAt = time.Now()
		return sm.bulkCache, nil
	}
	if err := json.Unmarshal(resp, &bulk); err != nil {
		log.GetLogger().Warnf("legacy bulk-package unmarshal failed, key=%s, err=%s", key, err.Error())
		return nil, err
	}
	sm.bulkCache = bulk
	sm.bulkLoadedAt = time.Now()
	return sm.bulkCache, nil
}

// splitSessionKey splits a session cache key ("sessionID" or
// "sessionID\x00sessionCtxID") back into its parts, using the same NUL
// separator as types.JoinKey. Used only to search the legacy bulk-package,
// which is keyed by raw SessionID/SessionCtxID, not by the cache key.
func splitSessionKey(sessionKey string) (sessionID, sessionCtxID string) {
	if i := strings.IndexByte(sessionKey, 0x00); i >= 0 {
		return sessionKey[:i], sessionKey[i+1:]
	}
	return sessionKey, ""
}

// deleteSessionFromStore 异步删除外部记录。
func (sm *sessionManager) deleteSessionFromStore(sessionKey string) {
	sm.coord.DeleteRecord(sessionKey)
}

// saveSessionToStore 从 sessionRecord 映射出 StoreRecord 快照并异步入队。record 快照是
// 值拷贝，避免 insElem 后续被改。concurrencyscheduler 特有的线程级字段（concurrency）
// 一并记录以便恢复原始绑定参数。
func (sm *sessionManager) saveSessionToStore(sessionKey string, record *sessionRecord) {
	if record == nil || record.insElem == nil || record.insElem.instance == nil {
		return
	}
	storeRecord := session.StoreRecord{
		InstanceID:   record.insElem.instance.InstanceID,
		SchedulerID:  sm.currentSchedulerID,
		SessionID:    record.sessionID,
		SessionCtxID: record.sessionCtxID,
		SessionTTL:   int(record.ttl.Seconds()),
		Concurrency:  record.concurrency,
	}
	sm.coord.SaveRecord(sessionKey, storeRecord)
}

// stopAndClean 取消 Coordinator worker。不删 per-session 外部记录——清理由
// CleanExternalSessionRecords（queue 销毁时）显式触发，scheduler 重建场景不调。
func (sm *sessionManager) stopAndClean() {
	sm.coord.Stop()
}

// cleanExternalRecords 删除本 scheduler 在外部存储的全部 per-session 记录。
// 锁内拷贝 sessionKey 列表（避免迭代期间 map 被改），交由 Coordinator 同步 Delete。
// 同步执行（Destroy 是 teardown 非热路径，不经过异步队列，避免 worker 停止后丢 op）。
// 仅场景 1/2（函数删除 / resKey 下线，queue 彻底销毁）调用；场景 3（scheduler 重建）不调。
func (sm *sessionManager) cleanExternalRecords() {
	sm.RLock()
	keys := make([]string, 0, len(sm.sessionMap))
	for key := range sm.sessionMap {
		keys = append(keys, key)
	}
	sm.RUnlock()
	sm.coord.CleanRecords(keys)
}

func makeSessionCacheKey(funcName, funcKeyWithRes string) string {
	hash := sha256.Sum256([]byte(funcKeyWithRes))
	hashStr := hex.EncodeToString(hash[:])[:16] // hashStr len is 16
	return fmt.Sprintf("sessioncache-%s-%s", funcName, hashStr)
}
