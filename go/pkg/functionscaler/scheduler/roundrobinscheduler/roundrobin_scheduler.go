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

// Package roundrobinscheduler -
package roundrobinscheduler

import (
	"fmt"
	"sync"
	"time"

	"go.uber.org/zap"

	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	"yuanrong.org/kernel/pkg/common/uuid"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/pkg/functionscaler/scaler"
	"yuanrong.org/kernel/pkg/functionscaler/scheduler"
	"yuanrong.org/kernel/pkg/functionscaler/signalmanager"
	"yuanrong.org/kernel/pkg/functionscaler/types"
	"yuanrong.org/kernel/pkg/functionscaler/utils"
)

const (
	randomAllocationIDLength = 4
	checkScalingInterval     = 50 * time.Millisecond
)

type instanceObserver struct {
	callback func(interface{})
}

// RoundRobinScheduler will schedule instance according to concurrency usage for reserved instance
type RoundRobinScheduler struct {
	instanceScaler       scaler.InstanceScaler
	instanceQueue        []*types.Instance
	subHealthInstance    map[string]*types.Instance
	coldStartTraceMu     sync.Mutex
	coldStartTraceQueue  []*types.TraceContext
	observers            map[scheduler.InstanceTopic][]*instanceObserver
	funcKeyWithRes       string
	priorityAZ           string
	priorityHealthyCount int
	prioritySubHealthCnt int
	curIndex             int
	isReserve            bool
	checkScalingTimeout  time.Duration
	sync.RWMutex
}

// NewRoundRobinScheduler creates RoundRobinScheduler
func NewRoundRobinScheduler(funcKeyWithRes string, isReserve bool,
	requestTimeout time.Duration) scheduler.InstanceScheduler {
	return &RoundRobinScheduler{
		funcKeyWithRes:      funcKeyWithRes,
		instanceQueue:       make([]*types.Instance, 0, utils.DefaultSliceSize),
		subHealthInstance:   make(map[string]*types.Instance, utils.DefaultMapSize),
		coldStartTraceQueue: make([]*types.TraceContext, 0, utils.DefaultSliceSize),
		observers:           make(map[scheduler.InstanceTopic][]*instanceObserver, utils.DefaultMapSize),
		curIndex:            0,
		isReserve:           isReserve,
		checkScalingTimeout: requestTimeout,
	}
}

// GetInstanceNumber gets instance number inside instance queue
func (rs *RoundRobinScheduler) GetInstanceNumber(onlySelf bool) int {
	rs.RLock()
	insNum := len(rs.instanceQueue) + len(rs.subHealthInstance)
	rs.RUnlock()
	return insNum
}

// CheckInstanceExist checks if instance exist
func (rs *RoundRobinScheduler) CheckInstanceExist(instance *types.Instance) bool {
	rs.RLock()
	existSubHealth := rs.getSubHealthyInstance(instance.InstanceID) != nil
	existHealth := rs.getHealthyInstance(instance.InstanceID) != nil
	rs.RUnlock()
	return existSubHealth || existHealth
}

// AcquireInstance acquires an instance chosen by instanceScheduler
func (rs *RoundRobinScheduler) AcquireInstance(insAcqReq *types.InstanceAcquireRequest) (
	*types.InstanceAllocation, error) {
	var (
		instance *types.Instance
		exist    bool
	)
	rs.Lock()
	if len(insAcqReq.DesignateInstanceID) != 0 {
		insAlloc, err := rs.acquireInstanceDesignateInstanceID(insAcqReq, instance, exist)
		rs.Unlock()
		return insAlloc, err
	}
	rs.Unlock()
	if rs.instanceScaler.GetExpectInstanceNumber() <= 0 {
		// 这里如果是静态函数，则会触发到wisecloudscaler，触发一次nuwa cold start，如果不是静态函数，则会走到replicascaler，没有其他影响
		rs.recordColdStartTrace(insAcqReq.TraceID, insAcqReq.TraceParent)
		rs.publishInsThdEvent(scheduler.TriggerScaleTopic, nil)
	}
	if config.GlobalConfig.Scenario == types.ScenarioWiseCloud && !rs.isReserve {
		return nil, scheduler.ErrNoInsAvailable
	}
	for checkTime := time.Duration(0); checkTime <= rs.checkScalingTimeout; checkTime += checkScalingInterval {
		rs.Lock()
		currentInstanceNum := len(rs.instanceQueue) + len(rs.subHealthInstance)
		rs.Unlock()
		if currentInstanceNum > 0 || currentInstanceNum >= rs.instanceScaler.GetExpectInstanceNumber() {
			break
		}
		time.Sleep(checkScalingInterval)
	}
	rs.Lock()
	defer rs.Unlock()
	if len(rs.instanceQueue) == 0 {
		return nil, scheduler.ErrNoInsAvailable
	}
	instance = rs.selectHealthyInstance()
	return &types.InstanceAllocation{
		Instance:     instance,
		AllocationID: fmt.Sprintf("%s-%d-%s", instance.InstanceID, time.Now().UnixMilli(), uuid.New().String()),
	}, nil
}

