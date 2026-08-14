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
	"testing"

	"github.com/smartystreets/goconvey/convey"

	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	commontypes "yuanrong.org/kernel/pkg/common/faas_common/types"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

func TestMapStatus(t *testing.T) {
	convey.Convey("running maps to Running", t, func() {
		convey.So(mapStatus(int32(constant.KernelInstanceStatusRunning)), convey.ShouldEqual, InstanceStatusRunning)
	})
	convey.Convey("subhealth maps to SubHealth", t, func() {
		convey.So(mapStatus(int32(constant.KernelInstanceStatusSubHealth)), convey.ShouldEqual, InstanceStatusSubHealth)
	})
	convey.Convey("fatal/evicted/exiting map to Unavailable", t, func() {
		convey.So(mapStatus(int32(constant.KernelInstanceStatusFatal)), convey.ShouldEqual, InstanceStatusUnavailable)
		convey.So(mapStatus(int32(constant.KernelInstanceStatusEvicted)), convey.ShouldEqual, InstanceStatusUnavailable)
		convey.So(mapStatus(int32(constant.KernelInstanceStatusExiting)), convey.ShouldEqual, InstanceStatusUnavailable)
	})
	convey.Convey("new/scheduling/creating map to Unavailable (pending)", t, func() {
		convey.So(mapStatus(int32(constant.KernelInstanceStatusNew)), convey.ShouldEqual, InstanceStatusUnavailable)
		convey.So(mapStatus(int32(constant.KernelInstanceStatusCreating)), convey.ShouldEqual, InstanceStatusUnavailable)
	})
}

func TestBuildLiteInstanceFromInstance(t *testing.T) {
	convey.Convey("build copies fields from types.Instance", t, func() {
		ins := &types.Instance{
			InstanceID: "ins1", FuncKey: "t1/f/v1", ConcurrentNum: 5,
			InstanceIP: "10.0.0.1", InstancePort: "8080", FuncSig: "sig",
		}
		ins.InstanceStatus.Code = int32(constant.KernelInstanceStatusRunning)
		lite := buildLiteInstanceFromInstance(ins)
		convey.So(lite.InstanceID, convey.ShouldEqual, "ins1")
		convey.So(lite.Capacity, convey.ShouldEqual, 5)
		convey.So(lite.Status, convey.ShouldEqual, InstanceStatusRunning)
		convey.So(lite.InstanceIP, convey.ShouldEqual, "10.0.0.1")
	})
	convey.Convey("nil input returns nil", t, func() {
		convey.So(buildLiteInstanceFromInstance(nil), convey.ShouldBeNil)
	})
	convey.Convey("build copies all fields from a fully populated types.Instance", t, func() {
		ins := &types.Instance{
			InstanceID:      "ins-full",
			FuncKey:         "t2/f2/v2",
			ConcurrentNum:   7,
			InstanceIP:      "10.0.0.2",
			InstancePort:    "9090",
			NodeIP:          "192.168.1.1",
			NodePort:        "10250",
			FuncSig:         "sig-full",
			FunctionProxyID: "proxy-1",
			RouteAddress:    "route-1",
			AZ:              "az-1",
		}
		ins.InstanceStatus.Code = int32(constant.KernelInstanceStatusRunning)
		lite := buildLiteInstanceFromInstance(ins)
		convey.So(lite.InstanceID, convey.ShouldEqual, "ins-full")
		convey.So(lite.FuncKey, convey.ShouldEqual, "t2/f2/v2")
		convey.So(lite.Capacity, convey.ShouldEqual, 7)
		convey.So(lite.Status, convey.ShouldEqual, InstanceStatusRunning)
		convey.So(lite.InstanceIP, convey.ShouldEqual, "10.0.0.2")
		convey.So(lite.InstancePort, convey.ShouldEqual, "9090")
		convey.So(lite.NodeIP, convey.ShouldEqual, "192.168.1.1")
		convey.So(lite.NodePort, convey.ShouldEqual, "10250")
		convey.So(lite.FuncSig, convey.ShouldEqual, "sig-full")
		convey.So(lite.FunctionProxyID, convey.ShouldEqual, "proxy-1")
		convey.So(lite.RouteAddress, convey.ShouldEqual, "route-1")
		convey.So(lite.AZ, convey.ShouldEqual, "az-1")
	})
}

func TestCandidateSlotsAreSessionContextIsolated(t *testing.T) {
	pool := &LiteFunctionPool{instances: map[string]*LiteInstance{
		"a": {InstanceID: "a", SessionCtxID: "ctx-a", Status: InstanceStatusRunning, Capacity: 1},
		"b": {InstanceID: "b", SessionCtxID: "ctx-b", Status: InstanceStatusRunning, Capacity: 1},
		"r": {InstanceID: "r", SessionCtxID: "ctx-a", Status: InstanceStatusRunning, Capacity: 1, Reclaiming: true},
	},
		funcSpec: &types.FunctionSpecification{
			ExtendedMetaData: commontypes.ExtendedMetaData{
				EnableSessionCtx: true,
			},
		}}
	slots := pool.candidateSlotsLocked("ctx-a")
	convey.Convey("only the requested non-reclaiming context is returned", t, func() {
		convey.So(len(slots), convey.ShouldEqual, 1)
		convey.So(slots[0].InstanceID, convey.ShouldEqual, "a")
		convey.So(pool.sessionBindingKey("same", "ctx-a"), convey.ShouldNotEqual,
			pool.sessionBindingKey("same", "ctx-b"))
	})
}
