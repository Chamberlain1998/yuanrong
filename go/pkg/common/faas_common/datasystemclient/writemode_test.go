/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */

package datasystemclient

import (
	"testing"

	"github.com/stretchr/testify/require"

	"yuanrong.org/kernel/runtime/libruntime/api"
)

func TestParseDefaultWriteMode(t *testing.T) {
	tests := []struct {
		value string
		mode  api.WriteModeEnum
		ok    bool
	}{
		{"", api.NoneL2Cache, true},
		{"0", api.NoneL2Cache, true},
		{"none_l2_cache", api.NoneL2Cache, true},
		{"1", api.WriteThroughL2Cache, true},
		{"write_through_l2_cache", api.WriteThroughL2Cache, true},
		{"2", api.WriteBackL2Cache, true},
		{"write_back_l2_cache", api.WriteBackL2Cache, true},
		{"3", api.NoneL2CacheEvict, true},
		{"none_l2_cache_evict", api.NoneL2CacheEvict, true},
		{"invalid", api.NoneL2Cache, false},
	}
	for _, test := range tests {
		t.Run(test.value, func(t *testing.T) {
			mode, ok := parseDefaultWriteMode(test.value)
			require.Equal(t, test.mode, mode)
			require.Equal(t, test.ok, ok)
		})
	}
}
