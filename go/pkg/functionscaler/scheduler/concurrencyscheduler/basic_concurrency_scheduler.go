/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
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
	"errors"
	"fmt"
	"os"
	"sync"
	"time"

	"go.uber.org/zap"

	"yuanrong.org/kernel/runtime/libruntime/api"

	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	"yuanrong.org/kernel/pkg/common/faas_common/queue"
	"yuanrong.org/kernel/pkg/common/faas_common/resspeckey"
	commonTypes "yuanrong.org/kernel/pkg/common/faas_common/types"
	commonUtils "yuanrong.org/kernel/pkg/common/faas_common/utils"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/pkg/functionscaler/lease"
	"yuanrong.org/kernel/pkg/functionscaler/metrics"
	"yuanrong.org/kernel/pkg/functionscaler/requestqueue"
	"yuanrong.org/kernel/pkg/functionscaler/scheduler"
	"yuanrong.org/kernel/pkg/functionscaler/selfregister"
	"yuanrong.org/kernel/pkg/functionscaler/session"
	"yuanrong.org/kernel/pkg/functionscaler/signalmanager"
	"yuanrong.org/kernel/pkg/functionscaler/types"
	"yuanrong.org/kernel/pkg/functionscaler/utils"
)

type popDirection bool

const (
	forward  popDirection = true
	backward popDirection = false

	randomThreadIDLength = 8

	maxColdStartTraceQueue = 1024
)

var (
	// ErrInsThdNotExist is the error of instance thread not exist
	ErrInsThdNotExist = errors.New("instance thread not exist")
	// ErrNoInsThdAvail is the error of instance thread not exist
	ErrNoInsThdAvail = errors.New("no instance thread available")
)

type instanceElement struct {
	instance       *types.Instance
	threadIndex    int
	threadIDPrefix string
	isNewInstance  bool
	isPriorityAZ   bool
	threadMap      map[string]struct{}
}

func getInstancePriorityBonus(insElem *instanceElement) int {
	if insElem == nil || insElem.instance == nil {
		return 0
	}

	bonus := 0
	if insElem.isNewInstance && len(insElem.threadMap) > 0 {
		bonus += insElem.instance.ConcurrentNum
	}
	if insElem.isPriorityAZ && len(insElem.threadMap) > 0 {
		bonus += insElem.instance.ConcurrentNum
	}
	return bonus
}

func checkInstancePriorityAZ(instance *types.Instance, funcSpec *types.FunctionSpecification) bool {
	if instance == nil || funcSpec == nil {
		return false
	}
	return funcSpec.ExtendedMetaData.PriorityAZ != "" && funcSpec.ExtendedMetaData.PriorityAZ == instance.AZ
}

func (i *instanceElement) PutThreadToThreadMap(threadID string) {
	// 如果put回来的租约在map中仍然存在，则不处理（这种情况理论上不存在）
	if _, ok := i.threadMap[threadID]; ok {
		return
	}
	if len(i.threadMap) >= i.instance.ConcurrentNum {
		return
	}
	i.threadMap[fmt.Sprintf("%s-thread%s-%d", i.instance.InstanceID, i.threadIDPrefix, i.threadIndex)] = struct{}{}
	i.threadIndex++
}

func (i *instanceElement) GetThreadFromThreadMap() string {
	var (
		threadID string
	)
	for key := range i.threadMap {
		threadID = key
		break
	}
	delete(i.threadMap, threadID)
	return threadID
}

func (i *instanceElement) initThreadMap() {
	i.threadIndex = 1
	i.threadMap = make(map[string]struct{}, i.instance.ConcurrentNum)
	i.threadIDPrefix = utils.GenRandomString(randomThreadIDLength)
	for ; i.threadIndex <= i.instance.ConcurrentNum; i.threadIndex++ {
		i.threadMap[fmt.Sprintf("%s-thread%s-%d", i.instance.InstanceID, i.threadIDPrefix, i.threadIndex)] = struct{}{}
	}
	return
}

type instanceObserver struct {
	callback func(interface{})
}

type instanceQueueWithSubHealthAndEvictingRecord struct {
	instanceQueue   queue.Queue
	subHealthRecord map[string]*instanceElement
	evictingRecord  map[string]*instanceElement
}

// Front -
func (i *instanceQueueWithSubHealthAndEvictingRecord) Front() interface{} {
	return i.instanceQueue.Front()
}

// Back -
func (i *instanceQueueWithSubHealthAndEvictingRecord) Back() interface{} {
	return i.instanceQueue.Back()
}

// PopFront -
func (i *instanceQueueWithSubHealthAndEvictingRecord) PopFront() interface{} {
	return i.instanceQueue.PopFront()
}

// PopBack -
func (i *instanceQueueWithSubHealthAndEvictingRecord) PopBack() interface{} {
	return i.instanceQueue.PopBack()
}

// PopSubHealth -
func (i *instanceQueueWithSubHealthAndEvictingRecord) PopSubHealth() interface{} {
	insElem := getSubHealthInstanceFromRecord(i.subHealthRecord)
	if !commonUtils.IsNil(insElem) {
		delete(i.subHealthRecord, insElem.instance.InstanceID)
		return insElem
	}
	return nil
}

// PushBack -
func (i *instanceQueueWithSubHealthAndEvictingRecord) PushBack(obj interface{}) error {
	insElem, ok := obj.(*instanceElement)
	if !ok {
		return scheduler.ErrTypeConvertFail
	}
	_, existSubHealth := i.subHealthRecord[insElem.instance.InstanceID]
	_, existGShut := i.evictingRecord[insElem.instance.InstanceID]
	existHealth := i.instanceQueue.GetByID(insElem.instance.InstanceID) != nil
	if existSubHealth || existHealth || existGShut {
		return scheduler.ErrInsAlreadyExist
	}
	switch insElem.instance.InstanceStatus.Code {
	case int32(constant.KernelInstanceStatusRunning):
		log.GetLogger().Infof("put into instanceQueue, ins: %+v", insElem)
		return i.instanceQueue.PushBack(insElem)
	case int32(constant.KernelInstanceStatusSubHealth):
		i.subHealthRecord[insElem.instance.InstanceID] = insElem
	case int32(constant.KernelInstanceStatusEvicting):
		i.evictingRecord[insElem.instance.InstanceID] = insElem
	default:
		log.GetLogger().Warnf("ignore instance %s with unexpected status code %d", insElem.instance.InstanceID,
			insElem.instance.InstanceStatus.Code)
		return scheduler.ErrInternal
	}
	return nil
}

// GetByID -
func (i *instanceQueueWithSubHealthAndEvictingRecord) GetByID(objID string) interface{} {
	if insElem, exist := i.subHealthRecord[objID]; exist {
		return insElem
	}
	if insElem, exist := i.evictingRecord[objID]; exist {
		return insElem
	}
	return i.instanceQueue.GetByID(objID)
}

// DelByID -
func (i *instanceQueueWithSubHealthAndEvictingRecord) DelByID(objID string) error {
	_, existSubHealth := i.subHealthRecord[objID]
	existHealth := i.instanceQueue.GetByID(objID) != nil
	_, existGShut := i.evictingRecord[objID]
	if !existSubHealth && !existHealth && !existGShut {
		return scheduler.ErrInsNotExist
	}

	delete(i.evictingRecord, objID)
	delete(i.subHealthRecord, objID)
	i.instanceQueue.DelByID(objID)
	return nil
}

// UpdateObjByID -
func (i *instanceQueueWithSubHealthAndEvictingRecord) UpdateObjByID(objID string, obj interface{}) error {
	insElem, ok := obj.(*instanceElement)
	if !ok {
		return scheduler.ErrTypeConvertFail
	}
	_, existSubHealth := i.subHealthRecord[objID]
	existHealth := i.instanceQueue.GetByID(objID) != nil
	_, existGShut := i.evictingRecord[objID]
	if !existSubHealth && !existHealth && !existGShut {
		return scheduler.ErrInsNotExist
	}

	i.instanceQueue.DelByID(objID)
	delete(i.subHealthRecord, objID)
	delete(i.evictingRecord, objID)
	switch insElem.instance.InstanceStatus.Code {
	case int32(constant.KernelInstanceStatusRunning):
		err := i.instanceQueue.PushBack(insElem)
		if err != nil {
			return err
		}
	case int32(constant.KernelInstanceStatusSubHealth):
		i.subHealthRecord[objID] = insElem
	case int32(constant.KernelInstanceStatusEvicting):
		i.evictingRecord[objID] = insElem
	default:
		log.GetLogger().Warnf("ignore instance %s with unexpected status code %d", insElem.instance.InstanceID,
			insElem.instance.InstanceStatus.Code)
		return scheduler.ErrInternal
	}
	return nil
}

// Len -
func (i *instanceQueueWithSubHealthAndEvictingRecord) Len() int {
	return i.instanceQueue.Len() + len(i.subHealthRecord) // 判断长度不需要考虑gShutRrecord中实例数
}

// Range -
func (i *instanceQueueWithSubHealthAndEvictingRecord) Range(f func(obj interface{}) bool) bool {
	if !i.instanceQueue.Range(f) {
		return false
	}
	for _, insElem := range i.subHealthRecord {
		if !f(insElem) {
			return false
		}
	}

	for _, insElem := range i.evictingRecord {
		if !f(insElem) {
			return false
		}
	}
	return true
}

