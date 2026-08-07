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

// Package config -
package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"testing"

	"github.com/smartystreets/goconvey/convey"

	"yuanrong.org/kernel/pkg/common/faas_common/alarm"
	"yuanrong.org/kernel/pkg/common/faas_common/etcd3"
	"yuanrong.org/kernel/pkg/common/faas_common/sts/raw"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

func TestInitConfig(t *testing.T) {
	cfg := &types.Configuration{
		CPU:    999,
		Memory: 999,
		AutoScaleConfig: types.AutoScaleConfig{
			SLAQuota:      1,
			ScaleDownTime: 1,
			BurstScaleNum: 1,
		},
		LeaseSpan: 1,
		RouterETCDConfig: etcd3.EtcdConfig{
			Password: "321",
		},
		MetaETCDConfig: etcd3.EtcdConfig{
			Password: "123",
		},
		SchedulerNum:   1,
		DockerRootPath: "dockerRootPath",
		RawStsConfig: raw.StsConfig{
			StsEnable:        true,
			SensitiveConfigs: raw.SensitiveConfigs{},
			ServerConfig:     raw.ServerConfig{},
			MgmtServerConfig: raw.MgmtServerConfig{},
		},
	}
	rawInitStsSDK := initStsSDK
	initStsSDK = func(serverCfg raw.ServerConfig) error { return nil }
	defer func() { initStsSDK = rawInitStsSDK }()
	cfgByte, _ := json.Marshal(cfg)
	convey.Convey("success", t, func() {
		err := InitConfig(cfgByte)
		convey.So(err, convey.ShouldBeNil)
	})
	convey.Convey("DockerRootPath is empty", t, func() {
		cfg := &types.Configuration{
			CPU:    999,
			Memory: 999,
			AutoScaleConfig: types.AutoScaleConfig{
				SLAQuota:      1,
				ScaleDownTime: 1,
				BurstScaleNum: 1,
			},
			LeaseSpan: 1,
			RouterETCDConfig: etcd3.EtcdConfig{
				Password: "321",
			},
			MetaETCDConfig: etcd3.EtcdConfig{
				Password: "123",
			},
			SchedulerNum:   1,
			DockerRootPath: "",
			RawStsConfig: raw.StsConfig{
				StsEnable:        true,
				SensitiveConfigs: raw.SensitiveConfigs{},
				ServerConfig:     raw.ServerConfig{},
				MgmtServerConfig: raw.MgmtServerConfig{},
			},
		}
		cfgByte, _ := json.Marshal(cfg)
		err := InitConfig(cfgByte)
		convey.So(err, convey.ShouldBeNil)
	})
	convey.Convey("GetDecryptFromEnv success", t, func() {
		rawGetDecryptFromEnv := getDecryptFromEnv
		getDecryptFromEnv = func() (map[string]string, error) {
			return map[string]string{"metaEtcdPwd": "qwerdf"}, nil
		}
		defer func() { getDecryptFromEnv = rawGetDecryptFromEnv }()
		err := InitConfig(cfgByte)
		convey.So(err, convey.ShouldBeNil)
		convey.So(GlobalConfig.MetaETCDConfig.Password, convey.ShouldEqual, "qwerdf")
	})
	convey.Convey("GetDecryptFromEnv error", t, func() {
		rawGetDecryptFromEnv := getDecryptFromEnv
		getDecryptFromEnv = func() (map[string]string, error) {
			return nil, fmt.Errorf("get decrypt from env error")
		}
		defer func() { getDecryptFromEnv = rawGetDecryptFromEnv }()
		err := InitConfig(cfgByte)
		convey.So(err, convey.ShouldNotBeNil)
	})
	convey.Convey("Unmarshal error", t, func() {
		err := InitConfig(cfgByte[2:20])
		convey.So(err, convey.ShouldNotBeNil)
	})
	convey.Convey("ValidateStruct error", t, func() {
		cfg.MetaETCDConfig = etcd3.EtcdConfig{}
		cfgByte, _ := json.Marshal(cfg)
		err := InitConfig(cfgByte)
		convey.So(err, convey.ShouldNotBeNil)
	})
	convey.Convey("sts init error", t, func() {
		initStsSDK = func(serverCfg raw.ServerConfig) error {
			return errors.New("init sts error")
		}
		defer func() { initStsSDK = rawInitStsSDK }()
		err := InitConfig(cfgByte)
		convey.So(err, convey.ShouldNotBeNil)
	})

	convey.Convey("sts init error", t, func() {
		initStsSDK = func(serverCfg raw.ServerConfig) error { return nil }
		defer func() { initStsSDK = rawInitStsSDK }()
		rawSetEnv := setEnv
		setEnv = func(key, value string) error {
			return errors.New("set env error")
		}
		defer func() { setEnv = rawSetEnv }()
		err := InitConfig(cfgByte)
		convey.So(err, convey.ShouldNotBeNil)
	})

}

