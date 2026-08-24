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

#include <fcntl.h>
#include <unistd.h>

#include <gtest/gtest.h>

#include "src/libruntime/utils/fd_utils.h"

namespace YR {
namespace test {

TEST(FdUtilsTest, MarksDescriptorCloseOnExec)
{
    int pipeFds[2];
    ASSERT_EQ(pipe(pipeFds), 0);
    ASSERT_EQ(fcntl(pipeFds[0], F_SETFD, 0), 0);

    ASSERT_TRUE(Libruntime::SetCloseOnExec(pipeFds[0]));
    const int flags = fcntl(pipeFds[0], F_GETFD);
    ASSERT_NE(flags, -1);
    EXPECT_NE(flags & FD_CLOEXEC, 0);

    close(pipeFds[0]);
    close(pipeFds[1]);
}

}  // namespace test
}  // namespace YR
