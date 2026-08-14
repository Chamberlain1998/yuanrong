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
package redisclient

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/agiledragon/gomonkey/v2"
	"github.com/redis/go-redis/v9"
	"github.com/smartystreets/goconvey/convey"
	"github.com/stretchr/testify/assert"

	commonTLS "yuanrong.org/kernel/pkg/common/faas_common/tls"
	"yuanrong.org/kernel/pkg/common/faas_common/utils"
)

func TestZADDMetricsToRedis(t *testing.T) {
	err := ZADDMetricsToRedis("mockKey", 1, 3, 5*time.Second)
	assert.NotNil(t, err)
	convey.Convey("TestZADDMetricsToRedis", t, func() {
		convey.Convey("ZCard exception", func() {
			redisCmd = &Client{client: &redis.Client{}}
			patches := [...]*gomonkey.Patches{
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.Client{}), "ZCard",
					func(cli *redis.Client, ctx context.Context, key string) *redis.IntCmd {
						return &redis.IntCmd{}
					}),
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.IntCmd{}), "Result",
					func(_ *redis.IntCmd) (int64, error) {
						return 0, errors.New("mock ZCard error")
					}),
			}
			defer func() {
				for idx := range patches {
					patches[idx].Reset()
				}
			}()
			err = ZADDMetricsToRedis("mockKey", 1, 3, 5*time.Second)
			assert.NotNil(t, err)
			assert.Equal(t, "mock ZCard error", err.Error())
		})

		convey.Convey("ZRange exception", func() {
			redisCmd = &Client{client: &redis.Client{}}
			patches := [...]*gomonkey.Patches{
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.Client{}), "ZCard",
					func(cli *redis.Client, ctx context.Context, key string) *redis.IntCmd {
						return &redis.IntCmd{}
					}),
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.IntCmd{}), "Result",
					func(_ *redis.IntCmd) (int64, error) {
						return 3, nil
					}),
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.Client{}), "ZRange",
					func(cli *redis.Client, ctx context.Context, key string, start, stop int64) *redis.StringSliceCmd {
						return &redis.StringSliceCmd{}
					}),
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.StringSliceCmd{}), "Result",
					func(_ *redis.StringSliceCmd) ([]string, error) {
						return nil, errors.New("mock ZRange error")
					}),
			}
			defer func() {
				for idx := range patches {
					patches[idx].Reset()
				}
			}()
			err = ZADDMetricsToRedis("mockKey", 1, 3, 5*time.Second)
			assert.NotNil(t, err)
			assert.Equal(t, "mock ZRange error", err.Error())
		})

		convey.Convey("ZAdd success", func() {
			redisCmd = &Client{client: &redis.Client{}}
			patches := [...]*gomonkey.Patches{
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.Client{}), "ZCard",
					func(cli *redis.Client, ctx context.Context, key string) *redis.IntCmd {
						return &redis.IntCmd{}
					}),
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.IntCmd{}), "Result",
					func(_ *redis.IntCmd) (int64, error) {
						return 3, nil
					}),
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.Client{}), "ZRange",
					func(cli *redis.Client, ctx context.Context, key string, start, stop int64) *redis.StringSliceCmd {
						return &redis.StringSliceCmd{}
					}),
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.StringSliceCmd{}), "Result",
					func(_ *redis.StringSliceCmd) ([]string, error) {
						return []string{"0", "1", "2"}, nil
					}),
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.Client{}), "ZRem",
					func(cli *redis.Client, ctx context.Context, key string, members ...interface{}) *redis.IntCmd {
						return &redis.IntCmd{}
					}),
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.Client{}), "ZAdd",
					func(cli *redis.Client, ctx context.Context, key string, members ...redis.Z) *redis.IntCmd {
						return &redis.IntCmd{}
					}),
				gomonkey.ApplyMethod(reflect.TypeOf(&redis.Client{}), "Expire",
					func(cli *redis.Client, ctx context.Context, key string, expiration time.Duration) *redis.BoolCmd {
						return &redis.BoolCmd{}
					}),
			}
			defer func() {
				for idx := range patches {
					patches[idx].Reset()
				}
			}()
			err = ZADDMetricsToRedis("mockKey", 1, 3, 5*time.Second)
			assert.Nil(t, err)
		})
	})
}

