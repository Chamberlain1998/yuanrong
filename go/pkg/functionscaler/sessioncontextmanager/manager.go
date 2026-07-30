/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */

package sessioncontextmanager

import (
	"encoding/json"
	"sync"
	"time"

	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	"yuanrong.org/kernel/pkg/common/faas_common/snerror"
	"yuanrong.org/kernel/pkg/functionscaler/litescheduler"
	"yuanrong.org/kernel/pkg/functionscaler/sessioncontextregistry"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

const (
	copyBatchSize    = 100
	instanceStopWait = 30 * time.Second
)

type InstancePool interface {
	CreateInstance(*types.InstanceCreateRequest) (*types.Instance, snerror.SNError)
	DeleteInstance(*types.Instance) snerror.SNError
	ListInstances(funcKey string) []*types.Instance
}

type Registry interface {
	Register(sessioncontextregistry.Request, string) error
}

type registryUnregister interface {
	Unregister(sessioncontextregistry.Request, string) error
}

type registryLookup interface {
	Exists(sessioncontextregistry.Request, string) (bool, error)
}

type runtimeOperation struct {
	Creating     bool
	CreateDone   chan struct{}
	PendingScale bool
	LastError    error
}

type Manager struct {
	pool       InstancePool
	store      Store
	registry   Registry
	getFunc    func(string) *types.FunctionSpecification
	now        func() time.Time
	operations map[string]*runtimeOperation
	opMu       sync.Mutex
	locksMu    sync.Mutex
	locks      map[string]*sync.Mutex
}

func New(pool InstancePool, store Store, registry Registry,
	getFunc func(string) *types.FunctionSpecification) *Manager {
	if store == nil {
		store = DataSystemStore{}
	}
	return &Manager{
		pool: pool, store: store, registry: registry, getFunc: getFunc,
		now: time.Now, operations: map[string]*runtimeOperation{}, locks: map[string]*sync.Mutex{},
	}
}

func (m *Manager) contextLock(key string) *sync.Mutex {
	m.locksMu.Lock()
	defer m.locksMu.Unlock()
	lock := m.locks[key]
	if lock == nil {
		lock = &sync.Mutex{}
		m.locks[key] = lock
	}
	return lock
}

func operationKey(funcKey, sessionCtxID string) string { return types.JoinKey(funcKey, sessionCtxID) }

func (m *Manager) HandleScaleHint(hint *litescheduler.ScaleHint, traceID string) error {
	if hint == nil || hint.FuncKey == "" || hint.SessionCtxID == "" {
		return &Error{Code: "INVALID_SESSION_CONTEXT", Message: "funcKey and sessionCtxId are required"}
	}
	spec := m.getFunc(hint.FuncKey)
	if spec == nil {
		return &Error{Code: "FUNCTION_NOT_FOUND", Message: "function not found"}
	}
	key := operationKey(hint.FuncKey, hint.SessionCtxID)
	lock := m.contextLock(key)
	lock.Lock()
	defer lock.Unlock()

	control, err := m.readControl(spec, hint.TenantID, hint.SessionCtxID, traceID)
	if err != nil {
		return err
	}
	if control != nil {
		switch control.State {
		case StateDeleting:
			return &Error{Code: "SESSION_CTX_DELETING", Message: "SessionContext is being deleted"}
		case StateCreating:
			m.opMu.Lock()
			op := m.operations[key]
			if op == nil {
				op = &runtimeOperation{}
				m.operations[key] = op
			}
			op.PendingScale = true
			m.opMu.Unlock()
			return nil
		}
	}
	for _, instance := range m.pool.ListInstances(hint.FuncKey) {
		if sameContext(instance, hint.SessionCtxID) && !isExiting(instance) {
			if m.registry != nil {
				return m.registry.Register(
					registryRequest(spec, hint.TenantID, hint.SessionCtxID), traceID)
			}
			return nil
		}
	}
	m.opMu.Lock()
	op := m.operations[key]
	if op == nil {
		op = &runtimeOperation{}
		m.operations[key] = op
	}
	op.Creating = true
	op.CreateDone = make(chan struct{})
	m.opMu.Unlock()

	sessionCtxID := hint.SessionCtxID
	_, poolErr := m.pool.CreateInstance(&types.InstanceCreateRequest{
		FuncSpec: spec, TraceID: traceID, SessionCtxID: &sessionCtxID,
	})
	var createErr error = poolErr
	if createErr == nil && m.registry != nil {
		createErr = m.registry.Register(registryRequest(spec, hint.TenantID, hint.SessionCtxID), traceID)
	}
	m.opMu.Lock()
	op.Creating = false
	op.LastError = createErr
	close(op.CreateDone)
	m.opMu.Unlock()
	if createErr != nil {
		return createErr
	}
	return nil
}

func sameContext(instance *types.Instance, sessionCtxID string) bool {
	return instance != nil && instance.SessionCtxID != nil && *instance.SessionCtxID == sessionCtxID
}

func isExiting(instance *types.Instance) bool {
	if instance == nil {
		return true
	}
	code := constant.InstanceStatus(instance.InstanceStatus.Code)
	return code != constant.KernelInstanceStatusRunning && code != constant.KernelInstanceStatusSubHealth &&
		code != constant.KernelInstanceStatusCreating && code != constant.KernelInstanceStatusScheduling &&
		code != constant.KernelInstanceStatusNew
}

func (m *Manager) HandleIdle(report types.IdleReport) error {
	for _, item := range report.Instances {
		for _, instance := range m.pool.ListInstances(item.FuncKey) {
			if instance.InstanceID != item.InstanceID || !sameContext(instance, item.SessionCtxID) {
				continue
			}
			if isExiting(instance) {
				break
			}
			if err := m.pool.DeleteInstance(instance); err != nil {
				return err
			}
			break
		}
	}
	return nil
}

func (m *Manager) Fork(req ForkRequest) (*ForkResponse, error) {
	spec := m.getFunc(req.FuncKey)
	if spec == nil {
		return nil, &Error{Code: "FUNCTION_NOT_FOUND", Message: "function not found"}
	}
	first, second := operationKey(req.FuncKey, req.SourceSessionCtxID), operationKey(req.FuncKey, req.TargetSessionCtxID)
	if first > second {
		first, second = second, first
	}
	firstLock, secondLock := m.contextLock(first), m.contextLock(second)
	firstLock.Lock()
	defer firstLock.Unlock()
	if second != first {
		secondLock.Lock()
		defer secondLock.Unlock()
	}

	sourceControl, err := m.readControl(spec, req.TenantID, req.SourceSessionCtxID, req.TraceID)
	if err != nil {
		return nil, err
	}
	if sourceControl != nil && sourceControl.State == StateDeleting {
		return nil, &Error{Code: "SOURCE_SESSION_CTX_DELETING", Message: "source SessionContext is being deleted"}
	}
	turns, events, err := m.readHistory(spec, req.TenantID, req.SourceSessionCtxID, req.TraceID)
	if err != nil {
		return nil, err
	}
	var forkTurn *TurnRecord
	forkSeq := 0
	for index := range turns {
		if turns[index].TurnID == req.TurnID {
			forkTurn = &turns[index]
			for _, event := range events {
				if event.TurnID == req.TurnID && event.Type == "turn.completed" {
					forkSeq = event.Seq
				}
			}
			break
		}
	}
	if forkTurn == nil {
		return nil, &Error{Code: "TURN_NOT_FOUND", Message: "fork Turn not found"}
	}
	if forkSeq == 0 {
		return nil, &Error{Code: "TURN_NOT_COMPLETED", Message: "fork requires a COMPLETED Turn"}
	}

	targetControl, err := m.readControl(spec, req.TenantID, req.TargetSessionCtxID, req.TraceID)
	if err != nil {
		return nil, err
	}
	if targetControl != nil {
		sameFork := targetControl.ForkedFrom == req.SourceSessionCtxID &&
			targetControl.ForkTurnID == req.TurnID
		if !sameFork {
			return nil, &Error{Code: "TARGET_SESSION_CTX_EXISTS", Message: "target SessionContext already exists"}
		}
		if targetControl.State == StateReady {
			return &ForkResponse{SessionContextID: req.TargetSessionCtxID, State: "DORMANT"}, nil
		}
	} else {
		if lookup, ok := m.registry.(registryLookup); ok {
			exists, lookupErr := lookup.Exists(
				registryRequest(spec, req.TenantID, req.TargetSessionCtxID), req.TraceID)
			if lookupErr != nil {
				return nil, lookupErr
			}
			if exists {
				return nil, &Error{Code: "TARGET_SESSION_CTX_EXISTS",
					Message: "target SessionContext already exists"}
			}
		}
		exists, existsErr := m.historyExists(spec, req.TenantID, req.TargetSessionCtxID, req.TraceID)
		if existsErr != nil {
			return nil, existsErr
		}
		if exists {
			return nil, &Error{Code: "TARGET_SESSION_CTX_EXISTS", Message: "target SessionContext already exists"}
		}
		now := m.now().UTC().Format(time.RFC3339Nano)
		targetControl = &ControlRecord{
			SchemaVersion: 1, State: StateCreating, ForkedFrom: req.SourceSessionCtxID,
			ForkTurnID: req.TurnID, ForkTurnIndex: forkTurn.TurnIndex, ForkSeq: forkSeq,
			CreatedAt: now, UpdatedAt: now,
		}
		if err = m.writeControl(spec, req.TenantID, req.TargetSessionCtxID, targetControl, req.TraceID); err != nil {
			return nil, err
		}
	}
	if err = m.copyHistory(spec, req, turns, events, targetControl); err != nil {
		return nil, err
	}
	targetControl.State = StateReady
	targetControl.UpdatedAt = m.now().UTC().Format(time.RFC3339Nano)
	if err = m.writeControl(spec, req.TenantID, req.TargetSessionCtxID, targetControl, req.TraceID); err != nil {
		return nil, err
	}
	if m.registry != nil {
		if err = m.registry.Register(registryRequest(spec, req.TenantID, req.TargetSessionCtxID), req.TraceID); err != nil {
			return nil, err
		}
	}
	key := operationKey(req.FuncKey, req.TargetSessionCtxID)
	m.opMu.Lock()
	op := m.operations[key]
	pending := op != nil && op.PendingScale
	if op != nil {
		op.PendingScale = false
	}
	m.opMu.Unlock()
	if pending {
		go func() {
			if scaleErr := m.HandleScaleHint(&litescheduler.ScaleHint{
				FuncKey: req.FuncKey, TenantID: req.TenantID, SessionCtxID: req.TargetSessionCtxID,
				Reason: "pending_after_fork", TraceID: req.TraceID,
			}, req.TraceID); scaleErr != nil {
				log.GetLogger().Errorf("pending SessionContext scale hint failed for %s/%s: %v",
					req.FuncKey, req.TargetSessionCtxID, scaleErr)
			}
		}()
	}
	return &ForkResponse{SessionContextID: req.TargetSessionCtxID, State: "DORMANT"}, nil
}

func (m *Manager) copyHistory(spec *types.FunctionSpecification, req ForkRequest, turns []TurnRecord,
	events []Event, control *ControlRecord) error {
	for _, turn := range turns {
		if turn.TurnIndex <= control.CopiedTurn || turn.TurnIndex > control.ForkTurnIndex {
			continue
		}
		turn.SessionContextID = req.TargetSessionCtxID
		value, _ := json.Marshal(turn)
		if err := m.store.Put(TurnKey(req.TenantID, spec.FuncMetaData.Name, spec.FuncMetaData.Version,
			req.TargetSessionCtxID, turn.TurnIndex), value, req.TenantID, req.TraceID); err != nil {
			return err
		}
		control.CopiedTurn = turn.TurnIndex
		if turn.TurnIndex%copyBatchSize == 0 {
			control.UpdatedAt = m.now().UTC().Format(time.RFC3339Nano)
			if err := m.writeControl(spec, req.TenantID, req.TargetSessionCtxID, control, req.TraceID); err != nil {
				return err
			}
		}
	}
	for _, event := range events {
		if event.Seq <= control.CopiedSeq || event.Seq > control.ForkSeq {
			continue
		}
		event.SessionContextID = req.TargetSessionCtxID
		value, _ := json.Marshal(event)
		if err := m.store.Put(EventKey(req.TenantID, spec.FuncMetaData.Name, spec.FuncMetaData.Version,
			req.TargetSessionCtxID, event.Seq), value, req.TenantID, req.TraceID); err != nil {
			return err
		}
		control.CopiedSeq = event.Seq
		if event.Seq%copyBatchSize == 0 {
			control.UpdatedAt = m.now().UTC().Format(time.RFC3339Nano)
			if err := m.writeControl(spec, req.TenantID, req.TargetSessionCtxID, control, req.TraceID); err != nil {
				return err
			}
		}
	}
	control.UpdatedAt = m.now().UTC().Format(time.RFC3339Nano)
	return m.writeControl(spec, req.TenantID, req.TargetSessionCtxID, control, req.TraceID)
}

func (m *Manager) Delete(req DeleteRequest) error {
	spec := m.getFunc(req.FuncKey)
	if spec == nil {
		return &Error{Code: "FUNCTION_NOT_FOUND", Message: "function not found"}
	}
	lock := m.contextLock(operationKey(req.FuncKey, req.SessionCtxID))
	lock.Lock()
	defer lock.Unlock()
	control, err := m.readControl(spec, req.TenantID, req.SessionCtxID, req.TraceID)
	if err != nil {
		return err
	}
	turns, events, err := m.readHistory(spec, req.TenantID, req.SessionCtxID, req.TraceID)
	if err != nil {
		return err
	}
	registered := false
	if lookup, ok := m.registry.(registryLookup); ok {
		registered, err = lookup.Exists(registryRequest(spec, req.TenantID, req.SessionCtxID), req.TraceID)
		if err != nil {
			return err
		}
	}
	if control == nil && len(turns) == 0 && len(events) == 0 && !registered {
		return nil
	}
	if control == nil || control.State != StateDeleting {
		now := m.now().UTC().Format(time.RFC3339Nano)
		createdAt := now
		if control != nil && control.CreatedAt != "" {
			createdAt = control.CreatedAt
		}
		control = &ControlRecord{
			SchemaVersion: 1, State: StateDeleting, CreatedAt: createdAt, UpdatedAt: now,
			DeleteTurnUpperBound: len(turns), DeleteSeqUpperBound: len(events),
		}
		if len(turns) != 0 {
			control.DeleteTurnUpperBound = turns[len(turns)-1].TurnIndex
		}
		if len(events) != 0 {
			control.DeleteSeqUpperBound = events[len(events)-1].Seq
		}
		if err = m.writeControl(spec, req.TenantID, req.SessionCtxID, control, req.TraceID); err != nil {
			return err
		}
	}
	for _, instance := range m.pool.ListInstances(req.FuncKey) {
		if sameContext(instance, req.SessionCtxID) {
			if deleteErr := m.pool.DeleteInstance(instance); deleteErr != nil {
				return deleteErr
			}
		}
	}
	deadline := time.Now().Add(instanceStopWait)
	for {
		present := false
		for _, instance := range m.pool.ListInstances(req.FuncKey) {
			if sameContext(instance, req.SessionCtxID) {
				present = true
				break
			}
		}
		if !present {
			break
		}
		if time.Now().After(deadline) {
			return &Error{Code: "INSTANCE_STOP_TIMEOUT", Message: "timed out waiting for SessionContext instance to stop"}
		}
		time.Sleep(50 * time.Millisecond)
	}
	for index := 1; index <= control.DeleteTurnUpperBound; index++ {
		if err = m.store.Delete(TurnKey(req.TenantID, spec.FuncMetaData.Name, spec.FuncMetaData.Version,
			req.SessionCtxID, index), req.TenantID, req.TraceID); err != nil {
			return err
		}
	}
	for seq := 1; seq <= control.DeleteSeqUpperBound; seq++ {
		if err = m.store.Delete(EventKey(req.TenantID, spec.FuncMetaData.Name, spec.FuncMetaData.Version,
			req.SessionCtxID, seq), req.TenantID, req.TraceID); err != nil {
			return err
		}
	}
	if unregister, ok := m.registry.(registryUnregister); ok {
		if err = unregister.Unregister(registryRequest(spec, req.TenantID, req.SessionCtxID), req.TraceID); err != nil {
			return err
		}
	}
	return m.store.Delete(ControlKey(req.TenantID, spec.FuncMetaData.Name, spec.FuncMetaData.Version,
		req.SessionCtxID), req.TenantID, req.TraceID)
}

func (m *Manager) readHistory(spec *types.FunctionSpecification, tenantID, sessionCtxID, traceID string) (
	[]TurnRecord, []Event, error) {
	turns := make([]TurnRecord, 0)
	for index := 1; ; index++ {
		raw, err := m.store.Get(TurnKey(tenantID, spec.FuncMetaData.Name, spec.FuncMetaData.Version,
			sessionCtxID, index), tenantID, traceID)
		if err != nil {
			return nil, nil, err
		}
		if raw == nil {
			break
		}
		var turn TurnRecord
		if err = json.Unmarshal(raw, &turn); err != nil {
			return nil, nil, err
		}
		turns = append(turns, turn)
	}
	events := make([]Event, 0)
	for seq := 1; ; seq++ {
		raw, err := m.store.Get(EventKey(tenantID, spec.FuncMetaData.Name, spec.FuncMetaData.Version,
			sessionCtxID, seq), tenantID, traceID)
		if err != nil {
			return nil, nil, err
		}
		if raw == nil {
			break
		}
		var event Event
		if err = json.Unmarshal(raw, &event); err != nil {
			return nil, nil, err
		}
		events = append(events, event)
	}
	return turns, events, nil
}

func (m *Manager) historyExists(spec *types.FunctionSpecification, tenantID, sessionCtxID, traceID string) (bool, error) {
	for _, key := range []string{
		TurnKey(tenantID, spec.FuncMetaData.Name, spec.FuncMetaData.Version, sessionCtxID, 1),
		EventKey(tenantID, spec.FuncMetaData.Name, spec.FuncMetaData.Version, sessionCtxID, 1),
	} {
		raw, err := m.store.Get(key, tenantID, traceID)
		if err != nil {
			return false, err
		}
		if raw != nil {
			return true, nil
		}
	}
	return false, nil
}

func (m *Manager) readControl(spec *types.FunctionSpecification, tenantID, sessionCtxID,
	traceID string) (*ControlRecord, error) {
	raw, err := m.store.Get(ControlKey(tenantID, spec.FuncMetaData.Name, spec.FuncMetaData.Version,
		sessionCtxID), tenantID, traceID)
	if err != nil || raw == nil {
		return nil, err
	}
	var control ControlRecord
	if err = json.Unmarshal(raw, &control); err != nil {
		return nil, err
	}
	return &control, nil
}

func (m *Manager) writeControl(spec *types.FunctionSpecification, tenantID, sessionCtxID string,
	control *ControlRecord, traceID string) error {
	value, err := json.Marshal(control)
	if err != nil {
		return err
	}
	return m.store.Put(ControlKey(tenantID, spec.FuncMetaData.Name, spec.FuncMetaData.Version,
		sessionCtxID), value, tenantID, traceID)
}

func registryRequest(spec *types.FunctionSpecification, tenantID, sessionCtxID string) sessioncontextregistry.Request {
	return sessioncontextregistry.Request{
		TenantID: tenantID, RegisteredName: spec.FuncMetaData.Name,
		FunctionVersion: spec.FuncMetaData.Version, SessionContextID: sessionCtxID,
	}
}
