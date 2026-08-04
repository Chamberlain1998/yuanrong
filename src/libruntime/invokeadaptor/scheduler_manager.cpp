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
#include "scheduler_manager.h"

#include <algorithm>
#include <chrono>
#include <random>

#include "src/libruntime/invokeadaptor/load_balancer.h"

namespace YR {
namespace Libruntime {

SchedulerManager::SchedulerManager(size_t lruCacheSize) : lruCache_(lruCacheSize)
{
    auto seed = std::chrono::steady_clock::now().time_since_epoch().count();
    rng_ = std::mt19937(seed);
}

void SchedulerManager::ShuffleList()
{
    if (schedulerInfoMap_.empty()) {
        shuffledSchedulerList_.clear();
        return;
    }

    std::vector<std::string> list;
    list.reserve(schedulerInfoMap_.size());
    for (const auto &pair : schedulerInfoMap_) {
        list.push_back(pair.second);
    }
    std::shuffle(list.begin(), list.end(), rng_);

    shuffledSchedulerList_.clear();
    shuffledSchedulerList_.reserve(list.size());
    for (const auto &schedulerId : list) {
        shuffledSchedulerList_.push_back(schedulerId);
    }
    currentIndex_ = 0;
}

void SchedulerManager::Add(const std::string &schedulerName, const std::string &schedulerId)
{
    if (schedulerName.empty() || schedulerId.empty()) {
        YRLOG_WARN("scheduler name: {} or id: {} is empty, no need add", schedulerName, schedulerId);
        return;
    }
    absl::WriterMutexLock lk(&mtx_);
    schedulerInfoMap_[schedulerName] = schedulerId;

    auto it = std::find(shuffledSchedulerList_.begin(), shuffledSchedulerList_.end(), schedulerId);
    if (it != shuffledSchedulerList_.end()) {
        YRLOG_DEBUG("schedulerId: {} already exists, skip adding", schedulerId);
        return;
    }
    shuffledSchedulerList_.push_back(schedulerId);
    YRLOG_DEBUG("Added scheduler name: {}, id: {}, total count: {}", schedulerName, schedulerId,
                schedulerInfoMap_.size());
}

void SchedulerManager::Remove(const std::string &schedulerName)
{
    absl::WriterMutexLock lk(&mtx_);
    auto it = schedulerInfoMap_.find(schedulerName);
    if (it == schedulerInfoMap_.end()) {
        YRLOG_WARN("Scheduler name: {} not found, nothing to remove", schedulerName);
        return;
    }

    auto listIt = std::remove(shuffledSchedulerList_.begin(), shuffledSchedulerList_.end(), it->second);
    shuffledSchedulerList_.erase(listIt, shuffledSchedulerList_.end());
    schedulerInfoMap_.erase(it);
    YRLOG_DEBUG("shuffledSchedulerList_ updated: removed scheduler '{}', now size: {}", schedulerName,
                shuffledSchedulerList_.size());
}

void SchedulerManager::RemoveAll()
{
    absl::WriterMutexLock lk(&mtx_);
    schedulerInfoMap_.clear();
    shuffledSchedulerList_.clear();
    currentIndex_ = 0;
    lruCache_.Clear();
    YRLOG_DEBUG("All schedulers removed and LRU cache cleared");
}

void SchedulerManager::ResetAll(const std::vector<SchedulerInstance> &schedulerInfoList)
{
    absl::WriterMutexLock lk(&mtx_);
    schedulerInfoMap_.clear();
    for (const auto &info : schedulerInfoList) {
        if (info.InstanceName.empty() || info.InstanceID.empty() || !info.isAvailable) {
            YRLOG_WARN("Invalid scheduler info: name={}, id={}", info.InstanceName, info.InstanceID);
            continue;
        }
        schedulerInfoMap_[info.InstanceName] = info.InstanceID;
    }
    ShuffleList();
    lruCache_.Clear();
    YRLOG_INFO("Reset all schedulers, total count: {}, LRU cache cleared", schedulerInfoMap_.size());
}

void SchedulerManager::SetRoute(const std::string &functionId, const std::string &schedulerId)
{
    absl::WriterMutexLock lk(&mtx_);
    lruCache_.Insert(functionId, schedulerId);
}

void SchedulerManager::RemoveRoute(const std::string &functionId)
{
    absl::WriterMutexLock lk(&mtx_);
    lruCache_.Erase(functionId);
}

bool SchedulerManager::ContainsSchedulerId(const std::string &schedulerId) const
{
    if (schedulerId.empty()) {
        return false;
    }
    absl::ReaderMutexLock lk(&mtx_);
    return std::any_of(schedulerInfoMap_.begin(), schedulerInfoMap_.end(),
                       [&schedulerId](const auto &item) { return item.second == schedulerId; });
}

std::string SchedulerManager::Next(const std::string &functionId,
                                   const std::shared_ptr<AvailableSchedulerInfos> &schedulerInfos)
{
    absl::ReaderMutexLock lk(&mtx_);
    if (functionId.empty()) {
        YRLOG_ERROR("functionId is empty, cannot find scheduler");
        return ALL_SCHEDULER_UNAVAILABLE;
    }
    return GetSchedulerFromLruOrRoundRobin(functionId, schedulerInfos);
}

std::string SchedulerManager::GetSchedulerFromLruOrRoundRobin(
    const std::string &functionId, const std::shared_ptr<AvailableSchedulerInfos> &schedulerInfos)
{
    if (const auto &schedulerId = TryGetFromLru(functionId, schedulerInfos); !schedulerId.empty()) {
        return schedulerId;
    }

    if (const auto &schedulerId = TryGetFromShuffledList(functionId, schedulerInfos); !schedulerId.empty()) {
        return schedulerId;
    }

    YRLOG_ERROR("No available scheduler found for functionId: {}", functionId);
    return ALL_SCHEDULER_UNAVAILABLE;
}

std::string SchedulerManager::TryGetFromLru(const std::string &functionId,
                                            const std::shared_ptr<AvailableSchedulerInfos> &schedulerInfos)
{
    auto result = lruCache_.Get(functionId);
    if (!result.has_value()) {
        YRLOG_DEBUG("LRU cache miss for functionId: {}", functionId);
        return "";
    }

    const std::string &schedulerId = result.value();
    if (!schedulerInfos || schedulerInfos->schedulerInstanceList.empty()) {
        YRLOG_DEBUG("No schedulerInfos provided, return LRU hit: {}", schedulerId);
        return schedulerId;
    }

    for (const auto &ins : schedulerInfos->schedulerInstanceList) {
        if (ins->InstanceID == schedulerId) {
            if (!ins->isAvailable) {
                YRLOG_DEBUG("LRU hit but schedulerId={} is not available", schedulerId);
                return "";
            }
            YRLOG_DEBUG("LRU hit and schedulerId={} is available -> return", schedulerId);
            return schedulerId;
        }
    }

    YRLOG_DEBUG("LRU hit: schedulerId={} not in schedulerInfos, assume available", schedulerId);
    return schedulerId;
}

std::string SchedulerManager::TryGetFromShuffledList(
    const std::string &functionId, const std::shared_ptr<AvailableSchedulerInfos> &schedulerInfos)
{
    const auto &list = shuffledSchedulerList_;
    if (list.empty()) {
        YRLOG_DEBUG("shuffledSchedulerList_ is empty");
        return "";
    }

    size_t size = list.size();

    for (size_t i = 0; i < size; i++) {
        size_t idx = currentIndex_.fetch_add(1) % list.size();
        const std::string &schedulerId = list[idx];

        if (!schedulerInfos || schedulerInfos->schedulerInstanceList.empty()) {
            YRLOG_DEBUG("No schedulerInfos provided, return schedulerId from shuffled list: {}", schedulerId);
            return schedulerId;
        }

        bool foundInExternal = false;
        for (const auto &ins : schedulerInfos->schedulerInstanceList) {
            if (ins->InstanceID == schedulerId) {
                foundInExternal = true;
                if (!ins->isAvailable) {
                    YRLOG_DEBUG("schedulerId={} in external list but is not available, skip", schedulerId);
                    break;
                }
                YRLOG_DEBUG("Found available schedulerId={} from shuffled list -> return", schedulerId);
                return schedulerId;
            }
        }

        if (!foundInExternal) {
            YRLOG_DEBUG("schedulerId={} not in external list -> assume available, return", schedulerId);
            return schedulerId;
        }
    }

    YRLOG_DEBUG("No available scheduler found in shuffled list for functionId: {}", functionId);
    return "";
}
}  // namespace Libruntime
}  // namespace YR