func TestNew(t *testing.T) {
	type args struct {
		serverMode string
		serverAddr string
		password   string
		options    []Option
	}
	var a args
	var b args
	option := SetEnableTLS(false)
	b.serverMode = "single"
	b.options = append(b.options, option)
	var c args
	c.serverMode = "cluster"
	c.options = append(b.options, option)
	c.options = append(b.options, SetGetRealTimeServerAddrFunc(func() (string, TimeoutConf, error) {
		return "", TimeoutConf{}, nil
	}))
	tests := []struct {
		name    string
		args    args
		want    redis.Cmdable
		wantErr bool
	}{
		{"case1", a, nil, true},
		{"case2", b, nil, true},
		{"case3", c, nil, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := New(NewRedisClientParam{
				tt.args.serverMode,
				tt.args.serverAddr,
				tt.args.password,
				TimeoutConf{},
				false,
				nil,
			}, nil, tt.args.options...)
			if (err != nil) != tt.wantErr {
				t.Errorf("New() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if got != nil {
				t.Errorf("New() got = %v, want %v", got, tt.want)
			}
		})
	}

	patches := utils.InitPatchSlice()
	statusCMD := &redis.StatusCmd{}
	patches.Append(utils.PatchSlice{gomonkey.ApplyFunc((*redis.Client).Ping,
		func(_ *redis.Client, _ context.Context) *redis.StatusCmd {
			return statusCMD
		})})
	defer patches.ResetAll()
	_, err := New(NewRedisClientParam{
		"single",
		"",
		"",
		TimeoutConf{},
		false,
		nil,
	}, nil)
	if err != nil {
		t.Errorf("failed to test new client with alarm switch on: %s", err.Error())
	}
}

func Test_buildCfg(t *testing.T) {
	type args struct {
		caFile   string
		certFile string
		keyFile  string
	}
	var a args
	tests := []struct {
		name    string
		args    args
		want    *tls.Config
		wantErr bool
	}{
		{"case1", a, nil, true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, _ := buildCfg(tt.args.caFile, tt.args.certFile, tt.args.keyFile)
			assert.Equalf(t, tt.want, got, "buildCfg(%v, %v, %v)", tt.args.caFile, tt.args.certFile, tt.args.keyFile)
		})
	}
}

func TestEmptyClients(t *testing.T) {
	opt := redisClientOption{enableTLS: true}
	redisCMD := newSingleClient(opt)
	assert.Equal(t, redisCMD, nil)
	redisCMD = newClusterClient(opt)
	assert.Equal(t, redisCMD, nil)
}

func TestBuildCfg(t *testing.T) {
	patches := utils.InitPatchSlice()
	patches.Append(utils.PatchSlice{gomonkey.ApplyFunc(commonTLS.GetX509CACertPool,
		func(caCertFilePath string) (caCertPool *x509.CertPool, err error) {
			return nil, nil
		})})
	defer patches.ResetAll()
	convey.Convey("Test build cfg error", t, func() {
		tlsConfig, err := buildCfg(DefaultCAFile, DefaultCertFile, DefaultKeyFile)
		convey.So(tlsConfig, convey.ShouldBeNil)
		convey.So(err, convey.ShouldNotBeNil)
	})
}

func Test_getNewRedisOption(t *testing.T) {
	type args struct {
		param NewRedisClientParam
	}
	tests := []struct {
		name string
		args args
		want redisClientOption
	}{
		{
			name: "case1",
			args: args{
				param: NewRedisClientParam{
					"single",
					"127.0.0.1",
					"aaa",
					TimeoutConf{},
					false,
					nil,
				},
			},
			want: redisClientOption{
				serverAddr:   "127.0.0.1",
				serverMode:   "single",
				password:     "aaa",
				dialTimeout:  dialTimeout,
				readTimeout:  readTimeout,
				writeTimeout: writeTimeout,
				idleTimeout:  idleTimeout,
			},
		},
		{
			name: "case1",
			args: args{
				param: NewRedisClientParam{
					"single",
					"127.0.0.1",
					"aaa",
					TimeoutConf{
						DialTimeout:  1,
						ReadTimeout:  1,
						WriteTimeout: 1,
						IdleTimeout:  1,
					},
					false,
					nil,
				},
			},
			want: redisClientOption{
				serverAddr:   "127.0.0.1",
				serverMode:   "single",
				password:     "aaa",
				dialTimeout:  1 * time.Second,
				readTimeout:  1 * time.Second,
				writeTimeout: 1 * time.Second,
				idleTimeout:  1 * time.Second,
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := getNewRedisOption(tt.args.param); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("getNewRedisOption() = %v, want %v", got, tt.want)
			}
		})
	}
}

