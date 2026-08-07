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
	"sync"
	"testing"
	"time"

	"github.com/agiledragon/gomonkey/v2"
	"github.com/smartystreets/goconvey/convey"
	"github.com/stretchr/testify/assert"

	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/common/faas_common/etcd3"
	"yuanrong.org/kernel/pkg/common/faas_common/instanceconfig"
	"yuanrong.org/kernel/pkg/common/faas_common/queue"
	"yuanrong.org/kernel/pkg/common/faas_common/resspeckey"
	commonTypes "yuanrong.org/kernel/pkg/common/faas_common/types"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/pkg/functionscaler/lease"
	"yuanrong.org/kernel/pkg/functionscaler/metrics"
	"yuanrong.org/kernel/pkg/functionscaler/registry"
	"yuanrong.org/kernel/pkg/functionscaler/scheduler"
	"yuanrong.org/kernel/pkg/functionscaler/selfregister"
	"yuanrong.org/kernel/pkg/functionscaler/session"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

func TestPopColdStartTraceDoesNotWaitForSchedulerLock(t *testing.T) {
	scheduler := &basicConcurrencyScheduler{
		RWMutex: &sync.RWMutex{},
		coldStartTraceQueue: []*coldStartTraceContext{{
			traceContext: &types.TraceContext{TraceID: "trace-id"},
			createdAt:    time.Now(),
		}},
		leaseInterval: time.Second,
	}

	scheduler.Lock()
	done := make(chan struct{})
	go func() {
		_ = scheduler.PopColdStartTrace()
		close(done)
	}()

	select {
	case <-done:
		scheduler.Unlock()
	case <-time.After(200 * time.Millisecond):
		scheduler.Unlock()
		<-done
		t.Fatal("PopColdStartTrace blocked on the scheduler lock")
	}
}

type fakeInstanceScaler struct {
	timer           *time.Timer
	scaling         bool
	scaleUpFunc     func()
	targetRsvInsNum int
	triggerCount    int
}

func (f *fakeInstanceScaler) SetFuncOwner(isManaged bool) {
}

func (f *fakeInstanceScaler) SetEnable(enable bool) {
}

func (f *fakeInstanceScaler) TriggerScale(_ int) {
	f.triggerCount++
	go func() {
		time.Sleep(10 * time.Millisecond)
		if f.scaleUpFunc != nil {
			f.scaleUpFunc()
		}
	}()
}

func (f *fakeInstanceScaler) CheckScaling() bool {
	if f.timer == nil {
		return false
	}
	select {
	case <-f.timer.C:
		f.scaling = false
		return false
	default:
		return f.scaling
	}
}

func (f *fakeInstanceScaler) UpdateCreateMetrics(coldStartTime time.Duration) {
}

func (f *fakeInstanceScaler) HandleInsThdUpdate(inUseInsThdDiff, totalInsThdDiff int) {
}

func (f *fakeInstanceScaler) HandleFuncSpecUpdate(funcSpec *types.FunctionSpecification) {
}

func (f *fakeInstanceScaler) HandleInsConfigUpdate(insConfig *instanceconfig.Configuration) {
}

func (f *fakeInstanceScaler) HandleCreateError(createError error) {
}

func (f *fakeInstanceScaler) GetExpectInstanceNumber() int {
	return f.targetRsvInsNum
}

func (f *fakeInstanceScaler) Destroy() {
}

func TestMain(m *testing.M) {
	patches := []*gomonkey.Patches{
		gomonkey.ApplyFunc((*etcd3.EtcdWatcher).StartList, func(_ *etcd3.EtcdWatcher) {}),
		gomonkey.ApplyFunc(etcd3.GetRouterEtcdClient, func() *etcd3.EtcdClient { return &etcd3.EtcdClient{} }),
		gomonkey.ApplyFunc(etcd3.GetMetaEtcdClient, func() *etcd3.EtcdClient { return &etcd3.EtcdClient{} }),
		gomonkey.ApplyFunc(etcd3.GetCAEMetaEtcdClient, func() *etcd3.EtcdClient { return &etcd3.EtcdClient{} }),
		gomonkey.ApplyFunc((*registry.FaasSchedulerRegistry).WaitForETCDList, func() {}),
		gomonkey.ApplyFunc((*etcd3.EtcdClient).AttachAZPrefix, func(_ *etcd3.EtcdClient, key string) string { return key }),
	}
	defer func() {
		for _, patch := range patches {
			time.Sleep(100 * time.Millisecond)
			patch.Reset()
		}
	}()
	config.GlobalConfig = types.Configuration{}
	config.GlobalConfig.AutoScaleConfig = types.AutoScaleConfig{
		SLAQuota:      1000,
		ScaleDownTime: 1000,
		BurstScaleNum: 100000,
	}
	config.GlobalConfig.LeaseSpan = 500
	registry.InitRegistry(make(chan struct{}))
	m.Run()
}

func TestNewBasicConcurrencyScheduler(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 1},
	}, resspeckey.ResSpecKey{}, "", nil, nil)
	assert.NotNil(t, bcs)
}

func TestGetInstanceNumber(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 1},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	err := bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	assert.Nil(t, err)
	getNum := bcs.GetInstanceNumber(true)
	assert.Equal(t, 0, getNum)
	bcs.isFuncOwner = true
	err = bcs.AddInstance(&types.Instance{
		InstanceID:     "instance2",
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusSubHealth)},
	})
	assert.Nil(t, err)
	getNum = bcs.GetInstanceNumber(true)
	assert.Equal(t, 1, getNum)
}

func TestAddInstanceSetIsNewInstance(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:           "testFunction",
		FuncMetaSignature: "new-sig",
		InstanceMetaData:  commonTypes.InstanceMetaData{ConcurrentNum: 1},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.isFuncOwner = true
	err := bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		FuncSig:        "old-sig",
		ConcurrentNum:  1,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	assert.Nil(t, err)
	obj := bcs.selfInstanceQueue.GetByID("instance1")
	insElem, ok := obj.(*instanceElement)
	assert.True(t, ok)
	assert.False(t, insElem.isNewInstance)
}

func TestAcquireInstanceBasic(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.isFuncOwner = true
	checkInUseInsThd := 0
	checkAvailInsThd := 0
	bcs.addObservers(scheduler.InUseInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkInUseInsThd += delta
	})
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	acqIns1, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{DesignateInstanceID: "instance2"})
	assert.Equal(t, scheduler.ErrInsNotExist, err)
	assert.Nil(t, acqIns1)
	acqIns2, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{})
	assert.Nil(t, err)
	assert.Equal(t, "instance1", acqIns2.Instance.InstanceID)
	assert.Equal(t, 1, checkInUseInsThd)
	assert.Equal(t, 1, checkAvailInsThd)
	acqIns3, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{DesignateInstanceID: "instance1"})
	assert.Nil(t, err)
	assert.Equal(t, "instance1", acqIns3.Instance.InstanceID)
	assert.Equal(t, 2, checkInUseInsThd)
	assert.Equal(t, 0, checkAvailInsThd)
	acqIns4, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{})
	assert.Equal(t, scheduler.ErrNoInsAvailable, err)
	assert.Nil(t, acqIns4)
	defer gomonkey.ApplyFunc((*lease.GenericInstanceLeaseManager).CreateInstanceLease,
		func(_ *lease.GenericInstanceLeaseManager,
			insAlloc *types.InstanceAllocation, interval time.Duration, callback func()) (types.InstanceLease, error) {
			return nil, errors.New("some error")
		}).Reset()
	bcs.ReleaseInstance(acqIns3)
	_, err = bcs.AcquireInstance(&types.InstanceAcquireRequest{})
	assert.NotNil(t, err)
}