// SortedRange -
func (i *instanceQueueWithSubHealthAndEvictingRecord) SortedRange(f func(obj interface{}) bool) bool {
	if !i.instanceQueue.SortedRange(f) {
		return false
	}
	for _, insElem := range i.subHealthRecord {
		if !f(insElem) {
			return false
		}
	}

	for _, insElem := range i.evictingRecord {
		if !f(insElem) {
			return false
		}
	}
	return true
}

type instanceQueueWithObserver struct {
	instanceQueueWithSubHealthAndEvictingRecord
	/*
	 * insAvailThdCount记录队列中实例的可用租约数，这里作用是当外部修改的queue中实例中租约信息，通过该count可以计算出可用实例数的变化，
	 * 以确保指标上报时可以提供准确的数值。
	 * 注意，该count不考虑实例的状态，仅供指标上报，不做他用。
	 */
	insAvailThdCount  map[string]int
	pubAvailTopicFunc func(int)
	pubInUseTopicFunc func(int)
	pubTotalTopicFunc func(int)
}

// PopFront -
func (i *instanceQueueWithObserver) PopFront() interface{} {
	obj := i.instanceQueueWithSubHealthAndEvictingRecord.PopFront() // 仅pop health实例
	if obj == nil {
		return nil
	}
	insElem, ok := obj.(*instanceElement)
	if !ok {
		return nil
	}
	delete(i.insAvailThdCount, insElem.instance.InstanceID)
	i.pubAvailTopicFunc(-len(insElem.threadMap))
	i.pubInUseTopicFunc(-(insElem.instance.ConcurrentNum - len(insElem.threadMap)))
	i.pubTotalTopicFunc(-insElem.instance.ConcurrentNum)
	return insElem
}

// PopBack -
func (i *instanceQueueWithObserver) PopBack() interface{} {
	obj := i.instanceQueueWithSubHealthAndEvictingRecord.PopBack() // 仅pop health实例
	if obj == nil {
		return nil
	}
	insElem, ok := obj.(*instanceElement)
	if !ok {
		return nil
	}
	delete(i.insAvailThdCount, insElem.instance.InstanceID)
	i.pubAvailTopicFunc(-len(insElem.threadMap))
	i.pubInUseTopicFunc(-(insElem.instance.ConcurrentNum - len(insElem.threadMap)))
	i.pubTotalTopicFunc(-insElem.instance.ConcurrentNum)
	return insElem
}

// PopSubHealth -
func (i *instanceQueueWithObserver) PopSubHealth() interface{} {
	obj := i.instanceQueueWithSubHealthAndEvictingRecord.PopSubHealth()
	if obj == nil {
		return nil
	}
	insElem, ok := obj.(*instanceElement)
	if !ok {
		return nil
	}
	// sub-health instance doesn't have availInsThd
	delete(i.insAvailThdCount, insElem.instance.InstanceID)
	i.pubInUseTopicFunc(-(insElem.instance.ConcurrentNum - len(insElem.threadMap)))
	i.pubTotalTopicFunc(-insElem.instance.ConcurrentNum)
	return insElem
}

// PushBack - pushback仅考虑queue中无实例场景
func (i *instanceQueueWithObserver) PushBack(obj interface{}) error {
	insElem, ok := obj.(*instanceElement)
	if !ok {
		return scheduler.ErrTypeConvertFail
	}
	if err := i.instanceQueueWithSubHealthAndEvictingRecord.PushBack(obj); err != nil {
		return err
	}

	i.insAvailThdCount[insElem.instance.InstanceID] = len(insElem.threadMap)
	switch insElem.instance.InstanceStatus.Code {
	case int32(constant.KernelInstanceStatusRunning):
		i.pubAvailTopicFunc(len(insElem.threadMap))
		i.pubTotalTopicFunc(insElem.instance.ConcurrentNum)
	case int32(constant.KernelInstanceStatusSubHealth):
		i.pubTotalTopicFunc(insElem.instance.ConcurrentNum)
	case int32(constant.KernelInstanceStatusEvicting):
	default:

	}
	return nil
}

// DelByID -
func (i *instanceQueueWithObserver) DelByID(objID string) error {
	if _, ok := i.evictingRecord[objID]; ok {
		delete(i.evictingRecord, objID)
		return nil
	}
	obj := i.instanceQueueWithSubHealthAndEvictingRecord.GetByID(objID)
	if obj == nil {
		return scheduler.ErrInsNotExist
	}
	insElem, ok := obj.(*instanceElement)
	if !ok {
		return scheduler.ErrTypeConvertFail
	}
	if err := i.instanceQueueWithSubHealthAndEvictingRecord.DelByID(objID); err != nil {
		return err
	}
	delete(i.insAvailThdCount, insElem.instance.InstanceID)
	switch insElem.instance.InstanceStatus.Code {
	case int32(constant.KernelInstanceStatusRunning):
		i.pubAvailTopicFunc(-len(insElem.threadMap))
		i.pubInUseTopicFunc(-(insElem.instance.ConcurrentNum - len(insElem.threadMap)))
		i.pubTotalTopicFunc(-insElem.instance.ConcurrentNum)
	case int32(constant.KernelInstanceStatusSubHealth):
		i.pubInUseTopicFunc(-(insElem.instance.ConcurrentNum - len(insElem.threadMap)))
		i.pubTotalTopicFunc(-insElem.instance.ConcurrentNum)

	// 忽略优雅退出实例的指标上报
	case int32(constant.KernelInstanceStatusEvicting):
	default:
	}
	return nil
}

// UpdateObjByID - updateObjByID考虑queue中有实例场景
func (i *instanceQueueWithObserver) UpdateObjByID(objID string, obj interface{}) error {
	insElem, ok := obj.(*instanceElement)
	if !ok {
		return scheduler.ErrTypeConvertFail
	}

	oldInstanceStatus := int32(constant.KernelInstanceStatusRunning)
	_, ok = i.subHealthRecord[objID]
	if ok {
		oldInstanceStatus = int32(constant.KernelInstanceStatusSubHealth)
	}
	_, ok = i.evictingRecord[objID]
	if ok {
		oldInstanceStatus = int32(constant.KernelInstanceStatusEvicting)
	}
	if err := i.instanceQueueWithSubHealthAndEvictingRecord.UpdateObjByID(objID, insElem); err != nil {
		return err
	}
	oldInsAvailThdCount, exist := i.insAvailThdCount[objID]
	if !exist {
		return scheduler.ErrInternal
	}
	i.insAvailThdCount[objID] = len(insElem.threadMap)

	switch oldInstanceStatus {
	case int32(constant.KernelInstanceStatusRunning):
		i.handleHealthInstanceUpdateMetrics(oldInsAvailThdCount, insElem)
	case int32(constant.KernelInstanceStatusSubHealth):
		i.handleSubHealthInstanceUpdateMetrics(oldInsAvailThdCount, insElem)

	// 处于优雅退出状态的实例，不会转换成health或者subhealth实例
	// 处于优雅退出状态的实例的状态变化，不用考虑指标变化和上报
	case int32(constant.KernelInstanceStatusEvicting):
	default:

	}
	return nil
}

func (i *instanceQueueWithObserver) handleHealthInstanceUpdateMetrics(oldInsAvailThdCount int,
	insElem *instanceElement) {
	availInsThdDiff := len(insElem.threadMap) - oldInsAvailThdCount
	switch insElem.instance.InstanceStatus.Code {
	case int32(constant.KernelInstanceStatusRunning): // health实例的申请租约
		i.pubInUseTopicFunc(-availInsThdDiff)
		i.pubAvailTopicFunc(availInsThdDiff)
	case int32(constant.KernelInstanceStatusSubHealth): // health实例转换成了subhealth实例
		//	i.pubInUseTopicFunc(-availInsThdDiff)     // 这个diff应该是0
		i.pubAvailTopicFunc(-len(insElem.threadMap)) // 这里暗示的是 new和old的可用租约数要一致
	case int32(constant.KernelInstanceStatusEvicting): // health实例转换成了evicting实例
		i.pubTotalTopicFunc(-insElem.instance.ConcurrentNum)
		i.pubAvailTopicFunc(-len(insElem.threadMap))
		i.pubInUseTopicFunc(-(insElem.instance.ConcurrentNum - len(insElem.threadMap)))
	default:

	}
}

func (i *instanceQueueWithObserver) handleSubHealthInstanceUpdateMetrics(oldInsAvailThdCount int,
	insElem *instanceElement) {
	availInsThdDiff := len(insElem.threadMap) - oldInsAvailThdCount
	switch insElem.instance.InstanceStatus.Code {
	case int32(constant.KernelInstanceStatusRunning): // subhealth实例转换成health实例了
		i.pubInUseTopicFunc(-availInsThdDiff)
		i.pubAvailTopicFunc(len(insElem.threadMap))
	case int32(constant.KernelInstanceStatusSubHealth): // subhealth实例重复收到subhealth事件，不用更新指标
	case int32(constant.KernelInstanceStatusEvicting): // subhealth实例转换成了evicting实例
		i.pubTotalTopicFunc(-insElem.instance.ConcurrentNum)
		i.pubInUseTopicFunc(-(insElem.instance.ConcurrentNum - len(insElem.threadMap)))
	default:

	}
}

