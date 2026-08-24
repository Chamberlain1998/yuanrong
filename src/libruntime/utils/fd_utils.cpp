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

#include "src/libruntime/utils/fd_utils.h"

#include <cerrno>
#include <fcntl.h>

namespace YR {
namespace Libruntime {

bool SetCloseOnExec(int fd) noexcept
{
    int flags;
    do {
        flags = fcntl(fd, F_GETFD);
    } while (flags == -1 && errno == EINTR);
    if (flags == -1 || (flags & FD_CLOEXEC) != 0) {
        return flags != -1;
    }

    int result;
    do {
        result = fcntl(fd, F_SETFD, flags | FD_CLOEXEC);
    } while (result == -1 && errno == EINTR);
    return result != -1;
}

}  // namespace Libruntime
}  // namespace YR
