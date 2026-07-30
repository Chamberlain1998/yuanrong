/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 */

// Package sessioncontextregistry maintains the function-level SessionContext index.
package sessioncontextregistry

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"yuanrong.org/kernel/pkg/common/faas_common/datasystemclient"
)

const (
	registryKeyPrefix = "ar:i:"
	schemaVersion     = 1
	lockShardCount    = 32
	hashHexLength     = 32
)

// Entry is one discoverable SessionContext.
type Entry struct {
	FunctionVersion  string `json:"functionVersion"`
	SessionContextID string `json:"sessionContextId"`
	CreatedAt        string `json:"createdAt"`
}

// Registry is the persisted function-level index.
type Registry struct {
	SchemaVersion   int     `json:"schemaVersion"`
	SessionContexts []Entry `json:"sessionContexts"`
}

// Request contains the trusted function scope used for registration.
type Request struct {
	TenantID         string
	RegisteredName   string
	FunctionVersion  string
	SessionContextID string
}

type registryStore interface {
	Get(key, tenantID, traceID string) ([]byte, error)
	Put(key string, value []byte, tenantID, traceID string) error
}

type dataSystemStore struct{}

func (dataSystemStore) Get(key, tenantID, traceID string) ([]byte, error) {
	return datasystemclient.KVGetWithRetry(key, &datasystemclient.Option{TenantID: tenantID}, traceID)
}

func (dataSystemStore) Put(key string, value []byte, tenantID, traceID string) error {
	return datasystemclient.KVPutWithRetry(key, value, &datasystemclient.Option{
		TenantID:  tenantID,
		WriteMode: datasystemclient.GetDefaultWriteMode(),
	}, traceID)
}

// Manager serializes read-modify-write operations for each in-process key shard.
type Manager struct {
	store registryStore
	now   func() time.Time
	locks [lockShardCount]sync.Mutex
}

// NewManager creates a manager backed by the scheduler's tenant-aware DataSystem client.
func NewManager() *Manager {
	return &Manager{store: dataSystemStore{}, now: time.Now}
}

// Register adds an entry if the same version and SessionContext ID are not already present.
func (m *Manager) Register(req Request, traceID string) error {
	if req.TenantID == "" || req.RegisteredName == "" || req.FunctionVersion == "" ||
		req.SessionContextID == "" {
		return fmt.Errorf("session context registry scope is incomplete")
	}
	key := Key(req.RegisteredName)
	lock := &m.locks[lockIndex(key)]
	lock.Lock()
	defer lock.Unlock()

	registry := Registry{SchemaVersion: schemaVersion, SessionContexts: []Entry{}}
	raw, err := m.store.Get(key, req.TenantID, traceID)
	if err != nil && !errors.Is(err, datasystemclient.ErrKeyNotFound) {
		return fmt.Errorf("read session context registry: %w", err)
	}
	if err == nil {
		if unmarshalErr := json.Unmarshal(raw, &registry); unmarshalErr != nil {
			return fmt.Errorf("decode session context registry: %w", unmarshalErr)
		}
		if registry.SchemaVersion != schemaVersion {
			return fmt.Errorf("unsupported session context registry schemaVersion %d", registry.SchemaVersion)
		}
	}
	for _, entry := range registry.SessionContexts {
		if entry.FunctionVersion == req.FunctionVersion &&
			entry.SessionContextID == req.SessionContextID {
			return nil
		}
	}
	registry.SessionContexts = append(registry.SessionContexts, Entry{
		FunctionVersion:  req.FunctionVersion,
		SessionContextID: req.SessionContextID,
		CreatedAt:        m.now().UTC().Format(time.RFC3339Nano),
	})
	value, err := json.Marshal(registry)
	if err != nil {
		return fmt.Errorf("encode session context registry: %w", err)
	}
	if err = m.store.Put(key, value, req.TenantID, traceID); err != nil {
		return fmt.Errorf("write session context registry: %w", err)
	}
	return nil
}

// Unregister removes one version-scoped SessionContext entry. It is idempotent.
func (m *Manager) Unregister(req Request, traceID string) error {
	if req.TenantID == "" || req.RegisteredName == "" || req.FunctionVersion == "" ||
		req.SessionContextID == "" {
		return fmt.Errorf("session context registry scope is incomplete")
	}
	key := Key(req.RegisteredName)
	lock := &m.locks[lockIndex(key)]
	lock.Lock()
	defer lock.Unlock()
	raw, err := m.store.Get(key, req.TenantID, traceID)
	if errors.Is(err, datasystemclient.ErrKeyNotFound) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("read session context registry: %w", err)
	}
	var registry Registry
	if err = json.Unmarshal(raw, &registry); err != nil {
		return fmt.Errorf("decode session context registry: %w", err)
	}
	entries := registry.SessionContexts[:0]
	for _, entry := range registry.SessionContexts {
		if entry.FunctionVersion == req.FunctionVersion &&
			entry.SessionContextID == req.SessionContextID {
			continue
		}
		entries = append(entries, entry)
	}
	if len(entries) == len(registry.SessionContexts) {
		return nil
	}
	registry.SessionContexts = entries
	value, err := json.Marshal(registry)
	if err != nil {
		return fmt.Errorf("encode session context registry: %w", err)
	}
	if err = m.store.Put(key, value, req.TenantID, traceID); err != nil {
		return fmt.Errorf("write session context registry: %w", err)
	}
	return nil
}

// Exists reports whether the version-scoped SessionContext is registered.
func (m *Manager) Exists(req Request, traceID string) (bool, error) {
	if req.TenantID == "" || req.RegisteredName == "" || req.FunctionVersion == "" ||
		req.SessionContextID == "" {
		return false, fmt.Errorf("session context registry scope is incomplete")
	}
	raw, err := m.store.Get(Key(req.RegisteredName), req.TenantID, traceID)
	if errors.Is(err, datasystemclient.ErrKeyNotFound) {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("read session context registry: %w", err)
	}
	var registry Registry
	if err = json.Unmarshal(raw, &registry); err != nil {
		return false, fmt.Errorf("decode session context registry: %w", err)
	}
	for _, entry := range registry.SessionContexts {
		if entry.FunctionVersion == req.FunctionVersion &&
			entry.SessionContextID == req.SessionContextID {
			return true, nil
		}
	}
	return false, nil
}

// Key returns the deterministic DataSystem key for all versions of one registered function name.
func Key(registeredName string) string {
	var encoded bytes.Buffer
	encoder := json.NewEncoder(&encoded)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode([]string{registeredName}); err != nil {
		panic(fmt.Sprintf("encode session context registry key: %v", err))
	}
	sum := sha256.Sum256(bytes.TrimSuffix(encoded.Bytes(), []byte{'\n'}))
	return registryKeyPrefix + hex.EncodeToString(sum[:])[:hashHexLength]
}

func lockIndex(key string) int {
	sum := sha256.Sum256([]byte(key))
	return int(sum[0]) % lockShardCount
}
