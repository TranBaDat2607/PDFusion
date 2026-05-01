//! Spawns and supervises the Python FastAPI sidecar.
//!
//! Lifecycle:
//!   1. Locate the Python interpreter (env var `PDFUSION_PYTHON`, then `python` on PATH).
//!   2. Spawn `python -m desktop_pdf_translator.api.server` with stdout piped.
//!   3. Read stdout until we see `READY port=<int> token=<str>`.
//!   4. Health-poll `http://127.0.0.1:<port>/health` until it answers OK.
//!   5. Store the handle in a global so Tauri commands and shutdown can use it.

use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Mutex;
use std::time::Duration;

use once_cell::sync::OnceCell;
use serde::Serialize;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::time::{sleep, Instant};

#[derive(Debug)]
pub enum SidecarError {
    PythonNotFound,
    Spawn(std::io::Error),
    EarlyExit,
    Timeout,
    BadReadyLine(String),
    Health(String),
}

impl std::fmt::Display for SidecarError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::PythonNotFound => write!(
                f,
                "Python interpreter not found. Set PDFUSION_PYTHON or add 'python' to PATH."
            ),
            Self::Spawn(e) => write!(f, "Failed to spawn sidecar: {e}"),
            Self::EarlyExit => write!(f, "Sidecar exited before becoming ready"),
            Self::Timeout => write!(f, "Timed out waiting for sidecar to become ready"),
            Self::BadReadyLine(s) => write!(f, "Could not parse READY line: {s}"),
            Self::Health(s) => write!(f, "Health check failed: {s}"),
        }
    }
}

impl std::error::Error for SidecarError {}

impl From<std::io::Error> for SidecarError {
    fn from(e: std::io::Error) -> Self {
        Self::Spawn(e)
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct SidecarInfo {
    pub port: u16,
    pub token: String,
}

pub struct SidecarHandle {
    pub info: SidecarInfo,
    child: Mutex<Option<Child>>,
}

impl SidecarHandle {
    pub fn shutdown(&self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                // start_kill is non-blocking; the OS handles teardown.
                let _ = child.start_kill();
            }
        }
    }
}

static SIDECAR: OnceCell<SidecarHandle> = OnceCell::new();

pub fn current() -> Option<&'static SidecarHandle> {
    SIDECAR.get()
}

fn locate_python() -> Result<PathBuf, SidecarError> {
    if let Ok(explicit) = std::env::var("PDFUSION_PYTHON") {
        let p = PathBuf::from(explicit);
        if p.exists() {
            return Ok(p);
        }
    }
    // Common conda env path on Windows
    if let Ok(home) = std::env::var("USERPROFILE") {
        let candidate = PathBuf::from(&home).join("anaconda3/envs/pdfusion/python.exe");
        if candidate.exists() {
            return Ok(candidate);
        }
        let candidate2 = PathBuf::from(&home).join("miniconda3/envs/pdfusion/python.exe");
        if candidate2.exists() {
            return Ok(candidate2);
        }
    }
    // Fall back to PATH
    which::which("python")
        .or_else(|_| which::which("python3"))
        .map_err(|_| SidecarError::PythonNotFound)
}

fn project_root() -> Option<PathBuf> {
    // src-tauri lives at <root>/desktop/src-tauri ; the Python package lives at <root>/src
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    Some(manifest_dir.parent()?.parent()?.to_path_buf())
}

fn parse_ready_line(line: &str) -> Result<SidecarInfo, SidecarError> {
    let stripped = line.strip_prefix("READY ").ok_or_else(|| {
        SidecarError::BadReadyLine(format!("missing 'READY' prefix: {line}"))
    })?;
    let mut port: Option<u16> = None;
    let mut token: Option<String> = None;
    for kv in stripped.split_whitespace() {
        if let Some(rest) = kv.strip_prefix("port=") {
            port = rest.parse().ok();
        } else if let Some(rest) = kv.strip_prefix("token=") {
            token = Some(rest.to_string());
        }
    }
    let port = port.ok_or_else(|| SidecarError::BadReadyLine(format!("missing port: {line}")))?;
    let token = token.ok_or_else(|| SidecarError::BadReadyLine(format!("missing token: {line}")))?;
    Ok(SidecarInfo { port, token })
}

/// Spawn the sidecar and block until it reports `READY`. Returns once `/health`
/// answers OK. Stores the handle in the global so other code can read it.
pub async fn spawn() -> Result<SidecarInfo, SidecarError> {
    let python = locate_python()?;
    let root = project_root().ok_or(SidecarError::PythonNotFound)?;
    let src_dir = root.join("src");

    log::info!("Sidecar python: {}", python.display());
    log::info!("Sidecar PYTHONPATH: {}", src_dir.display());

    let mut command = Command::new(&python);
    command
        .arg("-m")
        .arg("desktop_pdf_translator.api.server")
        .current_dir(&root)
        .env("PYTHONPATH", &src_dir)
        // Force unbuffered stdout so we see READY immediately
        .env("PYTHONUNBUFFERED", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .stdin(Stdio::null());

    // CREATE_NO_WINDOW so no second console pops up in production builds.
    // tokio::process::Command exposes `creation_flags` natively on Windows.
    #[cfg(windows)]
    command.creation_flags(0x0800_0000);

    let mut child = command.spawn()?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| SidecarError::Health("no stdout pipe".into()))?;
    let mut reader = BufReader::new(stdout).lines();

    let deadline = Instant::now() + Duration::from_secs(30);
    let info = loop {
        if Instant::now() > deadline {
            let _ = child.start_kill();
            return Err(SidecarError::Timeout);
        }
        tokio::select! {
            line = reader.next_line() => {
                match line {
                    Ok(Some(text)) => {
                        log::info!("[sidecar stdout] {text}");
                        if text.starts_with("READY ") {
                            break parse_ready_line(&text)?;
                        }
                    }
                    Ok(None) => {
                        return Err(SidecarError::EarlyExit);
                    }
                    Err(e) => {
                        return Err(SidecarError::Health(format!("stdout read error: {e}")));
                    }
                }
            }
            status = child.wait() => {
                let st = status.map_err(SidecarError::Spawn)?;
                return Err(SidecarError::Health(format!("sidecar exited early: {st}")));
            }
        }
    };

    // Drain the rest of stdout to a background task so the pipe doesn't fill up.
    tokio::spawn(async move {
        let mut reader = reader;
        while let Ok(Some(line)) = reader.next_line().await {
            log::info!("[sidecar stdout] {line}");
        }
    });

    health_check(info.port, &info.token).await?;

    let handle = SidecarHandle {
        info: info.clone(),
        child: Mutex::new(Some(child)),
    };
    SIDECAR
        .set(handle)
        .map_err(|_| SidecarError::Health("sidecar handle already set".into()))?;

    Ok(info)
}

async fn health_check(port: u16, token: &str) -> Result<(), SidecarError> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .map_err(|e| SidecarError::Health(e.to_string()))?;

    let deadline = Instant::now() + Duration::from_secs(15);
    loop {
        let resp = client
            .get(format!("http://127.0.0.1:{port}/auth/ping"))
            .bearer_auth(token)
            .send()
            .await;

        if let Ok(r) = resp {
            if r.status().is_success() {
                log::info!("Sidecar healthy on port {port}");
                return Ok(());
            }
        }
        if Instant::now() > deadline {
            return Err(SidecarError::Health("auth/ping never returned 200".into()));
        }
        sleep(Duration::from_millis(300)).await;
    }
}
