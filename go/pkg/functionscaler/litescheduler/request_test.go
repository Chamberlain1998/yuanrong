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

// Package litescheduler -
package litescheduler

import (
	"encoding/json"
	"testing"

	"github.com/smartystreets/goconvey/convey"
	commonTypes "yuanrong.org/kernel/pkg/common/faas_common/types"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

func mustExtraData(t *testing.T, sess commonTypes.InstanceSessionConfig) []byte {
	m := map[string][]byte{}
	if sess.SessionID != "" {
		b, _ := json.Marshal(sess)
		m["instanceSessionConfig"] = b // constant.InstanceSessionConfig
	}
	out, _ := json.Marshal(m)
	return out
}

func TestParseRequestAcquireWithSession(t *testing.T) {
	orig := config.GlobalConfig.LiteScheduler
	defer func() { config.GlobalConfig.LiteScheduler = orig }()
	config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true, EnableAllTenants: true}

	ls := &LiteScheduler{}
	extra := mustExtraData(t, commonTypes.InstanceSessionConfig{SessionID: "sess1", SessionTTL: 30, Concurrency: 1})
	req, ok := ls.ParseRequest("acquire", "t1/fA/v1", extra, "tr1")
	convey.Convey("acquire with sessionID enters lite", t, func() {
		convey.So(ok, convey.ShouldBeTrue)
		convey.So(req.Op, convey.ShouldEqual, "acquire")
		convey.So(req.SessionID, convey.ShouldEqual, "sess1")
		convey.So(req.FuncKey, convey.ShouldEqual, "t1/fA/v1")
		convey.So(req.NeedReverseLookup, convey.ShouldBeFalse)
	})
}

func TestParseRequestAcquireWithoutSession(t *testing.T) {
	orig := config.GlobalConfig.LiteScheduler
	defer func() { config.GlobalConfig.LiteScheduler = orig }()
	config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true, EnableAllTenants: true}

	ls := &LiteScheduler{}
	req, ok := ls.ParseRequest("acquire", "t1/fA/v1", []byte("{}"), "tr1")
	convey.Convey("acquire without sessionID falls back to legacy", t, func() {
		convey.So(ok, convey.ShouldBeFalse)
		convey.So(req, convey.ShouldBeNil)
	})
}

func TestParseRequestAcquireWithSessionContext(t *testing.T) {
	orig := config.GlobalConfig.LiteScheduler
	defer func() { config.GlobalConfig.LiteScheduler = orig }()
	config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true, EnableAllTenants: true}
	extra, err := json.Marshal(map[string][]byte{"sessionCtxID": []byte("ctx-a")})
	if err != nil {
		t.Fatal(err)
	}
	ls := &LiteScheduler{funcSpecGetter: func(string) *types.FunctionSpecification {
		return &types.FunctionSpecification{
			ExtendedMetaData: commonTypes.ExtendedMetaData{EnableSessionCtx: true},
		}
	}}
	req, ok := ls.ParseRequest("acquire", "t1/fA/v1", extra, "tr1")
	convey.Convey("SessionContext without InstanceSession enters lite", t, func() {
		convey.So(ok, convey.ShouldBeTrue)
		convey.So(req.SessionCtxID, convey.ShouldEqual, "ctx-a")
		convey.So(req.SessionID, convey.ShouldEqual, "")
	})
}

func TestParseRequestReleaseLitePrefix(t *testing.T) {
	orig := config.GlobalConfig.LiteScheduler
	defer func() { config.GlobalConfig.LiteScheduler = orig }()
	config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true, EnableAllTenants: true}

	ls := &LiteScheduler{}
	allocID := genAllocationID("t1/sess1", "ins1", 1)
	req, ok := ls.ParseRequest("release", allocID, []byte("{}"), "tr1")
	convey.Convey("release with lite prefix enters lite, needs reverse lookup", t, func() {
		convey.So(ok, convey.ShouldBeTrue)
		convey.So(req.NeedReverseLookup, convey.ShouldBeTrue)
		convey.So(req.AllocationIDs, convey.ShouldResemble, []string{allocID})
	})
}

func TestParseRequestReleaseNonLitePrefix(t *testing.T) {
	orig := config.GlobalConfig.LiteScheduler
	defer func() { config.GlobalConfig.LiteScheduler = orig }()
	config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true, EnableAllTenants: true}

	ls := &LiteScheduler{}
	_, ok := ls.ParseRequest("release", "ins123-thread1", []byte("{}"), "tr1")
	convey.Convey("release non-lite prefix falls back", t, func() {
		convey.So(ok, convey.ShouldBeFalse)
	})
}

func TestParseRequestBatchRetainMixedPrefix(t *testing.T) {
	orig := config.GlobalConfig.LiteScheduler
	defer func() { config.GlobalConfig.LiteScheduler = orig }()
	config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true, EnableAllTenants: true}

	ls := &LiteScheduler{}
	liteID := genAllocationID("t1/sess1", "ins1", 1)
	// mixed: one lite, one non-lite
	target := liteID + ",ins123-thread1"
	_, ok := ls.ParseRequest("batchRetain", target, []byte("{}"), "tr1")
	convey.Convey("batchRetain mixed prefix falls back to legacy", t, func() {
		convey.So(ok, convey.ShouldBeFalse)
	})
}

func TestParseRequestBatchRetainAllLite(t *testing.T) {
	orig := config.GlobalConfig.LiteScheduler
	defer func() { config.GlobalConfig.LiteScheduler = orig }()
	config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true, EnableAllTenants: true}

	ls := &LiteScheduler{}
	id1 := genAllocationID("t1/sess1", "ins1", 1)
	id2 := genAllocationID("t1/sess1", "ins2", 2)
	target := id1 + "," + id2
	req, ok := ls.ParseRequest("batchRetain", target, []byte("{}"), "tr1")
	convey.Convey("batchRetain all lite enters lite branch", t, func() {
		convey.So(ok, convey.ShouldBeTrue)
		convey.So(req.NeedReverseLookup, convey.ShouldBeTrue)
		convey.So(req.AllocationIDs, convey.ShouldResemble, []string{id1, id2})
	})
}

func TestParseRequestCreateNotHandled(t *testing.T) {
	orig := config.GlobalConfig.LiteScheduler
	defer func() { config.GlobalConfig.LiteScheduler = orig }()
	config.GlobalConfig.LiteScheduler = types.LiteSchedulerConfig{Enable: true, EnableAllTenants: true}

	ls := &LiteScheduler{}
	_, ok := ls.ParseRequest("create", "t1/fA/v1", []byte("{}"), "tr1")
	convey.Convey("create not handled", t, func() {
		convey.So(ok, convey.ShouldBeFalse)
	})
}