func TestAcquireInstanceOtherQueue(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.isFuncOwner = false
	checkInUseInsThd := 0
	checkAvailInsThd := 0
	bcs.addObservers(scheduler.InUseInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkInUseInsThd += delta
	})
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	acqIns1, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{DesignateInstanceID: "instance2"})
	assert.Equal(t, scheduler.ErrInsNotExist, err)
	assert.Nil(t, acqIns1)
	acqIns2, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{})
	assert.Nil(t, err)
	assert.Equal(t, "instance1", acqIns2.Instance.InstanceID)
	assert.Equal(t, 0, checkInUseInsThd)
	assert.Equal(t, 0, checkAvailInsThd)
	acqIns3, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{DesignateInstanceID: "instance1"})
	assert.Nil(t, err)
	assert.Equal(t, "instance1", acqIns3.Instance.InstanceID)
	assert.Equal(t, 0, checkInUseInsThd)
	assert.Equal(t, 0, checkAvailInsThd)
	acqIns4, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{})
	assert.Equal(t, scheduler.ErrNoInsAvailable, err)
	assert.Nil(t, acqIns4)
}

func TestAcquireInstanceWithSession(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 4},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	//bcs.isFuncOwner = true
	bcs.HandleFuncOwnerUpdate(true)
	checkInUseInsThd := 0
	checkAvailInsThd := 0
	bcs.addObservers(scheduler.InUseInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkInUseInsThd += delta
	})
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  4,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  4,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	acqIns1, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{
			SessionID:   "session1",
			Concurrency: 2,
		},
	})
	assert.Nil(t, err)
	assert.Equal(t, "instance2", acqIns1.Instance.InstanceID)
	assert.Equal(t, 2, checkInUseInsThd)
	assert.Equal(t, 6, checkAvailInsThd)
	acqIns2, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{
			SessionID:   "session1",
			Concurrency: 2,
		},
	})
	assert.Nil(t, err)
	assert.Equal(t, "instance2", acqIns2.Instance.InstanceID)
	assert.Equal(t, 2, checkInUseInsThd)
	assert.Equal(t, 6, checkAvailInsThd)
	acqIns3, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{
			SessionID:   "session1",
			Concurrency: 2,
		},
	})
	assert.Nil(t, err)
	assert.Equal(t, "instance2", acqIns3.Instance.InstanceID)
	assert.Equal(t, 3, checkInUseInsThd)
	assert.Equal(t, 5, checkAvailInsThd)
}

func TestReleaseInstance(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.isFuncOwner = true
	checkInUseInsThd := 0
	checkAvailInsThd := 0
	bcs.addObservers(scheduler.InUseInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkInUseInsThd += delta
	})
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusSubHealth)},
	})
	err := bcs.ReleaseInstance(&types.InstanceAllocation{
		Instance: &types.Instance{
			InstanceID: "instance3",
		},
	})
	assert.Equal(t, scheduler.ErrInsNotExist, err)
	acqIns1, _ := bcs.AcquireInstance(&types.InstanceAcquireRequest{})
	err = bcs.ReleaseInstance(acqIns1)
	assert.Nil(t, err)
	assert.Equal(t, 0, checkInUseInsThd)
	assert.Equal(t, 2, checkAvailInsThd)
	err = bcs.ReleaseInstance(&types.InstanceAllocation{
		Instance: &types.Instance{
			InstanceID: "instance2",
		},
	})
	assert.Nil(t, err)
	assert.Equal(t, 2, checkAvailInsThd)
}

func TestReleaseInstanceWithSession(t *testing.T) {
	defer gomonkey.ApplyFunc((*lease.GenericInstanceLeaseManager).CreateInstanceLease,
		func(_ *lease.GenericInstanceLeaseManager,
			insAlloc *types.InstanceAllocation, interval time.Duration, callback func()) (types.InstanceLease, error) {
			return nil, nil
		}).Reset()
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	mockTimer := time.NewTimer(100 * time.Millisecond)
	bcs.newSessionExpireTimer = func(d time.Duration) *time.Timer {
		mockTimer.Reset(100 * time.Millisecond)
		return mockTimer
	}
	bcs.isFuncOwner = true
	checkInUseInsThd := 0
	checkAvailInsThd := 0
	bcs.addObservers(scheduler.InUseInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkInUseInsThd += delta
	})
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  4,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	acqIns1, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{
			SessionID:   "session1",
			SessionTTL:  1,
			Concurrency: 2,
		},
	})
	assert.Nil(t, err)
	assert.Equal(t, "instance1", acqIns1.Instance.InstanceID)
	err = bcs.ReleaseInstance(acqIns1)
	assert.Nil(t, err)
	assert.Equal(t, 2, checkInUseInsThd)
	assert.Equal(t, 2, checkAvailInsThd)
	time.Sleep(50 * time.Millisecond)
	acqIns2, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{
			SessionID:   "session1",
			SessionTTL:  1,
			Concurrency: 2,
		},
	})
	assert.Nil(t, err)
	assert.Equal(t, "instance1", acqIns2.Instance.InstanceID)
	time.Sleep(50 * time.Millisecond)
	err = bcs.ReleaseInstance(acqIns2)
	assert.Nil(t, err)
	assert.Equal(t, 2, checkInUseInsThd)
	assert.Equal(t, 2, checkAvailInsThd)
	time.Sleep(150 * time.Millisecond)
	assert.Equal(t, 0, checkInUseInsThd)
	assert.Equal(t, 4, checkAvailInsThd)
}

func TestAgentSessionOverAcquireShouldNotReportLeaseMetrics(t *testing.T) {
	patches := gomonkey.NewPatches()
	defer patches.Reset()

	acquireCount := 0
	releaseCount := 0
	patches.ApplyFunc(metrics.OnAcquireLease, func(_ *types.InstanceAllocation) {
		acquireCount++
	})
	patches.ApplyFunc(metrics.OnReleaseLease, func(_ *types.InstanceAllocation) {
		releaseCount++
	})

	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey: "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{
			ConcurrentNum: 1,
		},
		ExtendedMetaData: commonTypes.ExtendedMetaData{
			EnableAgentSession: true,
		},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))

	insElem := &instanceElement{
		instance: &types.Instance{
			InstanceID:     "instance1",
			ConcurrentNum:  1,
			ResKey:         resspeckey.ResSpecKey{},
			InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
		},
		threadMap: map[string]struct{}{},
	}
	ctx, cancelFunc := context.WithCancel(context.Background())
	defer cancelFunc()
	record := &sessionRecord{
		ctx:           ctx,
		sessionID:     "session1",
		availThdMap:   map[string]struct{}{},
		allocThdMap:   map[string]struct{}{"thread-1": {}},
		overAcqThdMap: make(map[string]struct{}),
		insElem:       insElem,
		cancelFunc:    cancelFunc,
	}
	bcs.sessionManager.addSession(record.sessionID, record)

	insAlloc, err := bcs.createOverAcqThread(record, &types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{
			SessionID: "session1",
		},
	})
	assert.Nil(t, err)
	assert.Equal(t, 0, acquireCount)
	assert.Len(t, record.overAcqThdMap, 1)

	err = bcs.releaseInstanceThreadWithSession(bcs.selfInstanceQueue, insElem, insAlloc)
	assert.Nil(t, err)
	assert.Equal(t, 0, releaseCount)
	assert.Len(t, record.overAcqThdMap, 0)
}

func TestAddInstance(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.isFuncOwner = true
	checkAvailInsThd := 0
	checkTotalInsThd := 0
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.addObservers(scheduler.TotalInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkTotalInsThd += delta
	})
	err := bcs.AddInstance(&types.Instance{
		InstanceID:    "instance1",
		ConcurrentNum: 2,
		ResKey:        resspeckey.ResSpecKey{},
	})
	assert.Equal(t, scheduler.ErrInternal, err)
	assert.Equal(t, 0, checkAvailInsThd)
	assert.Equal(t, 0, checkTotalInsThd)
	err = bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	assert.Nil(t, err)
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 2, checkTotalInsThd)
	err = bcs.AddInstance(&types.Instance{
		InstanceID:    "instance1",
		ConcurrentNum: 2,
		ResKey:        resspeckey.ResSpecKey{},
	})
	assert.Equal(t, scheduler.ErrInsAlreadyExist, err)
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 2, checkTotalInsThd)
	err = bcs.AddInstance(&types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusSubHealth)},
	})
	assert.Nil(t, err)
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 4, checkTotalInsThd)
	err = bcs.AddInstance(&types.Instance{
		InstanceID:    "instance2",
		ConcurrentNum: 2,
		ResKey:        resspeckey.ResSpecKey{},
	})
	assert.Equal(t, scheduler.ErrInsAlreadyExist, err)
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 4, checkTotalInsThd)

	// evicting实例能添加进去，但是指标不上报
	err = bcs.AddInstance(&types.Instance{
		InstanceID:     "instance3",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusEvicting)},
	})
	assert.Nil(t, err)
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 4, checkTotalInsThd)
}