func TestInitModuleConfig(t *testing.T) {
	cfg := &types.Configuration{
		CPU:    999,
		Memory: 999,
		AutoScaleConfig: types.AutoScaleConfig{
			SLAQuota:      1,
			ScaleDownTime: 1,
			BurstScaleNum: 1,
		},
		LeaseSpan: 1,
		RouterETCDConfig: etcd3.EtcdConfig{
			Password: "321",
		},
		MetaETCDConfig: etcd3.EtcdConfig{
			Password: "123",
		},
		SchedulerNum: 1,
	}
	cfgByte, _ := json.Marshal(cfg)
	convey.Convey("success", t, func() {
		rawGetEnv := getEnv
		getEnv = func(key string) string {
			return string(cfgByte)
		}
		defer func() { getEnv = rawGetEnv }()
		err := InitModuleConfig()
		convey.So(err, convey.ShouldBeNil)
	})
	convey.Convey("Unmarshal error", t, func() {
		rawGetEnv := getEnv
		getEnv = func(key string) string {
			return "{"
		}
		defer func() { getEnv = rawGetEnv }()
		err := InitModuleConfig()
		convey.So(err, convey.ShouldNotBeNil)
	})
}

func TestInitEtcd(t *testing.T) {
	convey.Convey("Test InitEtcd", t, func() {
		convey.Convey("global config is nil", func() {
			rawGConfig := GlobalConfig
			GlobalConfig = types.Configuration{}
			stopCh := make(chan struct{})
			err := InitEtcd(stopCh)
			GlobalConfig = rawGConfig
			convey.So(err, convey.ShouldNotBeNil)
		})
		convey.Convey("init route etcd error", func() {
			rawGConfig := GlobalConfig
			GlobalConfig = types.Configuration{
				RouterETCDConfig: etcd3.EtcdConfig{},
			}
			err := InitEtcd(nil)
			GlobalConfig = rawGConfig
			convey.So(err, convey.ShouldNotBeNil)
		})
		convey.Convey("init metadata etcd error", func() {
			rawGConfig := GlobalConfig
			GlobalConfig = types.Configuration{
				MetaETCDConfig: etcd3.EtcdConfig{},
			}
			err := InitEtcd(nil)
			GlobalConfig = rawGConfig
			convey.So(err, convey.ShouldNotBeNil)
		})
	})
}

func TestClearSensitiveInfo(t *testing.T) {
	convey.Convey("Test ClearSensitiveInfo", t, func() {
		convey.Convey("global config is nil", func() {
			rawGConfig := GlobalConfig
			GlobalConfig = types.Configuration{}
			ClearSensitiveInfo()
			GlobalConfig = rawGConfig
		})
		convey.Convey("clear sensitive Info", func() {
			rawGConfig := GlobalConfig
			GlobalConfig = types.Configuration{
				RouterETCDConfig: etcd3.EtcdConfig{},
				MetaETCDConfig:   etcd3.EtcdConfig{},
			}
			ClearSensitiveInfo()
			GlobalConfig = rawGConfig
		})
	})
}

