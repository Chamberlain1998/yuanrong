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
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strconv"
	"strings"
	"time"

	"yuanrong.org/kernel/pkg/functionscaler/types"
)

// liteAllocPrefix is the prefix of LiteScheduler allocation IDs.
const liteAllocPrefix = "lite"

// Allocation maps an allocationID to its session/instance context for reverse lookup.
type Allocation struct {
	AllocationID string
	SessionID    string // full sessionID (allocationID only stores hash)
	SessionCtxID string
	SessionTTL   int // seconds; 0 means default; used for idle-unbind timer
	TenantID     string
	InstanceID   string
	FuncKey      string
	Lease        types.InstanceLease
	ExpireAt     time.Time
	CreatedAt    time.Time
}

// sessionHash returns the first 16 hex chars of sha256(sessionID).
// Full sessionID is NOT embedded in allocationID to avoid leakage.
func sessionHash(sessionID string) string {
	h := sha256.Sum256([]byte(sessionID))
	return hex.EncodeToString(h[:])[:16]
}

func allocationSessionID(sessionID, sessionCtxID string) string {
	if sessionID != "" {
		return sessionID
	}
	return sessionCtxID
}

// genAllocationID produces: lite:{sessionHash}:{instanceID}:thread:{seq}
func genAllocationID(sessionID, instanceID string, seq int) string {
	return fmt.Sprintf("%s:%s:%s:thread:%d", liteAllocPrefix, sessionHash(sessionID), instanceID, seq)
}

// parseLiteAllocationID parses an allocationID; isLite=false if not lite-prefixed.
func parseLiteAllocationID(allocID string) (isLite bool, sessHash string, instanceID string, seq int) {
	if !strings.HasPrefix(allocID, liteAllocPrefix+":") {
		return false, "", "", 0
	}
	// lite:{hash}:{instanceID}:thread:{seq}
	rest := strings.TrimPrefix(allocID, liteAllocPrefix+":")
	parts := strings.SplitN(rest, ":", 3) // [hash, instanceID, "thread:{seq}"]
	if len(parts) != 3 {
		return false, "", "", 0
	}
	sessHash = parts[0]
	instanceID = parts[1]
	threadPart := parts[2]
	if !strings.HasPrefix(threadPart, "thread:") {
		return false, "", "", 0
	}
	seqStr := strings.TrimPrefix(threadPart, "thread:")
	n, err := strconv.Atoi(seqStr)
	if err != nil {
		return false, "", "", 0
	}
	seq = n
	return true, sessHash, instanceID, seq
}

// IsLiteAllocationID reports whether allocID belongs to LiteScheduler branch.
func IsLiteAllocationID(allocID string) bool {
	isLite, _, _, _ := parseLiteAllocationID(allocID)
	return isLite
}