type basicConcurrencyScheduler struct {
	funcSpec              *types.FunctionSpecification
	insAcqReqQueue        *requestqueue.InsAcqReqQueue
	leaseManager          lease.InstanceLeaseManager
	selfInstanceQueue     queue.Queue
	otherInstanceQueue    queue.Queue
	selfSubHealthRecord   map[string]*instanceElement
	otherSubHealthRecord  map[string]*instanceElement
	sessionManager        *sessionManager
	observers             map[scheduler.InstanceTopic][]*instanceObserver
	funcKeyWithRes        string
	concurrentNum         int
	isFuncOwner           bool
	stopped               bool
	leaseInterval         time.Duration
	sessionCtxIdleHandler func(*types.Instance)
	// newSessionExpireTimer 创建 session 过期计时器。生产用 time.NewTimer；测试注入可控
	// factory 避免全局 patch time.NewTimer（否则会干扰包内其他测试遗留的 lease goroutine）。
	newSessionExpireTimer func(d time.Duration) *time.Timer
	*sync.RWMutex
	*sync.Cond
	coldStartTraceMu    sync.Mutex
	coldStartTraceQueue []*coldStartTraceContext
}

type coldStartTraceContext struct {
	traceContext *types.TraceContext
	createdAt    time.Time
}

func newBasicConcurrencyScheduler(funcSpec *types.FunctionSpecification, resKey resspeckey.ResSpecKey,
	instanceType types.InstanceType, selfInstanceQueue queue.Queue,
	otherInstanceQueue queue.Queue) basicConcurrencyScheduler {
	leaseInterval := time.Duration(config.GlobalConfig.LeaseSpan) * time.Millisecond
	if leaseInterval < types.MinLeaseInterval {
		leaseInterval = types.MinLeaseInterval
	}
	mutex := &sync.RWMutex{}
	funcKeyWitRes := fmt.Sprintf("%s-%s", funcSpec.FuncKey, resKey.String())
	funcCacheKey := makeSessionCacheKey(funcSpec.FuncMetaData.Name, funcKeyWitRes)
	store := session.MakeStore(funcSpec.FuncKey)
	bcs := basicConcurrencyScheduler{
		funcSpec:              funcSpec,
		funcKeyWithRes:        funcKeyWitRes,
		leaseManager:          lease.NewGenericLeaseManager(funcKeyWitRes),
		selfSubHealthRecord:   make(map[string]*instanceElement, utils.DefaultMapSize),
		otherSubHealthRecord:  make(map[string]*instanceElement, utils.DefaultMapSize),
		sessionManager:        makeSessionManager(funcCacheKey, os.Getenv("HOST_IP"), instanceType, store),
		observers:             make(map[scheduler.InstanceTopic][]*instanceObserver, utils.DefaultMapSize),
		concurrentNum:         utils.GetConcurrentNum(funcSpec.InstanceMetaData.ConcurrentNum),
		leaseInterval:         leaseInterval,
		newSessionExpireTimer: time.NewTimer,
		RWMutex:               mutex,
		Cond:                  sync.NewCond(mutex),
		isFuncOwner:           selfregister.GlobalSchedulerProxy.IsFuncOwner(funcSpec.FuncKey),
		coldStartTraceQueue:   make([]*coldStartTraceContext, 0, utils.DefaultSliceSize),
	}
	bcs.sessionManager.setFuncOwner(bcs.isFuncOwner)
	bcs.createOtherInstanceQueue(otherInstanceQueue)
	bcs.createSelfInstanceQueue(selfInstanceQueue)
	return bcs
}

func (bcs *basicConcurrencyScheduler) recordColdStartTrace(traceID, traceParent string) {
	if traceID == "" && traceParent == "" {
		return
	}
	bcs.coldStartTraceMu.Lock()
	if len(bcs.coldStartTraceQueue) >= maxColdStartTraceQueue {
		bcs.popColdStartTraceLocked()
	}
	bcs.coldStartTraceQueue = append(bcs.coldStartTraceQueue, &coldStartTraceContext{
		traceContext: &types.TraceContext{
			TraceID:     traceID,
			TraceParent: traceParent,
		},
		createdAt: time.Now(),
	})
	bcs.coldStartTraceMu.Unlock()
}

func (bcs *basicConcurrencyScheduler) popColdStartTraceLocked() *coldStartTraceContext {
	if len(bcs.coldStartTraceQueue) == 0 {
		return nil
	}
	traceCtx := bcs.coldStartTraceQueue[0]
	last := len(bcs.coldStartTraceQueue) - 1
	copy(bcs.coldStartTraceQueue, bcs.coldStartTraceQueue[1:])
	bcs.coldStartTraceQueue[last] = nil
	bcs.coldStartTraceQueue = bcs.coldStartTraceQueue[:last]
	return traceCtx
}

// PopColdStartTrace returns the oldest non-expired request trace for the next cold start.
func (bcs *basicConcurrencyScheduler) PopColdStartTrace() *types.TraceContext {
	bcs.coldStartTraceMu.Lock()
	defer bcs.coldStartTraceMu.Unlock()
	expireBefore := time.Now().Add(-bcs.leaseInterval)
	for len(bcs.coldStartTraceQueue) > 0 {
		traceCtx := bcs.popColdStartTraceLocked()
		if traceCtx == nil || traceCtx.traceContext == nil {
			continue
		}
		if traceCtx.createdAt.Before(expireBefore) {
			continue
		}
		return traceCtx.traceContext
	}
	return nil
}

func (bcs *basicConcurrencyScheduler) createOtherInstanceQueue(instanceQueue queue.Queue) {
	bcs.otherInstanceQueue = &instanceQueueWithSubHealthAndEvictingRecord{
		instanceQueue:   instanceQueue,
		subHealthRecord: make(map[string]*instanceElement, utils.DefaultMapSize),
		evictingRecord:  make(map[string]*instanceElement, utils.DefaultMapSize),
	}
}

func (bcs *basicConcurrencyScheduler) createSelfInstanceQueue(instanceQueue queue.Queue) {
	InsQueWithSubHealth := instanceQueueWithSubHealthAndEvictingRecord{
		instanceQueue:   instanceQueue,
		subHealthRecord: make(map[string]*instanceElement, utils.DefaultMapSize),
		evictingRecord:  make(map[string]*instanceElement, utils.DefaultMapSize),
	}
	bcs.selfInstanceQueue = &instanceQueueWithObserver{
		instanceQueueWithSubHealthAndEvictingRecord: InsQueWithSubHealth,
		insAvailThdCount:  make(map[string]int, utils.DefaultMapSize),
		pubAvailTopicFunc: func(data int) { bcs.publishInsThdEvent(scheduler.AvailInsThdTopic, data) },
		pubInUseTopicFunc: func(data int) { bcs.publishInsThdEvent(scheduler.InUseInsThdTopic, data) },
		pubTotalTopicFunc: func(data int) { bcs.publishInsThdEvent(scheduler.TotalInsThdTopic, data) },
	}
}

func (bcs *basicConcurrencyScheduler) scheduleRequest(insAcqReq *types.InstanceAcquireRequest) (
	*types.InstanceAllocation, error) {
	return bcs.acquireWithSessionResolve(insAcqReq)
}

// acquireWithSessionResolve 是 scheduleRequest/AcquireInstance 共用的锁拆分实现：
//  0. 短路：noNeedQuerySession 返回 true（请求已预设 DesignateInstanceID，或不含 SessionID）时，
//     直接跳到 Phase 3 末段的 route + acquire，不查本地缓存也不查外部存储。
//  1. Phase 1（bcs 锁内）：peekLocalSession 查本地 sessionMap。命中→设 DesignateInstanceID。
//     纯内存查询，持锁无 I/O。
//  2. Phase 2（锁外）：本地 miss 时调 getSessionFromStore（singleflight 去重并发同 session）。
//     存储 I/O 不在 bcs 锁内，不阻塞同函数其他 acquire/release/租约回收。
//  3. Phase 3（bcs 锁内）：若 Phase 1/2 都 miss，applyStoreDesignate 重检本地（Phase 2 期间别的
//     goroutine 可能已绑定），仍 miss 才用 store 记录填 DesignateInstanceID + TTL/Concurrency；
//     之后 routeDesignateInstance 修正 self/other 队列，acquireInstanceInternal 实际获取实例。
//
// Phase 3 重检保证正确性：两个并发同 session acquire 都 miss→都 Get（singleflight 只实际查一次）
// →先重检的先绑，后重检的看到本地已命中直接复用，不重复绑。
func (bcs *basicConcurrencyScheduler) acquireWithSessionResolve(insAcqReq *types.InstanceAcquireRequest) (
	*types.InstanceAllocation, error) {
	designateInsHit := noNeedQuerySession(insAcqReq)
	var (
		sessionKey  string
		localHit    bool
		storeRecord *session.StoreRecord
	)
	if !designateInsHit {
		bcs.Lock()
		sessionKey = bcs.getSessionCacheKey(insAcqReq.InstanceSession.SessionID, insAcqReq.SessionCtxID)
		localHit = bcs.peekLocalSession(sessionKey, insAcqReq)
		bcs.Unlock()
		if !localHit {
			var err error
			storeRecord, err = bcs.sessionManager.getSessionFromStore(sessionKey)
			if err != nil {
				log.GetLogger().With(zap.Any("sessionID", sessionKey)).
					Debugf("get session from store failed, err: %v", err)
			}
		}
	}

	bcs.Lock()
	defer bcs.Unlock()
	if !designateInsHit && !localHit {
		bcs.applyStoreDesignate(insAcqReq, sessionKey, storeRecord)
	}
	useSelfInstance := bcs.isFuncOwner || insAcqReq.TrafficLimited
	useSelfInstance = bcs.routeDesignateInstance(insAcqReq, useSelfInstance)
	var (
		insAlloc *types.InstanceAllocation
		err      error
	)
	if useSelfInstance {
		insAlloc, err = bcs.acquireInstanceInternal(bcs.selfInstanceQueue, insAcqReq)
	} else {
		insAlloc, err = bcs.acquireInstanceInternal(bcs.otherInstanceQueue, insAcqReq)
	}
	return insAlloc, err
}

