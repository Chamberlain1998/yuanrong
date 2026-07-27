//! Native Rust reverse-tunnel server (replaces spawning the python tunnel_server).
//!
//! Port A (ws_port, 0.0.0.0): WebSocket endpoint the external TunnelClient connects to.
//! Port B (http_port, 127.0.0.1): HTTP surface the sandbox's own code hits; each
//! request is framed (JSON, base64 body) and forwarded over the Port-A WS to the
//! client, which relays it to the real upstream and frames the response back.
//!
//! The paired sandbox-sdk TunnelClient uses JSON text frames {type, id, ...};
//! bodies are base64 and headers are ordered [name, value] pairs. The server
//! answers the client's app-level PingFrame with a PongFrame (heartbeat),
//! forwards Port-B HTTP as http_req, and resolves http_resp by id. WebSocket
//! upgrades are rejected until both peers implement reverse WebSocket proxying.

use super::codec::yr_deserialize;
use crate::posix::common::Arg;
use base64::Engine;
use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use http_body_util::{BodyExt, Full, LengthLimitError, Limited};
use hyper::body::Incoming;
use hyper::header::{HeaderMap, HeaderName, HeaderValue, CONTENT_LENGTH, UPGRADE};
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper::{Method, Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use rmpv::Value;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::convert::Infallible;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{mpsc, oneshot};
use tokio::task::AbortHandle;
use tokio_tungstenite::tungstenite::protocol::Message;

const HTTP_TIMEOUT: Duration = Duration::from_secs(600);
/// In-flight HTTP requests are cached this long for resend on client reconnect.
const PENDING_REQUEST_TTL: Duration = Duration::from_secs(120);
const MAX_HTTP_HEADER_BYTES: usize = 64 * 1024;
const MAX_HTTP_HEADERS: usize = 200;
const MAX_HTTP_BODY_BYTES: usize = 64 * 1024 * 1024;

type HeaderList = Vec<(String, String)>;

fn b64() -> base64::engine::general_purpose::GeneralPurpose {
    base64::engine::general_purpose::STANDARD
}

/// Server-local unique frame id (only needs to be unique among in-flight frames).
fn make_id() -> String {
    static N: AtomicU64 = AtomicU64::new(1);
    format!("rrt-{}", N.fetch_add(1, Ordering::Relaxed))
}

// ───────────────────────── wire frames ─────────────────────────
// Matches tunnel_protocol.py. `body` / ws binary `data` are base64 strings.
#[derive(Serialize, Deserialize, Debug, Clone)]
#[serde(tag = "type")]
enum Frame {
    #[serde(rename = "http_req")]
    HttpReq {
        id: String,
        method: String,
        path: String,
        headers: HeaderList,
        #[serde(default)]
        body: String,
    },
    #[serde(rename = "http_resp")]
    HttpResp {
        id: String,
        status: u16,
        #[serde(default)]
        headers: HeaderList,
        #[serde(default)]
        body: String,
    },
    #[serde(rename = "error")]
    Error { id: String, message: String },
    #[serde(rename = "ping")]
    Ping { id: String, timestamp: f64 },
    #[serde(rename = "pong")]
    Pong { id: String, timestamp: f64 },
}

impl Frame {
    fn to_msg(&self) -> Message {
        Message::Text(serde_json::to_string(self).unwrap_or_default())
    }
}

// ───────────────────────── shared state ─────────────────────────
#[derive(Default)]
struct State {
    /// Outbound channel to the active TunnelClient WS (None when no client connected).
    sdk_tx: Mutex<Option<mpsc::UnboundedSender<Message>>>,
    /// HTTP request id -> oneshot waiting for the http_resp / error frame.
    pending_http: Mutex<HashMap<String, oneshot::Sender<Frame>>>,
    /// In-flight HTTP request frames, cached for resend when a client reconnects.
    pending_requests: Mutex<HashMap<String, (Frame, Instant)>>,
}

impl State {
    fn send_to_client(&self, frame: &Frame) -> Result<(), ()> {
        let guard = self.sdk_tx.lock().unwrap();
        match guard.as_ref() {
            Some(tx) => tx.send(frame.to_msg()).map_err(|_| ()),
            None => Err(()),
        }
    }
}

fn aborts() -> &'static Mutex<Vec<AbortHandle>> {
    static A: OnceLock<Mutex<Vec<AbortHandle>>> = OnceLock::new();
    A.get_or_init(|| Mutex::new(Vec::new()))
}

/// Start the native tunnel server. Positional args carry ws_port then http_port
/// (akernel `start_tunnel_server.invoke(ws, http)`). Returns Nil once Port B is
/// listening (parity with the python ready check), Err if it never binds.
pub fn start_tunnel_server(args: &[Arg], deploy_dir: &str) -> Result<Value, String> {
    let pos: Vec<i64> = args
        .iter()
        .skip(2)
        .step_by(2)
        .filter_map(|a| yr_deserialize(&a.value))
        .filter_map(|v| v.as_i64())
        .collect();
    let ws_port = pos.first().copied().unwrap_or(8765) as u16;
    let http_port = pos.get(1).copied().unwrap_or(8766) as u16;
    let _ = deploy_dir;
    rrt_info!("[rrt-runtime] tunnel start ws={ws_port} http={http_port}");

    let handle = tokio::runtime::Handle::try_current()
        .map_err(|_| "no tokio runtime to host tunnel server".to_string())?;
    let state = Arc::new(State::default());
    let jh = handle.spawn(run_servers(ws_port, http_port, state));
    aborts().lock().unwrap().push(jh.abort_handle());

    // Wait for Port B to accept connections (multi-thread runtime serves the
    // spawned task on another worker while we poll here).
    for _ in 0..50 {
        if std::net::TcpStream::connect(("127.0.0.1", http_port)).is_ok() {
            return Ok(Value::Nil);
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err(format!(
        "tunnel_server not ready on port {http_port} within 5s"
    ))
}

/// Standalone entry (tools/tests): run the tunnel server forever on the given
/// ports, without the RuntimeRPC dispatch wrapper.
pub async fn run_standalone(ws_port: u16, http_port: u16) {
    run_servers(ws_port, http_port, Arc::new(State::default())).await;
}

async fn run_servers(ws_port: u16, http_port: u16, state: Arc<State>) {
    let porta = match TcpListener::bind(("0.0.0.0", ws_port)).await {
        Ok(l) => l,
        Err(e) => {
            rrt_error!("[rrt-runtime] tunnel port_a_bind_failed ws_port={ws_port} error={e}");
            return;
        }
    };
    let portb = match TcpListener::bind(("127.0.0.1", http_port)).await {
        Ok(l) => l,
        Err(e) => {
            rrt_error!("[rrt-runtime] tunnel port_b_bind_failed http_port={http_port} error={e}");
            return;
        }
    };
    rrt_info!("[rrt-runtime] tunnel listening ws=0.0.0.0:{ws_port} http=127.0.0.1:{http_port}");
    serve(porta, portb, state).await;
}

/// Drive both accept loops over pre-bound listeners (split out for tests).
async fn serve(porta: TcpListener, portb: TcpListener, state: Arc<State>) {
    let s2 = state.clone();
    tokio::join!(accept_port_a(porta, state), accept_port_b(portb, s2));
}

// ───────────────────────── Port A: TunnelClient WS ─────────────────────────
async fn accept_port_a(listener: TcpListener, state: Arc<State>) {
    loop {
        match listener.accept().await {
            Ok((stream, _)) => {
                let st = state.clone();
                tokio::spawn(async move {
                    if let Err(e) = handle_client(stream, st).await {
                        rrt_warn!("[rrt-runtime] tunnel client_conn_ended error={e}");
                    }
                });
            }
            Err(e) => {
                rrt_error!("[rrt-runtime] tunnel port_a_accept_error error={e}");
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
        }
    }
}

async fn handle_client(stream: TcpStream, state: Arc<State>) -> Result<(), String> {
    let ws = tokio_tungstenite::accept_async(stream)
        .await
        .map_err(|e| format!("ws accept: {e}"))?;
    let (mut sink, mut rx_ws) = ws.split();
    let _active = super::activity::enter(); // Count the tunnel WS client connection as busy.
    let (tx, mut rx) = mpsc::unbounded_channel::<Message>();
    // This connection becomes the active client (a reconnect replaces the previous).
    *state.sdk_tx.lock().unwrap() = Some(tx);
    rrt_info!("[rrt-runtime] tunnel client connected");
    // Resend HTTP requests that were in flight when the previous client dropped.
    resend_pending_requests(&state);

    // Outbound pump: frames queued by Port B -> client WS.
    let out = tokio::spawn(async move {
        while let Some(m) = rx.recv().await {
            if sink.send(m).await.is_err() {
                break;
            }
        }
    });

    // Inbound: client frames -> dispatch.
    while let Some(msg) = rx_ws.next().await {
        match msg {
            Ok(Message::Text(t)) => match serde_json::from_str::<Frame>(&t) {
                Ok(frame) => dispatch_from_client(frame, &state),
                Err(e) => rrt_warn!("[rrt-runtime] tunnel drop_malformed_frame error={e}"),
            },
            Ok(Message::Close(_)) | Err(_) => break,
            _ => {}
        }
    }

    out.abort();
    rrt_info!("[rrt-runtime] tunnel client disconnected");
    Ok(())
}

/// Drop cached requests older than the TTL (and unblock their waiters).
fn cleanup_expired_requests(state: &Arc<State>) {
    let now = Instant::now();
    let expired: Vec<String> = {
        let mut pr = state.pending_requests.lock().unwrap();
        let ex: Vec<String> = pr
            .iter()
            .filter(|(_, (_, ts))| now.duration_since(*ts) > PENDING_REQUEST_TTL)
            .map(|(k, _)| k.clone())
            .collect();
        for k in &ex {
            pr.remove(k);
        }
        ex
    };
    // Dropping the oneshot sender unblocks the waiting HTTP handler (-> closes conn).
    let mut ph = state.pending_http.lock().unwrap();
    for k in &expired {
        ph.remove(k);
    }
}

/// On client (re)connect, resend any HTTP requests still in flight.
fn resend_pending_requests(state: &Arc<State>) {
    cleanup_expired_requests(state);
    let frames: Vec<Frame> = state
        .pending_requests
        .lock()
        .unwrap()
        .values()
        .map(|(f, _)| f.clone())
        .collect();
    if !frames.is_empty() {
        rrt_info!(
            "[rrt-runtime] tunnel resending_pending_requests count={}",
            frames.len()
        );
        for f in &frames {
            let _ = state.send_to_client(f);
        }
    }
}

fn dispatch_from_client(frame: Frame, state: &Arc<State>) {
    match &frame {
        Frame::Ping { id, timestamp } => {
            let _ = state.send_to_client(&Frame::Pong {
                id: id.clone(),
                timestamp: *timestamp,
            });
        }
        Frame::HttpResp { id, .. } => {
            if let Some(tx) = state.pending_http.lock().unwrap().remove(id) {
                let _ = tx.send(frame);
            }
        }
        Frame::Error { id, .. } => {
            if let Some(tx) = state.pending_http.lock().unwrap().remove(id) {
                let _ = tx.send(frame);
            }
        }
        _ => {}
    }
}

// ───────────────────────── Port B: sandbox HTTP ─────────────────────────
async fn accept_port_b(listener: TcpListener, state: Arc<State>) {
    loop {
        match listener.accept().await {
            Ok((stream, _)) => {
                let st = state.clone();
                tokio::spawn(async move {
                    if let Err(error) = handle_port_b(stream, st).await {
                        rrt_warn!("[rrt-runtime] tunnel port_b_connection_ended error={error}");
                    }
                });
            }
            Err(e) => {
                rrt_error!("[rrt-runtime] tunnel port_b_accept_error error={e}");
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
        }
    }
}

async fn handle_port_b(stream: TcpStream, state: Arc<State>) -> Result<(), String> {
    handle_port_b_http(stream, state).await
}

fn is_fixed_hop_by_hop(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "connection"
            | "keep-alive"
            | "proxy-authenticate"
            | "proxy-authorization"
            | "proxy-connection"
            | "te"
            | "trailer"
            | "transfer-encoding"
            | "upgrade"
    )
}

fn connection_tokens(headers: &HeaderList) -> HashSet<String> {
    headers
        .iter()
        .filter(|(name, _)| name.eq_ignore_ascii_case("connection"))
        .flat_map(|(_, value)| value.split(','))
        .map(|token| token.trim().to_ascii_lowercase())
        .filter(|token| !token.is_empty())
        .collect()
}

fn header_list(headers: &HeaderMap) -> HeaderList {
    headers
        .iter()
        .map(|(name, value)| {
            (
                name.as_str().to_string(),
                String::from_utf8_lossy(value.as_bytes()).into_owned(),
            )
        })
        .collect()
}

fn request_headers_for_frame(headers: &HeaderMap) -> HeaderList {
    let headers = header_list(headers);
    let dynamic = connection_tokens(&headers);
    headers
        .into_iter()
        .filter(|(name, _)| {
            let lower = name.to_ascii_lowercase();
            !is_fixed_hop_by_hop(&lower)
                && !dynamic.contains(&lower)
                && !matches!(lower.as_str(), "host" | "content-length" | "expect")
        })
        .collect()
}

fn response_headers_for_downstream(
    headers: HeaderList,
    method: &Method,
    status: StatusCode,
    body_len: usize,
) -> HeaderList {
    let dynamic = connection_tokens(&headers);
    let representation_length = headers
        .iter()
        .filter(|(name, _)| name.eq_ignore_ascii_case("content-length"))
        .filter_map(|(_, value)| value.parse::<usize>().ok())
        .next();
    let mut result: HeaderList = headers
        .into_iter()
        .filter(|(name, _)| {
            let lower = name.to_ascii_lowercase();
            !is_fixed_hop_by_hop(&lower) && !dynamic.contains(&lower) && lower != "content-length"
        })
        .collect();

    if method == Method::HEAD {
        if let Some(length) = representation_length {
            result.push(("content-length".into(), length.to_string()));
        }
    } else if !status.is_informational()
        && status != StatusCode::NO_CONTENT
        && status != StatusCode::NOT_MODIFIED
    {
        result.push(("content-length".into(), body_len.to_string()));
    }
    result
}

fn plain_response(status: StatusCode, message: &str) -> Response<Full<Bytes>> {
    Response::builder()
        .status(status)
        .header(CONTENT_LENGTH, message.len())
        .body(Full::new(Bytes::copy_from_slice(message.as_bytes())))
        .expect("static HTTP response is valid")
}

async fn proxy_http_request(
    request: Request<Incoming>,
    state: Arc<State>,
) -> Response<Full<Bytes>> {
    let (parts, body) = request.into_parts();
    let method = parts.method;
    let is_websocket_upgrade = parts
        .headers
        .get_all(UPGRADE)
        .iter()
        .any(|value| value.as_bytes().eq_ignore_ascii_case(b"websocket"));
    if is_websocket_upgrade {
        return plain_response(
            StatusCode::NOT_IMPLEMENTED,
            "Reverse WebSocket tunnel is not implemented",
        );
    }
    let path = parts
        .uri
        .path_and_query()
        .map(|value| value.as_str().to_string())
        .unwrap_or_else(|| "/".into());
    let headers = request_headers_for_frame(&parts.headers);
    let body = match Limited::new(body, MAX_HTTP_BODY_BYTES).collect().await {
        Ok(collected) => collected.to_bytes(),
        Err(error) => {
            let message = error.to_string();
            let status = if error.downcast_ref::<LengthLimitError>().is_some() {
                StatusCode::PAYLOAD_TOO_LARGE
            } else {
                StatusCode::BAD_REQUEST
            };
            return plain_response(status, &message);
        }
    };
    let id = make_id();
    let frame = Frame::HttpReq {
        id: id.clone(),
        method: method.to_string(),
        path,
        headers,
        body: b64().encode(&body),
    };
    let (tx, rx) = oneshot::channel::<Frame>();
    state.pending_http.lock().unwrap().insert(id.clone(), tx);
    // Cache for resend if the client drops and reconnects mid-request.
    state
        .pending_requests
        .lock()
        .unwrap()
        .insert(id.clone(), (frame.clone(), Instant::now()));
    // Best-effort send; if no client, we still wait (parity: tunnel may reconnect).
    let _ = state.send_to_client(&frame);

    let result = tokio::time::timeout(HTTP_TIMEOUT, rx).await;
    state.pending_http.lock().unwrap().remove(&id);
    state.pending_requests.lock().unwrap().remove(&id);

    let (status, headers, response_body) = match result {
        Ok(Ok(Frame::HttpResp {
            status,
            headers,
            body,
            ..
        })) => {
            let response_body = match b64().decode(body.as_bytes()) {
                Ok(body) => body,
                Err(_) => {
                    return plain_response(StatusCode::BAD_GATEWAY, "Invalid tunnel response body");
                }
            };
            (
                StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
                headers,
                response_body,
            )
        }
        Ok(Ok(Frame::Error { .. })) => {
            return plain_response(StatusCode::BAD_GATEWAY, "Tunnel upstream error");
        }
        Ok(Ok(_)) | Ok(Err(_)) => {
            return plain_response(StatusCode::BAD_GATEWAY, "Invalid tunnel response");
        }
        Err(_) => {
            return plain_response(StatusCode::GATEWAY_TIMEOUT, "Tunnel timeout");
        }
    };

    let downstream_headers =
        response_headers_for_downstream(headers, &method, status, response_body.len());
    let suppress_body = method == Method::HEAD
        || status.is_informational()
        || status == StatusCode::NO_CONTENT
        || status == StatusCode::NOT_MODIFIED;
    let mut response = Response::builder().status(status);
    for (name, value) in downstream_headers {
        if let (Ok(name), Ok(value)) = (
            HeaderName::from_bytes(name.as_bytes()),
            HeaderValue::from_str(&value),
        ) {
            response
                .headers_mut()
                .expect("response builder")
                .append(name, value);
        }
    }
    response
        .body(Full::new(if suppress_body {
            Bytes::new()
        } else {
            Bytes::from(response_body)
        }))
        .unwrap_or_else(|_| plain_response(StatusCode::BAD_GATEWAY, "Invalid response headers"))
}

async fn handle_port_b_http(stream: TcpStream, state: Arc<State>) -> Result<(), String> {
    let service = service_fn(move |request| {
        let state = state.clone();
        async move { Ok::<Response<Full<Bytes>>, Infallible>(proxy_http_request(request, state).await) }
    });
    http1::Builder::new()
        .max_headers(MAX_HTTP_HEADERS)
        .max_buf_size(MAX_HTTP_HEADER_BYTES)
        .serve_connection(TokioIo::new(stream), service)
        .await
        .map_err(|error| format!("port B HTTP connection: {error}"))
}

// ───────────────────────── E2E regression tests ─────────────────────────
// Drive the real server over localhost: a fake (Rust) TunnelClient on Port A
// and raw HTTP / a WS client on Port B, exercising the actual frame protocol
// the python TunnelClient also speaks.
#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio_tungstenite::connect_async;

    async fn spawn_test_server() -> (u16, u16) {
        let porta = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let portb = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let wp = porta.local_addr().unwrap().port();
        let hp = portb.local_addr().unwrap().port();
        tokio::spawn(serve(porta, portb, Arc::new(State::default())));
        (wp, hp)
    }

    async fn connect_client(
        ws_port: u16,
    ) -> tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<TcpStream>> {
        let (c, _) = connect_async(format!("ws://127.0.0.1:{ws_port}/"))
            .await
            .unwrap();
        // Let the server register this as the active client.
        tokio::time::sleep(Duration::from_millis(100)).await;
        c
    }

    async fn next_frame<S>(ws: &mut S) -> Frame
    where
        S: StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>> + Unpin,
    {
        loop {
            match ws.next().await {
                Some(Ok(Message::Text(t))) => return serde_json::from_str(&t).unwrap(),
                Some(Ok(_)) => continue,
                other => panic!("expected text frame, got {other:?}"),
            }
        }
    }

    async fn http_get(port: u16, path: &str) -> String {
        let mut s = TcpStream::connect(("127.0.0.1", port)).await.unwrap();
        let req = format!("GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n");
        s.write_all(req.as_bytes()).await.unwrap();
        let mut buf = Vec::new();
        let _ = s.read_to_end(&mut buf).await;
        String::from_utf8_lossy(&buf).to_string()
    }

    async fn raw_http(port: u16, request: &[u8]) -> String {
        let mut stream = TcpStream::connect(("127.0.0.1", port)).await.unwrap();
        stream.write_all(request).await.unwrap();
        let mut response = Vec::new();
        tokio::time::timeout(Duration::from_secs(2), stream.read_to_end(&mut response))
            .await
            .expect("HTTP response timed out")
            .unwrap();
        String::from_utf8_lossy(&response).into_owned()
    }

    #[test]
    fn frame_json_matches_python_protocol() {
        // Header pairs retain duplicate fields and their original order.
        let f = Frame::HttpReq {
            id: "x".into(),
            method: "GET".into(),
            path: "/p".into(),
            headers: Vec::new(),
            body: b64().encode(b"hi"),
        };
        let j: serde_json::Value =
            serde_json::from_str(&serde_json::to_string(&f).unwrap()).unwrap();
        assert_eq!(j["type"], "http_req");
        assert_eq!(j["id"], "x");
        assert_eq!(j["body"], "aGk="); // base64("hi")
        assert!(j["headers"].is_array(), "headers={}", j["headers"]);

        let raw = r#"{"type":"http_resp","id":"x","status":201,"headers":[["Set-Cookie","a=1"],["Set-Cookie","b=2"]],"body":"cG9uZw=="}"#;
        match serde_json::from_str::<Frame>(raw).unwrap() {
            Frame::HttpResp {
                status,
                headers,
                body,
                ..
            } => {
                assert_eq!(status, 201);
                assert_eq!(headers.len(), 2);
                assert_eq!(b64().decode(body.as_bytes()).unwrap(), b"pong");
            }
            o => panic!("{o:?}"),
        }
    }

    #[test]
    fn response_content_length_obeys_method_and_status_semantics() {
        let representation_headers = vec![
            ("Content-Length".into(), "123".into()),
            ("Set-Cookie".into(), "a=1".into()),
            ("Set-Cookie".into(), "b=2".into()),
        ];

        let head = response_headers_for_downstream(
            representation_headers.clone(),
            &Method::HEAD,
            StatusCode::OK,
            0,
        );
        assert_eq!(
            head.iter()
                .filter(|(name, _)| name.eq_ignore_ascii_case("content-length"))
                .map(|(_, value)| value.as_str())
                .collect::<Vec<_>>(),
            vec!["123"]
        );

        let no_content = response_headers_for_downstream(
            representation_headers.clone(),
            &Method::GET,
            StatusCode::NO_CONTENT,
            0,
        );
        assert!(
            no_content
                .iter()
                .all(|(name, _)| !name.eq_ignore_ascii_case("content-length")),
            "headers={no_content:?}"
        );

        let not_modified = response_headers_for_downstream(
            representation_headers,
            &Method::GET,
            StatusCode::NOT_MODIFIED,
            0,
        );
        assert!(
            not_modified
                .iter()
                .all(|(name, _)| !name.eq_ignore_ascii_case("content-length")),
            "headers={not_modified:?}"
        );
        assert_eq!(
            not_modified
                .iter()
                .filter(|(name, _)| name.eq_ignore_ascii_case("set-cookie"))
                .count(),
            2
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn ambiguous_request_framing_is_handled_safely() {
        let (_ws_port, http_port) = spawn_test_server().await;
        let response = raw_http(
            http_port,
            b"POST /ambiguous HTTP/1.1\r\nHost: local\r\nContent-Length: 1\r\nContent-Length: 2\r\nConnection: close\r\n\r\nx",
        )
        .await;
        assert!(
            response.starts_with("HTTP/1.1 400"),
            "response={response:?}"
        );

        let (ws_port, http_port) = spawn_test_server().await;
        let mut client = connect_client(ws_port).await;
        let request = tokio::spawn(async move {
            raw_http(
                http_port,
                b"POST /canonical HTTP/1.1\r\nHost: local\r\nContent-Length: 99\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n1\r\nx\r\n0\r\n\r\n",
            )
            .await
        });
        let id = match next_frame(&mut client).await {
            Frame::HttpReq {
                id, headers, body, ..
            } => {
                assert_eq!(b64().decode(body.as_bytes()).unwrap(), b"x");
                assert!(headers.iter().all(|(name, _)| {
                    !name.eq_ignore_ascii_case("content-length")
                        && !name.eq_ignore_ascii_case("transfer-encoding")
                }));
                id
            }
            other => panic!("expected canonical http_req, got {other:?}"),
        };
        client
            .send(
                Frame::HttpResp {
                    id,
                    status: 200,
                    headers: Vec::new(),
                    body: b64().encode(b"ok"),
                }
                .to_msg(),
            )
            .await
            .unwrap();
        assert!(request.await.unwrap().starts_with("HTTP/1.1 200"));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn excessive_header_count_is_rejected() {
        let (_ws_port, http_port) = spawn_test_server().await;
        let mut request = String::from("GET /headers HTTP/1.1\r\nHost: local\r\n");
        for index in 0..=MAX_HTTP_HEADERS {
            request.push_str(&format!("X-Test-{index}: value\r\n"));
        }
        request.push_str("Connection: close\r\n\r\n");
        let response = raw_http(http_port, request.as_bytes()).await;
        assert!(
            response.starts_with("HTTP/1.1 431"),
            "response={response:?}"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn chunked_request_is_decoded_before_framing() {
        let (ws_port, http_port) = spawn_test_server().await;
        let mut client = connect_client(ws_port).await;
        let request_task = tokio::spawn(async move {
            let mut stream = TcpStream::connect(("127.0.0.1", http_port)).await.unwrap();
            stream
                .write_all(
                    b"POST /chunked HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nTransfer-Encoding: chunked\r\nContent-Type: application/json\r\n\r\n5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n",
                )
                .await
                .unwrap();
            let mut response = Vec::new();
            let _ = stream.read_to_end(&mut response).await;
            response
        });

        let frame = tokio::time::timeout(Duration::from_secs(2), next_frame(&mut client))
            .await
            .expect("RRT did not frame the chunked request");
        let id = match frame {
            Frame::HttpReq {
                id, headers, body, ..
            } => {
                assert_eq!(b64().decode(body.as_bytes()).unwrap(), b"hello world");
                assert!(
                    headers
                        .iter()
                        .all(|(name, _)| { !name.eq_ignore_ascii_case("transfer-encoding") }),
                    "headers={headers:?}"
                );
                id
            }
            other => panic!("expected http_req, got {other:?}"),
        };
        client
            .send(
                Frame::HttpResp {
                    id,
                    status: 200,
                    headers: Vec::new(),
                    body: b64().encode(b"ok"),
                }
                .to_msg(),
            )
            .await
            .unwrap();
        let response = request_task.await.unwrap();
        assert!(
            String::from_utf8_lossy(&response).contains("200"),
            "response={response:?}"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn http_tunnel_roundtrip() {
        let (ws_port, http_port) = spawn_test_server().await;
        let mut client = connect_client(ws_port).await;
        let task = tokio::spawn(async move {
            let f = next_frame(&mut client).await;
            let id = match f {
                Frame::HttpReq {
                    id, path, method, ..
                } => {
                    assert_eq!(path, "/hello");
                    assert_eq!(method, "GET");
                    id
                }
                o => panic!("expected http_req, got {o:?}"),
            };
            client
                .send(
                    Frame::HttpResp {
                        id,
                        status: 200,
                        headers: Vec::new(),
                        body: b64().encode(b"pong"),
                    }
                    .to_msg(),
                )
                .await
                .unwrap();
        });
        let resp = http_get(http_port, "/hello").await;
        assert!(resp.contains("200"), "resp={resp}");
        assert!(resp.ends_with("pong"), "resp={resp}");
        task.await.unwrap();
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn ping_gets_pong() {
        let (ws_port, _hp) = spawn_test_server().await;
        let mut client = connect_client(ws_port).await;
        client
            .send(
                Frame::Ping {
                    id: "p1".into(),
                    timestamp: 1.5,
                }
                .to_msg(),
            )
            .await
            .unwrap();
        match next_frame(&mut client).await {
            Frame::Pong { id, timestamp } => {
                assert_eq!(id, "p1");
                assert_eq!(timestamp, 1.5);
            }
            o => panic!("expected pong, got {o:?}"),
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn http_error_returns_bad_gateway() {
        let (ws_port, http_port) = spawn_test_server().await;
        let mut client = connect_client(ws_port).await;
        let task = tokio::spawn(async move {
            let id = match next_frame(&mut client).await {
                Frame::HttpReq { id, .. } => id,
                o => panic!("{o:?}"),
            };
            client
                .send(
                    Frame::Error {
                        id,
                        message: "upstream unreachable".into(),
                    }
                    .to_msg(),
                )
                .await
                .unwrap();
        });
        let resp = http_get(http_port, "/boom").await;
        assert!(resp.starts_with("HTTP/1.1 502"), "resp={resp}");
        assert!(resp.ends_with("Tunnel upstream error"), "resp={resp}");
        task.await.unwrap();
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn ws_upgrade_is_rejected_until_supported() {
        let (_ws_port, http_port) = spawn_test_server().await;
        match connect_async(format!("ws://127.0.0.1:{http_port}/chat")).await {
            Err(tokio_tungstenite::tungstenite::Error::Http(response)) => {
                assert_eq!(response.status(), StatusCode::NOT_IMPLEMENTED);
            }
            other => {
                panic!("expected HTTP 501 WebSocket rejection, got {other:?}");
            }
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn reconnect_resends_pending_http() {
        let (ws_port, http_port) = spawn_test_server().await;
        // client1 connects, receives the request, then drops WITHOUT responding.
        let mut c1 = connect_client(ws_port).await;
        let http_task = tokio::spawn(async move { http_get(http_port, "/persist").await });
        match next_frame(&mut c1).await {
            Frame::HttpReq { path, .. } => assert_eq!(path, "/persist"),
            o => panic!("expected http_req, got {o:?}"),
        }
        drop(c1); // simulate tunnel client disconnect mid-request
        tokio::time::sleep(Duration::from_millis(150)).await;
        // client2 reconnects -> server must resend the still-pending request.
        let mut c2 = connect_client(ws_port).await;
        let id = match next_frame(&mut c2).await {
            Frame::HttpReq { id, path, .. } => {
                assert_eq!(path, "/persist");
                id
            }
            o => panic!("reconnect should resend http_req, got {o:?}"),
        };
        c2.send(
            Frame::HttpResp {
                id,
                status: 200,
                headers: Vec::new(),
                body: b64().encode(b"resent-ok"),
            }
            .to_msg(),
        )
        .await
        .unwrap();
        let resp = http_task.await.unwrap();
        assert!(
            resp.contains("200") && resp.ends_with("resent-ok"),
            "resp={resp}"
        );
    }
}
