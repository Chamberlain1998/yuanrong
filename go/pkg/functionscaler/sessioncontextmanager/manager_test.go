/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */

package sessioncontextmanager

import (
	"context"
	"encoding/json"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/require"

	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/common/faas_common/snerror"
	commonTypes "yuanrong.org/kernel/pkg/common/faas_common/types"
	"yuanrong.org/kernel/pkg/functionscaler/litescheduler"
	"yuanrong.org/kernel/pkg/functionscaler/sessioncontextregistry"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

type memoryStore struct {
	sync.Mutex
	values map[string][]byte
}

func (s *memoryStore) Get(key, _, _ string) ([]byte, error) {
	s.Lock()
	defer s.Unlock()
	value := s.values[key]
	return append([]byte(nil), value...), nil
}

func (s *memoryStore) Put(key string, value []byte, _, _ string) error {
	s.Lock()
	defer s.Unlock()
	s.values[key] = append([]byte(nil), value...)
	return nil
}

func (s *memoryStore) Delete(key, _, _ string) error {
	s.Lock()
	defer s.Unlock()
	delete(s.values, key)
	return nil
}

type fakePool struct {
	sync.Mutex
	instances []*types.Instance
	creates   int
	deletes   int
}

type blockingCreatePool struct {
	entered chan string
	release chan struct{}
}

func (p *blockingCreatePool) CreateInstance(request *types.InstanceCreateRequest) (*types.Instance, snerror.SNError) {
	p.entered <- *request.SessionCtxID
	<-p.release
	return &types.Instance{FuncKey: request.FuncSpec.FuncKey, SessionCtxID: request.SessionCtxID}, nil
}

func (p *blockingCreatePool) DeleteInstance(*types.Instance) snerror.SNError { return nil }

func (p *blockingCreatePool) ListInstances(string) []*types.Instance { return nil }

func (p *fakePool) CreateInstance(request *types.InstanceCreateRequest) (*types.Instance, snerror.SNError) {
	p.Lock()
	defer p.Unlock()
	p.creates++
	instance := &types.Instance{
		InstanceID: "instance", FuncKey: request.FuncSpec.FuncKey, SessionCtxID: request.SessionCtxID,
		InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)},
	}
	p.instances = append(p.instances, instance)
	return instance, nil
}

func (p *fakePool) DeleteInstance(instance *types.Instance) snerror.SNError {
	p.Lock()
	defer p.Unlock()
	p.deletes++
	for index, current := range p.instances {
		if current.InstanceID == instance.InstanceID {
			p.instances = append(p.instances[:index], p.instances[index+1:]...)
			break
		}
	}
	return nil
}

func (p *fakePool) ListInstances(_ string) []*types.Instance {
	p.Lock()
	defer p.Unlock()
	result := make([]*types.Instance, len(p.instances))
	copy(result, p.instances)
	return result
}

func (p *fakePool) createCount() int {
	p.Lock()
	defer p.Unlock()
	return p.creates
}

func newManagerTestPool() *fakePool {
	return &fakePool{}
}

type fakeRegistry struct {
	sync.Mutex
	entries map[string]bool
}

func (r *fakeRegistry) Register(request sessioncontextregistry.Request, _ string) error {
	r.Lock()
	defer r.Unlock()
	r.entries[request.FunctionVersion+"\x00"+request.SessionContextID] = true
	return nil
}

func (r *fakeRegistry) Unregister(request sessioncontextregistry.Request, _ string) error {
	r.Lock()
	defer r.Unlock()
	delete(r.entries, request.FunctionVersion+"\x00"+request.SessionContextID)
	return nil
}

func (r *fakeRegistry) Exists(request sessioncontextregistry.Request, _ string) (bool, error) {
	r.Lock()
	defer r.Unlock()
	return r.entries[request.FunctionVersion+"\x00"+request.SessionContextID], nil
}