// noNeedQuerySession 判断请求是否需要走 session 解析路径。
// 返回 true 表示调用方已预设 DesignateInstanceID（指名请求某实例），或请求不含 SessionID
// （无 session 亲和性需求）。acquireWithSessionResolve 据此跳过本地缓存查询、外部存储查询、
// applyStoreDesignate 三步，直接进入 route + acquire。
func noNeedQuerySession(insAcqReq *types.InstanceAcquireRequest) bool {
	return insAcqReq.DesignateInstanceID != "" || len(insAcqReq.InstanceSession.SessionID) == 0
}

// peekLocalSession 查询本地 sessionMap；命中时把绑定实例的 InstanceID 写入
// insAcqReq.DesignateInstanceID 并打 Debug 日志。sessionKey 由调用方用 getSessionCacheKey
// 预先构造。调用方必须已持有 bcs.Lock（acquireWithSessionResolve 的 Phase 1 与
// applyStoreDesignate 的重检均满足此约束）。返回是否命中。
func (bcs *basicConcurrencyScheduler) peekLocalSession(sessionKey string,
	insAcqReq *types.InstanceAcquireRequest) bool {
	record, exist := bcs.sessionManager.getSession(sessionKey)
	if exist {
		insAcqReq.DesignateInstanceID = record.insElem.instance.InstanceID
		log.GetLogger().With(zap.Any("sessionID", sessionKey)).
			Debugf("get session from cache success, instanceID: %s", insAcqReq.DesignateInstanceID)
	}
	return exist
}

// applyStoreDesignate 在 bcs 锁内应用外部存储查询结果（Phase 3）。
// 先 peekLocalSession 重检本地（Phase 2 期间别的 goroutine 可能已绑定），命中则用本地实例直接返回；
// 仍 miss 才用 storeRecord 填 DesignateInstanceID + 用 store 的 Concurrency 回填请求
// （Concurrency 是结构性参数，绑定时确定后不可动态改，懒恢复须沿用原绑定的值；
// 与缓存命中路径对齐——缓存命中时复用既有 record.concurrency，请求的 Concurrency 被忽略）。
// TTL 不从 store 覆写：与缓存命中路径对齐——缓存命中时 acquireSessionThread 用请求的 TTL
// 更新 record.ttl；diff-write 保证 store 已是最近一次请求的 TTL，懒恢复时直接用请求的 TTL 即可，
// 避免用 store 的旧值覆盖客户端故意变更的新 TTL。storeRecord == nil（外部 miss 或异常）时
// fail-open，不填 DesignateInstanceID，按新 session 处理。
func (bcs *basicConcurrencyScheduler) applyStoreDesignate(insAcqReq *types.InstanceAcquireRequest,
	sessionKey string, storeRecord *session.StoreRecord) {
	logger := log.GetLogger().With(zap.Any("sessionID", sessionKey))
	if bcs.peekLocalSession(sessionKey, insAcqReq) {
		return
	}
	if storeRecord == nil {
		logger.Debugf("get session from store miss")
		return
	}
	insAcqReq.DesignateInstanceID = storeRecord.InstanceID
	insAcqReq.InstanceSession.Concurrency = storeRecord.Concurrency
	logger.Debugf("get session from store success, instanceID: %s", insAcqReq.DesignateInstanceID)
}

// routeDesignateInstance 在 DesignateInstanceID 已设置时，按实例所在队列修正 useSelfInstance。
// 懒恢复路径下 designate 实例可能在 self 或 other 队列（owner 崩溃后实例可能被重分配）。
// 若 designate 已从两个队列中删除（实例被删），保留原 useSelfInstance，使上层
// acquireDesignateInstance 返回 ErrInsNotExist 后能回退到 acquireSessionInstance 在原队列重新绑定新实例。
func (bcs *basicConcurrencyScheduler) routeDesignateInstance(insAcqReq *types.InstanceAcquireRequest,
	useSelfInstance bool) bool {
	if insAcqReq.DesignateInstanceID == "" {
		return useSelfInstance
	}
	if bcs.selfInstanceQueue.GetByID(insAcqReq.DesignateInstanceID) != nil {
		return true
	}
	if bcs.otherInstanceQueue.GetByID(insAcqReq.DesignateInstanceID) != nil {
		return false
	}
	return useSelfInstance
}

// GetInstanceNumber gets instance number inside instance queue
func (bcs *basicConcurrencyScheduler) GetInstanceNumber(onlySelf bool) int {
	bcs.RLock()
	insNum := bcs.selfInstanceQueue.Len()
	if !onlySelf {
		insNum += bcs.otherInstanceQueue.Len()
	}
	bcs.RUnlock()
	return insNum
}

// AcquireInstance acquires an instance
func (bcs *basicConcurrencyScheduler) AcquireInstance(insAcqReq *types.InstanceAcquireRequest) (
	*types.InstanceAllocation, error) {
	return bcs.acquireWithSessionResolve(insAcqReq)
}

func (bcs *basicConcurrencyScheduler) HandleFuncSpecUpdate(funcSpec *types.FunctionSpecification) {
	bcs.funcSpec = funcSpec
	bcs.handleFuncSpecUpdate(bcs.selfInstanceQueue, funcSpec)
	bcs.handleFuncSpecUpdate(bcs.otherInstanceQueue, funcSpec)
}

func (bcs *basicConcurrencyScheduler) handleFuncSpecUpdate(instanceQueue queue.Queue,
	funcSpec *types.FunctionSpecification) {
	needUpdate := make(map[string]*instanceElement)
	instanceQueue.Range(func(obj interface{}) bool {
		insElem, ok := obj.(*instanceElement)
		if !ok {
			return true
		}
		if insElem.instance.FuncSig != funcSpec.FuncMetaSignature && insElem.isNewInstance {
			insElem.isNewInstance = false
			needUpdate[insElem.instance.InstanceID] = insElem
		}
		if insElem.instance.FuncSig == funcSpec.FuncMetaSignature && !insElem.isNewInstance {
			insElem.isNewInstance = true
			needUpdate[insElem.instance.InstanceID] = insElem
		}
		isPriorityAZ := checkInstancePriorityAZ(insElem.instance, funcSpec)
		if insElem.isPriorityAZ != isPriorityAZ {
			insElem.isPriorityAZ = isPriorityAZ
			needUpdate[insElem.instance.InstanceID] = insElem
		}
		return true
	})
	for id, element := range needUpdate {
		if err := instanceQueue.UpdateObjByID(id, element); err != nil {
			log.GetLogger().Errorf("failed to update instance %s error %s", id, err.Error())
		}
	}
}

func (bcs *basicConcurrencyScheduler) acquireInstanceInternal(instanceQueue queue.Queue,
	insAcqReq *types.InstanceAcquireRequest) (*types.InstanceAllocation, error) {
	var (
		insAlloc *types.InstanceAllocation
		acqErr   error
	)
	if insAcqReq.DesignateInstanceID != "" {
		insAlloc, acqErr = bcs.acquireDesignateInstance(instanceQueue, insAcqReq)
		if acqErr == scheduler.ErrInsNotExist && len(insAcqReq.InstanceSession.SessionID) != 0 {
			insAcqReq.DesignateInstanceID = ""
			insAlloc, acqErr = bcs.acquireSessionInstance(instanceQueue, insAcqReq)
		}
	} else if len(insAcqReq.InstanceSession.SessionID) != 0 {
		insAlloc, acqErr = bcs.acquireSessionInstance(instanceQueue, insAcqReq)
	} else {
		insAlloc, acqErr = bcs.acquireDefaultInstance(instanceQueue, insAcqReq)
	}
	if acqErr != nil {
		return nil, acqErr
	}
	newLease, leaseErr := bcs.leaseManager.CreateInstanceLease(insAlloc, bcs.leaseInterval, func() {
		if err := bcs.ReleaseInstance(insAlloc); err != nil {
			log.GetLogger().Errorf("failed to release lease %s of instance %s for function %s error %s",
				insAlloc.AllocationID, insAlloc.Instance.InstanceID, bcs.funcKeyWithRes, err.Error())
		}
	})
	if leaseErr != nil {
		log.GetLogger().Errorf("failed to create lease of instance %s for function %s error %s",
			insAlloc.Instance.InstanceID, bcs.funcKeyWithRes, leaseErr.Error())
		if _, err := bcs.releaseInstanceInternal(instanceQueue, insAlloc); err != nil {
			log.GetLogger().Errorf("failed to release instance %s for function %s error %s",
				insAlloc.Instance.InstanceID, bcs.funcKeyWithRes, err.Error())
		}
		return nil, leaseErr
	}
	insAlloc.Lease = newLease
	return insAlloc, nil
}