func TestSetAlarmEnv(t *testing.T) {
	convey.Convey("Test SetAlarmEnv", t, func() {
		convey.Convey("success", func() {
			rawGConfig := GlobalConfig
			GlobalConfig = types.Configuration{
				ClusterName: "cluster1",
				AlarmConfig: alarm.Config{
					EnableAlarm: true,
				},
			}
			err := setAlarmEnv()
			GlobalConfig = rawGConfig
			convey.So(err, convey.ShouldBeNil)
		})
	})
}

func TestLoadFunctionConfigNormalizeLite(t *testing.T) {
	// Minimal required fields so govalidator.ValidateStruct passes and
	// loadFunctionConfig reaches the normalizeLiteScheduler call. Mirrors the
	// cfg shape used by TestInitConfig (sans StsSDK/DockerRootPath which are
	// optional and default-skipped here).
	baseRequired := func() types.Configuration {
		return types.Configuration{
			CPU:    999,
			Memory: 999,
			AutoScaleConfig: types.AutoScaleConfig{
				SLAQuota:      1,
				ScaleDownTime: 1,
				BurstScaleNum: 1,
			},
			LeaseSpan: 1,
			RouterETCDConfig: etcd3.EtcdConfig{
				Password: "321",
			},
			MetaETCDConfig: etcd3.EtcdConfig{
				Password: "123",
			},
			SchedulerNum: 1,
		}
	}
	convey.Convey("normalizeLiteScheduler fills defaults", t, func() {
		cfg := baseRequired()
		cfg.LiteScheduler = types.LiteSchedulerConfig{}
		err := loadFunctionConfig(&cfg)
		convey.So(err, convey.ShouldBeNil)
		convey.So(cfg.LiteScheduler.AcquireWaitTimeoutMs, convey.ShouldEqual, defaultAcquireWaitTimeoutMs)
		convey.So(cfg.LiteScheduler.EnabledTenants, convey.ShouldResemble, []string{})
		convey.So(cfg.LiteScheduler.EnabledFunctions, convey.ShouldResemble, []string{})
	})
	convey.Convey("normalizeLiteScheduler preserves set values", t, func() {
		cfg := baseRequired()
		cfg.LiteScheduler = types.LiteSchedulerConfig{
			Enable:               true,
			AcquireWaitTimeoutMs: 800,
			EnabledTenants:       []string{"tenant1"},
			EnabledFunctions:     []string{"tenant1/funcA"},
		}
		err := loadFunctionConfig(&cfg)
		convey.So(err, convey.ShouldBeNil)
		convey.So(cfg.LiteScheduler.AcquireWaitTimeoutMs, convey.ShouldEqual, 800)
		convey.So(cfg.LiteScheduler.EnabledTenants, convey.ShouldResemble, []string{"tenant1"})
		convey.So(cfg.LiteScheduler.EnabledFunctions, convey.ShouldResemble, []string{"tenant1/funcA"})
	})
}

func TestValidateSessionStoreBackend(t *testing.T) {
	convey.Convey("sessionStore.backend 留空默认填 datasystem", t, func() {
		cfg := types.SessionStoreConfig{}
		err := validateSessionStoreBackend(&cfg)
		convey.So(err, convey.ShouldBeNil)
		convey.So(cfg.Backend, convey.ShouldEqual, SessionStoreBackendDataSystem)
	})
	convey.Convey("redis/datasystem 透传不变", t, func() {
		for _, b := range []string{SessionStoreBackendRedis, SessionStoreBackendDataSystem} {
			cfg := types.SessionStoreConfig{Backend: b}
			err := validateSessionStoreBackend(&cfg)
			convey.So(err, convey.ShouldBeNil)
			convey.So(cfg.Backend, convey.ShouldEqual, b)
		}
	})
	convey.Convey("未知 backend 报错", t, func() {
		cfg := types.SessionStoreConfig{Backend: "foo"}
		err := validateSessionStoreBackend(&cfg)
		convey.So(err, convey.ShouldNotBeNil)
		convey.So(cfg.Backend, convey.ShouldEqual, "foo")
	})
}
