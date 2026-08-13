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

// Package session 提供按 session key 读写单条绑定记录的外部存储抽象。
//
// 设计目标见 faasscheduler Session 可靠性优化设计：删除函数维度整包备份与全量恢复，
// 改为请求路径懒恢复。本地 sessionMap 命中时不访问外部存储；本地 miss 时按 session
// cache key 查询外部记录，命中则懒恢复绑定关系。
//
// # 灰度（rollout）语义
//
// physicalKey 不含 SchedulerID，灰度期新老 scheduler 对同一 session 的请求会写入同一
// 物理 key、互相覆盖——这是 by design，与旧方案"按 SchedulerID 分桶 + 全量 merge"不同。
// 设计前提：路由层保证同一 session 同一时刻只归一个 scheduler 持有者；新的请求路径
// 写入会覆盖旧持有者的记录，体现"以最新 owner 为准"。崩溃后请求被路由到新 scheduler
// 时，新 scheduler 懒恢复读到的就是最新 owner 写的 record，正是正确语义。
//
// 若未来出现"灰度期同 session 同时被两个 scheduler 持有"的场景（金丝雀长期共存且
// 路由层不保证 session 粘性），需要在此处补 SchedulerID 维度的 key 分桶——目前无此场景。
package session

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"yuanrong.org/kernel/runtime/libruntime/api"

	"yuanrong.org/kernel/pkg/common/faas_common/datasystemclient"
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	"yuanrong.org/kernel/pkg/common/faas_common/redisclient"
	"yuanrong.org/kernel/pkg/common/uuid"
)

// Backend 枚举值。
//
// 生产环境只允许 Redis / DataSystem 两种（由 config 加载期强制校验）。
// BackendNoop 不对用户暴露：仅用作 MakeStore 在 New() 失败时的 fail-open
// 兜底 sentinel，使调度主流程在初始化异常时仍能继续（写入丢弃、读取 miss）。
const (
	BackendNoop       = "noop" // fail-open 兜底 sentinel，仅由 NoopStore 使用
	BackendDataSystem = "datasystem"
	BackendRedis      = "redis"

	// defaultRecoveryWindowSeconds 是外部存储 key 的默认物理 TTL（24h），Redis 与 DataSystem
	// 后端共用——保证两后端崩溃恢复窗口一致。业务 session TTL 不参与外部 key 过期（解耦，
	// 见设计文档）；正常运行时由 scheduler 内存 timer 主动 DEL 外部 key。
	defaultRecoveryWindowSeconds = 86400
	redisOpTimeout               = 500 * time.Millisecond

	hashMaxLength = 16
)

// errStoreDisabled 表示 backend 未配置或 Redis client 未初始化，store 操作 fail-open 时返回，不阻断调度主流程。
var errStoreDisabled = errors.New("session store disabled")

// Redis 健康检查与热更新共享状态。
//
// redisClientParam 是首次 BuildRedisClient 时分配的稳定指针，之后不换指针、只原地改字段，
// 使 redisclient.CheckRedisConnectivity 持有的同一指针在重连时读到最新配置（避免 clobber）。
// 字段读写用 redisclient 包的 paramMu（LockParam/RLockParam）跨包互斥，避免 string 字段
// （ServerAddr/Password 的 {ptr,len} 双字）赋值非原子导致的撕裂读。
//   - checkerOnce 保证 checker goroutine 全进程只启一次（首次成功调用 BuildRedisClient）
//   - redisClientParam 首次分配后稳定，Reload 原地改字段
var (
	checkerOnce      sync.Once
	redisClientParam *redisclient.NewRedisClientParam
)

// StoreRecord 是外部存储保存的一条 session 绑定记录。
//
// 该记录只作为恢复索引使用，业务 TTL 不作为懒恢复拦截条件；
// scheduler 崩溃后只要外部 key 仍存在且绑定实例可用，就允许按绑定关系懒恢复。正常运行时由 scheduler 内存 timer 主动过期并删除外部 key。
type StoreRecord struct {
	InstanceID      string `json:"instanceID"`
	SchedulerID     string `json:"schedulerID"`
	SessionID       string `json:"sessionID"`
	SessionCtxID    string `json:"sessionCtxID,omitempty"`
	SessionTTL      int    `json:"sessionTTL"`
	Concurrency     int    `json:"concurrency"`
	UpdatedAtUnixNs int64  `json:"updatedAtUnixNs"`
}