func (bcs *basicConcurrencyScheduler) acquireDefaultInstance(instanceQueue queue.Queue,
	insAcqReq *types.InstanceAcquireRequest) (*types.InstanceAllocation, error) {
	log.GetLogger().Debugf("acquire default instance for function %s traceID %s", bcs.funcKeyWithRes,
		insAcqReq.TraceID)
	obj := instanceQueue.Front()
	if obj == nil {
		return nil, scheduler.ErrNoInsAvailable
	}
	insElem, ok := obj.(*instanceElement)
	if !ok {
		return nil, scheduler.ErrTypeConvertFail
	}
	return acquireInstanceThread(insAcqReq.DesignateThreadID, instanceQueue, insElem)
}

type releaseInstanceResult struct{}

func (bcs *basicConcurrencyScheduler) getSessionCacheKey(sessionID, sessionCtxID string) string {
	if !bcs.funcSpec.ExtendedMetaData.EnableSessionCtx {
		return sessionID
	}
	return types.JoinKey(sessionID, sessionCtxID)
}

func (bcs *basicConcurrencyScheduler) getRecordCacheKey(record *sessionRecord) string {
	return bcs.getSessionCacheKey(record.sessionID, record.sessionCtxID)
}

func (bcs *basicConcurrencyScheduler) acquireSessionInstance(instanceQueue queue.Queue,
	insAcqReq *types.InstanceAcquireRequest) (*types.InstanceAllocation, error) {
	log.GetLogger().Infof("acquire session instance for function %s session %+v traceID %s",
		bcs.funcKeyWithRes, insAcqReq.InstanceSession, insAcqReq.TraceID)

	if bcs.isAgentSession(insAcqReq) {
		insAcqReq.InstanceSession.SessionTTL = 0
		insAcqReq.InstanceSession.Concurrency = 1
		log.GetLogger().Infof("AI Agent session processing for function %s, session %s, TTL=0, Concurrency=1",
			bcs.funcKeyWithRes, insAcqReq.InstanceSession.SessionID)
	}

	if insAcqReq.InstanceSession.Concurrency > bcs.concurrentNum {
		return nil, scheduler.ErrInvalidSession
	}
	if !bcs.isSessionExist(instanceQueue, insAcqReq) {
		var (
			ok      bool
			insElem *instanceElement
		)
		if instanceQueue.SortedRange(func(obj interface{}) bool {
			insElem, ok = obj.(*instanceElement)
			if !ok {
				return true
			}
			if insElem.instance.InstanceStatus.Code != int32(constant.KernelInstanceStatusRunning) {
				return true
			}
			// When EnableSessionCtx, only bind to instances whose SessionCtxID
			// matches the request's. This prevents binding to an instance with a
			// different SessionCtxID, which would cause acquireSessionThread to
			// fail with ErrInternal due to cache key mismatch.
			if bcs.funcSpec.ExtendedMetaData.EnableSessionCtx && insAcqReq.SessionCtxID != "" {
				instanceCtxID := ""
				if insElem.instance.SessionCtxID != nil {
					instanceCtxID = *insElem.instance.SessionCtxID
				}
				if instanceCtxID != insAcqReq.SessionCtxID {
					return true
				}
			}
			if insAcqReq.InstanceSession.Concurrency == -1 {
				// Full-concurrency session must monopolize the whole instance, so only fully idle instances
				// are eligible for binding.
				return len(insElem.threadMap) != insElem.instance.ConcurrentNum
			}
			if len(insElem.threadMap) >= insAcqReq.InstanceSession.Concurrency {
				return false
			}
			return true
		}) {
			return nil, scheduler.ErrNoInsAvailable
		}
		record, err := bcs.bindThdWithSession(instanceQueue, insElem, insAcqReq.InstanceSession)
		if err != nil {
			return nil, err
		}
		bcs.sessionManager.addSession(bcs.getRecordCacheKey(record), record)
	}
	insAlloc, acqErr := bcs.acquireSessionThread(insAcqReq.DesignateThreadID, insAcqReq.InstanceSession,
		insAcqReq.SessionCtxID)
	// if acqErr equals ErrNoInsThdAvail, try getting thread without session from the same instance
	if acqErr != ErrNoInsThdAvail {
		return insAlloc, acqErr
	}
	record, ok := bcs.sessionManager.getSession(bcs.getSessionCacheKey(insAcqReq.InstanceSession.SessionID,
		insAcqReq.SessionCtxID))
	if !ok {
		return insAlloc, acqErr
	}
	return bcs.acquireInstanceWithOverAcqSession(instanceQueue, record, insAcqReq)
}

func (bcs *basicConcurrencyScheduler) isSessionExist(instanceQueue queue.Queue,
	insAcqReq *types.InstanceAcquireRequest) bool {
	cacheKey := bcs.getSessionCacheKey(insAcqReq.InstanceSession.SessionID, insAcqReq.SessionCtxID)
	record, exist := bcs.sessionManager.getSession(cacheKey)
	if exist {
		// 缓存中有session但是instance已经被删除时，更新缓存
		obj := instanceQueue.GetByID(record.insElem.instance.InstanceID)
		if obj == nil {
			bcs.sessionManager.delSession(cacheKey)
			exist = false
		}
	}
	return exist
}

func (bcs *basicConcurrencyScheduler) acquireDesignateInstance(instanceQueue queue.Queue,
	insAcqReq *types.InstanceAcquireRequest) (*types.InstanceAllocation, error) {
	log.GetLogger().Infof("acquire designate instance %s for function %s session %+v traceID %s",
		insAcqReq.DesignateInstanceID, bcs.funcKeyWithRes, insAcqReq.InstanceSession, insAcqReq.TraceID)
	var (
		insAlloc *types.InstanceAllocation
		acqErr   error
	)
	obj := instanceQueue.GetByID(insAcqReq.DesignateInstanceID)
	if obj == nil {
		if len(insAcqReq.InstanceSession.SessionID) != 0 {
			bcs.sessionManager.delSession(bcs.getSessionCacheKey(insAcqReq.InstanceSession.SessionID,
				insAcqReq.SessionCtxID))
		}
		return nil, scheduler.ErrInsNotExist
	}
	insElem, ok := obj.(*instanceElement)
	if !ok {
		return nil, scheduler.ErrTypeConvertFail
	}
	if insElem.instance.InstanceStatus.Code == int32(constant.KernelInstanceStatusSubHealth) {
		return nil, scheduler.ErrInsSubHealthy
	}
	if len(insAcqReq.InstanceSession.SessionID) != 0 {
		// sessionCtx mismatch: the designated instance's SessionCtxID doesn't match
		// the request's. Delete the stale store record and fall through to new
		// session binding (ErrInsNotExist triggers acquireSessionInstance fallback
		// in acquireInstanceInternal, which filters by sessionCtx).
		if bcs.funcSpec.ExtendedMetaData.EnableSessionCtx && insAcqReq.SessionCtxID != "" {
			instanceCtxID := ""
			if insElem.instance.SessionCtxID != nil {
				instanceCtxID = *insElem.instance.SessionCtxID
			}
			if instanceCtxID != insAcqReq.SessionCtxID {
				log.GetLogger().With(zap.Any("sessionID", insAcqReq.InstanceSession.SessionID),
					zap.Any("funcKey", bcs.funcKeyWithRes)).
					Infof("acquire designate instance %s sessionCtx mismatch: instance=%s, request=%s"+
						", delete stale record and redispatch",
						insAcqReq.DesignateInstanceID, instanceCtxID, insAcqReq.SessionCtxID)
				bcs.sessionManager.delSession(bcs.getSessionCacheKey(insAcqReq.InstanceSession.SessionID,
					insAcqReq.SessionCtxID))
				return nil, scheduler.ErrInsNotExist
			}
		}
		record, ok := bcs.sessionManager.getSession(bcs.getSessionCacheKey(insAcqReq.InstanceSession.SessionID,
			insAcqReq.SessionCtxID))
		// 指定的实例与session绑定的实例冲突，以指定实例为准
		if ok && record.insElem.instance.InstanceID != insAcqReq.DesignateInstanceID {
			insAcqReq.InstanceSession.SessionID = ""
		}
		if !ok {
			record, acqErr = bcs.bindThdWithSession(instanceQueue, insElem, insAcqReq.InstanceSession)
			if acqErr != nil {
				return nil, scheduler.ErrInternal
			}
			bcs.sessionManager.addSession(bcs.getRecordCacheKey(record), record)
		}
		insAlloc, acqErr = bcs.acquireSessionThread(insAcqReq.DesignateThreadID,
			insAcqReq.InstanceSession, insAcqReq.SessionCtxID)
		// if acqErr equals ErrNoInsThdAvail, try getting thread without session from the same instance
		if acqErr == ErrNoInsThdAvail {
			return bcs.acquireInstanceWithOverAcqSession(instanceQueue, record, insAcqReq)
		}
		return insAlloc, acqErr
	}

	// 对于evicting实例，仅供绑定会话的请求使用，否则认为改实例不可用
	if insElem.instance.InstanceStatus.Code != int32(constant.KernelInstanceStatusRunning) {
		return nil, scheduler.ErrDesignateInsNotAvailable
	}
	return acquireInstanceThread(insAcqReq.DesignateThreadID, instanceQueue, insElem)
}