func testSpec() *types.FunctionSpecification {
	return &types.FunctionSpecification{
		FuncCtx: context.Background(), FuncKey: "default/0@agent@demo/latest",
		FuncMetaData: commonTypes.FuncMetaData{
			Name: "0@agent@demo", Version: "latest", TenantID: "default",
		},
	}
}

func putJSON(t *testing.T, store *memoryStore, key string, value any) {
	raw, err := json.Marshal(value)
	require.NoError(t, err)
	store.values[key] = raw
}

func TestForkCopiesCompletedBoundaryAndDeleteCleansHistory(t *testing.T) {
	spec := testSpec()
	store := &memoryStore{values: map[string][]byte{}}
	pool := newManagerTestPool()
	registry := &fakeRegistry{entries: map[string]bool{}}
	manager := New(pool, store, registry, func(key string) *types.FunctionSpecification {
		if key == spec.FuncKey {
			return spec
		}
		return nil
	})
	putJSON(t, store, TurnKey("default", spec.FuncMetaData.Name, "latest", "source", 1), TurnRecord{
		SessionContextID: "source", TurnIndex: 1, TurnID: "turn-1", StartSeq: 1, SchemaVersion: 1,
	})
	putJSON(t, store, TurnKey("default", spec.FuncMetaData.Name, "latest", "source", 2), TurnRecord{
		SessionContextID: "source", TurnIndex: 2, TurnID: "turn-2", StartSeq: 3, SchemaVersion: 1,
	})
	putJSON(t, store, EventKey("default", spec.FuncMetaData.Name, "latest", "source", 1), Event{
		SessionContextID: "source", TurnID: "turn-1", Seq: 1, Type: "output.message", SchemaVersion: 1,
	})
	putJSON(t, store, EventKey("default", spec.FuncMetaData.Name, "latest", "source", 2), Event{
		SessionContextID: "source", TurnID: "turn-1", Seq: 2, Type: "turn.completed", SchemaVersion: 1,
	})
	putJSON(t, store, EventKey("default", spec.FuncMetaData.Name, "latest", "source", 3), Event{
		SessionContextID: "source", TurnID: "turn-2", Seq: 3, Type: "turn.failed", SchemaVersion: 1,
	})

	response, err := manager.Fork(ForkRequest{
		TenantID: "default", FuncKey: spec.FuncKey, SourceSessionCtxID: "source",
		TargetSessionCtxID: "target", TurnID: "turn-1",
	})
	require.NoError(t, err)
	require.Equal(t, "DORMANT", response.State)
	require.NotNil(t, store.values[TurnKey("default", spec.FuncMetaData.Name, "latest", "target", 1)])
	require.Nil(t, store.values[TurnKey("default", spec.FuncMetaData.Name, "latest", "target", 2)])
	require.NotNil(t, store.values[EventKey("default", spec.FuncMetaData.Name, "latest", "target", 2)])
	require.Nil(t, store.values[EventKey("default", spec.FuncMetaData.Name, "latest", "target", 3)])

	_, err = manager.Fork(ForkRequest{
		TenantID: "default", FuncKey: spec.FuncKey, SourceSessionCtxID: "source",
		TargetSessionCtxID: "target", TurnID: "turn-1",
	})
	require.NoError(t, err)

	require.NoError(t, manager.Delete(DeleteRequest{
		TenantID: "default", FuncKey: spec.FuncKey, SessionCtxID: "target",
	}))
	require.Nil(t, store.values[TurnKey("default", spec.FuncMetaData.Name, "latest", "target", 1)])
	require.Nil(t, store.values[ControlKey("default", spec.FuncMetaData.Name, "latest", "target")])
	require.Empty(t, registry.entries)
}

func TestScaleHintCreatesOneContextInstance(t *testing.T) {
	spec := testSpec()
	store := &memoryStore{values: map[string][]byte{}}
	pool := newManagerTestPool()
	manager := New(pool, store, &fakeRegistry{entries: map[string]bool{}},
		func(string) *types.FunctionSpecification { return spec })
	hint := &litescheduler.ScaleHint{
		TenantID: "default", FuncKey: spec.FuncKey, SessionCtxID: "ctx",
	}
	var wait sync.WaitGroup
	errors := make(chan error, 32)
	for index := 0; index < 32; index++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			errors <- manager.HandleScaleHint(hint, "trace")
		}()
	}
	wait.Wait()
	close(errors)
	for err := range errors {
		require.NoError(t, err)
	}
	require.Equal(t, 1, pool.createCount())
}