// Store 抽象外部 session 绑定存储。
// Redis 与 DataSystem 实现同样的 Save/Get/Delete 语义，恢复路径保持一致，只有底层读写实现不同。
//
// 删除操作不引入 Lua 脚本，也不要求强原子 compare-and-delete；
// 新绑定和懒恢复成功时通过 Save() 覆盖旧值，正常过期时尽力 Delete()。
type Store interface {
	// Save 写入或覆盖一条 session 绑定记录。Redis 后端会刷新物理 TTL。
	Save(sessionKey string, record StoreRecord) error
	// Get 按 session cache key 查询外部记录。miss 时返回 (nil, nil)。
	Get(sessionKey string) (*StoreRecord, error)
	// Delete 删除外部记录。key 不存在不视为错误。
	Delete(sessionKey string) error
	// Backend 返回后端标识，用于日志和指标。
	Backend() string
}

// Config 是构造 Store 所需的参数。所有字段在构造时确定，store 生命周期内不变。
//
// Redis 后端不在此缓存 client 指针：redisStore 每次 op 都调 redisclient.GetRedisCmd()
// 取全局 client，使得 ReloadRedisClient 换全局后所有已存在的 redisStore 在下一次 op
// 立即生效（store 是函数级长期存活对象，必须能感知热更新）。
//
// 物理 key 只保留 session 绑定所必需的三个维度：函数（FuncCacheKey）、集群（Cluster）、
// session（sessionKey）。不再编 instanceType/resKey——它们与路由层重复（路由保证同一
// session 同一时刻只归一个 scheduler），且会阻碍 concurrencyscheduler 与 litescheduler
// 对同一函数的 session 记录共享同一物理 key。
type Config struct {
	// Backend 取值为 BackendRedis / BackendDataSystem。空字符串或未知值会被
	// config 加载期校验拦截；New() 不再接受空字符串作为合法 backend。
	Backend string
	// Cluster 集群隔离字段，避免多环境共享 Redis 时 key 冲突。
	Cluster string
	// FuncCacheKey 即函数级隔离 key（concurrencyscheduler 与 litescheduler 统一传
	// funcSpec.FuncKey），physicalKey 会对其再做一次 SHA256 取 16 hex。
	FuncCacheKey string
	// SchedulerID 写入 record 的 scheduler 标识，用于灰度、owner 和诊断。
	SchedulerID string
	// BackendTTLSeconds 外部存储 key 物理 TTL（恢复窗口），Redis 与 DataSystem 后端共用。
	// 默认 24h（defaultRecoveryWindowSeconds）。业务 session TTL 不参与外部 key 过期（解耦）。
	BackendTTLSeconds int
	// DSOption DataSystem 后端使用的 Option 构造参数。
	DSOption DSOptionConfig
}

// DSOptionConfig 描述 DataSystem 后端构造 Option 所需的字段。
type DSOptionConfig struct {
	TenantID  string
	NodeIP    string
	Cluster   string
	WriteMode api.WriteModeEnum
	TTLSecond uint32
}

// New 根据配置构造 SessionStore。Backend 必须是 redis 或 datasystem，空字符串与
// 未知值均返回错误（"不配置外部存储"的运行模式已废弃，由 config 加载期校验兜底，
// 此处再做一次防御性校验）。
//
// Redis 与 DataSystem 后端的物理 TTL 统一用 defaultRecoveryWindowSeconds（24h）兜底，
// 保证两后端崩溃恢复窗口一致。BackendTTLSeconds 显式配置时两后端都用它。
// Redis 后端不要求构造期 client 已就绪——redisStore 每次 op 调 GetRedisCmd()，未初始化时返回 errStoreDisabled fail-open。
// 启动期 client 就绪由 config.InitSessionStoreRedis（cmd 入口）保证。
func New(cfg Config) (Store, error) {
	switch cfg.Backend {
	case BackendDataSystem:
		if cfg.DSOption.TTLSecond <= 0 {
			cfg.DSOption.TTLSecond = defaultRecoveryWindowSeconds
		}
		return &dataSystemStore{cfg: cfg}, nil
	case BackendRedis:
		ttl := cfg.BackendTTLSeconds
		if ttl <= 0 {
			ttl = defaultRecoveryWindowSeconds
		}
		return &redisStore{cfg: cfg, ttl: time.Duration(ttl) * time.Second}, nil
	default:
		return nil, fmt.Errorf("invalid session store backend: %q, only %q/%q are supported",
			cfg.Backend, BackendRedis, BackendDataSystem)
	}
}