func Test_initClient(t *testing.T) {
	convey.Convey("Test redis Client is success", t, func() {
		param := NewRedisClientParam{
			ServerMode:      "122",
			ServerAddr:      "333",
			Password:        "1222",
			Timeout:         TimeoutConf{},
			EnableTLS:       false,
			HotloadConfFunc: nil,
		}
		oldNewRedisClient := newRedisClient
		newRedisClient = func(newClientParam NewRedisClientParam, stopCh <-chan struct{}, options ...Option) (*Client, error) {
			return &Client{}, nil
		}
		defer func() {
			newRedisClient = oldNewRedisClient
		}()
		stopCh := make(chan struct{})
		redisClient, _ := initClient(&param, stopCh)
		convey.So(redisClient, convey.ShouldNotBeNil)
	})
	convey.Convey("Test to not init redis client", t, func() {
		param := NewRedisClientParam{
			ServerMode:      "122",
			ServerAddr:      "333",
			Password:        "1222",
			Timeout:         TimeoutConf{},
			EnableTLS:       false,
			HotloadConfFunc: nil,
		}
		oldNewRedisClient := newRedisClient
		newRedisClient = func(newClientParam NewRedisClientParam, stopCh <-chan struct{}, options ...Option) (*Client, error) {
			return &Client{}, errors.New("redis is not ready")
		}
		defer func() {
			newRedisClient = oldNewRedisClient
		}()
		stopCh := make(chan struct{})
		_, err := initClient(&param, stopCh)
		convey.So(err, convey.ShouldNotBeNil)
	})
}

func TestCheckRedisConnectivity(t *testing.T) {
	var isCalled = 0
	convey.Convey("Test check redis to connect in cyclist", t, func() {
		param := NewRedisClientParam{
			ServerMode:      "122",
			ServerAddr:      "333",
			Password:        "1222",
			Timeout:         TimeoutConf{},
			EnableTLS:       false,
			HotloadConfFunc: nil,
		}
		oldNewRedisClient := newRedisClient
		newRedisClient = func(newClientParam NewRedisClientParam, stopCh <-chan struct{}, options ...Option) (*Client, error) {
			isCalled++
			return &Client{}, nil
		}
		patch := gomonkey.ApplyFunc((*redis.Client).Ping,
			func(_ *redis.Client, _ context.Context) *redis.StatusCmd {
				return &redis.StatusCmd{}
			})
		defer func() {
			newRedisClient = oldNewRedisClient
			patch.Reset()
			SetRedisCmd(nil)
		}()
		stopCh := make(chan struct{}, 0)
		tickerCh := make(chan time.Time)
		patch.ApplyFunc(time.NewTicker, func(_ time.Duration) *time.Ticker {
			return &time.Ticker{C: tickerCh}
		})

		SetRedisCmd(nil)
		go CheckRedisConnectivity(&param, stopCh)
		tickerCh <- time.Time{}
		stopCh <- struct{}{}
		convey.So(isCalled, convey.ShouldEqual, 1)
		newRedisClient = func(newClientParam NewRedisClientParam, stopCh <-chan struct{}, options ...Option) (*Client, error) {
			isCalled++
			return &Client{}, errors.New("state is not ready")
		}
		stopCh = make(chan struct{}, 0)
		SetRedisCmd(nil)
		go CheckRedisConnectivity(&param, stopCh)
		tickerCh <- time.Time{}
		tickerCh <- time.Time{}
		stopCh <- struct{}{}
		convey.So(isCalled, convey.ShouldEqual, 3)

		CheckRedisConnectivity(&param, nil)
		convey.So(isCalled, convey.ShouldEqual, 3)

		// Regression: 首次重建后全局 client 已健康，下一轮必须读到新全局而非陈旧捕获指针，不再触发重建。
		// 旧实现把首次 client 指针固定传入，重连后 client 局部赋值无法回写，下一轮仍 ping 旧指针，
		// 导致每轮都重建并 SetRedisCmd，连接池泄漏 + 抖动。
		isCalled = 0
		newRedisClient = func(_ NewRedisClientParam, _ <-chan struct{}, _ ...Option) (*Client, error) {
			isCalled++
			return &Client{client: &redis.Client{}}, nil
		}
		stopCh = make(chan struct{}, 0)
		tickerCh = make(chan time.Time)
		SetRedisCmd(nil)
		go CheckRedisConnectivity(&param, stopCh)
		tickerCh <- time.Time{}
		tickerCh <- time.Time{}
		stopCh <- struct{}{}
		convey.So(isCalled, convey.ShouldEqual, 1)
	})
}

