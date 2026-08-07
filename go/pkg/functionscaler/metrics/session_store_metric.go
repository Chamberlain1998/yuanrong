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

package metrics

import (
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// session store 外部存储操作指标。不受 Scenario 限制——这是 session 可靠性特性的
// 运维指标，任何部署都应可观测。注册到默认 registry，由 /metrics 端点（promhttp.Handler）暴露。
var (
	sessionStoreOpTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "yuanrong_session_store_op_total",
		Help: "Total session store operations by type (get/save/delete) and result (ok/err).",
	}, []string{"op_type", "result"})

	sessionStoreOpDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "yuanrong_session_store_op_duration_seconds",
		Help:    "Session store operation duration by type.",
		Buckets: []float64{0.001, 0.005, 0.01, 0.05, 0.1, 0.2, 0.5, 1, 5},
	}, []string{"op_type"})

	sessionStoreOpDropped = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "yuanrong_session_store_op_dropped_total",
		Help: "Session store operations dropped due to async queue full, by type.",
	}, []string{"op_type"})
)

// OnSessionStoreOp records a session store operation's duration and result.
// opType: "get" / "save" / "delete".
func OnSessionStoreOp(opType string, duration time.Duration, err error) {
	result := "ok"
	if err != nil {
		result = "err"
	}
	sessionStoreOpTotal.WithLabelValues(opType, result).Inc()
	sessionStoreOpDuration.WithLabelValues(opType).Observe(duration.Seconds())
}

// OnSessionStoreOpDropped records a session store operation dropped because the async
// queue was full (fail-open degradation).
func OnSessionStoreOpDropped(opType string) {
	sessionStoreOpDropped.WithLabelValues(opType).Inc()
}
