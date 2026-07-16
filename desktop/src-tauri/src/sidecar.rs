//! Spawns and supervises the Python FastAPI sidecar.
//!
//! Lifecycle:
//!   1. Locate the sidecar binary:
//!        - production: bundled `pdfusion-sidecar-<triple>.exe` next to the app
//!          (shipped via Tauri `externalBin`),
//!        - development: a Python interpreter (`PDFUSION_PYTHON`, conda env, or
//!          `python` on PATH) invoking `-m desktop_pdf_translator.api.server`.
//!   2. Spawn the process with stdout piped.
//!   3. Read stdout until we see `READY port=<int> token=<str>`.
//!   4. Health-poll `http://127.0.0.1:<port>/auth/ping` until it answers OK.
//!   5. Store the handle in a global so Tauri commands and shutdown can use it.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Mutex;
use std::time::Duration;

use once_cell::sync::OnceCell;
use serde::Serialize;
use tauri::{AppHandle, Manager};
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
                "No bundled sidecar found and no Python interpreter available. Set PDFUSION_PYTHON or add 'python' to PATH."
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

/// Filename of the bundled sidecar binary at install time.
/// Tauri's `externalBin` requires the triple suffix on the *source* file,
/// but renames it at bundle time to drop the suffix — so post-install
/// (and what `BaseDirectory::Resource` resolves against) it's just the
/// bare name.
#[cfg(windows)]
const BUNDLED_SIDECAR_FILENAME: &str = "pdfusion-sidecar.exe";
#[cfg(not(windows))]
const BUNDLED_SIDECAR_FILENAME: &str = "pdfusion-sidecar";

/// Resolve the bundled sidecar exe via Tauri's resource resolver.
/// Returns `None` if there's no `externalBin` ship of the sidecar (i.e. dev mode)
/// OR the staged file looks like a stub (size < 1 MiB) so dev mode can replace
/// the real exe with a placeholder and have the runtime fall through to the
/// local Python interpreter. The real PyInstaller-built exe is ~80 MiB.
fn resolve_bundled_sidecar(app: &AppHandle) -> Option<PathBuf> {
    const STUB_THRESHOLD_BYTES: u64 = 1024 * 1024; // 1 MiB
    app.path()
        .resolve(BUNDLED_SIDECAR_FILENAME, tauri::path::BaseDirectory::Resource)
        .ok()
        .filter(|p| {
            p.exists()
                && std::fs::metadata(p)
                    .map(|m| m.len() >= STUB_THRESHOLD_BYTES)
                    .unwrap_or(false)
        })
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

/// Writable working directory for the spawned sidecar. The bundled exe lives
/// in `C:\Program Files\PDFusion\` (per-machine MSI install), which is
/// non-writable for non-admin users. If we let the sidecar inherit that as its
/// cwd, every `Path.cwd()` / relative-path write in Python lands on a
/// read-only path and raises `WinError 5`. Pointing the child at
/// `%LOCALAPPDATA%\PDFusion\` matches where the rest of the app already
/// writes (config, logs, caches, translated PDFs) and defuses the whole
/// class of cwd-write bugs.
pub(crate) fn appdata_dir() -> PathBuf {
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        let dir = PathBuf::from(local).join("PDFusion");
        if std::fs::create_dir_all(&dir).is_ok() {
            return dir;
        }
    }
    // Cross-platform fallback (also covers a corrupt %LOCALAPPDATA%): try
    // ~/AppData/Local/PDFusion, then finally the system temp dir so we never
    // hand the child a non-writable path.
    if let Some(home) = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")) {
        let dir = PathBuf::from(home)
            .join("AppData")
            .join("Local")
            .join("PDFusion");
        if std::fs::create_dir_all(&dir).is_ok() {
            return dir;
        }
    }
    std::env::temp_dir()
}