// TestCheckAndReconnect_DiscardStaleOnParamChange 回归：initClient 创建期间共享 param 被
// BuildRedisClient 改字段并发布新 client 时，本路径基于旧 param 创建的 stale client 必须
// 被丢弃，不能 SetRedisCmd 覆盖 Reload 的新 client。
// 旧实现只 paramMu 保护字段拷贝，创建期间不持锁，会拿旧配置 client 覆盖回去。
func TestCheckAndReconnect_DiscardStaleOnParamChange(t *testing.T) {
	param := &NewRedisClientParam{
		ServerMode: "single", ServerAddr: "old", Password: "pw", Timeout: TimeoutConf{},
	}
	var reloaded *Client
	var staleReturned *Client
	oldNewRedisClient := newRedisClient
	newRedisClient = func(_ NewRedisClientParam, _ <-chan struct{}, _ ...Option) (*Client, error) {
		// 模拟 BuildRedisClient 在创建期间改 param 字段并已 SetRedisCmd 发布新 client。
		LockParam()
		param.ServerAddr = "new-addr-after-reload"
		UnlockParam()
		reloaded = &Client{client: &redis.Client{}}
		SetRedisCmd(reloaded)
		staleReturned = &Client{client: &redis.Client{}}
		return staleReturned, nil
	}
	defer func() {
		newRedisClient = oldNewRedisClient
		SetRedisCmd(nil)
	}()
	stopCh := make(chan struct{}, 0)
	defer close(stopCh)

	SetRedisCmd(nil)
	err := checkAndReconnectRedis(param, stopCh)
	convey.Convey("stale client discarded", t, func() {
		convey.So(err, convey.ShouldBeNil)
		convey.So(GetRedisCmd(), convey.ShouldEqual, reloaded)       // Reload 的新 client 未被覆盖
		convey.So(GetRedisCmd(), convey.ShouldNotEqual, staleReturned) // stale client 未发布
	})
}

func TestClient_Del(t *testing.T) {
	client := &Client{
		client: &redis.Client{},
	}
	ctx := context.Background()
	keys := []string{"key1", "key2"}

	mockResult := &redis.IntCmd{}
	patches := gomonkey.ApplyMethod(
		reflect.TypeOf(client.client), "Del",
		func(_ redis.Cmdable, _ context.Context, _ ...string) *redis.IntCmd {
			return mockResult
		},
	)
	defer patches.Reset()

	result := client.Del(ctx, keys...)

	assert.Equal(t, mockResult, result)
}

func TestClient_Get(t *testing.T) {
	client := &Client{
		client: &redis.Client{},
	}
	ctx := context.Background()
	key := "key2"

	mockResult := &redis.StringCmd{}
	patches := gomonkey.ApplyMethod(
		reflect.TypeOf(client.client), "Get",
		func(_ redis.Cmdable, _ context.Context, _ string) *redis.StringCmd {
			return mockResult
		},
	)
	defer patches.Reset()

	result := client.Get(ctx, key)

	assert.Equal(t, mockResult, result)
}
func TestClient_Ping(t *testing.T) {
	client := &Client{
		client: &redis.Client{},
	}
	ctx := context.Background()

	mockResult := &redis.StatusCmd{}
	patches := gomonkey.ApplyMethod(
		reflect.TypeOf(client.client), "Ping",
		func(_ redis.Cmdable, _ context.Context) *redis.StatusCmd {
			return mockResult
		},
	)
	defer patches.Reset()

	result := client.Ping(ctx)

	assert.Equal(t, mockResult, result)
}