func (rs *RoundRobinScheduler) recordColdStartTrace(traceID, traceParent string) {
	if traceID == "" && traceParent == "" {
		return
	}
	rs.coldStartTraceMu.Lock()
	rs.coldStartTraceQueue = append(rs.coldStartTraceQueue, &types.TraceContext{
		TraceID:     traceID,
		TraceParent: traceParent,
	})
	rs.coldStartTraceMu.Unlock()
}

// PopColdStartTrace returns the oldest request trace kept for the next cold start.
func (rs *RoundRobinScheduler) PopColdStartTrace() *types.TraceContext {
	rs.coldStartTraceMu.Lock()
	defer rs.coldStartTraceMu.Unlock()
	if len(rs.coldStartTraceQueue) == 0 {
		return nil
	}
	traceContext := rs.coldStartTraceQueue[0]
	rs.coldStartTraceQueue = rs.coldStartTraceQueue[1:]
	return traceContext
}

func (rs *RoundRobinScheduler) acquireInstanceDesignateInstanceID(insAcqReq *types.InstanceAcquireRequest,
	instance *types.Instance, exist bool) (*types.InstanceAllocation, error) {
	if instance = rs.getSubHealthyInstance(insAcqReq.DesignateInstanceID); instance != nil {
		return nil, scheduler.ErrInsSubHealthy
	}
	if instance = rs.getHealthyInstance(insAcqReq.DesignateInstanceID); instance != nil {
		return &types.InstanceAllocation{
			Instance: instance,
			AllocationID: fmt.Sprintf("%s-%d-%s", instance.InstanceID, time.Now().UnixMilli(),
				uuid.New().String()),
		}, nil
	}
	return nil, scheduler.ErrInsNotExist
}

// ReleaseInstance releases an instance to instanceScheduler
func (rs *RoundRobinScheduler) ReleaseInstance(insAlloc *types.InstanceAllocation) error {
	return nil
}

// AddInstance adds an instance to instanceScheduler
func (rs *RoundRobinScheduler) AddInstance(instance *types.Instance) error {
	rs.Lock()
	defer rs.Unlock()
	if rs.getSubHealthyInstance(instance.InstanceID) != nil || rs.getHealthyInstance(instance.InstanceID) != nil {
		return scheduler.ErrInsAlreadyExist
	}
	switch instance.InstanceStatus.Code {
	case int32(constant.KernelInstanceStatusSubHealth):
		rs.addSubHealthyInstance(instance)
	case int32(constant.KernelInstanceStatusRunning):
		rs.addHealthyInstance(instance)
	default:
		log.GetLogger().Warnf("ignore unexpected instance %s with status code %d", instance.InstanceID,
			instance.InstanceStatus.Code)
		return scheduler.ErrInternal
	}

	// notify observers that total thread number increases
	rs.publishInsThdEvent(scheduler.TotalInsThdTopic, instance.ConcurrentNum)
	return nil
}

// PopInstance pops an instance from instanceScheduler
func (rs *RoundRobinScheduler) PopInstance(force bool) *types.Instance {
	rs.Lock()
	defer rs.Unlock()
	var instance *types.Instance
	if instance = rs.popSubHealthyInstance(); instance == nil {
		instance = rs.popHealthyInstance()
	}
	if instance == nil {
		return nil
	}
	rs.publishInsThdEvent(scheduler.TotalInsThdTopic, -instance.ConcurrentNum)
	return instance
}