/// Pre-create the AppData subdirectories every sidecar subsystem expects.
/// Called from `lib.rs::setup` before the sidecar spawns so the Python side
/// never has to race on first-run `mkdir(parents=True)` against another
/// subsystem doing the same. Errors are logged and ignored — if AppData
/// itself is unwritable, the sidecar will surface a clearer downstream error.
pub fn ensure_appdata_layout() {
    let root = appdata_dir();
    // `translated_pdfs/` is intentionally absent: BabelDOC's live output now
    // lives in a per-job %TEMP%\pdfusion-translate-<rand>\ dir that the
    // sidecar wipes on the next translation start, on app exit (via
    // `cleanup_translate_temp_dirs` below), and on sidecar startup (via
    // the FastAPI lifespan orphan sweep).
    let subdirs: &[&str] = &[
        "logs",
        "translated_pdf_cache/files",
        "chroma_db",
        "translation_cache",
    ];
    for sub in subdirs {
        let dir = root.join(Path::new(sub));
        if let Err(e) = std::fs::create_dir_all(&dir) {
            log::warn!("Could not pre-create {}: {}", dir.display(), e);
        }
    }
    log::info!("AppData layout ready at {}", root.display());
}

/// Wipe every `pdfusion-translate-*` directory under the system temp root.
/// Called from the Tauri `ExitRequested` handler so we don't leak rolling
/// translation outputs across app launches. Best-effort: log and continue
/// on any individual failure (a file might still be held by a child process
/// mid-shutdown). Returns the number of dirs removed.
pub fn cleanup_translate_temp_dirs() -> usize {
    let temp_root = std::env::temp_dir();
    let entries = match std::fs::read_dir(&temp_root) {
        Ok(it) => it,
        Err(e) => {
            log::warn!("Translate temp cleanup: could not read {}: {}", temp_root.display(), e);
            return 0;
        }
    };
    let mut removed = 0usize;
    for entry in entries.flatten() {
        let path = entry.path();
        let is_match = path
            .file_name()
            .and_then(|n| n.to_str())
            .map(|n| n.starts_with("pdfusion-translate-"))
            .unwrap_or(false);
        if !is_match || !path.is_dir() {
            continue;
        }
        match std::fs::remove_dir_all(&path) {
            Ok(()) => removed += 1,
            Err(e) => log::warn!("Translate temp cleanup: could not remove {}: {}", path.display(), e),
        }
    }
    if removed > 0 {
        log::info!("Translate temp cleanup: removed {} dir(s)", removed);
    }
    removed
}

/// Build the spawn `Command` for the sidecar.
///
/// Prefers a bundled exe (production install). Falls back to a Python
/// interpreter running the module (developer machine, `pnpm tauri dev`).
fn build_command(app: &AppHandle) -> Result<Command, SidecarError> {
    if let Some(bundled) = resolve_bundled_sidecar(app) {
        log::info!("Sidecar (bundled): {}", bundled.display());
        let cwd = appdata_dir();
        log::info!("Sidecar cwd: {}", cwd.display());
        let mut cmd = Command::new(bundled);
        cmd.current_dir(cwd)
            .env("PYTHONUNBUFFERED", "1")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .stdin(Stdio::null());
        return Ok(cmd);
    }

    let python = locate_python()?;
    let root = project_root().ok_or(SidecarError::PythonNotFound)?;
    let src_dir = root.join("src");

    log::info!("Sidecar (dev) python: {}", python.display());
    log::info!("Sidecar (dev) PYTHONPATH: {}", src_dir.display());

    let mut cmd = Command::new(python);
    cmd.arg("-m")
        .arg("desktop_pdf_translator.api.server")
        .current_dir(&root)
        .env("PYTHONPATH", &src_dir)
        .env("PYTHONUNBUFFERED", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(Stdio::null());
    Ok(cmd)
}

/// Spawn the sidecar and block until it reports `READY`. Returns once `/auth/ping`
/// answers OK. Stores the handle in the global so other code can read it.
pub async fn spawn(app: AppHandle) -> Result<SidecarInfo, SidecarError> {
    let mut command = build_command(&app)?;

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

    // Drain stderr in the background so Python tracebacks / loader errors
    // land in our log instead of vanishing (the parent has no console on a
    // windowless build, so `Stdio::inherit()` would swallow them).
    if let Some(stderr) = child.stderr.take() {
        tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                log::warn!("[sidecar stderr] {line}");
            }
        });
    }

    let deadline = Instant::now() + Duration::from_secs(30);
    let info = loop {
        tokio::select! {
            // A silent-but-alive child never produces a line or an exit, so
            // the timeout must be a select branch of its own — checking the
            // deadline at the top of the loop only fires after output arrives.
            _ = tokio::time::sleep_until(deadline) => {
                let _ = child.start_kill();
                return Err(SidecarError::Timeout);
            }
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