func TestDelInstance(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.isFuncOwner = true
	checkAvailInsThd := 0
	checkInUsedInsThd := 0
	checkTotalInsThd := 0
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.addObservers(scheduler.InUseInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkInUsedInsThd += delta
	})
	bcs.addObservers(scheduler.TotalInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkTotalInsThd += delta
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusSubHealth)},
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance_evicting",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusEvicting)},
	})
	err := bcs.DelInstance(&types.Instance{
		InstanceID: "instance3",
	})
	assert.Equal(t, scheduler.ErrInsNotExist, err)
	err = bcs.DelInstance(&types.Instance{
		InstanceID: "instance1",
		ResKey:     resspeckey.ResSpecKey{},
	})
	assert.Nil(t, err)
	assert.Equal(t, 0, checkAvailInsThd)
	assert.Equal(t, 0, checkInUsedInsThd)
	assert.Equal(t, 2, checkTotalInsThd)
	err = bcs.DelInstance(&types.Instance{
		InstanceID: "instance2",
		ResKey:     resspeckey.ResSpecKey{},
	})
	assert.Nil(t, err)
	assert.Equal(t, 0, checkAvailInsThd)
	assert.Equal(t, 0, checkInUsedInsThd)
	assert.Equal(t, 0, checkTotalInsThd)

	// evicting实例能正常删除，并且不影响指标
	err = bcs.DelInstance(&types.Instance{
		InstanceID: "instance_evicting",
		ResKey:     resspeckey.ResSpecKey{},
	})
	assert.Nil(t, err)
	assert.Equal(t, 0, checkAvailInsThd)
	assert.Equal(t, 0, checkInUsedInsThd)
	assert.Equal(t, 0, checkTotalInsThd)

}

func TestPopInstanceElement(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.isFuncOwner = true
	checkAvailInsThd := 0
	checkInUsedInsThd := 0
	checkTotalInsThd := 0
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.addObservers(scheduler.InUseInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkInUsedInsThd += delta
	})
	bcs.addObservers(scheduler.TotalInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkTotalInsThd += delta
	})
	popIns1 := bcs.popInstanceElement(forward, nil, false)
	assert.Nil(t, popIns1)
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusSubHealth)},
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance_evicting",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusEvicting)},
	})
	popIns2 := bcs.popInstanceElement(forward, nil, false)
	assert.Equal(t, "instance2", popIns2.instance.InstanceID)
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 0, checkInUsedInsThd)
	assert.Equal(t, 2, checkTotalInsThd)
	popIns3 := bcs.popInstanceElement(forward, func(element *instanceElement) bool { return false }, false)
	assert.Nil(t, popIns3)
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 0, checkInUsedInsThd)
	assert.Equal(t, 2, checkTotalInsThd)
	popIns4 := bcs.popInstanceElement(forward, func(element *instanceElement) bool { return true }, false)
	assert.Equal(t, "instance1", popIns4.instance.InstanceID)
	assert.Equal(t, 0, checkAvailInsThd)
	assert.Equal(t, 0, checkInUsedInsThd)
	assert.Equal(t, 0, checkTotalInsThd)

	// evicting实例仅供绑定会话的申请租约请求使用，不干涉扩缩容逻辑，因此无法pop该实例
	popIns5 := bcs.popInstanceElement(forward, func(element *instanceElement) bool { return true }, false)
	assert.Nil(t, popIns5)
	assert.Equal(t, 0, checkAvailInsThd)
	assert.Equal(t, 0, checkInUsedInsThd)
	assert.Equal(t, 0, checkTotalInsThd)
}

func TestSignalAllInstances(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.isFuncOwner = true
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance_evicting",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusEvicting)},
	})
	insIDList := make([]string, 0, 3)
	bcs.SignalAllInstances(func(instance *types.Instance) {
		insIDList = append(insIDList, instance.InstanceID)
	})
	assert.Contains(t, insIDList, "instance1")
	assert.Contains(t, insIDList, "instance2")
	// evicting实例可能还需要给会话请求使用，因此仍然需要被signal
	assert.Contains(t, insIDList, "instance_evicting")
}

func TestHandleInstanceUpdate(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.isFuncOwner = true
	checkAvailInsThd := 0
	checkTotalInsThd := 0
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.addObservers(scheduler.TotalInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkTotalInsThd += delta
	})
	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusSubHealth)},
	})
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 4, checkTotalInsThd)
	_, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{DesignateInstanceID: "instance2"})
	assert.Equal(t, scheduler.ErrInsSubHealthy, err)
	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusSubHealth)},
	})
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 4, checkTotalInsThd)
	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusSubHealth)},
	})
	assert.Equal(t, 0, checkAvailInsThd)
	assert.Equal(t, 4, checkTotalInsThd)
	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 4, checkTotalInsThd)
	selfregister.GlobalSchedulerProxy.Add(&commonTypes.InstanceInfo{InstanceName: "scheduler1"}, "", "", true)
	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:        "instance3",
		ConcurrentNum:     2,
		ResKey:            resspeckey.ResSpecKey{},
		InstanceStatus:    commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
		CreateSchedulerID: "scheduler1",
		Permanent:         true,
	})
	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:        "instance3",
		ConcurrentNum:     2,
		ResKey:            resspeckey.ResSpecKey{},
		InstanceStatus:    commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusSubHealth)},
		CreateSchedulerID: "scheduler1",
		Permanent:         true,
	})
	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:        "instance3",
		ConcurrentNum:     2,
		ResKey:            resspeckey.ResSpecKey{},
		InstanceStatus:    commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
		CreateSchedulerID: "scheduler1",
		Permanent:         true,
	})
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 4, checkTotalInsThd)
}

func TestHandleInstanceUpdate_withEvictingInstance(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.isFuncOwner = true
	checkAvailInsThd := 0
	checkTotalInsThd := 0
	checkInUseInsThd := 0
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.addObservers(scheduler.TotalInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkTotalInsThd += delta
	})
	bcs.addObservers(scheduler.InUseInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkInUseInsThd += delta
	})
	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusSubHealth)},
	})
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 4, checkTotalInsThd)
	assert.Equal(t, 0, checkInUseInsThd)

	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusEvicting)},
	})
	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 2, checkTotalInsThd)
	assert.Equal(t, 0, checkInUseInsThd)

	_, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{DesignateInstanceID: "instance2"})
	assert.NotNil(t, err)

	assert.Equal(t, 2, checkAvailInsThd)
	assert.Equal(t, 2, checkTotalInsThd)
	assert.Equal(t, 0, checkInUseInsThd)

	bcs.HandleInstanceUpdate(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusEvicting)},
	})
	assert.Equal(t, 0, checkAvailInsThd)
	assert.Equal(t, 0, checkTotalInsThd)
	assert.Equal(t, 0, checkInUseInsThd)
}

