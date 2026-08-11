/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
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

package session

import (
	"os"

	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/runtime/libruntime/api"
)

// MakeStore builds a Store from the global SessionStoreConfig, parameterized by
// funcCacheKey (per-function isolation). Shared by concurrencyscheduler and
// litescheduler to avoid duplicated factory code. SchedulerID/NodeIP come from
// env (POD_IP/HOST_IP). Returns NoopStore only when New() fails (fail-open);
// backend 字段合法性由 config 加载期校验兜底，正常路径不会走到 NoopStore。
//
// funcCacheKey 统一传 funcSpec.FuncKey（不再编 instanceType/resKey），使 cc 与 lite
// 对同一函数的 session 落到同一物理 key，实现跨 scheduler 共享。instanceType 维度已从
// 物理 key 移除——它与路由层重复（路由保证同一 session 只归一个 scheduler）。
func MakeStore(funcCacheKey string) Store {
	storeCfg := config.GlobalConfig.SessionStoreConfig
	dsCfg := config.GlobalConfig.DataSystemConfig
	cfg := Config{
		Backend:           storeCfg.Backend,
		Cluster:           dsCfg.CurrentCluster,
		FuncCacheKey:      funcCacheKey,
		SchedulerID:       os.Getenv("POD_IP"),
		BackendTTLSeconds: storeCfg.BackendTTLSeconds,
		DSOption: DSOptionConfig{
			TenantID:  "0",
			NodeIP:    os.Getenv("HOST_IP"),
			Cluster:   dsCfg.CurrentCluster,
			WriteMode: api.WriteModeEnum(dsCfg.UploadWriteMode),
			TTLSecond: uint32(storeCfg.BackendTTLSeconds),
		},
	}
	store, err := New(cfg)
	if err != nil {
		log.GetLogger().Errorf("init session store failed, backend=%s, err: %s, fallback to noop",
			storeCfg.Backend, err.Error())
		return NoopStore{}
	}
	if store.Backend() != BackendNoop {
		log.GetLogger().Infof("session store initialized, backend=%s, funcCacheKey=%s",
			store.Backend(), funcCacheKey)
	}
	return store
}
