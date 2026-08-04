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

#include <thread>
#include <vector>

#include <gtest/gtest.h>

#include "src/libruntime/invokeadaptor/load_balancer.h"
#include "src/libruntime/invokeadaptor/scheduler_manager.h"

namespace YR {
namespace test {
using namespace YR::utility;
class SchedulerManagerTest : public ::testing::Test {
public:
    std::unique_ptr<Libruntime::SchedulerManager> schedulerMgr;
    std::shared_ptr<Libruntime::AvailableSchedulerInfos> schedulerInfos;

    void SetUp() override
    {
        Mkdir("/tmp/log");
        LogParam g_logParam = {
            .logLevel = "DEBUG",
            .logDir = "/tmp/log",
            .nodeName = "test-runtime",
            .modelName = "test",
            .maxSize = 100,
            .maxFiles = 1,
            .logFileWithTime = false,
            .logBufSecs = 30,
            .maxAsyncQueueSize = 1048510,
            .asyncThreadCount = 1,
            .alsoLog2Stderr = true,
        };
        InitLog(g_logParam);
        schedulerMgr = std::make_unique<Libruntime::SchedulerManager>(20);
        schedulerInfos = std::make_shared<Libruntime::AvailableSchedulerInfos>();
        schedulerInfos->schedulerInstanceList.clear();
    }

