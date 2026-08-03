/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */

package litescheduler

import (
	"net/http"
	"time"

	commonTls "yuanrong.org/kernel/pkg/common/faas_common/tls"
	"yuanrong.org/kernel/pkg/functionscaler/config"
)

func newHTTPClient(timeout time.Duration) (string, *http.Client) {
	scheme := "http"
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if config.GlobalConfig.HTTPSConfig != nil && config.GlobalConfig.HTTPSConfig.HTTPSEnable {
		scheme = "https"
		transport.TLSClientConfig = commonTls.GetClientTLSConfig()
	}
	return scheme, &http.Client{Timeout: timeout, Transport: transport}
}