// DelInstance deletes an instance from instanceScheduler
func (rs *RoundRobinScheduler) DelInstance(instance *types.Instance) error {
	rs.Lock()
	defer rs.Unlock()
	var (
		// concurrentNum may be updated and this instance could be an old one, so we get inQueIns from internal
		inQueIns *types.Instance
	)
	if inQueIns = rs.getSubHealthyInstance(instance.InstanceID); inQueIns != nil {
		rs.delSubHealthyInstance(instance.InstanceID)
	} else {
		if inQueIns = rs.getHealthyInstance(instance.InstanceID); inQueIns == nil {
			return scheduler.ErrInsNotExist
		}
		rs.delHealthyInstance(instance.InstanceID)
	}
	rs.publishInsThdEvent(scheduler.TotalInsThdTopic, -inQueIns.ConcurrentNum)
	return nil
}

// ConnectWithInstanceScaler connects instanceScheduler with an instanceScaler, currently connects with replicaScaler
func (rs *RoundRobinScheduler) ConnectWithInstanceScaler(instanceScaler scaler.InstanceScaler) {
	rs.instanceScaler = instanceScaler
	rs.addObservers(scheduler.TriggerScaleTopic, func(data interface{}) {
		// legacy acquire publishes a nil payload; TriggerScale(minConcurrency) publishes the declared demand
		minConcurrency, _ := data.(int)
		instanceScaler.TriggerScale(minConcurrency)
	})
	rs.addObservers(scheduler.TotalInsThdTopic, func(data interface{}) {
		totalInsThdDiff, ok := data.(int)
		if !ok {
			return
		}
		instanceScaler.HandleInsThdUpdate(0, totalInsThdDiff)
	})
	rs.addObservers(scheduler.CreateErrorTopic, func(data interface{}) {
		createError, ok := data.(error)
		if !ok {
			return
		}
		instanceScaler.HandleCreateError(createError)
	})
	return
}

// HandleFuncSpecUpdate handles funcSpec update comes from ETCD
func (rs *RoundRobinScheduler) HandleFuncSpecUpdate(funcSpec *types.FunctionSpecification) {
	rs.Lock()
	defer rs.Unlock()
	priorityAZ := ""
	if funcSpec != nil {
		priorityAZ = funcSpec.ExtendedMetaData.PriorityAZ
	}
	if rs.priorityAZ == priorityAZ {
		return
	}
	rs.priorityAZ = priorityAZ
	rs.refreshPriorityCountersLocked()
}

// roundrobinscheduler不处理evicting状态实例，
var instanceStatusCodeMap = map[int32]struct{}{
	int32(constant.KernelInstanceStatusRunning):   {},
	int32(constant.KernelInstanceStatusSubHealth): {},
}

// HandleInstanceUpdate handles instance update comes from ETCD
func (rs *RoundRobinScheduler) HandleInstanceUpdate(instance *types.Instance) {
	logger := log.GetLogger().With(zap.Any("funcKey", rs.funcKeyWithRes), zap.Any("instance", instance.InstanceID),
		zap.Any("instanceStatus", instance.InstanceStatus.Code))

	if _, ok := instanceStatusCodeMap[instance.InstanceStatus.Code]; !ok {
		logger.Infof("unexpect instance status, ignore it")
		return
	}
	rs.RLock()
	existHealth := rs.getHealthyInstance(instance.InstanceID) != nil
	existSubHealth := rs.getSubHealthyInstance(instance.InstanceID) != nil
	rs.RUnlock()
	if !existHealth && !existSubHealth {
		// 适配静态函数
		logger.Infof("update alias and faasscheduler event to instance")
		signalmanager.GetSignalManager().SignalInstance(instance, constant.KillSignalAliasUpdate)
		signalmanager.GetSignalManager().SignalInstance(instance, constant.KillSignalFaaSSchedulerUpdate)
	}

	switch instance.InstanceStatus.Code {
	case int32(constant.KernelInstanceStatusSubHealth):
		if existHealth && !existSubHealth {
			logger.Infof("instance transitions from healthy to sub-healthy")
			rs.Lock()
			rs.delHealthyInstance(instance.InstanceID)
			rs.addSubHealthyInstance(instance)
			rs.Unlock()
		} else if !existHealth && existSubHealth {
			logger.Warnf("no need to add sub-healthy instance repeatedly")
		} else if !existHealth && !existSubHealth {
			// maybe we should update this instance object to handle update inside an instance
			logger.Infof("add new sub-healthy instance from instance update")
			if err := rs.AddInstance(instance); err != nil {
				logger.Errorf("failed to add instance error %s", err.Error())
			}
		}
	case int32(constant.KernelInstanceStatusRunning):
		if existHealth && !existSubHealth {
			logger.Warnf("no need to add healthy instance repeatedly")
		} else if !existHealth && existSubHealth {
			// maybe we should update this instance object to handle update inside an instance
			rs.Lock()
			rs.delSubHealthyInstance(instance.InstanceID)
			rs.addHealthyInstance(instance)
			rs.Unlock()
		} else if !existHealth && !existSubHealth {
			logger.Infof("add new healthy instance from instance update")
			if err := rs.AddInstance(instance); err != nil {
				logger.Errorf("failed to add instance error %s", err.Error())
			}
		}
	default:
	}
}

