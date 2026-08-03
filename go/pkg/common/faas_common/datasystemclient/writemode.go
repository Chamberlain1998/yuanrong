/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */

package datasystemclient

import (
	"os"
	"strings"
	"sync"

	"yuanrong.org/kernel/runtime/libruntime/api"

	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
)

const defaultWriteModeEnv = "YR_DATASYSTEM_DEFAULT_WRITE_MODE"

var (
	defaultWriteModeOnce sync.Once
	defaultWriteMode     api.WriteModeEnum
)

// GetDefaultWriteMode returns the cluster-level DataSystem write mode also used
// by libruntime. Missing or invalid values preserve the NONE_L2_CACHE default.
func GetDefaultWriteMode() api.WriteModeEnum {
	defaultWriteModeOnce.Do(func() {
		value := os.Getenv(defaultWriteModeEnv)
		var ok bool
		defaultWriteMode, ok = parseDefaultWriteMode(value)
		if !ok {
			log.GetLogger().Warnf("invalid %s: %s, use NONE_L2_CACHE", defaultWriteModeEnv, value)
		}
	})
	return defaultWriteMode
}

func parseDefaultWriteMode(value string) (api.WriteModeEnum, bool) {
	switch strings.ToUpper(value) {
	case "", "0", "NONE_L2_CACHE":
		return api.NoneL2Cache, true
	case "1", "WRITE_THROUGH_L2_CACHE":
		return api.WriteThroughL2Cache, true
	case "2", "WRITE_BACK_L2_CACHE":
		return api.WriteBackL2Cache, true
	case "3", "NONE_L2_CACHE_EVICT":
		return api.NoneL2CacheEvict, true
	default:
		return api.NoneL2Cache, false
	}
}