func (bcs *basicConcurrencyScheduler) acquireInstanceWithOverAcqSession(instanceQueue queue.Queue,
	record *sessionRecord, insAcqReq *types.InstanceAcquireRequest) (*types.InstanceAllocation, error) {
	if bcs.isAgentSession(insAcqReq) {
		if len(record.overAcqThdMap) >= 1 {
			log.GetLogger().Warnf("AI Agent session %s over-acquire limit exceeded (max 1), function %s",
				insAcqReq.InstanceSession.SessionID, bcs.funcKeyWithRes)
			return nil, scheduler.ErrOverAcqLimitExceeded
		}
		return bcs.createOverAcqThread(record, insAcqReq)
	}
	insAlloc, acqErr := acquireInstanceThread(insAcqReq.DesignateThreadID, instanceQueue, record.insElem)
	if acqErr == nil {
		record.overAcqThdMap[insAlloc.AllocationID] = struct{}{}
		insAlloc.SessionInfo = types.SessionInfo{
			SessionID:  insAcqReq.InstanceSession.SessionID,
			SessionCtx: record.ctx,
		}
		log.GetLogger().Infof("Session %s over-acquired thread %s from pool, function %s",
			insAcqReq.InstanceSession.SessionID, insAlloc.AllocationID, bcs.funcKeyWithRes)
	}
	return insAlloc, acqErr
}

func acquireInstanceThread(designateThreadID string, insQue queue.Queue,
	insElem *instanceElement) (*types.InstanceAllocation, error) {
	// 这里如果指定了租约id，是需要可以超发的
	if len(insElem.threadMap) == 0 && designateThreadID == "" {
		return nil, scheduler.ErrNoInsAvailable
	}
	// 无论有没有指定租约id，都需要从map中取一个租约出来，如果map为空，也不会报错
	threadID := insElem.GetThreadFromThreadMap()
	if designateThreadID != "" {
		threadID = designateThreadID
	}
	err := insQue.UpdateObjByID(insElem.instance.InstanceID, insElem)
	if err != nil {
		log.GetLogger().Errorf("failed to update instance %s in queue error %s", insElem.instance.InstanceID,
			err.Error())
		return nil, err
	}
	insAlloc := &types.InstanceAllocation{
		Instance:     insElem.instance,
		AllocationID: threadID,
	}
	metrics.OnAcquireLease(insAlloc)
	return insAlloc, nil
}

func (bcs *basicConcurrencyScheduler) bindThdWithSession(insQue queue.Queue, insElem *instanceElement,
	session commonTypes.InstanceSessionConfig) (*sessionRecord, error) {
	requestedConcurrency := session.Concurrency
	if session.Concurrency == -1 {
		// Reject binding when the instance is not fully idle.
		if len(insElem.threadMap) != insElem.instance.ConcurrentNum {
			return nil, scheduler.ErrNoInsAvailable
		}
		session.Concurrency = insElem.instance.ConcurrentNum
		log.GetLogger().Debugf("session %s uses full concurrency mode, binding %d threads for function %s",
			session.SessionID, session.Concurrency, bcs.funcKeyWithRes)
	}
	if len(insElem.threadMap) < session.Concurrency {
		return nil, scheduler.ErrNoInsAvailable
	}
	ctx, cancelFunc := context.WithCancel(context.TODO())
	record := &sessionRecord{
		ctx:            ctx,
		sessionID:      session.SessionID,
		sessionCtxID:   "",
		ttl:            time.Duration(session.SessionTTL) * time.Second,
		availThdMap:    make(map[string]struct{}, utils.DefaultMapSize),
		allocThdMap:    make(map[string]struct{}, utils.DefaultMapSize),
		overAcqThdMap:  make(map[string]struct{}, utils.DefaultMapSize),
		concurrency:    session.Concurrency,
		expireCancelCh: make(chan struct{}, 1),
		cancelFunc:     cancelFunc,
		insElem:        insElem,
	}
	if bcs.funcSpec.ExtendedMetaData.EnableSessionCtx && insElem.instance.SessionCtxID != nil {
		record.sessionCtxID = *insElem.instance.SessionCtxID
	}
	for i := 0; i < session.Concurrency; i++ {
		threadID := insElem.GetThreadFromThreadMap()
		record.PutThreadToAllocThdMap(threadID)
		// there must be no error.
		if err := record.MarkThreadAsAvailable(threadID); err != nil {
			log.GetLogger().Errorf("acquire thread failed, skip")
		}
	}
	err := insQue.UpdateObjByID(insElem.instance.InstanceID, insElem)
	if err != nil {
		log.GetLogger().Errorf("failed to update instance %s during session binding of function %s error %s",
			insElem.instance.InstanceID, bcs.funcKeyWithRes, err.Error())
		return nil, err
	}
	for threadId, _ := range record.allocThdMap {
		insAlloc := &types.InstanceAllocation{
			Instance:     insElem.instance,
			AllocationID: threadId,
		}
		metrics.OnAcquireLease(insAlloc)
	}
	log.GetLogger().Infof("bind session %s with instance %s for function %s, "+
		"requestedConcurrency=%d boundConcurrency=%d, instanceConcurrency=%d availableAfterBind=%d", record.sessionID,
		insElem.instance.InstanceID, bcs.funcKeyWithRes, requestedConcurrency, record.concurrency,
		insElem.instance.ConcurrentNum, len(insElem.threadMap))
	return record, nil
}

func (bcs *basicConcurrencyScheduler) acquireSessionThread(designateThreadID string,
	sessionConfig commonTypes.InstanceSessionConfig, sessionCtxID string) (
	*types.InstanceAllocation, error) {
	record, exist := bcs.sessionManager.getSession(bcs.getSessionCacheKey(sessionConfig.SessionID, sessionCtxID))
	if !exist {
		log.GetLogger().Errorf("session %s is not bound with instance for function %s", sessionConfig.SessionID,
			bcs.funcKeyWithRes)
		return nil, scheduler.ErrInternal
	}
	expiring, _ := record.expiring.Load().(bool)
	if expiring {
		select {
		case record.expireCancelCh <- struct{}{}:
		default:
		}
	}
	// TTL 变化时差量写外部存储：先 snapshot oldTTL，赋值后比较，不同才异步入队 Save，
	// 避免稳态下每次 acquire 都写外部存储。saveSessionToStore 异步入队，不阻塞 acquire。
	oldTTL := record.ttl
	record.ttl = time.Duration(sessionConfig.SessionTTL) * time.Second
	if record.ttl != oldTTL {
		bcs.sessionManager.saveSessionToStore(bcs.getRecordCacheKey(record), record)
	}
	if len(record.availThdMap) == 0 {
		return nil, ErrNoInsThdAvail
	}
	// 指定租约id时，需要替换allocThdMap中的id
	threadID, err := record.GetOrReplaceDesignateThreadFromAvailThdMap(designateThreadID)
	if err != nil {
		log.GetLogger().Errorf("failed to acquire designate thread id %s, err %s", designateThreadID, err.Error())
		return nil, scheduler.ErrInternal
	}
	return &types.InstanceAllocation{
		Instance: record.insElem.instance.Copy(),
		SessionInfo: types.SessionInfo{
			SessionID:  sessionConfig.SessionID,
			SessionCtx: record.ctx,
		},
		AllocationID: threadID,
	}, nil
}

// ReleaseInstance releases an instance
func (bcs *basicConcurrencyScheduler) ReleaseInstance(insAlloc *types.InstanceAllocation) error {
	bcs.Lock()
	useSelfInstance := bcs.checkSelfInstance(insAlloc.Instance)
	var err error
	if useSelfInstance {
		_, err = bcs.releaseInstanceInternal(bcs.selfInstanceQueue, insAlloc)
	} else {
		_, err = bcs.releaseInstanceInternal(bcs.otherInstanceQueue, insAlloc)
	}
	bcs.Unlock()
	return err
}

func (bcs *basicConcurrencyScheduler) releaseInstanceInternal(instanceQueue queue.Queue,
	insAlloc *types.InstanceAllocation) (releaseInstanceResult, error) {
	instance := insAlloc.Instance
	obj := instanceQueue.GetByID(instance.InstanceID)
	if obj == nil {
		return releaseInstanceResult{}, scheduler.ErrInsNotExist
	}
	var ok bool
	insElem, ok := obj.(*instanceElement)
	if !ok {
		return releaseInstanceResult{}, scheduler.ErrTypeConvertFail
	}
	releaseInSession := false
	if len(insAlloc.SessionInfo.SessionID) != 0 {
		// not all insAlloc with session comes from session record, handle release of this type of insAlloc below like
		// normal insAlloc
		err := bcs.releaseInstanceThreadWithSession(instanceQueue, insElem, insAlloc)
		if err == nil {
			releaseInSession = true
		} else if err != ErrInsThdNotExist {
			return releaseInstanceResult{}, err
		}
	}
	if !releaseInSession {
		insElem.PutThreadToThreadMap(insAlloc.AllocationID)
	}
	err := instanceQueue.UpdateObjByID(instance.InstanceID, insElem)
	if err != nil {
		log.GetLogger().Errorf("failed to update instance %s during allocation release for function %s error %s",
			insAlloc.Instance.InstanceID, bcs.funcKeyWithRes, err.Error())
		return releaseInstanceResult{}, err
	}
	if !releaseInSession {
		metrics.OnReleaseLease(insAlloc)
	}
	return releaseInstanceResult{}, nil
}

