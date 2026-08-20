// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
// Licensed under the Apache License, Version 2.0.
// See the LICENSE file in this repository for the complete license text.

//! Local busy/idle tracking for the HTTP atomic-operation server, tunnel WS, and RuntimeRPC call handling.
//! The active counter reports via `KillRequest(signal=23)` only on `0 -> 1` / `1 -> 0` transitions,
//! letting function-proxy reuse IdleMgr to start or stop the idle timer.

use std::sync::atomic::{AtomicBool, AtomicI64, AtomicU64, Ordering};
use std::sync::{OnceLock, RwLock};

use tokio::sync::mpsc;

use crate::posix::runtime_rpc::StreamingMessage;

static ACTIVE: AtomicI64 = AtomicI64::new(0);
static REPORTED_BUSY: AtomicBool = AtomicBool::new(false);
static IDLE_EPOCH: AtomicU64 = AtomicU64::new(0);
static REPORTER: OnceLock<ActivityReporter> = OnceLock::new();
const IDLE_REPORT_DEBOUNCE: std::time::Duration = std::time::Duration::from_millis(800);

struct ActivityReporter {
    instance_id: RwLock<String>,
    tx: mpsc::Sender<StreamingMessage>,
}

impl ActivityReporter {
    fn new(instance_id: String, tx: mpsc::Sender<StreamingMessage>) -> Self {
        Self {
            instance_id: RwLock::new(instance_id),
            tx,
        }
    }

    fn rebind_instance_id(&self, instance_id: &str) {
        *self
            .instance_id
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = instance_id.to_string();
    }

    fn instance_id(&self) -> String {
        self.instance_id
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone()
    }
}

/// Initialize the activity counter. This is a reserved init hook; the global counter naturally maintains the initial value.
pub fn init() {}

/// Initialize the activity reporter before starting HTTP/tunnel servers so the first direct request can report busy.
pub fn init_reporter(instance_id: String, tx: mpsc::Sender<StreamingMessage>) {
    let _ = REPORTER.set(ActivityReporter::new(instance_id, tx));
}

/// Adopt the target logical identity after the restore environment has been
/// validated. Direct HTTP/tunnel activity survives checkpoint/restore, so its
/// reports must follow the same identity as the reconnected RuntimeRPC stream.
pub fn rebind_reporter_instance_id(instance_id: &str) {
    if let Some(reporter) = REPORTER.get() {
        reporter.rebind_instance_id(instance_id);
    }
}

/// RAII guard: increments activity on creation and decrements on drop, including connection/call end and panic unwinding.
#[must_use]
pub struct ActiveGuard;

/// Mark a connection/call active and return a guard; dropping the guard ends the activity.
pub fn enter() -> ActiveGuard {
    let previous = ACTIVE.fetch_add(1, Ordering::SeqCst);
    if state_transition(previous, true).is_some() {
        // Cancel any pending idle report when new activity arrives. If proxy still considers the runtime busy,
        // do not send duplicate busy reports; this avoids busy/idle flapping for short HTTP requests.
        IDLE_EPOCH.fetch_add(1, Ordering::SeqCst);
        if !REPORTED_BUSY.load(Ordering::SeqCst) && report_state_transition(true) {
            REPORTED_BUSY.store(true, Ordering::SeqCst);
        }
    }
    ActiveGuard
}

impl Drop for ActiveGuard {
    fn drop(&mut self) {
        let previous = ACTIVE.fetch_sub(1, Ordering::SeqCst);
        if state_transition(previous, false).is_some() {
            schedule_idle_report();
        }
    }
}

fn schedule_idle_report() {
    let epoch = IDLE_EPOCH.fetch_add(1, Ordering::SeqCst) + 1;
    let idle_task = async move {
        tokio::time::sleep(IDLE_REPORT_DEBOUNCE).await;
        if IDLE_EPOCH.load(Ordering::SeqCst) != epoch || ACTIVE.load(Ordering::SeqCst) != 0 {
            return;
        }
        if REPORTED_BUSY.load(Ordering::SeqCst) && report_state_transition(false) {
            REPORTED_BUSY.store(false, Ordering::SeqCst);
        }
    };
    if let Ok(handle) = tokio::runtime::Handle::try_current() {
        handle.spawn(idle_task);
    }
}

fn report_state_transition(busy: bool) -> bool {
    let Some(reporter) = REPORTER.get() else {
        return false;
    };
    let instance_id = reporter.instance_id();
    let state = if busy { "busy" } else { "idle" };
    let msg = super::activity_report_msg(&instance_id, state.as_bytes().to_vec());
    match reporter.tx.try_send(msg) {
        Ok(()) => {
            rrt_info!(
                "[rrt-runtime] activity state={} report_signal={} instance={} active_count={}",
                state,
                super::IDLE_REPORT_SIGNAL,
                instance_id,
                ACTIVE.load(Ordering::SeqCst)
            );
            true
        }
        Err(e) => {
            rrt_error!(
                "[rrt-runtime] activity report failed state={} instance={} error={}",
                state,
                instance_id,
                e
            );
            false
        }
    }
}

/// Current activity state text. Used to resynchronize state with function-proxy after MessageStream reconnects.
pub fn current_state() -> &'static str {
    if ACTIVE.load(Ordering::SeqCst) > 0 {
        "busy"
    } else {
        "idle"
    }
}

/// Current number of active connections/calls.
pub fn active_count() -> i64 {
    ACTIVE.load(Ordering::SeqCst)
}

/// Wait until all in-flight RuntimeRPC/HTTP/tunnel requests finish.
pub async fn wait_until_idle(timeout: std::time::Duration) -> bool {
    let deadline = tokio::time::Instant::now() + timeout;
    loop {
        if active_count() <= 0 {
            return true;
        }
        if tokio::time::Instant::now() >= deadline {
            return false;
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
}

/// Report only when the global active counter crosses the zero boundary.
fn state_transition(previous: i64, entering: bool) -> Option<&'static str> {
    match (previous, entering) {
        (0, true) => Some("busy"),
        (1, false) => Some("idle"),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn state_transition_only_on_zero_boundary() {
        assert_eq!(state_transition(0, true), Some("busy"));
        assert_eq!(state_transition(1, true), None);
        assert_eq!(state_transition(2, false), None);
        assert_eq!(state_transition(1, false), Some("idle"));
        assert_eq!(state_transition(0, false), None);
    }

    #[test]
    fn guard_increments_then_decrements() {
        let base = active_count();
        {
            let _g = enter();
            assert_eq!(active_count(), base + 1);
        }
        assert_eq!(active_count(), base);
    }

    #[test]
    fn reporter_uses_rebound_target_logical_identity() {
        let (tx, _rx) = mpsc::channel(1);
        let reporter = ActivityReporter::new("source-sandbox".to_string(), tx);

        reporter.rebind_instance_id("clone-sandbox");

        assert_eq!(reporter.instance_id(), "clone-sandbox");
    }
}
