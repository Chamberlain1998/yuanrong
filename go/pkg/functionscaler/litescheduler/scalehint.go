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
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	"go.uber.org/zap"

	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/common/faas_common/localauth"
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	"yuanrong.org/kernel/pkg/common/faas_common/statuscode"
	commonTypes "yuanrong.org/kernel/pkg/common/faas_common/types"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/pkg/functionscaler/selfregister"
	scalerTypes "yuanrong.org/kernel/pkg/functionscaler/types"
	"yuanrong.org/kernel/pkg/functionscaler/utils"
	"yuanrong.org/kernel/runtime/libruntime/api"
)

// ScaleHint is an idempotent capacity demand hint from LiteScheduler to Scaler.
// Scaler dedups by FuncKey and does not create N instances for N hints.
type ScaleHint struct {
	FuncKey                 string `json:"funcKey"`
	TenantID                string `json:"tenantId"`
	SessionID               string `json:"sessionId,omitempty"`
	SessionCtxID            string `json:"sessionCtxId,omitempty"`
	Reason                  string `json:"reason"` // cold_start, no_capacity, high_concurrency
	RequestedConcurrency    int    `json:"requestedConcurrency"`
	CurrentLocalConcurrency int    `json:"currentLocalConcurrency"`
	CurrentLocalCapacity    int    `json:"currentLocalCapacity"`
	SchedulerID             string `json:"schedulerId"`
	TraceID                 string `json:"traceId"`
	RequestID               string `json:"requestId"`
}

// noopSender is a placeholder ScaleHintSender that logs hints but does not dispatch
// them. It exists so that LiteScheduler's cold-start path (construct hint -> send ->
// waitForInstance) is fully exercised in unit tests and local dev without requiring
// a real scaler backend.
//
// Future implementations:
//   - httpSender: POST ScaleHint as JSON to a remote scaler service.
//   - grpcSender: stream ScaleHints over gRPC to a remote scaler service.
//
// The ScaleHintSender interface is the single seam; swapping noopSender for a real
// sender requires no changes to operation.go or litescheduler.go.
type noopSender struct{}

// NewNoopSender constructs a no-op ScaleHintSender suitable for testing and local
// development. Production code should inject a real sender (httpSender/grpcSender)
// when available.
func NewNoopSender() ScaleHintSender {
	return &noopSender{}
}

func (n *noopSender) Send(hint *ScaleHint) (*ScaleHintResponse, error) {
	if hint == nil {
		return nil, nil
	}
	log.GetLogger().With(zap.String("funcKey", hint.FuncKey),
		zap.String("sessionID", hint.SessionID), zap.String("traceID", hint.TraceID)).
		Debug("lite scaleHint noop (no scaler backend configured)")
	return nil, nil
}

const (
	// scaleHintURLPath is the receiver endpoint registered in functionscaler/httpserver.
	scaleHintURLPath = "/scalehint"
	// scaleHintAppID is the appID used for localauth request signing.
	scaleHintAppID = "scalehint"
	// scaleHintHTTPTimeout bounds a single POST; the receiver answers 202
	// immediately, so responses are millisecond-scale. Must stay well below
	// AcquireWaitTimeoutMs to not eat into waitForInstance's budget.
	scaleHintHTTPTimeout = 1 * time.Second
	// scaleHintMaxRetries bounds reroutes on non-owner (150464) responses.
	scaleHintMaxRetries = 2
)

// ScaleHintResponse is the body of a non-202 /scalehint response. For a non-owner
// rejection ErrorCode is AcquireNonOwnerSchedulerErrorCode and ErrorMessage
// carries the owner InstanceID, mirroring the legacy acquire "reject + tell the
// owner" convention.
type ScaleHintResponse struct {
	ErrorCode    int    `json:"errorCode"`
	ErrorMessage string `json:"errorMessage,omitempty"`
}