func TestScaleHintsForDifferentContextsCreateConcurrently(t *testing.T) {
	spec := testSpec()
	pool := &blockingCreatePool{entered: make(chan string, 2), release: make(chan struct{})}
	manager := New(pool, &memoryStore{values: map[string][]byte{}}, nil,
		func(string) *types.FunctionSpecification { return spec })

	var wait sync.WaitGroup
	errs := make(chan error, 2)
	for _, sessionCtxID := range []string{"ctx-a", "ctx-b"} {
		wait.Add(1)
		go func(id string) {
			defer wait.Done()
			errs <- manager.HandleScaleHint(&litescheduler.ScaleHint{
				TenantID: "default", FuncKey: spec.FuncKey, SessionCtxID: id,
			}, "trace")
		}(sessionCtxID)
	}

	entered := map[string]bool{}
	timer := time.NewTimer(time.Second)
	for len(entered) < 2 {
		select {
		case id := <-pool.entered:
			entered[id] = true
		case <-timer.C:
			close(pool.release)
			wait.Wait()
			t.Fatalf("different SessionContexts were serialized, entered: %v", entered)
		}
	}
	timer.Stop()
	close(pool.release)
	wait.Wait()
	close(errs)
	for err := range errs {
		require.NoError(t, err)
	}
	require.Equal(t, map[string]bool{"ctx-a": true, "ctx-b": true}, entered)
}

func TestIdleReportDeletesOnlyObservedInstance(t *testing.T) {
	ctxID := "ctx"
	pool := newManagerTestPool()
	pool.instances = []*types.Instance{
		{InstanceID: "observed", FuncKey: testSpec().FuncKey, SessionCtxID: &ctxID,
			InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)}},
		{InstanceID: "replacement", FuncKey: testSpec().FuncKey, SessionCtxID: &ctxID,
			InstanceStatus: commonTypes.InstanceStatus{Code: int32(constant.KernelInstanceStatusRunning)}},
	}
	manager := New(pool, &memoryStore{values: map[string][]byte{}}, nil,
		func(string) *types.FunctionSpecification { return testSpec() })
	require.NoError(t, manager.HandleIdle(types.IdleReport{Instances: []types.IdleInstance{{
		FuncKey: testSpec().FuncKey, SessionCtxID: ctxID, InstanceID: "observed",
	}}}))
	instances := pool.ListInstances(testSpec().FuncKey)
	require.Len(t, instances, 1)
	require.Equal(t, "replacement", instances[0].InstanceID)
}

func TestScaleHintHonorsPersistentLifecycleState(t *testing.T) {
	spec := testSpec()
	store := &memoryStore{values: map[string][]byte{}}
	manager := New(newManagerTestPool(), store, &fakeRegistry{entries: map[string]bool{}},
		func(string) *types.FunctionSpecification { return spec })
	hint := &litescheduler.ScaleHint{TenantID: "default", FuncKey: spec.FuncKey, SessionCtxID: "ctx"}

	putJSON(t, store, ControlKey("default", spec.FuncMetaData.Name, "latest", "ctx"),
		ControlRecord{SchemaVersion: 1, State: StateCreating})
	require.NoError(t, manager.HandleScaleHint(hint, "trace"))

	putJSON(t, store, ControlKey("default", spec.FuncMetaData.Name, "latest", "ctx"),
		ControlRecord{SchemaVersion: 1, State: StateDeleting})
	var managerErr *Error
	require.True(t, errors.As(manager.HandleScaleHint(hint, "trace"), &managerErr))
	require.Equal(t, "SESSION_CTX_DELETING", managerErr.Code)
}
