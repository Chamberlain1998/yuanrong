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
	"strings"

	"go.uber.org/zap"
	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	commonTypes "yuanrong.org/kernel/pkg/common/faas_common/types"
	"yuanrong.org/kernel/pkg/functionscaler/config"
)

// InstanceOperation mirrors faasscheduler.InstanceOperation to avoid import cycle.
type InstanceOperation string

// LiteRequest is the parsed request entering the LiteScheduler branch.
type LiteRequest struct {
	Op                InstanceOperation
	FuncKey           string
	TenantID          string
	SessionID         string
	SessionCtxID      string
	SessionTTL        int // seconds; 0 means use default
	Concurrency       int
	AllocationIDs     []string
	ExtraData         []byte
	MetricsData       []byte
	TraceID           string
	NeedReverseLookup bool
}

// ParseRequest is stateless: decides whether to enter the lite branch (ok=false -> legacy).
func (ls *LiteScheduler) ParseRequest(op InstanceOperation, targetName string,
	extraData []byte, traceID string) (req *LiteRequest, ok bool) {
	logger := log.GetLogger()
	traceField := zap.String("traceID", traceID)
	defer func() {
		if r := recover(); r != nil {
			logger.Error("lite parseRequest panic, fallback to legacy path", traceField, zap.Any("panic", r))
			req = nil
			ok = false
		}
	}()

	switch op {
	case "acquire", "release", "retain", "batchRetain":
	default:
		return nil, false // unsupported op -> legacy
	}

	if !config.GlobalConfig.LiteScheduler.Enable {
		return nil, false
	}

	switch op {
	case "acquire":
		sessionID, sessionCtxID, sessionTTL, concurrency := extractSessionDetails(extraData)
		funcKey := targetName
		if !ls.isFuncEnabled(funcKey) {
			return nil, false // 3: whitelist
		}
		sessionCtxEnabled := false
		if sessionCtxID != "" && ls.funcSpecGetter != nil {
			funcSpec := ls.funcSpecGetter(funcKey)
			sessionCtxEnabled = funcSpec != nil && funcSpec.ExtendedMetaData.EnableSessionCtx
		}
		if sessionID == "" && !sessionCtxEnabled {
			return nil, false // non-session call chain -> legacy
		}
		logger.Debug("lite parseRequest acquire enters lite branch", traceField, zap.String("funcKey", funcKey))
		return &LiteRequest{
			Op: op, FuncKey: funcKey, SessionID: sessionID, SessionCtxID: sessionCtxID,
			SessionTTL:  sessionTTL,
			Concurrency: concurrency,
			TenantID:    splitFuncKey(funcKey).tenantID,
			ExtraData:   extraData, TraceID: traceID,
		}, true
	case "release", "retain":
		if !IsLiteAllocationID(targetName) {
			return nil, false // 4e: non-lite prefix -> legacy
		}
		logger.Debug("lite parseRequest enters lite branch", traceField, zap.String("operation", string(op)),
			zap.String("allocationID", targetName))
		return &LiteRequest{
			Op: op, AllocationIDs: []string{targetName},
			ExtraData: extraData, MetricsData: extraData,
			TraceID: traceID, NeedReverseLookup: true,
		}, true
	case "batchRetain":
		ids := strings.Split(targetName, ",")
		liteCount := 0
		for _, id := range ids {
			if IsLiteAllocationID(id) {
				liteCount++
			}
		}
		if liteCount == 0 {
			return nil, false // all non-lite -> legacy
		}
		if liteCount != len(ids) {
			logger.Warn("batchRetain mixed lite/non-lite prefix, fallback to legacy", traceField,
				zap.String("target", targetName))
			return nil, false // 4f: mixed -> legacy
		}
		logger.Debug("lite parseRequest batchRetain enters lite branch", traceField,
			zap.Int("allocationCount", len(ids)))
		return &LiteRequest{
			Op: op, AllocationIDs: ids,
			MetricsData: extraData, TraceID: traceID,
			NeedReverseLookup: true,
		}, true
	}
	return nil, false
}

func extractSessionCtxID(extraData []byte) string {
	_, sessionCtxID, _, _ := extractSessionDetails(extraData)
	return sessionCtxID
}

// extractSessionConfig parses extraData for InstanceSessionConfig (key constant.InstanceSessionConfig).
// Returns sessionID, sessionTTL (seconds) and concurrency. sessionID is "" if absent.
func extractSessionConfig(extraData []byte) (sessionID string, sessionTTL int, concurrency int) {
	sessionID, _, sessionTTL, concurrency = extractSessionDetails(extraData)
	return
}

// extractSessionDetails decodes the outer extraData map once. Acquire and
// reacquire need both session and session-context fields; decoding them in two
// helpers doubled the transient JSON maps on these high-frequency paths.
func extractSessionDetails(extraData []byte) (sessionID, sessionCtxID string, sessionTTL, concurrency int) {
	if len(extraData) == 0 {
		return "", "", 0, 0
	}
	m := map[string][]byte{}
	if err := json.Unmarshal(extraData, &m); err != nil {
		log.GetLogger().Debugf("lite extractSessionConfig: extraData unmarshal failed: %v", err)
		return "", "", 0, 0
	}
	sessionCtxID = string(m[constant.SessionCtxID])
	raw, exists := m[constant.InstanceSessionConfig]
	if !exists {
		return "", sessionCtxID, 0, 0
	}
	sess := commonTypes.InstanceSessionConfig{}
	if err := json.Unmarshal(raw, &sess); err != nil {
		log.GetLogger().Debugf("lite extractSessionConfig: InstanceSessionConfig unmarshal failed: %v", err)
		return "", sessionCtxID, 0, 0
	}
	return sess.SessionID, sessionCtxID, sess.SessionTTL, sess.Concurrency
}