// httpSender POSTs ScaleHints to the funcKey owner's /scalehint endpoint. It
// always goes through HTTP - even when the owner is the local scheduler - so
// the LiteScheduler/Scaler boundary stays a protocol boundary and the two can
// be split into independent components later.
type httpSender struct {
	proxy      *selfregister.SchedulerProxy
	client     *http.Client
	dedup      *utils.TimeoutMap
	dedupMu    sync.Mutex
	dedupTTL   time.Duration
	maxRetries int
	scheme     string
}

type dedupToken struct {
	createdAt time.Time
}

// NewHTTPSender constructs a production ScaleHintSender backed by HTTP.
// The dedup window equals LiteScheduler.AcquireWaitTimeoutMs: within one
// cold-start wait window a function emits at most one hint, no matter how many
// concurrent acquires cold-start on it.
func NewHTTPSender(proxy *selfregister.SchedulerProxy) ScaleHintSender {
	scheme, client := newHTTPClient(scaleHintHTTPTimeout)
	dedupTTL := time.Duration(config.GlobalConfig.LiteScheduler.AcquireWaitTimeoutMs) * time.Millisecond
	if dedupTTL <= 0 {
		dedupTTL = time.Second
	}
	log.GetLogger().Infof("scaleHint http sender enabled, scheme %s, dedupTTL %d ms, maxRetries %d",
		scheme, dedupTTL.Milliseconds(), scaleHintMaxRetries)
	return &httpSender{
		proxy:      proxy,
		client:     client,
		dedup:      utils.NewTimeoutMap(time.Minute),
		dedupTTL:   dedupTTL,
		maxRetries: scaleHintMaxRetries,
		scheme:     scheme,
	}
}

// Send dispatches one idempotent capacity hint to the funcKey owner. It is
// synchronous but time-bounded (client timeout 1s): it runs in handleColdStart
// before waitForInstance and must not block the acquire for long. Failed
// dispatches release their dedup claim so a later cold-start can retry.
func (s *httpSender) Send(hint *ScaleHint) (*ScaleHintResponse, error) {
	if hint == nil {
		log.GetLogger().Warnf("receive nil scaleHint, skip send")
		return nil, nil
	}
	logger := log.GetLogger().With(zap.String("funcKey", hint.FuncKey),
		zap.String("sessionID", hint.SessionID), zap.String("traceID", hint.TraceID))
	dedupKey := hint.FuncKey
	if hint.SessionCtxID != "" {
		dedupKey = scalerTypes.JoinKey(hint.FuncKey, hint.SessionCtxID)
	}
	token, reserved := s.reserveDedup(dedupKey)
	if !reserved {
		logger.Debug("scaleHint deduped within window, skip")
		return nil, nil
	}
	accepted := false
	defer func() {
		s.finishDedup(dedupKey, token, accepted)
	}()
	hint.SchedulerID = selfregister.GetSchedulerProxyName()
	body, err := json.Marshal(hint)
	if err != nil {
		logger.Warnf("marshal scaleHint failed: %s", err.Error())
		return nil, err
	}
	owner, _ := s.proxy.FindHashOwner(hint.FuncKey)
	if owner == nil {
		logger.Warn("hash ring not ready, drop scaleHint (best-effort)")
		return nil, fmt.Errorf("scaleHint owner not found")
	}
	if owner.Address == "" {
		logger.Warnf("owner %s has no registered address, drop scaleHint", owner.InstanceName)
		return nil, fmt.Errorf("scaleHint owner address is empty")
	}
	response, err := s.sendWithRetry(owner, hint.TraceID, body, logger)
	accepted = err == nil && (response == nil || response.ErrorCode == 0)
	return response, err
}

// reserveDedup atomically claims a funcKey. The temporary TTL covers both the
// configured dedup window and the worst-case HTTP retry budget, so another
// cold-start cannot replace an in-flight claim before dispatch completes.
func (s *httpSender) reserveDedup(funcKey string) (*dedupToken, bool) {
	s.dedupMu.Lock()
	defer s.dedupMu.Unlock()
	if _, exists := s.dedup.Get(funcKey); exists {
		return nil, false
	}
	token := &dedupToken{createdAt: time.Now()}
	inFlightTTL := s.dedupTTL
	if s.client != nil && s.client.Timeout > 0 {
		attempts := s.maxRetries + 1
		if attempts < 1 {
			attempts = 1
		}
		inFlightTTL += time.Duration(attempts) * s.client.Timeout
	}
	s.dedup.Set(funcKey, token, inFlightTTL)
	return token, true
}

