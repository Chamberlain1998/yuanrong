/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 */

package sessioncontextregistry

import (
	"encoding/json"
	"errors"
	"sync"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	"yuanrong.org/kernel/pkg/common/faas_common/datasystemclient"
)

type memoryStore struct {
	mu         sync.Mutex
	values     map[string][]byte
	getTraceID string
	putTraceID string
}

func (s *memoryStore) Get(key, _, traceID string) ([]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.getTraceID = traceID
	value, ok := s.values[key]
	if !ok {
		return nil, datasystemclient.ErrKeyNotFound
	}
	return append([]byte(nil), value...), nil
}

func (s *memoryStore) Put(key string, value []byte, _, traceID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.putTraceID = traceID
	s.values[key] = append([]byte(nil), value...)
	return nil
}

func TestKeyMatchesPythonSDKHashConvention(t *testing.T) {
	require.Equal(t, "ar:i:44224b5b608c24b01ed67d10e61110bc", Key("0@agentrt@e2e0729"))
	require.Equal(t, "ar:i:78ff3361117058d209f96a2334ce7045", Key("a&b"))
	require.Equal(t, "ar:i:65bcb1a065d8b11ec83cecd06be6e3ae", Key("中文"))
}

func TestRegisterCreatesAndDeduplicatesEntries(t *testing.T) {
	store := &memoryStore{values: map[string][]byte{}}
	manager := &Manager{
		store: store,
		now: func() time.Time {
			return time.Date(2026, 7, 29, 9, 52, 25, 0, time.UTC)
		},
	}
	req := Request{
		TenantID:         "default",
		RegisteredName:   "0@agentrt@e2e0729",
		FunctionVersion:  "latest",
		SessionContextID: "session-1",
	}

	require.NoError(t, manager.Register(req, "acquire-trace"))
	require.NoError(t, manager.Register(req, "acquire-trace"))
	req.FunctionVersion = "v2"
	require.NoError(t, manager.Register(req, "acquire-trace"))

	var got Registry
	require.NoError(t, json.Unmarshal(store.values[Key(req.RegisteredName)], &got))
	require.Equal(t, schemaVersion, got.SchemaVersion)
	require.Len(t, got.SessionContexts, 2)
	require.Equal(t, "2026-07-29T09:52:25Z", got.SessionContexts[0].CreatedAt)
	require.Equal(t, "acquire-trace", store.getTraceID)
	require.Equal(t, "acquire-trace", store.putTraceID)
}

func TestRegisterRejectsCorruptedRegistry(t *testing.T) {
	store := &memoryStore{values: map[string][]byte{
		Key("func"): []byte("{"),
	}}
	manager := &Manager{store: store, now: time.Now}

	err := manager.Register(Request{
		TenantID: "tenant", RegisteredName: "func",
		FunctionVersion: "latest", SessionContextID: "session",
	}, "trace")

	require.Error(t, err)
	require.False(t, errors.Is(err, datasystemclient.ErrKeyNotFound))
}