// HandleCreateError handles create error
func (rs *RoundRobinScheduler) HandleCreateError(createErr error) {
	rs.publishInsThdEvent(scheduler.CreateErrorTopic, createErr)
}

// SignalAllInstances sends signal to all instances
func (rs *RoundRobinScheduler) SignalAllInstances(signalFunc scheduler.SignalInstanceFunc) {
	rs.RLock()
	for _, instance := range rs.instanceQueue {
		signalFunc(instance)
	}
	rs.RUnlock()
}

// CleanExternalSessionRecords no-op：roundrobin scheduler 不维护 session 绑定外部记录。
func (rs *RoundRobinScheduler) CleanExternalSessionRecords() {
}

// Destroy destroys instanceScheduler
func (rs *RoundRobinScheduler) Destroy() {
}

func (rs *RoundRobinScheduler) getHealthyInstance(targetID string) *types.Instance {
	for _, instance := range rs.instanceQueue {
		if instance.InstanceID == targetID {
			return instance
		}
	}
	return nil
}

func (rs *RoundRobinScheduler) addHealthyInstance(instance *types.Instance) {
	rs.instanceQueue = append(rs.instanceQueue, instance)
	if rs.isPriorityAZInstance(instance) {
		rs.priorityHealthyCount++
	}
}

func (rs *RoundRobinScheduler) delHealthyInstance(targetID string) {
	targetIndex := -1
	for index, instance := range rs.instanceQueue {
		if instance.InstanceID == targetID {
			targetIndex = index
			if rs.isPriorityAZInstance(instance) {
				rs.priorityHealthyCount--
			}
			break
		}
	}
	if targetIndex == -1 {
		return
	}
	rs.instanceQueue = append(rs.instanceQueue[:targetIndex], rs.instanceQueue[targetIndex+1:]...)
}

func (rs *RoundRobinScheduler) popHealthyInstance() *types.Instance {
	if instance := rs.popHealthyInstanceByPriority(false); instance != nil {
		return instance
	}
	return rs.popHealthyInstanceByPriority(true)
}

func (rs *RoundRobinScheduler) popHealthyInstanceByPriority(priority bool) *types.Instance {
	if len(rs.instanceQueue) == 0 {
		return nil
	}
	for index := len(rs.instanceQueue) - 1; index >= 0; index-- {
		instance := rs.instanceQueue[index]
		if rs.isPriorityAZInstance(instance) != priority {
			continue
		}
		rs.instanceQueue = append(rs.instanceQueue[:index], rs.instanceQueue[index+1:]...)
		if priority {
			rs.priorityHealthyCount--
		}
		return instance
	}
	return nil
}

