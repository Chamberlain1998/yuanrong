/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
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

#pragma once

#include <atomic>
#include <memory>
#include <mutex>
#include <random>
#include <shared_mutex>
#include <string>
#include <unordered_map>
#include <vector>

#include "absl/synchronization/mutex.h"

#include "src/dto/lru_cache.h"
#include "src/libruntime/invokeadaptor/scheduler_instance_info.h"
#include "src/utility/logger/logger.h"

namespace YR {
namespace Libruntime {
class SchedulerManager {
public:
    explicit SchedulerManager(size_t lruCacheSize = 20);

    ~SchedulerManager() = default;

    void Add(const std::string &schedulerName, const std::string &schedulerId);

    void Remove(const std::string &schedulerName);

    void RemoveAll();

    void ResetAll(const std::vector<SchedulerInstance> &schedulerInfoList);

    void SetRoute(const std::string &functionId, const std::string &schedulerId);

    void RemoveRoute(const std::string &functionId);

    bool ContainsSchedulerId(const std::string &schedulerId) const;

    std::string Next(const std::string &functionId,
                     const std::shared_ptr<AvailableSchedulerInfos> &schedulerInfos = nullptr);

private:
    std::unordered_map<std::string, std::string> schedulerInfoMap_;

    std::vector<std::string> shuffledSchedulerList_;

    mutable std::atomic<size_t> currentIndex_{0};

    mutable absl::Mutex mtx_;

    mutable std::mt19937 rng_;

    LRUCache<std::string, std::string> lruCache_;

    void ShuffleList();

    std::string GetSchedulerFromLruOrRoundRobin(const std::string &functionId,
                                                const std::shared_ptr<AvailableSchedulerInfos> &schedulerInfos);

    std::string TryGetFromLru(const std::string &functionId,
                              const std::shared_ptr<AvailableSchedulerInfos> &schedulerInfos);

    std::string TryGetFromShuffledList(const std::string &functionId,
                                       const std::shared_ptr<AvailableSchedulerInfos> &schedulerInfos);
};

}  // namespace Libruntime
}  // namespace YR