func (bcs *basicConcurrencyScheduler) releaseInstanceThreadWithSession(insQue queue.Queue, insElem *instanceElement,
	insAlloc *types.InstanceAllocation) error {
	log.GetLogger().Infof("start to unbind session %s with thread %s for function %s", insAlloc.SessionInfo.SessionID,
		insAlloc.AllocationID, bcs.funcKeyWithRes)
	sessionCtxID := ""
	if bcs.funcSpec.ExtendedMetaData.EnableSessionCtx && insAlloc.Instance.SessionCtxID != nil {
		sessionCtxID = *insAlloc.Instance.SessionCtxID
	}
	record, exist := bcs.sessionManager.getSession(bcs.getSessionCacheKey(insAlloc.SessionInfo.SessionID,
		sessionCtxID))
	if !exist {
		log.GetLogger().Errorf("session %s is not bound with instance %s for function %s",
			insAlloc.SessionInfo.SessionID, insElem.instance.InstanceID, bcs.funcKeyWithRes)
		return scheduler.ErrInternal
	}
	if _, ok := record.overAcqThdMap[insAlloc.AllocationID]; ok {
		log.GetLogger().Debugf("%s is over acquire thd for session %s, function %s", insAlloc.AllocationID,
			insAlloc.SessionInfo.SessionID, bcs.funcKeyWithRes)

		delete(record.overAcqThdMap, insAlloc.AllocationID)
		bcs.startUnbindInstanceSession(insQue, record)
		// overAcq的租约不属于这个session，因此需要返回ErrInsThdNotExist，但是如果是Agent Session，
		// 则需要返回nil，因为Agent Session的overAcq租约是属于session的
		if !bcs.funcSpec.ExtendedMetaData.EnableAgentSession {
			return ErrInsThdNotExist
		} else {
			return nil
		}
	}
	err := record.MarkThreadAsAvailable(insAlloc.AllocationID)
	if err != nil {
		log.GetLogger().Warnf("put thread to availthdmap failed, err %s, func %s", err.Error(), bcs.funcKeyWithRes)
		return ErrInsThdNotExist
	}
	bcs.startUnbindInstanceSession(insQue, record)
	return nil
}

func (bcs *basicConcurrencyScheduler) startUnbindInstanceSession(insQue queue.Queue, record *sessionRecord) {
	expiring, _ := record.expiring.Load().(bool)
	if len(record.overAcqThdMap) == 0 && len(record.availThdMap) == len(record.allocThdMap) && !expiring {
		record.expiring.Store(true)
		record.timer = bcs.newSessionExpireTimer(record.ttl)
		go bcs.unbindInstanceSession(insQue, record)
	}
}

func (bcs *basicConcurrencyScheduler) unbindInstanceSession(insQue queue.Queue, record *sessionRecord) {
	logger := log.GetLogger().With(zap.Any("sessionID", record.sessionID))
	select {
	case <-record.timer.C:
		bcs.L.Lock()
		if len(record.availThdMap) != len(record.allocThdMap) || len(record.overAcqThdMap) != 0 {
			<-record.expireCancelCh
			logger.Infof("avail thd has been acquired, expire canceled")
			record.timer.Stop()
			record.expiring.Store(false)
			bcs.L.Unlock()
			return
		}
		record.cancelFunc()
		if record.insElem == nil {
			logger.Infof("session has not bind by instance")
			return
		}
		if insQue.GetByID(record.insElem.instance.InstanceID) != nil {
			for threadID, _ := range record.allocThdMap {
				record.insElem.PutThreadToThreadMap(threadID)
				insAlloc := &types.InstanceAllocation{
					Instance:     record.insElem.instance,
					AllocationID: threadID,
				}
				metrics.OnReleaseLease(insAlloc)
			}
		} else {
			logger.Warnf("instance %s has been deleted before session released", record.insElem.instance.InstanceID)
		}
		bcs.sessionManager.delSession(bcs.getRecordCacheKey(record))
		if err := insQue.UpdateObjByID(record.insElem.instance.InstanceID, record.insElem); err != nil {
			logger.Errorf("failed to update instance %s during unbinding for function %s error %s",
				record.insElem.instance.InstanceID, bcs.funcKeyWithRes, err.Error())
		}
		bcs.L.Unlock()
		logger.Infof("unbind session with instance %s for function %s", record.insElem.instance.InstanceID,
			bcs.funcKeyWithRes)
	case <-record.expireCancelCh:
		// set lock here may cause deadlock because multiple acquire requests of a same session may trigger this
		// case many times
		logger.Infof("session expire canceled")
		record.timer.Stop()
		record.expiring.Store(false)
	}
}

// popInstanceElement pops an instance for scale down, use condition lock to wait for creating instances which already
// be processing by kernel to be enqueued
func (bcs *basicConcurrencyScheduler) popInstanceElement(popDirection popDirection,
	shouldPop func(*instanceElement) bool, wait bool) *instanceElement {
	bcs.L.Lock()
	defer bcs.L.Unlock()
	if wait && bcs.selfInstanceQueue.Len() == 0 {
		bcs.Wait()
	}
	instanceQueue, ok := bcs.selfInstanceQueue.(*instanceQueueWithObserver)
	if !ok {
		return nil
	}
	var obj interface{}
	if obj = instanceQueue.PopSubHealth(); obj != nil {
		insElem, ok := obj.(*instanceElement)
		if !ok {
			return nil
		}
		return insElem
	}
	if popDirection == forward {
		obj = bcs.selfInstanceQueue.Front()
	} else {
		obj = bcs.selfInstanceQueue.Back()
	}
	if obj == nil {
		return nil
	}
	insElem, ok := obj.(*instanceElement)
	if !ok {
		return nil
	}
	if shouldPop != nil && !shouldPop(insElem) {
		return nil
	}
	if popDirection == forward {
		bcs.selfInstanceQueue.PopFront()
	} else {
		bcs.selfInstanceQueue.PopBack()
	}
	return insElem
}

// AddInstance adds an instance to instanceScheduler
func (bcs *basicConcurrencyScheduler) AddInstance(instance *types.Instance) error {
	bcs.Lock()
	defer bcs.Unlock()
	isSelfInstance := bcs.checkSelfInstance(instance)
	var (
		err error
	)
	isNewInstance := true
	if instance.FuncSig != bcs.funcSpec.FuncMetaSignature {
		isNewInstance = false
	}
	insElem := &instanceElement{
		instance:      instance,
		isNewInstance: isNewInstance,
		isPriorityAZ:  checkInstancePriorityAZ(instance, bcs.funcSpec),
	}
	insElem.initThreadMap()
	if isSelfInstance {
		err = bcs.selfInstanceQueue.PushBack(insElem)
	} else {
		err = bcs.otherInstanceQueue.PushBack(insElem)
	}
	if err != nil {
		return err
	}
	// Wake popInstanceElement waiters: they block on bcs.Wait() until a creating instance is enqueued,
	// and nothing else in the scheduler signals this Cond.
	bcs.Broadcast()
	return err
}

// DelInstance deletes an instance from instanceScheduler
func (bcs *basicConcurrencyScheduler) DelInstance(instance *types.Instance) error {
	bcs.Lock()
	defer bcs.Unlock()
	isSelfInstance := bcs.checkSelfInstance(instance)
	var (
		err error
	)
	if isSelfInstance {
		err = bcs.selfInstanceQueue.DelByID(instance.InstanceID)
	} else {
		err = bcs.otherInstanceQueue.DelByID(instance.InstanceID)
	}
	bcs.leaseManager.HandleInstanceDelete(instance)
	if err != nil {
		return err
	}
	return err
}

// SignalAllInstances sends signal to all instances
func (bcs *basicConcurrencyScheduler) SignalAllInstances(signalFunc scheduler.SignalInstanceFunc) {
	bcs.RLock()
	bcs.selfInstanceQueue.Range(func(obj interface{}) bool {
		insElem, ok := obj.(*instanceElement)
		if !ok {
			return true
		}
		signalFunc(insElem.instance)
		return true
	})
	bcs.otherInstanceQueue.Range(func(obj interface{}) bool {
		insElem, ok := obj.(*instanceElement)
		if !ok {
			return true
		}
		signalFunc(insElem.instance)
		return true
	})
	bcs.RUnlock()
}

// HandleInstanceUpdate handles instance update comes from ETCD, it's worth noting that this method will also handle
// instance recover from scheduler state
func (bcs *basicConcurrencyScheduler) HandleInstanceUpdate(instance *types.Instance) {
	logger := log.GetLogger().With(zap.Any("funcKey", bcs.funcKeyWithRes), zap.Any("instance", instance.InstanceID),
		zap.Any("instanceStatus", instance.InstanceStatus.Code))
	isSelfInstance := bcs.checkSelfInstance(instance)
	logger.Infof("handle instance update isSelfInstance %t", isSelfInstance)
	bcs.Lock()
	defer bcs.Unlock()
	var instanceQueue queue.Queue
	if isSelfInstance {
		instanceQueue = bcs.selfInstanceQueue
	} else {
		instanceQueue = bcs.otherInstanceQueue
	}
	isNewInstance := true
	if instance.FuncSig != bcs.funcSpec.FuncMetaSignature {
		isNewInstance = false
	}
	obj := instanceQueue.GetByID(instance.InstanceID)
	if obj == nil {
		signalmanager.GetSignalManager().SignalInstance(instance, constant.KillSignalAliasUpdate)
		signalmanager.GetSignalManager().SignalInstance(instance, constant.KillSignalFaaSSchedulerUpdate)
		insElem := &instanceElement{
			instance:      instance,
			isNewInstance: isNewInstance,
			isPriorityAZ:  checkInstancePriorityAZ(instance, bcs.funcSpec),
		}
		insElem.initThreadMap()
		if err := instanceQueue.PushBack(insElem); err != nil {
			logger.Errorf("failed to add new instance with status %+v", instance.InstanceStatus)
			return
		}
	} else {
		insElem, ok := obj.(*instanceElement)
		if !ok {
			logger.Errorf("can't convert object to insQueElement type")
			return
		}
		insElem.instance = instance
		insElem.isNewInstance = isNewInstance
		insElem.isPriorityAZ = checkInstancePriorityAZ(instance, bcs.funcSpec)
		if err := instanceQueue.UpdateObjByID(instance.InstanceID, insElem); err != nil {
			logger.Errorf("failed to update instance %s with status %+v", instance.InstanceID, instance.InstanceStatus)
			return
		}
	}
	logger.Infof("handle instance update success")
}