// finishDedup keeps successful dispatches deduped for a fresh TTL window and
// releases failed claims immediately. Token matching prevents an old sender
// from deleting a newer claim if its in-flight entry has expired.
func (s *httpSender) finishDedup(funcKey string, token *dedupToken, accepted bool) {
	s.dedupMu.Lock()
	defer s.dedupMu.Unlock()
	current, exists := s.dedup.Get(funcKey)
	if !exists || current != token {
		return
	}
	if accepted {
		s.dedup.Set(funcKey, token, s.dedupTTL)
		return
	}
	s.dedup.Delete(funcKey)
}

// sendWithRetry POSTs the hint to owner; on a non-owner response it resolves
// the returned ownerID and retries against the new owner, bounded by
// maxRetries. Any other failure drops the hint.
func (s *httpSender) sendWithRetry(owner *commonTypes.InstanceInfo, traceID string, body []byte,
	logger api.FormatLogger,
) (*ScaleHintResponse, error) {
	for attempt := 0; ; attempt++ {
		statusCode, respBody, err := s.post(owner.Address, traceID, body)
		if err == nil && statusCode == http.StatusAccepted {
			logger.Infof("scaleHint accepted by owner %s (%s)", owner.InstanceName, owner.Address)
			return &ScaleHintResponse{}, nil
		}
		newOwnerID := parseNonOwnerRedirect(statusCode, respBody)
		if newOwnerID == "" {
			logger.Warnf("scaleHint to %s failed (status %d, err %v), resp %s, drop",
				owner.Address, statusCode, err, string(respBody))
			var response ScaleHintResponse
			if len(respBody) != 0 && json.Unmarshal(respBody, &response) == nil {
				return &response, nil
			}
			if err != nil {
				return nil, err
			}
			return nil, fmt.Errorf("scaleHint failed with HTTP status %d", statusCode)
		}
		if attempt >= s.maxRetries {
			logger.Warnf("scaleHint reroute retries exhausted (%d), drop", s.maxRetries)
			return nil, fmt.Errorf("scaleHint reroute retries exhausted")
		}
		next := s.proxy.FindByInstanceID(newOwnerID)
		if next == nil || next.Address == "" {
			logger.Warnf("redirected owner %s not found in ring, drop scaleHint", newOwnerID)
			return nil, fmt.Errorf("redirected scaleHint owner unavailable")
		}
		logger.Infof("scaleHint rerouted to new owner %s (%s)", next.InstanceName, next.Address)
		owner = next
	}
}

func (s *httpSender) post(addr, traceID string, body []byte) (int, []byte, error) {
	req, err := http.NewRequest(http.MethodPost, s.scheme+"://"+addr+scaleHintURLPath, bytes.NewReader(body))
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	authorization, timestamp := localauth.SignLocally(config.GlobalConfig.LocalAuth.AKey,
		config.GlobalConfig.LocalAuth.SKey, scaleHintAppID, config.GlobalConfig.LocalAuth.Duration)
	req.Header.Set(constant.HeaderAuthorization, authorization)
	req.Header.Set(constant.HeaderAuthTimestamp, timestamp)
	req.Header.Set(constant.HeaderTraceID, traceID)
	resp, err := s.client.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	respBody, err := io.ReadAll(resp.Body)
	return resp.StatusCode, respBody, err
}

// parseNonOwnerRedirect extracts the new owner InstanceID from a non-owner
// response body; returns "" for any other response.
func parseNonOwnerRedirect(statusCode int, body []byte) string {
	if statusCode != http.StatusOK {
		return ""
	}
	var hintResp ScaleHintResponse
	if err := json.Unmarshal(body, &hintResp); err != nil {
		return ""
	}
	if hintResp.ErrorCode == statuscode.AcquireNonOwnerSchedulerErrorCode {
		return hintResp.ErrorMessage
	}
	return ""
}
