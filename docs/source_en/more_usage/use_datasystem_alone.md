# Use Data System Alone

openYuanrong data system provides Python/C++ language interfaces, encapsulating heterogeneous object/KV/object multiple semantics to support business implementation of fast data read and write.

- heterogeneous object: Based on NPU card's HBM memory abstraction heterogeneous object interface, implements high-speed direct data transmission between Ascend NPU cards. Also provides H2D/D2H high-speed migration interface to implement fast data transfer between DRAM/HBM.
- KV: Implements copy-free KV data read and write based on shared memory, achieves high-performance data caching, supports data reliability semantics by docking with external components.
- object: Abstracts data objects based on host-side shared memory, implements reference counting-based lifecycle management, encapsulates shared memory as buffer, provides direct pointer read and write.

For more information and examples, please refer to openYuanrong data system [code repository](https://atomgit.com/openeuler/yuanrong-datasystem){target="_blank"} and [documentation](https://pages.openeuler.openatom.cn/openyuanrong-datasystem/docs/zh-cn/latest/index.html){target="_blank"}.

## Getting Started

First, install openYuanrong data system (including Python, C++ SDK and command-line tools):

```bash
pip install https://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/release/0.7.0/linux/x86_64/openyuanrong_datasystem-0.7.0-cp39-cp39-manylinux_2_34_x86_64.whl
```

Taking H2D (Host to Device)/D2H (Device to Host) data transfer as an example, heterogeneous object interface provides MGetH2D and MSetD2H interfaces to implement fast swap between HBM and DRAM.

::::{tab-set}
:::{tab-item} Python

```python
import acl
import random
from datasystem.ds_client import DsClient

def random_str(slen=10):
    seed = "1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!@#%^*()_+=-"
    sa = []
    for _ in range(slen):
        sa.append(random.choice(seed))
    return ''.join(sa)

def hetero_mset_d2h_mget_h2d():
    client = DsClient("127.0.0.1", 31501)
    client.init()

    acl.init()
    device_idx = 0
    acl.rt.set_device(device_idx)

    key_list = [ 'key1', 'key2', 'key3' ]
    data_size = 1024 * 1024
    test_value = random_str(data_size)

    in_data_blob_list = []
    for _ in key_list:
        tmp_batch_list = []
        for _ in range(4):
            dev_ptr, _ = acl.rt.malloc(data_size, 0)
            acl.rt.memcpy(dev_ptr, data_size, acl.util.bytes_to_ptr(test_value.encode()), data_size, 1)
            blob = Blob(dev_ptr, data_size)
            tmp_batch_list.append(blob)
        blob_list = DeviceBlobList(device_idx, tmp_batch_list)
        in_data_blob_list.append(blob_list)

    out_data_blob_list = []
    for _ in key_list:
        tmp_batch_list = []
        for _ in range(4):
            dev_ptr, _ = acl.rt.malloc(data_size, 0)
            blob = Blob(dev_ptr, data_size)
            tmp_batch_list.append(blob)
        blob_list = DeviceBlobList(device_idx, tmp_batch_list)
        out_data_blob_list.append(blob_list)

    client.hetero().mset_d2h(key_list, in_data_blob_list)

    client.hetero().mget_h2d(key_list, out_data_blob_list, 60000)
```

:::

:::{tab-item} C++

```cpp

#include "datasystem/hetero_client.h"
#include <acl/acl.h>

ConnectOptions connectOptions = { .host = "127.0.0.1", .port = 31501 };
auto client = std::make_shared<DsClient>(connectOptions);
ASSERT_TRUE(client->Init().IsOk());

// Initialize the ACL interface.
int deviceId = 1;
aclInit(nullptr);
aclrtSetDevice(deviceId); // Bind the NPU card.

std::vector<std::string> keys = { "test-key1" };
std::vector<uint64_t> blobSize = { 10, 20 };
int blobNum = blobSize.size();
std::vector<DeviceBlobList> swapOutBlobList;
swapOutBlobList.resize(keys.size());
// Allocate the HBM memory and fill it in the swapOutBlobList.
for (size_t i = 0; i < swapOutBlobList.size(); i++) {
    swapOutBlobList[i].deviceIdx = deviceId;
    for (int j = 0; j < blobNum; j++) {
        void *devPtr = nullptr;
        int code = aclrtMalloc(&devPtr, blobSize[j], ACL_MEM_MALLOC_HUGE_FIRST);
        // Copying Data to the Device Memory.
        // aclrtMemcpy(devPtr, blobSize[j], value.data(), size, aclrtMemcpyKind::ACL_MEMCPY_HOST_TO_DEVICE)
        ASSERT_EQ(code, 0);
        Blob blob = { .pointer = devPtr, .size = blobSize[j] };
        swapOutBlobList[i].blobs.emplace_back(std::move(blob));
    }
}

Status status = client->Hetero()->MSetD2H(keys, swapOutBlobList);
ASSERT_TRUE(status.IsOk());

std::vector<DeviceBlobList> swapInBlobList;
swapInBlobList.resize(keys.size());
// Allocate the HBM memory and fill it in the swapInBlobList.
for (size_t i = 0; i < swapInBlobList.size(); i++) {
    swapInBlobList[i].deviceIdx = deviceId;
    for (int j = 0; j < blobNum; j++) {
        void *devPtr = nullptr;
        int code = aclrtMalloc(&devPtr, blobSize[j], ACL_MEM_MALLOC_HUGE_FIRST);
        ASSERT_EQ(code, 0);
        Blob blob = { .pointer = devPtr, .size = blobSize[j] };
        swapInBlobList[i].blobs.emplace_back(std::move(blob));
    }
}
std::vector<std::string> failedList;
status = client->Hetero()->MGetH2D(keys, swapInBlobList, failedList, 1);
ASSERT_TRUE(status.IsOk());
```

:::
::::