func Test_basicConcurrencyScheduler_ReassignInstance(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	checkAvailInsThd := 0
	checkTotalInsThd := 0
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.addObservers(scheduler.TotalInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkTotalInsThd += delta
	})
	instance1 := &types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	}
	instance2 := &types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusSubHealth)},
	}
	instance_evicting := &types.Instance{
		InstanceID:     "instance_evicting",
		ConcurrentNum:  2,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusEvicting)},
	}
	convey.Convey("test HandleFuncOwnerUpdate", t, func() {
		convey.Convey("become owner", func() {
			checkAvailInsThd = 0
			checkTotalInsThd = 0
			bcs.isFuncOwner = false
			bcs.AddInstance(instance1)
			bcs.AddInstance(instance2)
			bcs.AddInstance(instance_evicting)
			defer bcs.DelInstance(instance_evicting)
			bcs.HandleFuncOwnerUpdate(true)
			assert.Equal(t, 2, checkAvailInsThd)
			assert.Equal(t, 4, checkTotalInsThd)
			assert.True(t, bcs.selfInstanceQueue.GetByID(instance_evicting.InstanceID) != nil)
			assert.True(t, bcs.otherInstanceQueue.GetByID(instance_evicting.InstanceID) != nil)
			bcs.DelInstance(instance1)
			bcs.DelInstance(instance2)
		})
		convey.Convey("resign owner", func() {
			checkAvailInsThd = 0
			checkTotalInsThd = 0
			bcs.isFuncOwner = true
			bcs.AddInstance(instance1)
			bcs.AddInstance(instance2)
			bcs.AddInstance(instance_evicting)
			defer bcs.DelInstance(instance_evicting)
			bcs.HandleFuncOwnerUpdate(false)
			assert.Equal(t, 0, checkAvailInsThd)
			assert.Equal(t, 0, checkTotalInsThd)
			assert.True(t, bcs.selfInstanceQueue.GetByID(instance_evicting.InstanceID) != nil)
			assert.True(t, bcs.otherInstanceQueue.GetByID(instance_evicting.InstanceID) != nil)
			bcs.DelInstance(instance1)
			bcs.DelInstance(instance2)
		})
		convey.Convey("no change", func() {
			checkAvailInsThd = 0
			checkTotalInsThd = 0
			bcs.isFuncOwner = true
			bcs.AddInstance(instance1)
			bcs.AddInstance(instance2)
			bcs.AddInstance(instance_evicting)
			defer bcs.DelInstance(instance_evicting)
			bcs.HandleFuncOwnerUpdate(true)
			assert.Equal(t, 2, checkAvailInsThd)
			assert.Equal(t, 4, checkTotalInsThd)
			assert.True(t, bcs.selfInstanceQueue.GetByID(instance_evicting.InstanceID) != nil)
			bcs.DelInstance(instance1)
			bcs.DelInstance(instance2)
		})
	})
}

func Test_basicConcurrencyScheduler_scheduleRequest(t *testing.T) {
	convey.Convey("test scheduleRequest", t, func() {
		convey.Convey("baseline", func() {
			bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
				FuncKey:          "testFunction",
				InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
			}, resspeckey.ResSpecKey{}, "",
				queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
				queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
			bcs.isFuncOwner = false
			p := gomonkey.ApplyFunc((*basicConcurrencyScheduler).acquireInstanceInternal,
				func(_ *basicConcurrencyScheduler,
					queue queue.Queue, request *types.InstanceAcquireRequest) (*types.InstanceAllocation, error) {
					return &types.InstanceAllocation{
						Instance: &types.Instance{
							InstanceType: "bbb",
							InstanceID:   "ccc",
						},
						AllocationID: "aaa",
					}, nil
				})
			defer p.Reset()
			insAlloc, err := bcs.scheduleRequest(&types.InstanceAcquireRequest{})
			convey.So(err, convey.ShouldBeNil)
			convey.So(insAlloc.Instance.InstanceID, convey.ShouldEqual, "ccc")
		})
		convey.Convey("acquire failed", func() {
			bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
				FuncKey:          "testFunction",
				InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 2},
			}, resspeckey.ResSpecKey{}, "",
				queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
				queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
			bcs.isFuncOwner = false
			p := gomonkey.ApplyFunc((*basicConcurrencyScheduler).acquireInstanceInternal,
				func(_ *basicConcurrencyScheduler,
					queue queue.Queue, request *types.InstanceAcquireRequest) (*types.InstanceAllocation, error) {
					return nil, fmt.Errorf("error")
				})
			defer p.Reset()
			insAlloc, err := bcs.scheduleRequest(&types.InstanceAcquireRequest{})
			convey.So(err, convey.ShouldNotBeNil)
			convey.So(insAlloc, convey.ShouldBeNil)
		})
		convey.Convey("session bind", func() {
			bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
				FuncKey:          "testFunction",
				InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 1},
			}, resspeckey.ResSpecKey{}, "",
				queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
				queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
			bcs.isFuncOwner = true
			bcs.AddInstance(&types.Instance{
				InstanceID:    "instance1",
				ConcurrentNum: 1,
				InstanceStatus: commonTypes.InstanceStatus{
					Code: int32(constant.KernelInstanceStatusRunning),
				},
			})
			insAcqReq := &types.InstanceAcquireRequest{
				InstanceSession: commonTypes.InstanceSessionConfig{
					SessionID:   "123",
					SessionTTL:  10,
					Concurrency: 1,
				},
			}
			insAlloc1, err := bcs.AcquireInstance(insAcqReq)
			convey.So(err, convey.ShouldBeNil)
			convey.So(insAlloc1.Instance.InstanceID, convey.ShouldEqual, "instance1")
			_, err = bcs.scheduleRequest(insAcqReq)
			convey.So(err, convey.ShouldNotBeNil)
			err = insAlloc1.Lease.Release()
			convey.So(err, convey.ShouldBeNil)
			insAlloc2, err := bcs.scheduleRequest(insAcqReq)
			convey.So(err, convey.ShouldBeNil)
			convey.So(insAlloc2.Instance.InstanceID, convey.ShouldEqual, "instance1")
		})
	})
}

func TestInstanceQueueWithSubHealthAndEvictingRecord(t *testing.T) {
	convey.Convey("Test instanceQueueWithSubHealthAndEvictingRecord", t, func() {
		// 创建 mock queue 和 mock instanceElement
		newInstanceQueue := func() *instanceQueueWithSubHealthAndEvictingRecord {
			mockQueue := queue.NewFifoQueue(getInstanceID)
			mockSubHealthRecord := make(map[string]*instanceElement)
			mockEvictingRecord := make(map[string]*instanceElement)
			return &instanceQueueWithSubHealthAndEvictingRecord{
				instanceQueue:   mockQueue,
				subHealthRecord: mockSubHealthRecord,
				evictingRecord:  mockEvictingRecord,
			}
		}
		iq := newInstanceQueue()

		insID := "test-instance-id"
		insElem := &instanceElement{
			instance: &types.Instance{
				InstanceID: insID,
				InstanceStatus: commonTypes.InstanceStatus{
					Code: int32(constant.KernelInstanceStatusRunning),
				},
			},
		}

		convey.Convey("PushBack, instance already exists", func() {
			iq = newInstanceQueue()
			iq.subHealthRecord[insID] = insElem
			err := iq.PushBack(insElem)
			convey.So(err, convey.ShouldEqual, scheduler.ErrInsAlreadyExist)
		})

		convey.Convey("PopSubHealth, subHealthRecord is empty", func() {
			iq = newInstanceQueue()
			result := iq.PopSubHealth()
			convey.So(result, convey.ShouldBeNil)
		})

		convey.Convey("GetByID should return instance from subHealthRecord", func() {
			iq = newInstanceQueue()
			iq.subHealthRecord[insID] = insElem
			result := iq.GetByID(insID)
			convey.So(result, convey.ShouldEqual, insElem)
		})

		convey.Convey("DelByID", func() {
			iq = newInstanceQueue()
			iq.PushBack(insElem)

			err := iq.DelByID(insID)
			assert.NoError(t, err)
			assert.False(t, iq.subHealthRecord[insID] != nil)
			assert.False(t, iq.evictingRecord[insID] != nil)
			assert.Nil(t, iq.instanceQueue.GetByID(insID))
		})

		convey.Convey("complex", func() {
			iq = newInstanceQueue()
			err := iq.PushBack(insElem)
			convey.So(err, convey.ShouldBeNil)
			convey.So(iq.instanceQueue.GetByID(insElem.instance.InstanceID) == nil, convey.ShouldBeFalse)
			convey.So(len(iq.subHealthRecord), convey.ShouldEqual, 0)
			convey.So(len(iq.evictingRecord), convey.ShouldEqual, 0)
			convey.So(iq.Len(), convey.ShouldEqual, 1)

			insElem.instance.InstanceStatus.Code = int32(constant.KernelInstanceStatusSubHealth)
			err = iq.UpdateObjByID(insID, insElem)
			convey.So(err, convey.ShouldBeNil)
			convey.So(iq.instanceQueue.GetByID(insElem.instance.InstanceID) == nil, convey.ShouldBeTrue)
			_, ok := iq.subHealthRecord[insID]
			convey.So(ok, convey.ShouldBeTrue)
			convey.So(len(iq.evictingRecord), convey.ShouldEqual, 0)
			convey.So(iq.Len(), convey.ShouldEqual, 1)

			insElem.instance.InstanceStatus.Code = int32(constant.KernelInstanceStatusEvicting)
			err = iq.UpdateObjByID(insID, insElem)
			convey.So(err, convey.ShouldBeNil)
			convey.So(iq.instanceQueue.GetByID(insElem.instance.InstanceID) == nil, convey.ShouldBeTrue)
			_, ok = iq.evictingRecord[insID]
			convey.So(ok, convey.ShouldBeTrue)
			convey.So(len(iq.subHealthRecord), convey.ShouldEqual, 0)
			convey.So(iq.Len(), convey.ShouldEqual, 0)
		})
	})
}