// IsRedisBackend 在 backend=redis 时返回 true，datasystem 时返回 false，
// 其他值（含空字符串）返回 error。由 cmd 入口 InitSessionStoreRedis 调用，
// 决定是否初始化 Redis client。
func IsRedisBackend(backend string) (bool, error) {
	switch backend {
	case BackendDataSystem:
		return false, nil
	case BackendRedis:
		return true, nil
	default:
		return false, fmt.Errorf("invalid session store backend: %q, only %q/%q are supported",
			backend, BackendRedis, BackendDataSystem)
	}
}

// BuildRedisClient 是 Init/Reload 的公共实现：校验配置、创建 client、SetRedisCmd 换全局、
// 更新共享 param。健康检查 goroutine 由 sync.Once 保证全进程只启一次（首次成功调用）。
//
// clobber 修复（乐观校验）：redisClientParam 首次分配后指针稳定，后续 Reload 在
// redisclient.LockParam 下原地改字段；paramMu 仅保证 param 字段读写不撕裂，无法保证
// checker 重连用最新配置创建 client（initClient 在 RLock 拷出 param 后即 RUnlock，New
// 期间无锁，最长 ~8s dialTimeout）。故 redisclient.checkAndReconnectRedis 在创建前后各
// 做一次 param 快照比对，不一致则丢弃 stale client 不 SetRedisCmd，避免 checker 用旧
// 配置 client 覆盖 Reload 的新 client。
//
// 状态一致性：redisClientParam 的更新移到 New 成功之后——New 失败时不碰共享 param，
// 避免"param 已刷新为新配置但全局 client 仍是旧"的不一致。
//
// HotloadConfFunc 保留：redisclient.Config 不暴露 HotloadConfFunc 字段，toParam 无法从 cfg
// 拿到该回调。写共享 param 前从旧 param 沿用其值——避免 Reload 清零已存在的热加载回调
// （若其他模块通过别的路径设过该字段，sessionstore 的 Reload 不应破坏其热加载能力）。
// sessionstore 自身不依赖热加载（重连只读 ServerAddr 等字段），沿用旧值不影响本路径。
func BuildRedisClient(cfg redisclient.Config, stopCh <-chan struct{}) (*redisclient.Client, error) {
	if cfg.ServerAddr == "" {
		return nil, errors.New("redis serverAddr is empty")
	}
	newParam := toParam(cfg) // 值类型，无堆分配
	// New 失败时 redisClientParam 不更新，避免 param/client 状态不一致
	cli, err := redisclient.New(newParam, stopCh)
	if err != nil {
		return nil, fmt.Errorf("new redis client failed, err: %w", err)
	}
	// New 成功后才更新共享 param（首次分配 shell / Reload 原地改字段）
	redisclient.LockParam()
	if redisClientParam == nil {
		redisClientParam = &redisclient.NewRedisClientParam{} // 首次分配空 shell，之后指针稳定
	}
	// 沿用旧 param 的 HotloadConfFunc：toParam 不带该字段，直接覆盖会清零已有回调。
	newParam.HotloadConfFunc = redisClientParam.HotloadConfFunc
	*redisClientParam = newParam // 首次和 Reload 都走原地拷贝字段，checker 通过同一指针读到新配置
	redisclient.UnlockParam()
	redisclient.SetRedisCmd(cli)
	checkerOnce.Do(func() {
		go redisclient.CheckRedisConnectivity(redisClientParam, stopCh)
	})
	return cli, nil
}

// toParam 把 redisclient.Config 转成 redisclient.NewRedisClientParam（值类型，纯转换无堆分配）。
func toParam(cfg redisclient.Config) redisclient.NewRedisClientParam {
	return redisclient.NewRedisClientParam{
		ServerMode: cfg.ServerMode,
		ServerAddr: cfg.ServerAddr,
		Password:   cfg.Password,
		Timeout:    cfg.TimeoutConf,
		EnableTLS:  cfg.EnableTLS,
	}
}

