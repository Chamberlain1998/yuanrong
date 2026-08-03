/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */

package sessioncontextmanager

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
)

func runtimeFunctionName(registeredName string) string {
	if index := strings.LastIndex(registeredName, "@"); index >= 0 {
		return registeredName[index+1:]
	}
	return registeredName
}

func sessionPrefix(tenantID, registeredName, version, sessionCtxID string) string {
	return fmt.Sprintf("ar:s:%s:%s",
		hashParts(tenantID, runtimeFunctionName(registeredName), version),
		hashParts(sessionCtxID))
}

func TurnKey(tenantID, registeredName, version, sessionCtxID string, index int) string {
	return fmt.Sprintf("%s:t%d", sessionPrefix(tenantID, registeredName, version, sessionCtxID), index)
}

func EventKey(tenantID, registeredName, version, sessionCtxID string, seq int) string {
	return fmt.Sprintf("%s:e%d", sessionPrefix(tenantID, registeredName, version, sessionCtxID), seq)
}

func ControlKey(tenantID, registeredName, version, sessionCtxID string) string {
	return sessionPrefix(tenantID, registeredName, version, sessionCtxID) + ":c"
}

func hashParts(parts ...string) string {
	var encoded bytes.Buffer
	encoder := json.NewEncoder(&encoded)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(parts); err != nil {
		panic(fmt.Sprintf("encode session context key: %v", err))
	}
	sum := sha256.Sum256(bytes.TrimSuffix(encoded.Bytes(), []byte{'\n'}))
	return hex.EncodeToString(sum[:])[:32]
}