// newTestQueue 是一个辅助函数，用于创建一个带有预填充数据的测试实例
func newTestQueue(main, sub, evicting map[string]*instanceElement) *instanceQueueWithSubHealthAndEvictingRecord {
	// 注意：这里假设 instanceQueueWithSubHealthAndEvictingRecord 有一个可以接收 mockInstances 的构造函数
	// 或者它的成员变量可以直接赋值。如果您的实现不同，需要调整这部分。
	// 为了演示，我们假设可以这样初始化：
	instanceQueue := queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance)
	for _, ins := range main {
		instanceQueue.PushBack(ins)
	}
	q := &instanceQueueWithSubHealthAndEvictingRecord{
		// 假设 instanceQueue 是一个实现了 Range 和 SortedRange 的类型
		// 这里我们用一个 mockInstances 来模拟它
		instanceQueue:   instanceQueue,
		subHealthRecord: sub,
		evictingRecord:  evicting,
	}
	return q
}

func getMockInstanceMap(instanceNames []string, statusCode int32) map[string]*instanceElement {
	instanceMap := make(map[string]*instanceElement)
	for _, name := range instanceNames {
		ins := &instanceElement{
			instance: &types.Instance{
				InstanceStatus: commonTypes.InstanceStatus{
					Code: statusCode,
				},
				ResKey:     resspeckey.ResSpecKey{},
				InstanceID: name,
			},
			threadMap: make(map[string]struct{}),
		}
		instanceMap[name] = ins
	}
	return instanceMap
}

// --- Test Cases for Range ---
func TestInstanceQueue_Range(t *testing.T) {
	convey.Convey("TestInstanceQueue_Range", t, func() {
		testInstanceQueueWithSubHealthAndEvictingRecord := newTestQueue(
			getMockInstanceMap([]string{"a", "b", "c", "d", "e"}, int32(constant.KernelInstanceStatusRunning)),
			getMockInstanceMap([]string{"d", "e", "f"}, int32(constant.KernelInstanceStatusSubHealth)),
			getMockInstanceMap([]string{"e", "f", "g"}, int32(constant.KernelInstanceStatusEvicting)))

		var testInstance *instanceElement

		vistedStr := "d visited"
		testInstanceId := "d"
		testFunc := func(obj interface{}) bool {
			ins, ok := obj.(*instanceElement)
			if !ok {
				return true
			}
			ins.instance.FuncKey = vistedStr
			if ins.instance.InstanceID == testInstanceId {
				testInstance = ins
				return false
			}
			return true
		}
		testInstanceQueueWithSubHealthAndEvictingRecord.Range(testFunc)
		convey.So(testInstance, convey.ShouldNotBeNil)
		convey.So(testInstance.instance.InstanceID, convey.ShouldEqual, testInstanceId)
		for _, ins := range testInstanceQueueWithSubHealthAndEvictingRecord.subHealthRecord {
			convey.So(ins.instance.FuncKey, convey.ShouldNotEqual, vistedStr)
		}
		for _, ins := range testInstanceQueueWithSubHealthAndEvictingRecord.evictingRecord {
			convey.So(ins.instance.FuncKey, convey.ShouldNotEqual, vistedStr)
		}

		vistedStr = "d sortrange visited"
		testInstanceQueueWithSubHealthAndEvictingRecord.SortedRange(testFunc)
		convey.So(testInstance.instance.InstanceID, convey.ShouldEqual, testInstanceId)
		for _, ins := range testInstanceQueueWithSubHealthAndEvictingRecord.subHealthRecord {
			convey.So(ins.instance.FuncKey, convey.ShouldNotEqual, vistedStr)
		}
		for _, ins := range testInstanceQueueWithSubHealthAndEvictingRecord.evictingRecord {
			convey.So(ins.instance.FuncKey, convey.ShouldNotEqual, vistedStr)
		}

		testInstanceId = "f"
		testInstanceQueueWithSubHealthAndEvictingRecord.Range(testFunc)
		convey.So(testInstance.instance.InstanceID, convey.ShouldEqual, testInstanceId)
		convey.So(testInstance.instance.InstanceStatus.Code, convey.ShouldEqual, int32(constant.KernelInstanceStatusSubHealth))
		testInstance = nil
		testInstanceQueueWithSubHealthAndEvictingRecord.SortedRange(testFunc)
		convey.So(testInstance.instance.InstanceID, convey.ShouldEqual, testInstanceId)
		convey.So(testInstance.instance.InstanceStatus.Code, convey.ShouldEqual, int32(constant.KernelInstanceStatusSubHealth))

		testInstanceId = "g"
		testInstanceQueueWithSubHealthAndEvictingRecord.Range(testFunc)
		convey.So(testInstance.instance.InstanceID, convey.ShouldEqual, testInstanceId)
		convey.So(testInstance.instance.InstanceStatus.Code, convey.ShouldEqual, int32(constant.KernelInstanceStatusEvicting))
		testInstance = nil
		testInstanceQueueWithSubHealthAndEvictingRecord.SortedRange(testFunc)
		convey.So(testInstance.instance.InstanceID, convey.ShouldEqual, testInstanceId)
		convey.So(testInstance.instance.InstanceStatus.Code, convey.ShouldEqual, int32(constant.KernelInstanceStatusEvicting))
	})
}

func TestAcquireInstanceWithSessionFullConcurrency(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 4},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.HandleFuncOwnerUpdate(true)
	checkInUseInsThd := 0
	checkAvailInsThd := 0
	bcs.addObservers(scheduler.InUseInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkInUseInsThd += delta
	})
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  4,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	acqIns, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{
			SessionID:   "session1",
			Concurrency: -1,
		},
	})
	assert.Nil(t, err)
	assert.Equal(t, "instance1", acqIns.Instance.InstanceID)
	assert.Equal(t, 4, checkInUseInsThd)
	assert.Equal(t, 0, checkAvailInsThd)
	record, exist := bcs.sessionManager.getSession("session1")
	assert.True(t, exist)
	assert.Equal(t, 4, record.concurrency)
}

func TestAcquireInstanceWithSessionFullConcurrencyInsufficient(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 4},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.HandleFuncOwnerUpdate(true)
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  4,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	acqIns1, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{})
	assert.Nil(t, err)
	assert.Equal(t, "instance1", acqIns1.Instance.InstanceID)
	acqIns2, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{
			SessionID:   "session1",
			Concurrency: -1,
		},
	})
	assert.Equal(t, scheduler.ErrNoInsAvailable, err)
	assert.Nil(t, acqIns2)
}

