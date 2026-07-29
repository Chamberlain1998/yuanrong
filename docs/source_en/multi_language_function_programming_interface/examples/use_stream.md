# Using Data Streams in Functions

This section introduces how to use streams in functions through simple Python examples.

## Prerequisites

Refer to [Deploy on Hosts](../../deploy/deploy_processes/index.md) to complete openYuanrong deployment.

## Using Streams in Stateless Functions

We create a consumer `local_consumer` in the main program, which implicitly creates the stream `exp-stream`. The producer is a stateless function whose instances run remotely. The producer and consumer negotiate to use the string `::END::` as the stream end marker. After processing the stream, you need to actively call the interface `yr.delete_stream` to delete the stream and release resources.

```python
import sys
import subprocess
import yr
import time

@yr.invoke
def send_stream(stream_name, end_marker):
    try:
        # Create producer, configure automatic ACK
        # Stream sending will be buffered. For tasks with high real-time requirements, you can reduce the delay_flush_time value, default is 5ms
        producer_config = yr.ProducerConfig(delay_flush_time=5, page_size=1024 * 1024, max_stream_size=1024 * 1024 * 1024, auto_clean_up=True)
        stream_producer = yr.create_stream_producer(stream_name, producer_config)

        corpus = subprocess.check_output([sys.executable, "-c", "import this"])
        lines = corpus.decode().split("\n")

        i = 0
        for line in lines:
            if len(line) > 0:
                # Send stream
                stream_producer.send(yr.Element(line.encode(), i))
                print("send:" + line)
                i += 1

        # Send the business-agreed end marker, close the producer
        stream_producer.send(yr.Element(end_marker.encode(), i))
        stream_producer.close()
        print("stream producer is closed")
    except RuntimeError as exp:
        print("unexpected exp: ", exp)


if __name__ == '__main__':
    yr.init()

    stream_name = "exp-stream"
    end_marker = "::END::"
    # Create consumer, implicitly create stream
    config = yr.SubscriptionConfig("local_consumer")
    consumer = yr.create_stream_consumer(stream_name, config)
    send_stream.invoke(stream_name, end_marker)

    end = False
    while not end:
        # Return after 1000ms or when receiving 10 elements
        elements = consumer.receive(1000, 10)
        for e in elements:
            data_str = e.data.decode()
            print("receive:" + data_str)
            # After receiving the agreed end marker, close the consumer
            if data_str == end_marker:
                consumer.close()
                print("stream consumer is closed")
                end = True

    yr.finalize()
```

## Using Streams in Function Services

We create a function service as the stream producer, and an HTTP client as the consumer to receive the stream. When subscribing to a stream service via REST API, you need to trigger the subscription first, then execute stream production.

### Register Producer Function

```python
# producer.py
import sys
import subprocess
import yr

def handler(event, context):
    print("received request,event content:", event)

    try:
        # Read stream name and stream end marker from request parameters
        stream_name = event.get("stream_name")
        stream_end_marker = event.get("stream_end_marker")
        # Create producer, configure automatic ACK
        # Stream sending will be buffered. For tasks with high real-time requirements, you can reduce the delay_flush_time value, default is 5ms
        producer_config = yr.ProducerConfig(delay_flush_time=5, page_size=1024 * 1024, max_stream_size=1024 * 1024 * 1024, auto_clean_up=True)
        stream_producer = yr.create_stream_producer(stream_name, producer_config)

        corpus = subprocess.check_output([sys.executable, "-c", "import this"])
        lines = corpus.decode().split("\n")

        i = 0
        for line in lines:
            if len(line) > 0:
                stream_producer.send(yr.Element(line.encode(), i))
                print("send:" + line)
                i += 1

        # Send the business-agreed end marker, close the producer
        stream_producer.send(yr.Element(stream_end_marker.encode(), i))
        stream_producer.close()
        print("stream producer is closed")
    except RuntimeError as e:
        print(e)
        return "failed, yuanrong runtime error"
    except Exception as e:
        print(e)
        return "failed,request body format:{'stream_name':'this-stream','stream_end_marker':'::END::'}"

    return "ok"
```

Use curl tool to register the function. For parameter meanings, see [API Description](../api/function_service/register_function.md):

