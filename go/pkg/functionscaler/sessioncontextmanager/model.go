/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */

// Package sessioncontextmanager coordinates SessionContext lifecycle operations.
package sessioncontextmanager

import "encoding/json"

const (
	StateCreating = "CREATING"
	StateReady    = "READY"
	StateDeleting = "DELETING"
)

type ControlRecord struct {
	SchemaVersion        int    `json:"schemaVersion"`
	State                string `json:"state"`
	ForkedFrom           string `json:"forkedFrom,omitempty"`
	ForkTurnID           string `json:"forkTurnId,omitempty"`
	ForkTurnIndex        int    `json:"forkTurnIndex,omitempty"`
	ForkSeq              int    `json:"forkSeq,omitempty"`
	CopiedTurn           int    `json:"copiedTurn,omitempty"`
	CopiedSeq            int    `json:"copiedSeq,omitempty"`
	DeleteTurnUpperBound int    `json:"deleteTurnUpperBound,omitempty"`
	DeleteSeqUpperBound  int    `json:"deleteSeqUpperBound,omitempty"`
	CreatedAt            string `json:"createdAt"`
	UpdatedAt            string `json:"updatedAt"`
}

type TurnRecord struct {
	SessionContextID string `json:"sessionContextId"`
	TurnIndex        int    `json:"turnIndex"`
	TurnID           string `json:"turnId"`
	StartSeq         int    `json:"startSeq"`
	CreatedAt        string `json:"createdAt"`
	SchemaVersion    int    `json:"schemaVersion"`
}

type Event struct {
	SessionContextID string          `json:"sessionContextId"`
	TurnID           string          `json:"turnId"`
	Seq              int             `json:"seq"`
	EventID          string          `json:"eventId"`
	Source           string          `json:"source"`
	Type             string          `json:"type"`
	Data             json.RawMessage `json:"data"`
	SchemaVersion    int             `json:"schemaVersion"`
	CreatedAt        string          `json:"createdAt"`
}

type ForkRequest struct {
	TenantID           string `json:"tenantId"`
	FuncKey            string `json:"funcKey"`
	FunctionURN        string `json:"functionUrn"`
	SourceSessionCtxID string `json:"sourceSessionCtxId"`
	TargetSessionCtxID string `json:"targetSessionCtxId"`
	TurnID             string `json:"turnId"`
	TraceID            string `json:"traceId"`
}

type ForkResponse struct {
	SessionContextID string `json:"sessionContextId"`
	State            string `json:"state"`
}

type DeleteRequest struct {
	TenantID     string `json:"tenantId"`
	FuncKey      string `json:"funcKey"`
	FunctionURN  string `json:"functionUrn"`
	SessionCtxID string `json:"sessionCtxId"`
	TraceID      string `json:"traceId"`
}

type Error struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }
