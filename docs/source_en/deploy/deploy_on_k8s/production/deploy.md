# Deploy openYuanrong

This section introduces the process of deploying openYuanrong.

(k8s-deploy-download-release-package)=

## Add openYuanrong Helm Repository

openYuanrong K8s installation package depends on helm, you need to add the helm repository address provided by openYuanrong.

```bash
helm repo add yr http://openyuanrong.obs.cn-southwest-2.myhuaweicloud.com/charts/
helm repo update
```

## Configure openYuanrong Image Registry

openYuanrong version images are stored in a private image registry. On each K8s node terminal, execute `vim /etc/docker/daemon.json` command, add the following content to add openYuanrong whitelist image registry address for docker.

```json
{
    "insecure-registries": [
        "swr.cn-southwest-2.myhuaweicloud.com"
    ]
}
```

Execute the following commands to make the configuration take effect:

```shell
systemctl daemon-reload
systemctl restart docker
```

## Deployment

Deploy openYuanrong using helm commands on any K8s node.

DataSystem is deployed by default. For a no-DataSystem installation, read [Optional DataSystem Deployment](optional-datasystem.md) first and set both `global.dataSystem.enabled=false` and `global.dataSystem.bypass=true`.

Find openyuanrong version.

```bash  
helm search repo yr/openyuanrong
```

Result output:

```bash
NAME               CHART VERSION    APP VERSION    DESCRIPTION
yr/openyuanrong    0.2.6            1.16.0         A Helm chart for Kubernetes
```

Specify openyuanrong version to install.

```bash
helm pull --untar yr/openyuanrong --version 0.2.6
cd openyuanrong
```

Install the latest version. 

```bash
helm repo update
helm pull --untar yr/openyuanrong 
cd openyuanrong 
```

:::{caution}

If etcd in the cluster environment is not a fresh installation, please clean up residual data first to avoid deployment failure.

:::

- Deploy only openYuanrong without creating Pod resource pool, need to configure ETCD address and port information, MinIO AccessKey and SecretKey, suitable for scenarios where you create resource pool through [Resource Pool Management API](../api/index.md) by yourself. Among them, when ETCD is a cluster, addresses of multiple nodes are separated by commas, for example `192.168.10.1X:2379,192.168.10.2X:2379,192.168.10.3X:2379`.

  ```shell
  helm install openyuanrong --set global.etcd.etcdAddress=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.systemUpgradeConfig.systemUpgradeWatchAddress=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.etcdManagement.detcd=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.etcdManagement.metcd=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.obsManagement.s3AccessKey=${Your Minio AccessKey} \
    --set global.obsManagement.s3SecretKey=${Your Minio SecretKey}  .
  ```

- Deploy openYuanrong and create a Pod resource pool with default specification of (3 CPU cores, 6GB memory), recommended for running job applications.

  ```shell
  helm install openyuanrong --set global.etcd.etcdAddress=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.systemUpgradeConfig.systemUpgradeWatchAddress=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.etcdManagement.detcd=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.etcdManagement.metcd=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.obsManagement.s3AccessKey=${Your Minio AccessKey} \
    --set global.obsManagement.s3SecretKey=${Your Minio SecretKey} \
    --set global.pool.poolSize=1 \
    --set global.pool.requestCpu=3000m \
    --set global.pool.requestMemory=6144Mi \
    --set global.pool.limitCpu=3000m \
    --set global.pool.limitMemory=6144Mi .
  ```

- Deploy openYuanrong and create a Pod resource pool with default specification of 600 milli-core CPU, 512MB memory, recommended for running service applications.

  ```shell
  helm install openyuanrong --set global.etcd.etcdAddress=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.systemUpgradeConfig.systemUpgradeWatchAddress=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.etcdManagement.detcd=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.etcdManagement.metcd=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.obsManagement.s3AccessKey=${Your Minio AccessKey} \
    --set global.obsManagement.s3SecretKey=${Your Minio SecretKey} \
    --set global.pool.poolSize=1 \
    --set global.pool.requestCpu=600m \
    --set global.pool.requestMemory=512Mi \
    --set global.pool.limitCpu=600m \
    --set global.pool.limitMemory=512Mi .
  ```