func TestAcquireReleaseSessionFullConcurrency(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 4},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.HandleFuncOwnerUpdate(true)
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  4,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	acqIns1, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{
			SessionID:   "session1",
			Concurrency: -1,
		},
	})
	assert.Nil(t, err)
	assert.Equal(t, "instance1", acqIns1.Instance.InstanceID)
	record, exist := bcs.sessionManager.getSession("session1")
	assert.True(t, exist)
	assert.Equal(t, 3, len(record.availThdMap))
	acqIns2, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{
			SessionID:   "session1",
			Concurrency: -1,
		},
	})
	assert.Nil(t, err)
	assert.Equal(t, "instance1", acqIns2.Instance.InstanceID)
	assert.Equal(t, 2, len(record.availThdMap))
	err = bcs.ReleaseInstance(acqIns1)
	assert.Nil(t, err)
	assert.Equal(t, 3, len(record.availThdMap))
	err = bcs.ReleaseInstance(acqIns2)
	assert.Nil(t, err)
	assert.Equal(t, 4, len(record.availThdMap))
}

func TestAcquireInstanceWithSessionFullConcurrencyMonopolyChoosesFullyIdleInstance(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 4},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.HandleFuncOwnerUpdate(true)
	assert.NoError(t, bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  4,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	}))
	assert.NoError(t, bcs.AddInstance(&types.Instance{
		InstanceID:     "instance2",
		ConcurrentNum:  4,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	}))
	// Occupy one thread on instance2 so it is no longer fully idle.
	normalAlloc, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{DesignateInstanceID: "instance2"})
	assert.NoError(t, err)
	assert.Equal(t, "instance2", normalAlloc.Instance.InstanceID)
	fullAlloc, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{
			SessionID:   "session-full",
			Concurrency: -1,
		},
	})
	assert.NoError(t, err)
	assert.Equal(t, "instance1", fullAlloc.Instance.InstanceID)
	record, exist := bcs.sessionManager.getSession("session-full")
	assert.True(t, exist)
	assert.Equal(t, 4, record.concurrency)
	assert.Equal(t, 4, len(record.allocThdMap))
}

func TestSessionFullConcurrencyTTLExpire(t *testing.T) {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 4},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	mockTimer := time.NewTimer(100 * time.Millisecond)
	bcs.newSessionExpireTimer = func(d time.Duration) *time.Timer {
		mockTimer.Reset(100 * time.Millisecond)
		return mockTimer
	}
	bcs.HandleFuncOwnerUpdate(true)
	checkInUseInsThd := 0
	checkAvailInsThd := 0
	bcs.addObservers(scheduler.InUseInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkInUseInsThd += delta
	})
	bcs.addObservers(scheduler.AvailInsThdTopic, func(obj interface{}) {
		delta := obj.(int)
		checkAvailInsThd += delta
	})
	bcs.AddInstance(&types.Instance{
		InstanceID:     "instance1",
		ConcurrentNum:  4,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
	acqIns, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{
			SessionID:   "session1",
			SessionTTL:  1,
			Concurrency: -1,
		},
	})
	assert.Nil(t, err)
	assert.Equal(t, "instance1", acqIns.Instance.InstanceID)
	assert.Equal(t, 4, checkInUseInsThd)
	assert.Equal(t, 0, checkAvailInsThd)
	assert.NotNil(t, acqIns, "acqIns must not be nil before ReleaseInstance")
	err = bcs.ReleaseInstance(acqIns)
	assert.Nil(t, err)
	assert.Equal(t, 4, checkInUseInsThd)
	assert.Equal(t, 0, checkAvailInsThd)
	time.Sleep(150 * time.Millisecond)
	assert.Equal(t, 0, checkInUseInsThd)
	assert.Equal(t, 4, checkAvailInsThd)
	_, exist := bcs.sessionManager.getSession("session1")
	assert.False(t, exist)
}

// fakeSessionStore 记录 Save/Get/Delete 调用，用于懒恢复路径验证。
type fakeSessionStore struct {
	mu      sync.Mutex
	records map[string]*session.StoreRecord
	saveCnt int
	getCnt  int
	delCnt  int
	getErr  error
	saveErr error // 注入后 Save 返回该错误，用于验证 fail-open 不阻断主流程
}

func newFakeSessionStore() *fakeSessionStore {
	return &fakeSessionStore{records: make(map[string]*session.StoreRecord)}
}

func (f *fakeSessionStore) Save(key string, record session.StoreRecord) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.saveCnt++
	if f.saveErr != nil {
		return f.saveErr
	}
	cp := record
	f.records[key] = &cp
	return nil
}

func (f *fakeSessionStore) Get(key string) (*session.StoreRecord, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.getCnt++
	if f.getErr != nil {
		return nil, f.getErr
	}
	if r, ok := f.records[key]; ok {
		cp := *r
		return &cp, nil
	}
	return nil, nil
}

func (f *fakeSessionStore) Delete(key string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.delCnt++
	delete(f.records, key)
	return nil
}

func (f *fakeSessionStore) Backend() string { return "fake" }

func (f *fakeSessionStore) saveCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.saveCnt
}
func (f *fakeSessionStore) getCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.getCnt
}
func (f *fakeSessionStore) delCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.delCnt
}

// newBcsWithStore 构造一个 owner scheduler 并注入 fake store，便于懒恢复测试。
func newBcsWithStore(store session.Store) basicConcurrencyScheduler {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 4},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.sessionManager.coord = session.NewCoordinator(store)
	bcs.HandleFuncOwnerUpdate(true)
	return bcs
}

func addTestInstance(bcs *basicConcurrencyScheduler, instanceID string, concurrentNum int) {
	bcs.AddInstance(&types.Instance{
		InstanceID:     instanceID,
		ConcurrentNum:  concurrentNum,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	})
}

// TestLocalSessionHitDoesNotAccessStore: 本地命中时不访问外部存储。第一次 acquire 走
// miss→store miss→bind→Save；第二次 acquire 本地命中，store.Get/Save 不增加。
func TestLocalSessionHitDoesNotAccessStore(t *testing.T) {
	store := newFakeSessionStore()
	bcs := newBcsWithStore(store)
	addTestInstance(&bcs, "instance1", 4)
	acqIns1, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{SessionID: "s1", Concurrency: 2},
	})
	assert.NoError(t, err)
	assert.Equal(t, "instance1", acqIns1.Instance.InstanceID)
	assert.Equal(t, 1, store.getCount())
	bcs.sessionManager.coord.Drain(time.Second)
	assert.Equal(t, 1, store.saveCount())
	acqIns2, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{SessionID: "s1", Concurrency: 2},
	})
	assert.NoError(t, err)
	assert.Equal(t, "instance1", acqIns2.Instance.InstanceID)
	assert.Equal(t, 1, store.getCount(), "local hit must not access store")
	assert.Equal(t, 1, store.saveCount(), "local hit must not save")
}

// TestLocalMissStoreMissCreatesSessionAndSaves: 本地 miss + 外部 miss → 新绑定并 Save。
func TestLocalMissStoreMissCreatesSessionAndSaves(t *testing.T) {
	store := newFakeSessionStore()
	bcs := newBcsWithStore(store)
	addTestInstance(&bcs, "instance1", 4)
	acqIns, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{SessionID: "s1", Concurrency: 2},
	})
	assert.NoError(t, err)
	assert.Equal(t, "instance1", acqIns.Instance.InstanceID)
	assert.Equal(t, 1, store.getCount())
	bcs.sessionManager.coord.Drain(time.Second)
	assert.Equal(t, 1, store.saveCount())
	rec, exist := bcs.sessionManager.getSession("s1")
	assert.True(t, exist)
	assert.Equal(t, 2, rec.concurrency)
}

