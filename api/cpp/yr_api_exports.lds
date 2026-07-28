{
    global:
        *;
    local:
        /* libfunctionsdk.so consumes the non-curl ABI from libyr-api.so. */
        Curl_*;
        curl_*;
};