func (rs *RoundRobinScheduler) selectHealthyInstance() *types.Instance {
	queLen := len(rs.instanceQueue)
	if queLen == 0 {
		return nil
	}
	if rs.curIndex >= queLen {
		rs.curIndex = rs.curIndex % queLen
	}
	startIndex := rs.curIndex
	if rs.priorityAZ != "" && rs.priorityHealthyCount > 0 {
		for offset := 0; offset < queLen; offset++ {
			index := (startIndex + offset) % queLen
			instance := rs.instanceQueue[index]
			if !rs.isPriorityAZInstance(instance) {
				continue
			}
			rs.curIndex = (index + 1) % queLen
			return instance
		}
	}
	instance := rs.instanceQueue[startIndex]
	rs.curIndex = (startIndex + 1) % queLen
	return instance
}

func (rs *RoundRobinScheduler) getSubHealthyInstance(targetID string) *types.Instance {
	if instance, ok := rs.subHealthInstance[targetID]; ok {
		return instance
	}
	return nil
}

func (rs *RoundRobinScheduler) addSubHealthyInstance(instance *types.Instance) {
	rs.subHealthInstance[instance.InstanceID] = instance
	if rs.isPriorityAZInstance(instance) {
		rs.prioritySubHealthCnt++
	}
}

func (rs *RoundRobinScheduler) delSubHealthyInstance(targetID string) {
	if instance, ok := rs.subHealthInstance[targetID]; ok {
		if rs.isPriorityAZInstance(instance) {
			rs.prioritySubHealthCnt--
		}
		delete(rs.subHealthInstance, targetID)
	}
}

func (rs *RoundRobinScheduler) popSubHealthyInstance() *types.Instance {
	if len(rs.subHealthInstance) == 0 {
		return nil
	}
	for instanceID, instance := range rs.subHealthInstance {
		if rs.isPriorityAZInstance(instance) {
			continue
		}
		delete(rs.subHealthInstance, instanceID)
		return instance
	}
	if rs.prioritySubHealthCnt == 0 {
		return nil
	}
	for instanceID, instance := range rs.subHealthInstance {
		if !rs.isPriorityAZInstance(instance) {
			continue
		}
		delete(rs.subHealthInstance, instanceID)
		rs.prioritySubHealthCnt--
		return instance
	}
	return nil
}

func (rs *RoundRobinScheduler) isPriorityAZInstance(instance *types.Instance) bool {
	return instance != nil && rs.priorityAZ != "" && instance.AZ == rs.priorityAZ
}

func (rs *RoundRobinScheduler) refreshPriorityCountersLocked() {
	rs.priorityHealthyCount = 0
	for _, instance := range rs.instanceQueue {
		if rs.isPriorityAZInstance(instance) {
			rs.priorityHealthyCount++
		}
	}
	rs.prioritySubHealthCnt = 0
	for _, instance := range rs.subHealthInstance {
		if rs.isPriorityAZInstance(instance) {
			rs.prioritySubHealthCnt++
		}
	}
}

// publishInsThdEvent will notify observers of specific topic of instance
func (rs *RoundRobinScheduler) publishInsThdEvent(topic scheduler.InstanceTopic, data interface{}) {
	for _, observer := range rs.observers[topic] {
		observer.callback(data)
	}
}

// addObservers will add observer of instance scaledInsQue
func (rs *RoundRobinScheduler) addObservers(topic scheduler.InstanceTopic, callback func(interface{})) {
	topicObservers, exist := rs.observers[topic]
	if !exist {
		topicObservers = make([]*instanceObserver, 0, utils.DefaultSliceSize)
		rs.observers[topic] = topicObservers
	}
	rs.observers[topic] = append(topicObservers, &instanceObserver{
		callback: callback,
	})
}

// HandleFuncOwnerUpdate -
func (rs *RoundRobinScheduler) HandleFuncOwnerUpdate(isFuncOwner bool) {
}

// TriggerScale publishes TriggerScaleTopic to notify the connected instanceScaler.
// minConcurrency carries a lower-bound instance thread demand to the scaler (0 on
// the legacy path).
func (rs *RoundRobinScheduler) TriggerScale(minConcurrency int) {
	log.GetLogger().With(zap.String("funcKeyWithRes", rs.funcKeyWithRes)).
		Debugf("trigger scale, publish %s event", scheduler.TriggerScaleTopic)
	rs.publishInsThdEvent(scheduler.TriggerScaleTopic, minConcurrency)
}
