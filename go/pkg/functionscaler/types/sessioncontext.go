/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */

package types

import "strings"

const sessionContextKeySeparator = "\x00"

// JoinKey builds an unambiguous internal key from SessionContext-related parts.
func JoinKey(parts ...string) string {
	return strings.Join(parts, sessionContextKeySeparator)
}

// IdleReport describes SessionContext instances observed idle by a scheduler.
type IdleReport struct {
	SchedulerID string         `json:"schedulerId"`
	Instances   []IdleInstance `json:"instances"`
}

// IdleInstance identifies one SessionContext instance eligible for reclamation.
type IdleInstance struct {
	FuncKey      string `json:"funcKey"`
	SessionCtxID string `json:"sessionCtxId"`
	InstanceID   string `json:"instanceId"`
	IdleSince    string `json:"idleSince"`
}