// TestLocalMissStoreHitLazyRecoversAndRefreshes: 本地 miss + 外部 hit + 实例可用 → 懒恢复
// 并用 store 的 Concurrency 重建绑定（TTL 用请求的，与缓存命中路径对齐），Save 刷新物理 TTL。
func TestLocalMissStoreHitLazyRecoversAndRefreshes(t *testing.T) {
	store := newFakeSessionStore()
	sessionKey := "s1"
	// 预置外部记录：绑定 instance1，原始 concurrency=2, ttl=100s
	_ = store.Save(sessionKey, session.StoreRecord{
		InstanceID: "instance1", SessionID: sessionKey, SessionTTL: 100, Concurrency: 2,
	})
	store.saveCnt = 0 // 重置计数，只观测懒恢复期间的 Save
	bcs := newBcsWithStore(store)
	addTestInstance(&bcs, "instance1", 4)
	// 请求携带 Concurrency=0（哨兵），懒恢复时应被 store 的 Concurrency=2 覆盖；
	// TTL=100 由请求直接提供（applyStoreDesignate 不再覆写 TTL，与缓存命中路径对齐）。
	acqIns, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{SessionID: sessionKey,
			SessionTTL: 100, Concurrency: 0},
	})
	assert.NoError(t, err)
	assert.Equal(t, "instance1", acqIns.Instance.InstanceID)
	assert.Equal(t, 1, store.getCount(), "local miss must query store")
	bcs.sessionManager.coord.Drain(time.Second)
	assert.Equal(t, 1, store.saveCount(), "lazy recover must refresh store")
	rec, exist := bcs.sessionManager.getSession(sessionKey)
	assert.True(t, exist)
	assert.Equal(t, 2, rec.concurrency, "concurrency restored from store record")
	assert.Equal(t, 100*time.Second, rec.ttl, "ttl from request (not overwritten by store)")
}

// TestLocalMissStoreHitInstanceGoneDeletesAndRebinds: 本地 miss + 外部 hit 但实例已不在
// 任何队列 → 删除外部旧记录并回退到新 session 绑定到可用实例。
func TestLocalMissStoreHitInstanceGoneDeletesAndRebinds(t *testing.T) {
	store := newFakeSessionStore()
	sessionKey := "s1"
	_ = store.Save(sessionKey, session.StoreRecord{
		InstanceID: "instance-gone", SessionID: sessionKey, SessionTTL: 100, Concurrency: 2,
	})
	store.saveCnt = 0
	bcs := newBcsWithStore(store)
	addTestInstance(&bcs, "instance1", 4) // instance-gone 不在队列
	acqIns, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{SessionID: sessionKey, Concurrency: 2},
	})
	assert.NoError(t, err)
	assert.Equal(t, "instance1", acqIns.Instance.InstanceID)
	assert.Equal(t, 1, store.getCount(), "must query store on local miss")
	bcs.sessionManager.coord.Drain(time.Second)
	assert.Equal(t, 1, store.delCount(), "stale store record must be deleted")
	assert.Equal(t, 1, store.saveCount(), "new binding must save")
	// 外部记录应被新绑定覆盖
	rec, _ := store.Get(sessionKey)
	assert.NotNil(t, rec)
	assert.Equal(t, "instance1", rec.InstanceID)
}

// TestSessionExpireDeletesStoreRecord: session 正常过期解绑时删除外部记录。
func TestSessionExpireDeletesStoreRecord(t *testing.T) {
	defer gomonkey.ApplyFunc((*lease.GenericInstanceLeaseManager).CreateInstanceLease,
		func(_ *lease.GenericInstanceLeaseManager,
			insAlloc *types.InstanceAllocation, interval time.Duration, callback func()) (types.InstanceLease, error) {
			return nil, nil
		}).Reset()
	store := newFakeSessionStore()
	bcs := newBcsWithStore(store)
	mockTimer := time.NewTimer(100 * time.Millisecond)
	bcs.newSessionExpireTimer = func(d time.Duration) *time.Timer {
		mockTimer.Reset(100 * time.Millisecond)
		return mockTimer
	}
	addTestInstance(&bcs, "instance1", 4)
	acqIns, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{SessionID: "s1", SessionTTL: 1, Concurrency: -1},
	})
	assert.NoError(t, err)
	bcs.sessionManager.coord.Drain(time.Second)
	assert.Equal(t, 1, store.saveCount())
	assert.NotNil(t, acqIns, "acqIns must not be nil before ReleaseInstance")
	err = bcs.ReleaseInstance(acqIns)
	assert.Nil(t, err)
	time.Sleep(150 * time.Millisecond)
	bcs.sessionManager.coord.Drain(time.Second)
	assert.Equal(t, 1, store.delCount(), "session expire must delete store record")
	_, exist := bcs.sessionManager.getSession("s1")
	assert.False(t, exist)
}

// TestSessionStoreKeyWithSessionCtx: EnableSessionCtx 开启时，同一 sessionID 不同
// sessionCtxID 在外部存储落到不同 key（sessionManager 把逻辑 cache key 透传给 store）。
func TestSessionStoreKeyWithSessionCtx(t *testing.T) {
	store := newFakeSessionStore()
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 4},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.sessionManager.coord = session.NewCoordinator(store)
	bcs.HandleFuncOwnerUpdate(true)
	instance := &types.Instance{InstanceID: "instance1", ConcurrentNum: 4,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)}}
	// 直接用两个不同逻辑 cache key 调 addSession，验证 store 收到不同 key
	bcs.sessionManager.addSession("s1\x00ctxA", &sessionRecord{
		sessionID: "s1", sessionCtxID: "ctxA", concurrency: 1,
		insElem: &instanceElement{instance: instance},
	})
	bcs.sessionManager.addSession("s1\x00ctxB", &sessionRecord{
		sessionID: "s1", sessionCtxID: "ctxB", concurrency: 1,
		insElem: &instanceElement{instance: instance},
	})
	bcs.sessionManager.coord.Drain(time.Second)
	assert.Equal(t, 2, len(store.records), "different sessionCtx must produce different store keys")
}

// newBcsWithStoreAndSessionCtx 构造 EnableSessionCtx=true 的 owner scheduler，
// 用于 sessionCtx 维度的懒恢复与 designate 路径测试。
func newBcsWithStoreAndSessionCtx(store session.Store) basicConcurrencyScheduler {
	bcs := newBasicConcurrencyScheduler(&types.FunctionSpecification{
		FuncKey:          "testFunction",
		InstanceMetaData: commonTypes.InstanceMetaData{ConcurrentNum: 4},
		ExtendedMetaData: commonTypes.ExtendedMetaData{EnableSessionCtx: true},
	}, resspeckey.ResSpecKey{}, "",
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance),
		queue.NewPriorityQueue(getInstanceID, priorityFuncForReservedInstance))
	bcs.sessionManager.coord = session.NewCoordinator(store)
	bcs.HandleFuncOwnerUpdate(true)
	return bcs
}

// addTestInstanceWithCtx 添加一个带 SessionCtxID 的实例到 self queue。
func addTestInstanceWithCtx(bcs *basicConcurrencyScheduler, instanceID, sessionCtxID string, concurrentNum int) {
	ctxID := sessionCtxID
	bcs.AddInstance(&types.Instance{
		InstanceID:     instanceID,
		ConcurrentNum:  concurrentNum,
		ResKey:         resspeckey.ResSpecKey{},
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
		SessionCtxID:   &ctxID,
	})
}

// addTestInstanceToOtherQueue 手动把实例塞到 other queue，绕过 checkSelfInstance 路由，
// 用于 routeDesignateInstance 的 other-queue 命中分支测试。
func addTestInstanceToOtherQueue(bcs *basicConcurrencyScheduler, instanceID string, concurrentNum int) {
	insElem := &instanceElement{
		instance: &types.Instance{
			InstanceID:     instanceID,
			ConcurrentNum:  concurrentNum,
			ResKey:         resspeckey.ResSpecKey{},
			InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
		},
		isNewInstance: true,
	}
	insElem.initThreadMap()
	_ = bcs.otherInstanceQueue.PushBack(insElem)
}