// IsFuncOwner -
func (bcs *basicConcurrencyScheduler) IsFuncOwner() bool {
	bcs.RLock()
	isFuncOwner := bcs.isFuncOwner
	bcs.RUnlock()
	return isFuncOwner
}

func (bcs *basicConcurrencyScheduler) checkSelfInstance(instance *types.Instance) bool {
	return bcs.checkSelfInstanceInternal(instance)
}

func (bcs *basicConcurrencyScheduler) checkSelfInstanceInternal(instance *types.Instance) bool {
	if instance.Permanent {
		if instance.CreateSchedulerID == selfregister.GetSchedulerProxyName() {
			return true
		}
		funcOwnerExist := selfregister.GlobalSchedulerProxy.Contains(instance.CreateSchedulerID)
		return !funcOwnerExist && bcs.isFuncOwner
	}
	return bcs.isFuncOwner
}

func (bcs *basicConcurrencyScheduler) selectInstanceQueue(isSelfInstance bool) (queue.Queue,
	map[string]*instanceElement) {
	if isSelfInstance {
		return bcs.selfInstanceQueue, bcs.selfSubHealthRecord
	}
	return bcs.otherInstanceQueue, bcs.otherSubHealthRecord
}

// CleanExternalSessionRecords 删除本 scheduler 在外部存储的 per-session 记录。
// 仅 queue 彻底销毁时（函数删除/resKey 下线）由 ScaledInstanceQueue.Destroy 调用，且
// 必须在 Destroy（停 worker）之后调——Stop 已排空队列并退出 worker，同步 Delete 不会
// 与 in-flight Save 竞争。scheduler 重建场景（oldInstanceScheduler.Destroy 直接调，不经过
// queue.Destroy）不会触发，保留旧记录供新 scheduler 懒恢复。
func (bcs *basicConcurrencyScheduler) CleanExternalSessionRecords() {
	bcs.sessionManager.cleanExternalRecords()
}

// Destroy destroys instanceScheduler。不清理外部存储记录——清理由
// CleanExternalSessionRecords 在 queue 销毁路径显式触发（且必须在本调用之后）。
// Destroy 本身只同步停 worker（排空 + 等待退出）+ 清租约。
func (bcs *basicConcurrencyScheduler) Destroy() {
	bcs.Lock()
	bcs.stopped = true
	bcs.Unlock()
	bcs.sessionManager.stopAndClean()
	bcs.leaseManager.CleanAllLeases()
}

// publishInsThdEvent will notify observers of specific topic of instance
func (bcs *basicConcurrencyScheduler) publishInsThdEvent(topic scheduler.InstanceTopic, data interface{}) {
	if bcs.stopped {
		return
	}
	for _, observer := range bcs.observers[topic] {
		observer.callback(data)
	}
}

// addObservers will add observer of instance scaledInsQue
func (bcs *basicConcurrencyScheduler) addObservers(topic scheduler.InstanceTopic, callback func(interface{})) {
	topicObservers, exist := bcs.observers[topic]
	if !exist {
		topicObservers = make([]*instanceObserver, 0, utils.DefaultSliceSize)
		bcs.observers[topic] = topicObservers
	}
	bcs.observers[topic] = append(topicObservers, &instanceObserver{
		callback: callback,
	})
}

// HandleFuncOwnerUpdate will reset funcOwner and reassign instances if necessary
func (bcs *basicConcurrencyScheduler) HandleFuncOwnerUpdate(isFuncOwner bool) {
	logger := log.GetLogger().With(zap.Any("funcKey", bcs.funcKeyWithRes), zap.Any("isFuncOwner", isFuncOwner))
	logger.Infof("handle funcOwner update")
	bcs.Lock()
	defer bcs.Unlock()
	var (
		becomeOwner bool
		resignOwner bool
		srcQueue    queue.Queue
		dstQueue    queue.Queue
	)
	isOwnerBefore := bcs.isFuncOwner
	bcs.isFuncOwner = isFuncOwner
	bcs.sessionManager.setFuncOwner(isFuncOwner)
	if !isOwnerBefore && isFuncOwner {
		becomeOwner = true
		srcQueue = bcs.otherInstanceQueue
		dstQueue = bcs.selfInstanceQueue
	} else if isOwnerBefore && !isFuncOwner {
		resignOwner = true
		srcQueue = bcs.selfInstanceQueue
		dstQueue = bcs.otherInstanceQueue
	} else {
		logger.Warnf("funcOwner of function in this scheduler %s remains %t, no need to reassign instance",
			selfregister.SelfInstanceID, bcs.isFuncOwner)
		return
	}

	reassignList := bcs.reassignQueues(srcQueue, dstQueue, becomeOwner, resignOwner, logger)
	logger.Infof("funcOwner of function in this scheduler %s changes, succeed to reassign instances %+v",
		selfregister.SelfInstanceID, reassignList)
}

func (bcs *basicConcurrencyScheduler) reassignQueues(srcQueue queue.Queue, dstQueue queue.Queue,
	becomeOwner bool, resignOwner bool, logger api.FormatLogger) []string {
	reassignList := make([]string, 0, utils.DefaultSliceSize)
	srcQueue.Range(func(obj interface{}) bool {
		insElem, ok := obj.(*instanceElement)
		if !ok {
			return true
		}
		// evicting实例self和other都放
		if insElem.instance.InstanceStatus.Code == int32(constant.KernelInstanceStatusEvicting) {
			dstQueue.PushBack(insElem)
			return true
		}

		// isFuncOwner is set before calling this method, checkSelfInstanceInternal will work under new ownership
		if (becomeOwner && !bcs.checkSelfInstanceInternal(insElem.instance)) ||
			(resignOwner && bcs.checkSelfInstanceInternal(insElem.instance)) {
			return true
		}
		reassignList = append(reassignList, insElem.instance.InstanceID)
		if err := dstQueue.PushBack(insElem); err != nil {
			logger.Errorf("failed to push instance in instance queue error %s", err.Error())
		}
		return true
	})
	for _, instanceID := range reassignList {
		if err := srcQueue.DelByID(instanceID); err != nil {
			logger.Errorf("failed to delete instance in instance queue error %s", err.Error())
		}
	}
	return reassignList
}

func getInstanceID(obj interface{}) string {
	insElem, ok := obj.(*instanceElement)
	if ok && insElem.instance != nil {
		return insElem.instance.InstanceID
	}
	return ""
}

func getSubHealthInstanceFromRecord(subHealthRecord map[string]*instanceElement) *instanceElement {
	var ins *instanceElement
	for _, v := range subHealthRecord {
		if ins == nil {
			ins = v
		}
		if len(ins.threadMap) >= len(v.threadMap) {
			ins = v
		}
	}
	return ins
}

func (bcs *basicConcurrencyScheduler) isAgentSession(insAcqReq *types.InstanceAcquireRequest) bool {
	if !bcs.funcSpec.ExtendedMetaData.EnableAgentSession {
		return false
	}

	if insAcqReq.InstanceSession.SessionID == "" {
		return false
	}

	return true
}

func (bcs *basicConcurrencyScheduler) createOverAcqThread(record *sessionRecord,
	insAcqReq *types.InstanceAcquireRequest) (*types.InstanceAllocation, error) {
	threadID := fmt.Sprintf("overacq-%s-%d", record.sessionID, time.Now().UnixNano())

	record.overAcqThdMap[threadID] = struct{}{}

	insAlloc := &types.InstanceAllocation{
		Instance:     record.insElem.instance,
		AllocationID: threadID,
		SessionInfo: types.SessionInfo{
			SessionID:  insAcqReq.InstanceSession.SessionID,
			SessionCtx: record.ctx,
		},
	}

	log.GetLogger().Infof("Created over-acquired thread %s for AI Agent session %s, function %s",
		threadID, record.sessionID, bcs.funcKeyWithRes)

	return insAlloc, nil
}

// TriggerScale publishes TriggerScaleTopic to notify the connected instanceScaler
// to evaluate scale-up. Same entry as the cold-start acquire path. minConcurrency
// carries a lower-bound instance thread demand to the scaler (0 on the legacy path).
func (bcs *basicConcurrencyScheduler) TriggerScale(minConcurrency int) {
	log.GetLogger().With(zap.String("funcKeyWithRes", bcs.funcKeyWithRes)).
		Debugf("trigger scale, publish %s event", scheduler.TriggerScaleTopic)
	bcs.publishInsThdEvent(scheduler.TriggerScaleTopic, minConcurrency)
}