    void TearDown() override
    {
        schedulerMgr.reset();
        schedulerInfos.reset();
    }
};

TEST_F(SchedulerManagerTest, AddAndRemove)
{
    schedulerMgr->Add("sched1", "sched1ID");
    schedulerMgr->Add("sched2", "sched2ID");

    EXPECT_TRUE(schedulerMgr->ContainsSchedulerId("sched1ID"));
    EXPECT_TRUE(schedulerMgr->ContainsSchedulerId("sched2ID"));
    EXPECT_FALSE(schedulerMgr->ContainsSchedulerId(""));
    EXPECT_FALSE(schedulerMgr->ContainsSchedulerId("missing"));

    EXPECT_EQ(schedulerMgr->Next("func1", schedulerInfos), "sched1ID");
    EXPECT_EQ(schedulerMgr->Next("func2", schedulerInfos), "sched2ID");

    schedulerMgr->Remove("sched1");
    EXPECT_FALSE(schedulerMgr->ContainsSchedulerId("sched1ID"));
    EXPECT_EQ(schedulerMgr->Next("func1", schedulerInfos), "sched2ID");
}

TEST_F(SchedulerManagerTest, NotEmptySchedulerInfos)
{
    std::vector<Libruntime::SchedulerInstance> vec = {
        Libruntime::SchedulerInstance{.InstanceName = "sched1", .InstanceID = "sched1ID", .isAvailable = false},
        Libruntime::SchedulerInstance{.InstanceName = "sched2", .InstanceID = "sched2ID", .isAvailable = false},
        Libruntime::SchedulerInstance{.InstanceName = "sched3", .InstanceID = "sched3ID", .isAvailable = false},
    };
    std::vector<std::shared_ptr<Libruntime::SchedulerInstance>> vec1 = {
        std::make_shared<Libruntime::SchedulerInstance>(
            Libruntime::SchedulerInstance{.InstanceName = "sched1", .InstanceID = "sched1ID", .isAvailable = false}),
        std::make_shared<Libruntime::SchedulerInstance>(
            Libruntime::SchedulerInstance{.InstanceName = "sched2", .InstanceID = "sched2ID", .isAvailable = false}),
        std::make_shared<Libruntime::SchedulerInstance>(
            Libruntime::SchedulerInstance{.InstanceName = "sched3", .InstanceID = "sched3ID", .isAvailable = false}),
    };
    schedulerInfos->schedulerInstanceList = vec1;
    schedulerMgr->Add("sched1", "sched1ID");
    schedulerMgr->Add("sched2", "sched2ID");
    schedulerMgr->Add("sched3", "sched3ID");
    schedulerMgr->Add("sched4", "sched4ID");
    schedulerMgr->Add("sched5", "sched5ID");

    EXPECT_EQ(schedulerMgr->Next("func1", schedulerInfos), "sched4ID");
    EXPECT_EQ(schedulerMgr->Next("func2", schedulerInfos), "sched5ID");

    schedulerMgr->Remove("sched5");

    EXPECT_EQ(schedulerMgr->Next("func3", schedulerInfos), "sched4ID");
    EXPECT_EQ(schedulerMgr->Next("func4", schedulerInfos), "sched4ID");

    schedulerMgr->ResetAll(vec);
    schedulerMgr->SetRoute("func5", "sched5ID");
    schedulerMgr->SetRoute("func6", "sched1ID");

    EXPECT_EQ(schedulerMgr->Next("func5", schedulerInfos), "sched5ID");
    EXPECT_EQ(schedulerMgr->Next("func6", schedulerInfos), Libruntime::ALL_SCHEDULER_UNAVAILABLE);

    schedulerMgr->Add("sched4", "sched4ID");

    EXPECT_EQ(schedulerMgr->Next("func6", schedulerInfos), "sched4ID");
}

TEST_F(SchedulerManagerTest, ResetAll)
{
    schedulerMgr->Add("sched1", "sched1ID");
    schedulerMgr->Add("sched2", "sched2ID");

    std::vector<Libruntime::SchedulerInstance> vec = {
        Libruntime::SchedulerInstance{.InstanceName = "sched1", .InstanceID = "sched1ID", .isAvailable = true},
        Libruntime::SchedulerInstance{.InstanceName = "sched2", .InstanceID = "sched2ID", .isAvailable = true},
        Libruntime::SchedulerInstance{.InstanceName = "sched3", .InstanceID = "sched3ID", .isAvailable = true},
    };

    schedulerMgr->ResetAll(vec);

    EXPECT_TRUE(schedulerMgr->Next("func1", schedulerInfos) != Libruntime::ALL_SCHEDULER_UNAVAILABLE);
    EXPECT_TRUE(schedulerMgr->Next("func2", schedulerInfos) != Libruntime::ALL_SCHEDULER_UNAVAILABLE);
}

TEST_F(SchedulerManagerTest, SetAndRemoveRoute)
{
    schedulerMgr->SetRoute("func1", "sched1ID");
    EXPECT_EQ(schedulerMgr->Next("func1", schedulerInfos), "sched1ID");

    schedulerMgr->RemoveRoute("func1");
    EXPECT_EQ(schedulerMgr->Next("func1", schedulerInfos), Libruntime::ALL_SCHEDULER_UNAVAILABLE);
}

TEST_F(SchedulerManagerTest, NextRetryRoundRobin)
{
    schedulerMgr->Add("sched1", "sched1ID");
    schedulerMgr->Add("sched2", "sched2ID");
    schedulerMgr->Add("sched3", "sched3ID");

    EXPECT_EQ(schedulerMgr->Next("sched1", schedulerInfos), "sched1ID");
    EXPECT_EQ(schedulerMgr->Next("sched2", schedulerInfos), "sched2ID");
    EXPECT_EQ(schedulerMgr->Next("sched3", schedulerInfos), "sched3ID");
    EXPECT_EQ(schedulerMgr->Next("sched1", schedulerInfos), "sched1ID");
}

TEST_F(SchedulerManagerTest, LRUCacheHit)
{
    schedulerMgr = std::make_unique<Libruntime::SchedulerManager>(2);
    schedulerMgr->SetRoute("func1", "sched1ID");
    schedulerMgr->Add("sched2", "sched2ID");
    schedulerMgr->Add("sched3", "sched3ID");

    EXPECT_EQ(schedulerMgr->Next("func1", schedulerInfos), "sched1ID");
    EXPECT_EQ(schedulerMgr->Next("func1", schedulerInfos), "sched1ID");

    schedulerMgr->SetRoute("func2", "sched2ID");
    schedulerMgr->SetRoute("func3", "sched3ID");

    EXPECT_EQ(schedulerMgr->Next("func1", schedulerInfos), "sched2ID");
}

TEST_F(SchedulerManagerTest, AddSameSchedulerId)
{
    schedulerMgr->Add("sched1", "sched1ID");
    schedulerMgr->Add("sched2", "sched2ID");
    schedulerMgr->Add("sched3", "sched2ID");
    schedulerMgr->Add("sched4", "sched2ID");

    EXPECT_EQ(schedulerMgr->Next("func1", schedulerInfos), "sched1ID");
    EXPECT_EQ(schedulerMgr->Next("func2", schedulerInfos), "sched2ID");
    EXPECT_EQ(schedulerMgr->Next("func3", schedulerInfos), "sched1ID");
}

TEST_F(SchedulerManagerTest, RemoveAll)
{
    schedulerMgr->SetRoute("func1", "sched1ID");
    schedulerMgr->Add("sched2", "sched2ID");
    schedulerMgr->Add("sched3", "sched3ID");

    schedulerMgr->RemoveAll();
    EXPECT_EQ(schedulerMgr->Next("func1", schedulerInfos), Libruntime::ALL_SCHEDULER_UNAVAILABLE);
    EXPECT_EQ(schedulerMgr->Next("func2", schedulerInfos), Libruntime::ALL_SCHEDULER_UNAVAILABLE);
}

TEST_F(SchedulerManagerTest, InvalidLRUCacheSize)
{
    schedulerMgr = std::make_unique<Libruntime::SchedulerManager>(0);

    schedulerMgr->SetRoute("func1", "sched1ID");
    schedulerMgr->SetRoute("func2", "sched2ID");
    schedulerMgr->Add("sched2", "sched2ID");
    schedulerMgr->Add("sched3", "sched3ID");

    EXPECT_EQ(schedulerMgr->Next("func1", schedulerInfos), "sched1ID");
    EXPECT_EQ(schedulerMgr->Next("func1", schedulerInfos), "sched1ID");
    EXPECT_EQ(schedulerMgr->Next("func2", schedulerInfos), "sched2ID");
}
}  // namespace test
}  // namespace YR