// TestAcquireEarlyReturnNoStoreAccess: DesignateInstanceID 已设置或 SessionID 为空时，
// acquire 路径不查 store（peekLocalSession 早返回 sessionKey=""，Phase 2 跳过）。
func TestAcquireEarlyReturnNoStoreAccess(t *testing.T) {
	defer gomonkey.ApplyFunc((*lease.GenericInstanceLeaseManager).CreateInstanceLease,
		func(_ *lease.GenericInstanceLeaseManager,
			insAlloc *types.InstanceAllocation, interval time.Duration, callback func()) (types.InstanceLease, error) {
			return nil, nil
		}).Reset()
	store := newFakeSessionStore()
	bcs := newBcsWithStore(store)
	addTestInstance(&bcs, "instance1", 4)

	// 分支1: DesignateInstanceID 已设置 → 不查 store
	req1 := &types.InstanceAcquireRequest{
		DesignateInstanceID: "instance1",
		InstanceSession:     commonTypes.InstanceSessionConfig{SessionID: "s1", Concurrency: 2},
	}
	acqIns, err := bcs.AcquireInstance(req1)
	assert.NoError(t, err)
	assert.Equal(t, "instance1", acqIns.Instance.InstanceID)
	assert.Equal(t, 0, store.getCount(), "must not query store when DesignateInstanceID is preset")

	// 分支2: SessionID 为空 → 不查 store
	req2 := &types.InstanceAcquireRequest{}
	acqIns2, err := bcs.AcquireInstance(req2)
	assert.NoError(t, err)
	assert.Equal(t, "instance1", acqIns2.Instance.InstanceID)
	assert.Equal(t, 0, store.getCount(), "must not query store when SessionID is empty")
}

// TestAcquireStoreErrorFailOpen: 本地 miss + store.Get 出错 → fail-open, 不填
// DesignateInstanceID，按新 session 绑定，acquire 仍成功。
func TestAcquireStoreErrorFailOpen(t *testing.T) {
	defer gomonkey.ApplyFunc((*lease.GenericInstanceLeaseManager).CreateInstanceLease,
		func(_ *lease.GenericInstanceLeaseManager,
			insAlloc *types.InstanceAllocation, interval time.Duration, callback func()) (types.InstanceLease, error) {
			return nil, nil
		}).Reset()
	store := newFakeSessionStore()
	store.getErr = errors.New("redis down")
	bcs := newBcsWithStore(store)
	addTestInstance(&bcs, "instance1", 4)

	acqIns, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{SessionID: "s1", Concurrency: 2},
	})
	assert.NoError(t, err, "store error must not block acquire (fail-open)")
	assert.Equal(t, "instance1", acqIns.Instance.InstanceID)
	assert.Equal(t, 1, store.getCount(), "local miss must query store once")
}

// TestRouteDesignateInstanceThreeBranches: routeDesignateInstance 三分支
//   - designate 在 self queue → true
//   - designate 在 other queue → false
//   - designate 都不在 → 保留原 useSelfInstance
func TestRouteDesignateInstanceThreeBranches(t *testing.T) {
	store := newFakeSessionStore()
	bcs := newBcsWithStore(store)
	addTestInstance(&bcs, "instance-self", 4)              // self queue
	addTestInstanceToOtherQueue(&bcs, "instance-other", 4) // other queue

	// 分支1: designate 在 self queue
	got := bcs.routeDesignateInstance(&types.InstanceAcquireRequest{
		DesignateInstanceID: "instance-self",
	}, false)
	assert.True(t, got, "designate in self queue must return true")

	// 分支2: designate 在 other queue
	got = bcs.routeDesignateInstance(&types.InstanceAcquireRequest{
		DesignateInstanceID: "instance-other",
	}, true)
	assert.False(t, got, "designate in other queue must return false")

	// 分支3: designate 都不在 → 保留原值（true / false 各测一次）
	got = bcs.routeDesignateInstance(&types.InstanceAcquireRequest{
		DesignateInstanceID: "not-exist",
	}, true)
	assert.True(t, got, "designate in neither queue must preserve original useSelfInstance=true")
	got = bcs.routeDesignateInstance(&types.InstanceAcquireRequest{
		DesignateInstanceID: "not-exist",
	}, false)
	assert.False(t, got, "designate in neither queue must preserve original useSelfInstance=false")

	// 空 designate → 保留原值（短路返回）
	got = bcs.routeDesignateInstance(&types.InstanceAcquireRequest{}, true)
	assert.True(t, got, "empty designate must short-circuit and preserve original")
}

// TestAcquireDesignateInstanceSessionCtxMismatchDeletesStore: 懒恢复命中但实例 SessionCtx
// 与请求不匹配 → 视为陈旧记录，删除外部记录。这是 commit 新增的分支（acquireDesignateInstance:969）。
func TestAcquireDesignateInstanceSessionCtxMismatchDeletesStore(t *testing.T) {
	store := newFakeSessionStore()
	bcs := newBcsWithStoreAndSessionCtx(store)
	addTestInstanceWithCtx(&bcs, "instance1", "ctx-instance", 4)

	// 预置外部记录：sessionKey = "s1\x00ctx-req"（EnableSessionCtx 时 cacheKey 拼接规则）
	// 指向 instance1，但 instance1 的 SessionCtxID="ctx-instance" ≠ 请求 "ctx-req"
	sessionKey := "s1\x00ctx-req"
	_ = store.Save(sessionKey, session.StoreRecord{
		InstanceID: "instance1", SessionID: "s1", SessionTTL: 100, Concurrency: 2,
	})
	store.saveCnt = 0 // 重置，只观测 acquire 期间的 Save

	// acquire：本地 miss → store hit → 填 DesignateInstanceID="instance1"
	// → acquireDesignateInstance → matchesSessionCtx("ctx-instance", "ctx-req")=false → delSession
	acqIns, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{SessionID: "s1", Concurrency: 2},
		SessionCtxID:    "ctx-req",
	})
	// 没有匹配 sessionCtx 的可用实例，acquire 失败是预期；关键是验证旧 store 记录被删
	assert.Error(t, err, "no instance matches sessionCtx, acquire must fail")
	assert.Nil(t, acqIns)
	assert.Equal(t, 1, store.getCount(), "must query store on local miss")
	bcs.sessionManager.coord.Drain(time.Second)
	assert.Equal(t, 1, store.delCount(), "stale store record (sessionCtx mismatch) must be deleted")
	assert.Equal(t, 0, store.saveCount(), "no new binding should be saved when acquire fails")
}

// TestSaveSessionToStoreFailureDoesNotBlockAcquire: store.Save 返回错误时，fail-open
// 不阻断主流程，acquire 仍成功。覆盖 saveSessionToStore 的 fail-open 行为。
func TestSaveSessionToStoreFailureDoesNotBlockAcquire(t *testing.T) {
	defer gomonkey.ApplyFunc((*lease.GenericInstanceLeaseManager).CreateInstanceLease,
		func(_ *lease.GenericInstanceLeaseManager,
			insAlloc *types.InstanceAllocation, interval time.Duration, callback func()) (types.InstanceLease, error) {
			return nil, nil
		}).Reset()
	store := newFakeSessionStore()
	store.saveErr = errors.New("redis write down")
	bcs := newBcsWithStore(store)
	addTestInstance(&bcs, "instance1", 4)

	acqIns, err := bcs.AcquireInstance(&types.InstanceAcquireRequest{
		InstanceSession: commonTypes.InstanceSessionConfig{SessionID: "s1", Concurrency: 2},
	})
	assert.NoError(t, err, "store Save failure must not block acquire (fail-open)")
	assert.Equal(t, "instance1", acqIns.Instance.InstanceID)
	bcs.sessionManager.coord.Drain(time.Second)
	assert.Equal(t, 1, store.saveCount(), "save must be attempted once despite error")
	// 本地 session 仍应建立（外部存储失败不影响本地绑定）
	rec, exist := bcs.sessionManager.getSession("s1")
	assert.True(t, exist, "local session must be established despite store failure")
	assert.Equal(t, 2, rec.concurrency)
}