- Others: Example of modifying image version of a single component.

  ```shell
  helm install openyuanrong --set global.etcd.etcdAddress=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.systemUpgradeConfig.systemUpgradeWatchAddress=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.etcdManagement.detcd=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.etcdManagement.metcd=${Your ETCD ADDRESS}:${Your ETCD Port} \
    --set global.obsManagement.s3AccessKey=${Your Minio AccessKey} \
    --set global.obsManagement.s3SecretKey=${Your Minio SecretKey} \
    --set global.images.functionAgent=function-agent:${version} .
  ```

Check deployment results: Deployment is successful when all the following Pods are in Running status.

```shell
kubectl get pods -owide -w

NAME                                                              READY   STATUS    RESTARTS   AGE     IP              NODE           NOMINATED NODE   READINESS GATE
ds-core-etcd-0                                                    1/1     Running   0          19d     10.x.x.122     dggphis18024   <none>           <none>
ds-worker-llcq8                                                   1/1     Running   0          2m56s   10.x.x.56      dggphis18023   <none>           <none>
ds-worker-wrmtn                                                   1/1     Running   0          2m56s   10.x.x.146     dggphis18024   <none>           <none>
ds-worker-wrztn                                                   1/1     Running   0          2m56s   10.x.x.146     dggphis18024   <none>           <none>
frontend-8d47bf8d5f-k5s2x                                         1/1     Running   0          104s    10.x.x.217     dggphis18023   <none>           <none>
function-mananger-c886466cd-xkspb                                 1/1     Running   0          91s     10.x.x.218     dggphis18023   <none>           <none>
function-master-777f6bb8c5-csn5m                                  1/1     Running   0          2m56s   10.x.x.215     dggphis18023   <none>           <none>
function-proxy-5z27x                                              1/1     Running   0          2m56s   10.x.x.146     dggphis18024   <none>           <none>
function-proxy-l7d59                                              1/1     Running   0          2m56s   10.x.x.56      dggphis18023   <none>           <none>
function-proxy-z7d59                                              1/1     Running   0          2m56s   10.x.x.56      dggphis18023   <none>           <none>
function-scheduler-b862b1c6d8eb-346f745bbd-7w6gj                  1/1     Running   0          81s     10.x.x.219     dggphis18023   <none>           <none>
function-scheduler-e19efb73cb3c-446f745bbd-7w6gj                  1/1     Running   0          81s     10.x.x.220     dggphis18023   <none>           <none>
iam-adaptor-bb5cf566-dhsvm                                        1/1     Running   0          2m56s   10.x.x.214     dggphis18023   <none>           <none>
meta-service-587d5fc6db-p5wh7                                     1/1     Running   0          2m56s   10.x.x.216     dggphis18023   <none>           <none>
minio-884b9bdb6-bc2bj                                             1/1     Running   0          2m56s   10.x.x.216     dggphis18023   <none>           <none>
```

Refer to [Run Function Service in K8s Cluster](example-project-function-k8s-service) to further verify deployment results.

openYuanrong logs are enabled by default and mounted to K8s nodes. Among them, data worker component log path is `/home/sn/datasystem/logs/`, other component log paths are `/var/paas/sys/log/cff/default/componentlogs`, function instance runtime log path is `/var/paas/sys/log/cff/default/servicelogs`, function instance user log path is `/var/paas/sys/log/cff/default/processrouters/stdlogs`. If deployment fails, you can analyze the cause through logs.

## Customize Pod Resource Pool

The default Pod resource pool created during deployment is usually used for development or testing. In actual production scenarios, to better match business workloads, openYuanrong supports customizing Pod resource pools. You can create it by modifying the `values.yaml` file during deployment, or dynamically create it through [Resource Pool Management API](../api/index.md), both methods have the same effect.

When multiple businesses or environments such as development, testing, etc. share a set of K8s, you can create multiple custom Pod resource pools and label them according to business or environment type, use openYuanrong's [affinity scheduling strategy](../../../multi_language_function_programming_interface/development_guide/scheduling/affinity.md) to assign function instances to specific Pods to achieve isolation. In addition, [Create Pod Resource Pool API](../api/create_pod_pool.md) provides `node_selector` and `affinities` fields, natively supports [k8s pod affinity scheduling](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/){target="_blank"}, to assign created Pods to specific nodes. Combining the two can achieve more flexible resource matching.

## Uninstall

Execute the following command to uninstall:

```shell
helm uninstall openyuanrong
```