// NoopStore 是 MakeStore 在 New() 失败时的 fail-open 兜底实现：所有方法均为 no-op。
// 不对用户配置暴露——backend 必须显式 redis/datasystem，空字符串会在 config 加载期
// 被拦截，不会走到 NoopStore。仅在初始化异常时使用，保证调度主流程不因外部存储
// 初始化失败而中断。
type NoopStore struct{}

// Save -
func (NoopStore) Save(string, StoreRecord) error {
	return nil
}

// Get -
func (NoopStore) Get(string) (*StoreRecord, error) {
	return nil, nil
}

// Delete -
func (NoopStore) Delete(string) error {
	return nil
}

// Backend -
func (NoopStore) Backend() string {
	return BackendNoop
}

// physicalKey 把所有可变成分 SHA256 取 16 hex 拼成物理 key，保证字符集（纯 hex + ':'）
// 满足 DataSystem key 正则 ^[a-zA-Z0-9\-_!@#%\^\*\(\)\+\=\:;]*$，且无 '{}'。
//
// 格式: faasscheduler:session:<funcCacheKeyHash16>:<clusterHash16>:<sessionKeyHash16>
//
// 只保留 session 绑定所必需的三个维度（函数/集群/session），不再编 instanceType：
// instanceType（scaled/reserved/lite）与路由层重复——路由保证同一 session 同一时刻只归
// 一个 scheduler 持有者，去掉后 concurrencyscheduler 与 litescheduler 对同一函数的
// session 会落到同一物理 key，实现跨 scheduler 的 session 记录共享与懒恢复兼容。
//
// 长度上限 = 22("faasscheduler:session:") + 16 + 1 + 16 + 1 + 16 = 72 ≤ 255 ✓
// 16 hex = 64 bit，函数/集群数量小、session 数百万级，碰撞概率可忽略。
//
// Redis 后端不使用 hash tag：本方案仅做单 key GET/SET/DEL，无跨 key 操作，不需要同 slot 归并。
func physicalKey(cfg Config, sessionKey string) string {
	funcHash := sha256.Sum256([]byte(cfg.FuncCacheKey))
	clusterHash := sha256.Sum256([]byte(cfg.Cluster))
	sessionHash := sha256.Sum256([]byte(sessionKey))
	key := fmt.Sprintf("faasscheduler:session:%s:%s:%s",
		hex.EncodeToString(funcHash[:])[:hashMaxLength],
		hex.EncodeToString(clusterHash[:])[:hashMaxLength],
		hex.EncodeToString(sessionHash[:])[:hashMaxLength])
	// DEBUG 日志：记录生成的物理 key 与 hash 前的各字段原值，便于从 Redis/DataSystem 中
	// 看到哈希 key 时反查它对应的函数/集群/session。sessionKey 用 %q 转义（含 \x00 等控制字符）。
	log.GetLogger().Debugf("sessionstore physical key generated, key=%s, "+
		"funcCacheKey=%s, cluster=%s, sessionKey=%q",
		key, cfg.FuncCacheKey, cfg.Cluster, sessionKey)
	return key
}

// fillRecord 用当前时间戳补齐 record 的诊断字段。
func fillRecord(cfg Config, record *StoreRecord) {
	if record.SchedulerID == "" {
		record.SchedulerID = cfg.SchedulerID
	}
	record.UpdatedAtUnixNs = time.Now().UnixNano()
}

type redisStore struct {
	cfg Config
	ttl time.Duration
}

func (s *redisStore) Save(sessionKey string, record StoreRecord) error {
	cli := redisclient.GetRedisCmd()
	if cli == nil {
		return errStoreDisabled
	}
	if sessionKey == "" {
		return fmt.Errorf("sessionKey is empty")
	}
	fillRecord(s.cfg, &record)
	value, err := json.Marshal(record)
	if err != nil {
		return fmt.Errorf("marshal session record failed: %w", err)
	}
	key := physicalKey(s.cfg, sessionKey)
	ctx, cancel := context.WithTimeout(context.Background(), redisOpTimeout)
	defer cancel()
	if err := cli.SetEX(ctx, key, string(value), s.ttl).Err(); err != nil {
		log.GetLogger().Warnf("sessionstore redis SET failed, key=%s, err=%s", key, err.Error())
		return err
	}
	return nil
}