```bash
# Replace /opt/mycode/service with the directory where your producer.py code is located
META_SERVICE_ENDPOINT=<meta service component endpoint, default: http://{master node IP}:31182>
curl -H "Content-type: application/json" -X POST -i ${META_SERVICE_ENDPOINT}/serverless/v1/functions -d '{"name":"0@myService@stream-producer","runtime":"python3.9","handler":"producer.handler","kind":"faas","cpu":600,"memory":512,"timeout":60,"storageType":"local","codePath":"/opt/mycode/service"}'
```

The result returns in the following format. Record the value of the `functionVersionUrn` field for invocation, which corresponds to `sn:cn:yrk:default:function:0@myService@stream-producer:latest` here.

```bash
{"code":0,"message":"SUCCESS","function":{"id":"sn:cn:yrk:default:function:0@myService@stream-producer:latest","createTime":"2025-06-28 08:57:06.856 UTC","updateTime":"","functionUrn":"sn:cn:yrk:default:function:0@myService@stream-producer","name":"0@myService@stream-producer","tenantId":"default","businessId":"yrk","productId":"","reversedConcurrency":0,"description":"","tag":null,"functionVersionUrn":"sn:cn:yrk:default:function:0@myService@stream-producer:latest","revisionId":"20250628085706856","codeSize":0,"codeSha256":"","bucketId":"","objectId":"","handler":"producer.handler","layers":null,"cpu":600,"memory":512,"runtime":"python3.9","timeout":60,"versionNumber":"latest","versionDesc":"latest","environment":{},"customResources":null,"statefulFlag":0,"lastModified":"","Published":"2025-06-28 08:57:06.856 UTC","minInstance":0,"maxInstance":100,"concurrentNum":100,"funcLayer":[],"status":"","instanceNum":0,"device":{},"created":""}}
```

### Start Consumer Process

Run the following program, call the [Subscribe Stream Service API](../api/function_service/stream_invocation.md) to subscribe to stream `this-stream`. Refer to [Get frontend component endpoint](api-frontend-endpoint) to replace `{frontend_endpoint}` in the code.

```python
# consumer.py
import requests

url = 'http://{frontend_endpoint}/serverless/v1/stream/subscribe'
headers = {
    'X-Stream-Name': 'this-stream',
    'X-Expect-Num': '10',
    'X-Timeout-Ms': '5000',
    'X-Tenant-Id': 'default'
}

# Send request and enable streaming response
try:
    response = requests.get(url, headers=headers, stream=True)

    if response.status_code == 200:
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                print(chunk.decode("utf-8"))
        print('successed')
    else:
        print(f'subscribe failed: http_code={response.status_code}, body={response.text}')
except requests.RequestException as e:
    print(f"request failed: {e}")
```

### Run Producer Function

Use curl tool to invoke the producer service, create stream `this-stream` and produce content. For API parameter meanings, see [API Description](../api/function_service/function_invocation.md):

```bash
FRONTEND_ENDPOINT=<frontend component endpoint, default: http://{master node IP}:8888>
FUNCTION_VERSION_URN=<functionVersionUrn recorded in previous step>
curl -H "Content-type: application/json" -X POST -i ${FRONTEND_ENDPOINT}/serverless/v1/functions/${FUNCTION_VERSION_URN}/invocations -d '{"stream_name":"this-stream","stream_end_marker":"::END::"}'
```

Result output:

```bash
HTTP/1.1 200 OK
Content-Type: application/json
X-Billing-Duration: this is billing duration TODO
X-Inner-Code: 0
X-Invoke-Summary:
X-Log-Result: dGhpcyBpcyB1c2VyIGxvZyBUT0RP
Date: Sat, 28 Jun 2025 08:59:28 GMT
Content-Length: 4

"ok"
```

At this time, the consumer process outputs the following:

```bash
The Zen of Python, by Tim PetersBeautiful is better than ugly.Explicit is better than implicit.Simple is better than complex.Complex is better than complicated.Flat is better than nested.Sparse is better than dense.Readability counts.Special cases aren't special enough to break the rules.Although practicality beats purity.

Errors should never pass silently.Unless explicitly silenced.In the face of ambiguity, refuse the temptation to guess.There should be one-- and preferably only one --obvious way to do it.Although that way may not be obvious at first unless you're Dutch.Now is better than never.Although never is often better than *right* now.If the implementation is hard to explain, it's a bad idea.If the implementation is easy to explain, it may be a good idea.Namespaces are one honking great idea -- let's do more of those!

::END::

successed
```
