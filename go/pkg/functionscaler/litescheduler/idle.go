/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */

package litescheduler

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"yuanrong.org/kernel/pkg/common/faas_common/constant"
	"yuanrong.org/kernel/pkg/common/faas_common/localauth"
	"yuanrong.org/kernel/pkg/common/faas_common/logger/log"
	commonTypes "yuanrong.org/kernel/pkg/common/faas_common/types"
	"yuanrong.org/kernel/pkg/functionscaler/config"
	"yuanrong.org/kernel/pkg/functionscaler/selfregister"
	"yuanrong.org/kernel/pkg/functionscaler/types"
)

const defaultSessionContextIdleTTL = 5 * time.Second

const idleReportHTTPTimeout = 5 * time.Second

type idleReporter struct {
	scheme string
	client *http.Client
}

func sessionContextRoutingKey(funcKey, sessionCtxID string) string {
	return splitFuncKey(funcKey).tenantID + "/" + funcKey + "/" + sessionCtxID
}

func (ls *LiteScheduler) processSessionContextIdle() {
	ticker := time.NewTicker(defaultSessionContextIdleTTL)
	defer ticker.Stop()
	for {
		select {
		case <-ls.stopCh:
			return
		case now := <-ticker.C:
			ls.scanSessionContextIdle(now)
		}
	}
}

func (ls *LiteScheduler) scanSessionContextIdle(now time.Time) {
	if ls.ownerProxy == nil {
		return
	}
	grouped := map[string]*types.IdleReport{}
	owners := map[string]*commonTypes.InstanceInfo{}
	for _, pool := range ls.Pools() {
		pool.Lock()
		for _, instance := range pool.instances {
			if instance.SessionCtxID == "" {
				continue
			}
			routingKey := sessionContextRoutingKey(pool.funcKey, instance.SessionCtxID)
			_, owned := ls.ownerProxy.CheckHashOwner(routingKey)
			if !owned {
				instance.IdleSince = time.Time{}
				continue
			}
			if instance.InUse > 0 {
				instance.IdleSince = time.Time{}
				instance.Reclaiming = false
				continue
			}
			if instance.Reclaiming {
				continue
			}
			if instance.IdleSince.IsZero() {
				instance.IdleSince = now
				continue
			}
			if now.Sub(instance.IdleSince) < defaultSessionContextIdleTTL {
				continue
			}
			// SessionContext ownership decides who observes idleness. The
			// function owner remains the destination because it owns the
			// InstancePool and performs the actual deletion.
			owner, _ := ls.ownerProxy.FindHashOwner(pool.funcKey)
			if owner == nil {
				continue
			}
			report := grouped[owner.InstanceName]
			if report == nil {
				report = &types.IdleReport{SchedulerID: selfregister.GetSchedulerProxyName()}
				grouped[owner.InstanceName] = report
				owners[owner.InstanceName] = owner
			}
			instance.Reclaiming = true
			report.Instances = append(report.Instances, types.IdleInstance{
				FuncKey: pool.funcKey, SessionCtxID: instance.SessionCtxID,
				InstanceID: instance.InstanceID, IdleSince: instance.IdleSince.UTC().Format(time.RFC3339Nano),
			})
		}
		pool.Unlock()
	}
	for ownerID, report := range grouped {
		if err := ls.sendIdleReport(owners[ownerID], report); err != nil {
			log.GetLogger().Warnf("send SessionContext idle report failed: %v", err)
			ls.resetReclaiming(report)
		}
	}
}

func (ls *LiteScheduler) resetReclaiming(report *types.IdleReport) {
	for _, item := range report.Instances {
		if pool := ls.getPool(item.FuncKey); pool != nil {
			pool.Lock()
			if instance := pool.instances[item.InstanceID]; instance != nil {
				instance.Reclaiming = false
			}
			pool.Unlock()
		}
	}
}

func (ls *LiteScheduler) sendIdleReport(owner *commonTypes.InstanceInfo, report *types.IdleReport) error {
	if owner == nil || owner.Address == "" {
		return fmt.Errorf("function owner unavailable")
	}
	body, err := json.Marshal(report)
	if err != nil {
		return err
	}
	reporter := ls.getIdleReporter()
	req, err := http.NewRequest(http.MethodPost, reporter.scheme+"://"+owner.Address+"/sessioncontext/idle",
		bytes.NewReader(body))
	if err != nil {
		return err
	}
	authorization, timestamp := localauth.SignLocally(config.GlobalConfig.LocalAuth.AKey,
		config.GlobalConfig.LocalAuth.SKey, "sessioncontext-idle", config.GlobalConfig.LocalAuth.Duration)
	req.Header.Set(constant.HeaderAuthorization, authorization)
	req.Header.Set(constant.HeaderAuthTimestamp, timestamp)
	req.Header.Set(constant.HeaderTraceID, selfregister.GetSchedulerProxyName())
	req.Header.Set("Content-Type", "application/json")
	response, err := reporter.client.Do(req)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusNoContent {
		value, _ := io.ReadAll(response.Body)
		return fmt.Errorf("idle report returned %d: %s", response.StatusCode, string(value))
	}
	return nil
}

func (ls *LiteScheduler) getIdleReporter() *idleReporter {
	ls.idleOnce.Do(func() {
		scheme, client := newHTTPClient(idleReportHTTPTimeout)
		ls.idleHTTP = &idleReporter{scheme: scheme, client: client}
	})
	return ls.idleHTTP
}