func (s *redisStore) Get(sessionKey string) (*StoreRecord, error) {
	cli := redisclient.GetRedisCmd()
	if cli == nil {
		return nil, errStoreDisabled
	}
	if sessionKey == "" {
		return nil, fmt.Errorf("sessionKey is empty")
	}
	key := physicalKey(s.cfg, sessionKey)
	ctx, cancel := context.WithTimeout(context.Background(), redisOpTimeout)
	defer cancel()
	value, err := cli.Get(ctx, key).Result()
	if err != nil {
		if errors.Is(err, redisclient.Nil) {
			return nil, nil
		}
		log.GetLogger().Warnf("sessionstore redis GET failed, key=%s, err=%s", key, err.Error())
		return nil, err
	}
	var record StoreRecord
	if err := json.Unmarshal([]byte(value), &record); err != nil {
		log.GetLogger().Warnf("sessionstore redis GET unmarshal failed, key=%s, err=%s", key, err.Error())
		return nil, nil
	}
	return &record, nil
}

func (s *redisStore) Delete(sessionKey string) error {
	cli := redisclient.GetRedisCmd()
	if cli == nil {
		return errStoreDisabled
	}
	if sessionKey == "" {
		return fmt.Errorf("sessionKey is empty")
	}
	key := physicalKey(s.cfg, sessionKey)
	ctx, cancel := context.WithTimeout(context.Background(), redisOpTimeout)
	defer cancel()
	if err := cli.Del(ctx, key).Err(); err != nil {
		log.GetLogger().Warnf("sessionstore redis DEL failed, key=%s, err=%s", key, err.Error())
		return err
	}
	return nil
}

func (s *redisStore) Backend() string {
	return BackendRedis
}

type dataSystemStore struct {
	cfg Config
}

func (s *dataSystemStore) Save(sessionKey string, record StoreRecord) error {
	if sessionKey == "" {
		return fmt.Errorf("sessionKey is empty")
	}
	fillRecord(s.cfg, &record)
	value, err := json.Marshal(record)
	if err != nil {
		return fmt.Errorf("marshal session record failed: %w", err)
	}
	key := physicalKey(s.cfg, sessionKey)
	opt := s.buildOption()
	if err := datasystemclient.KVPutWithRetry(key, value, opt, uuid.New().String()); err != nil {
		log.GetLogger().Warnf("sessionstore datasystem PUT failed, key=%s, err=%s", key, err.Error())
		return err
	}
	return nil
}

func (s *dataSystemStore) Get(sessionKey string) (*StoreRecord, error) {
	if sessionKey == "" {
		return nil, fmt.Errorf("sessionKey is empty")
	}
	key := physicalKey(s.cfg, sessionKey)
	resp, err := datasystemclient.KVGetWithRetry(key, s.buildOption(), uuid.New().String())
	if err != nil {
		log.GetLogger().Warnf("sessionstore datasystem GET failed, key=%s, err=%s", key, err.Error())
		return nil, err
	}
	if len(resp) == 0 {
		return nil, nil
	}
	var record StoreRecord
	if err := json.Unmarshal(resp, &record); err != nil {
		log.GetLogger().Warnf("sessionstore datasystem GET unmarshal failed, key=%s, err=%s", key, err.Error())
		return nil, nil
	}
	return &record, nil
}

func (s *dataSystemStore) Delete(sessionKey string) error {
	if sessionKey == "" {
		return fmt.Errorf("sessionKey is empty")
	}
	key := physicalKey(s.cfg, sessionKey)
	if err := datasystemclient.KVDelWithRetry(key, s.buildOption(), uuid.New().String()); err != nil {
		log.GetLogger().Warnf("sessionstore datasystem DEL failed, key=%s, err=%s", key, err.Error())
		return err
	}
	return nil
}

func (s *dataSystemStore) Backend() string {
	return BackendDataSystem
}

func (s *dataSystemStore) buildOption() *datasystemclient.Option {
	return &datasystemclient.Option{
		TenantID:  s.cfg.DSOption.TenantID,
		NodeIP:    s.cfg.DSOption.NodeIP,
		Cluster:   s.cfg.DSOption.Cluster,
		WriteMode: s.cfg.DSOption.WriteMode,
		TTLSecond: s.cfg.DSOption.TTLSecond,
	}
}
