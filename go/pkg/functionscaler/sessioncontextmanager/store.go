/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */

package sessioncontextmanager

import (
	"errors"

	"yuanrong.org/kernel/pkg/common/faas_common/datasystemclient"
)

type Store interface {
	Get(key, tenantID, traceID string) ([]byte, error)
	Put(key string, value []byte, tenantID, traceID string) error
	Delete(key, tenantID, traceID string) error
}

type DataSystemStore struct{}

func (DataSystemStore) Get(key, tenantID, traceID string) ([]byte, error) {
	value, err := datasystemclient.KVGetWithRetry(key, &datasystemclient.Option{TenantID: tenantID}, traceID)
	if errors.Is(err, datasystemclient.ErrKeyNotFound) {
		return nil, nil
	}
	return value, err
}

func (DataSystemStore) Put(key string, value []byte, tenantID, traceID string) error {
	return datasystemclient.KVPutWithRetry(key, value, &datasystemclient.Option{
		TenantID: tenantID, WriteMode: datasystemclient.GetDefaultWriteMode(),
	}, traceID)
}

func (DataSystemStore) Delete(key, tenantID, traceID string) error {
	err := datasystemclient.KVDelWithRetry(key, &datasystemclient.Option{TenantID: tenantID}, traceID)
	if errors.Is(err, datasystemclient.ErrKeyNotFound) {
		return nil
	}
	return err
}
