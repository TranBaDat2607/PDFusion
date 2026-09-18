//! Spawns and supervises the Python FastAPI sidecar.
//!
//! Lifecycle:
//!   1. Locate the sidecar binary:
//!        - production: the bundled PyInstaller build — an `externalBin` next
//!          to the app on Windows, a resource directory on Linux and macOS
//!          (see `BUNDLED_SIDECAR_FILENAME`),
//!        - development: a Python interpreter (`PDFUSION_PYTHON`, conda env, or
//!          `python` on PATH) invoking `-m desktop_pdf_translator.api.server`.
//!   2. Spawn the process with stdout piped.
//!   3. Read stdout until we see `READY port=<int> token=<str>`.
//!   4. Health-poll `http://127.0.0.1:<port>/auth/ping` until it answers OK.
//!   5. Store the handle in a global so Tauri commands and shutdown can use it.

use std::borrow::Cow;
use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::process::{ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use once_cell::sync::OnceCell;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::time::{sleep, Instant};

/// How long the shell waits for the sidecar's READY line.
///
/// Generous because what it covers is not Python work: the bundled sidecar is a
/// PyInstaller one-dir build, so before `main()` runs at all the bootloader has
/// to page in thousands of files past Defender's on-access scanner. The Python
/// side of startup is well under a second (see `api/server.py`'s docstring), and
/// no fix on that side shortens a first-launch cold scan.
const READY_TIMEOUT: Duration = Duration::from_secs(90);

/// How long `/auth/ping` gets to answer once READY has been printed. Covers the
/// FastAPI lifespan: the orphan-temp-dir sweep and decrypting the stored API
/// keys, both of which touch a possibly-cold disk.
const HEALTH_TIMEOUT: Duration = Duration::from_secs(30);

/// How often the post-boot supervisor polls the child for an unrequested exit.
const SUPERVISOR_POLL_INTERVAL: Duration = Duration::from_millis(250);

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

#[derive(Debug, Clone, Serialize)]
struct ExitedPayload {
    code: Option<i32>,
}

pub struct SidecarHandle {
    pub info: SidecarInfo,
    child: Mutex<Option<Child>>,
    shutting_down: AtomicBool,
}

impl SidecarHandle {
    pub fn shutdown(&self) {
        // Set before taking the child so the supervisor can never observe
        // "child gone" without also observing "on purpose."
        self.shutting_down.store(true, Ordering::SeqCst);
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.start_kill();
            }
        }
    }
}

static SIDECAR: OnceCell<SidecarHandle> = OnceCell::new();

pub fn current() -> Option<&'static SidecarHandle> {
    SIDECAR.get()
}

/// Where the bundled sidecar lands at install time, relative to
/// `BaseDirectory::Resource`. It differs by platform because the *packaging*
/// does, and PyInstaller's one-dir bootloader hard-requires `_internal/` to sit
/// next to the executable it belongs to.
///
/// **Windows.** Shipped through Tauri's `externalBin`, which requires the
/// rustc-triple suffix on the source file and renames it at bundle time to drop
/// the suffix — so post-install it is the bare name, at the install root, which
/// is also the resource root. `_internal/**/*` is shipped as a resource and
/// lands beside it.
///
/// **Linux and macOS.** `externalBin` would put it in `/usr/bin` (deb,
/// AppImage) or `Contents/MacOS` (.app) while `resources` go to
/// `/usr/lib/<product>` / `Contents/Resources` — different directories, so
/// `_internal/` would no longer be a sibling and the bootloader would fail to
/// find `libpython3.11.so`. So off Windows the sidecar is not an `externalBin`
/// at all: the whole PyInstaller one-dir tree ships as a single resource
/// directory, which keeps the two together wherever the bundler puts it (#69).
#[cfg(windows)]
const BUNDLED_SIDECAR_FILENAME: &str = "pdfusion-sidecar.exe";
#[cfg(not(windows))]
const BUNDLED_SIDECAR_FILENAME: &str = "sidecar/pdfusion-sidecar";

/// A staged binary below this is `build-sidecar --stub`'s placeholder, not a
/// real build. The PyInstaller executable is ~80 MiB.
const STUB_THRESHOLD_BYTES: u64 = 1024 * 1024; // 1 MiB

/// Whether a staged file is a real sidecar rather than a `-Stub` placeholder.
/// Returns the observed size on rejection so the caller can name it in a log.
fn check_staged_size(path: &Path) -> Result<(), Option<u64>> {
    match std::fs::metadata(path) {
        Ok(m) if m.len() >= STUB_THRESHOLD_BYTES => Ok(()),
        Ok(m) => Err(Some(m.len())),
        Err(_) => Err(None),
    }
}

/// Resolve the bundled sidecar exe via Tauri's resource resolver.
/// Returns `None` if there's no `externalBin` ship of the sidecar (i.e. dev mode)
/// OR the staged file looks like a stub so dev mode can replace the real exe
/// with a placeholder and have the runtime fall through to the local Python
/// interpreter.
///
/// Every rejection is logged. Falling through silently made a stubbed or
/// truncated exe in a *shipped* install indistinguishable from ordinary dev
/// output: the only trace was the "Sidecar (dev) python" line that follows,
/// or `PythonNotFound` on a machine that has no Python at all.
fn resolve_bundled_sidecar(app: &AppHandle) -> Option<PathBuf> {
    let path = app
        .path()
        .resolve(BUNDLED_SIDECAR_FILENAME, tauri::path::BaseDirectory::Resource)
        .ok()?;
    if !path.exists() {
        log::info!(
            "No bundled sidecar at {} — falling back to local Python",
            path.display()
        );
        return None;
    }
    match check_staged_size(&path) {
        Ok(()) => {
            ensure_executable(&path);
            Some(path)
        }
        Err(Some(len)) => {
            log::warn!(
                "Ignoring bundled sidecar {}: {} bytes is below the {} byte stub threshold. This is a `build-sidecar --stub` placeholder, not a real build — falling back to local Python.",
                path.display(),
                len,
                STUB_THRESHOLD_BYTES
            );
            None
        }
        Err(None) => {
            log::warn!(
                "Ignoring bundled sidecar {}: could not read its size — falling back to local Python.",
                path.display()
            );
            None
        }
    }
}

/// The user's home directory, however this platform spells it.
///
/// `USERPROFILE` first so a Windows machine running under an MSYS/Git-Bash
/// shell — which exports a POSIX-shaped `HOME` — still gets the Windows
/// profile the conda installers actually wrote to.
fn home_dir() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .filter(|h| !h.is_empty())
        .map(PathBuf::from)
}

/// Where a conda-style `pdfusion` env's interpreter would be, best first.
///
/// Pure so it can be tested off the machine it describes. `pdfusion` is the
/// name in the project's setup docs; `pdfusion-env` is accepted too since
/// nothing enforces one canonical name across machines. `miniforge3` is the
/// usual conda-forge install on macOS and on Linux boxes that never wanted
/// Anaconda's defaults channel. Anything else needs `PDFUSION_PYTHON`.
///
/// The layouts genuinely differ: Windows envs put the interpreter at the env
/// root (`envs/pdfusion/python.exe`), every POSIX one puts it in `bin/`
/// (`envs/pdfusion/bin/python`). Looking only for `python.exe` under
/// `%USERPROFILE%` — which is what this did — misses on Linux and macOS twice
/// over, and the fall-through to `which` then picks up whatever interpreter is
/// on `$PATH` rather than the project's env (#69).
fn conda_python_candidates(home: &Path) -> Vec<PathBuf> {
    const CONDA_DISTS: [&str; 3] = ["anaconda3", "miniconda3", "miniforge3"];
    const ENV_NAMES: [&str; 2] = ["pdfusion", "pdfusion-env"];
    #[cfg(windows)]
    const INTERPRETER: [&str; 1] = ["python.exe"];
    #[cfg(not(windows))]
    const INTERPRETER: [&str; 2] = ["bin", "python"];

    let mut out = Vec::new();
    for dist in CONDA_DISTS {
        for env_name in ENV_NAMES {
            let mut candidate = home.join(dist).join("envs").join(env_name);
            for part in INTERPRETER {
                candidate = candidate.join(part);
            }
            out.push(candidate);
        }
    }
    out
}

/// Make sure the staged sidecar still carries its executable bit.
///
/// Off Windows it can lose it: the bundler copies `resources` file by file,
/// and an AppImage, a `.deb` built on a machine with an unusual umask, or a
/// checkout restored from a zip can all arrive without `+x`. A `Permission
/// denied` on spawn would otherwise read as "no sidecar" and send a shipped
/// install down the local-Python fallback. Best-effort, and it never rejects
/// the binary — if the chmod fails too, spawning it produces a far clearer
/// error than silently pretending it isn't there.
#[cfg(unix)]
fn ensure_executable(path: &Path) {
    use std::os::unix::fs::PermissionsExt;

    let Ok(metadata) = std::fs::metadata(path) else {
        return;
    };
    let mut perms = metadata.permissions();
    if perms.mode() & 0o111 != 0 {
        return;
    }
    perms.set_mode(perms.mode() | 0o755);
    match std::fs::set_permissions(path, perms) {
        Ok(()) => log::warn!(
            "Bundled sidecar {} was not executable; restored its executable bit",
            path.display()
        ),
        Err(e) => log::error!(
            "Bundled sidecar {} is not executable and could not be made so: {e}",
            path.display()
        ),
    }
}

#[cfg(not(unix))]
fn ensure_executable(_path: &Path) {}

fn locate_python() -> Result<PathBuf, SidecarError> {
    if let Ok(explicit) = std::env::var("PDFUSION_PYTHON") {
        let p = PathBuf::from(explicit);
        if p.exists() {
            return Ok(p);
        }
    }
    if let Some(home) = home_dir() {
        for candidate in conda_python_candidates(&home) {
            if candidate.exists() {
                return Ok(candidate);
            }
        }
    }
    // Fall back to PATH. `python3` first off Windows, where `python` is as
    // likely to be absent as to be Python 2; `python` first on Windows, where
    // `python3` is usually the Microsoft Store stub that opens the Store
    // instead of running anything.
    #[cfg(windows)]
    let found = which::which("python").or_else(|_| which::which("python3"));
    #[cfg(not(windows))]
    let found = which::which("python3").or_else(|_| which::which("python"));
    found.map_err(|_| SidecarError::PythonNotFound)
}

fn project_root() -> Option<PathBuf> {
    // src-tauri lives at <root>/desktop/src-tauri ; the Python package lives at <root>/src
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    Some(manifest_dir.parent()?.parent()?.to_path_buf())
}

/// Strip the bearer token out of a line before it reaches the log.
///
/// The handshake carries the sidecar's token, which is the only thing standing
/// between a local process and the sidecar's API — so it should not be printed
/// at all, wherever the printing happens to land.
///
/// Where it lands today: `log::info!` here goes to `tauri_plugin_log`
/// (`lib.rs::run`), which writes both to *this* process's stdout — the
/// `pnpm tauri dev` terminal, and any CI/console capture of it — and to
/// `~/AppData/Local/PDFusion/logs/shell.log`. It does **not** reach
/// `~/AppData/Local/PDFusion/logs/app.log`: that file is written by the
/// Python child (`utils/logging_setup.py::configure_logging`), and the token
/// never goes through Python's `logging` — `server.py::main` `print`s it to
/// stdout. Redacting here keeps it out of both the dev terminal and
/// `shell.log`, not just the terminal.
fn redact_ready_line(line: &str) -> Cow<'_, str> {
    if !line.starts_with("READY ") {
        return Cow::Borrowed(line);
    }
    Cow::Owned(
        line.split_whitespace()
            .map(|kv| {
                if kv.starts_with("token=") {
                    "token=<redacted>"
                } else {
                    kv
                }
            })
            .collect::<Vec<&str>>()
            .join(" "),
    )
}

/// Parse the handshake line. Note the errors below render the line through
/// `redact_ready_line` as well: `SidecarError` reaches `log::error!` and the
/// `sidecar://error` event, so redacting only at the two `log::info!` call
/// sites would leave the token in every sink a *malformed* line reaches.
fn parse_ready_line(line: &str) -> Result<SidecarInfo, SidecarError> {
    let safe = redact_ready_line(line);
    let stripped = line.strip_prefix("READY ").ok_or_else(|| {
        SidecarError::BadReadyLine(format!("missing 'READY' prefix: {safe}"))
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
    let port = port.ok_or_else(|| SidecarError::BadReadyLine(format!("missing port: {safe}")))?;
    let token = token.ok_or_else(|| SidecarError::BadReadyLine(format!("missing token: {safe}")))?;
    Ok(SidecarInfo { port, token })
}

/// The name of every data root below, and of the folder the installer creates.
const APP_DIR: &str = "PDFusion";

/// Handed to the sidecar so the two resolvers cannot disagree. `utils/paths.py`
/// reads it first and falls back to the same rules as `data_dir_candidates`
/// below — the fallback is for a sidecar run by hand, not for the shipped app.
const DATA_DIR_ENV: &str = "PDFUSION_DATA_DIR";

/// The ordered data-root candidates, given what the environment answered.
///
/// Pure, and every input passed in rather than read here, so the table below
/// can be tested for the platform the test is describing rather than the one
/// the test happens to run on.
///
/// | Platform | Root |
/// |---|---|
/// | Windows | `%LOCALAPPDATA%\PDFusion`, then `~/AppData/Local/PDFusion` |
/// | macOS | `~/Library/Application Support/PDFusion` |
/// | Linux / other | `$XDG_DATA_HOME/PDFusion`, else `~/.local/share/PDFusion` |
///
/// `explicit` (`$PDFUSION_DATA_DIR`) wins everywhere. Off Windows the literal
/// `AppData` folder the pre-#69 code used is *not* a candidate: it is nobody's
/// convention, and a config left there is carried across once by
/// `utils/paths.adopt_legacy_config` instead of being written to forever.
fn data_dir_candidates(
    explicit: Option<PathBuf>,
    local_appdata: Option<PathBuf>,
    xdg_data_home: Option<PathBuf>,
    home: Option<PathBuf>,
) -> Vec<PathBuf> {
    // Every platform arm below reads a different subset of these, and the two
    // it doesn't read would otherwise be `unused_variables` warnings on that
    // platform. Borrowing them here is the use; each arm still consumes them.
    let _ = (&local_appdata, &xdg_data_home);

    let mut out = Vec::new();
    if let Some(dir) = explicit {
        out.push(dir);
    }

    #[cfg(windows)]
    {
        if let Some(local) = local_appdata {
            out.push(local.join(APP_DIR));
        }
        // Where the app wrote before #59, so a machine with a broken
        // %LOCALAPPDATA% still finds its own data rather than starting fresh.
        if let Some(home) = home {
            out.push(home.join("AppData").join("Local").join(APP_DIR));
        }
    }

    #[cfg(target_os = "macos")]
    {
        if let Some(home) = home {
            out.push(home.join("Library").join("Application Support").join(APP_DIR));
        }
    }

    #[cfg(all(not(windows), not(target_os = "macos")))]
    {
        // The spec says a relative XDG_DATA_HOME is invalid and must be
        // ignored. Not a nicety here: the shell makes the data root the
        // sidecar's cwd, so a relative value would resolve against the very
        // directory it is supposed to be choosing.
        match xdg_data_home.filter(|p| p.is_absolute()) {
            Some(xdg) => out.push(xdg.join(APP_DIR)),
            None => {
                if let Some(home) = home {
                    out.push(home.join(".local").join("share").join(APP_DIR));
                }
            }
        }
    }

    out
}

/// Writable working directory for the spawned sidecar, and the root every
/// store the app owns lives under.
///
/// Two reasons it is not simply the install directory. On Windows a
/// per-machine install lands in `C:\Program Files\PDFusion\`, which is
/// non-writable for non-admin users — inherit that as the child's cwd and
/// every `Path.cwd()` / relative-path write in Python raises `WinError 5`. On
/// Linux the install directory is `/usr/lib/...`, which is worse. Pointing the
/// child at the platform's user data directory matches where the rest of the
/// app already writes (config, logs, caches) and defuses the whole class of
/// cwd-write bugs.
pub(crate) fn appdata_dir() -> PathBuf {
    let candidates = data_dir_candidates(
        std::env::var_os(DATA_DIR_ENV).filter(|v| !v.is_empty()).map(PathBuf::from),
        std::env::var_os("LOCALAPPDATA").filter(|v| !v.is_empty()).map(PathBuf::from),
        std::env::var_os("XDG_DATA_HOME").filter(|v| !v.is_empty()).map(PathBuf::from),
        home_dir(),
    );
    for dir in candidates {
        if std::fs::create_dir_all(&dir).is_ok() {
            return dir;
        }
    }
    // Last resort, so we never hand the child a path it cannot write to.
    std::env::temp_dir()
}

/// Pre-create the AppData subdirectories every sidecar subsystem expects.
/// Called from `lib.rs::setup` before the sidecar spawns so the Python side
/// never has to race on first-run `mkdir(parents=True)` against another
/// subsystem doing the same. Errors are logged and ignored — if AppData
/// itself is unwritable, the sidecar will surface a clearer downstream error.
pub fn ensure_appdata_layout() {
    let root = appdata_dir();
    ensure_appdata_layout_in(&root);
    log::info!("AppData layout ready at {}", root.display());
}

/// `ensure_appdata_layout` against an explicit root, so a test can run it in a
/// temp dir instead of the real `%LOCALAPPDATA%`.
fn ensure_appdata_layout_in(root: &Path) {
    // `translated_pdfs/` is intentionally absent: BabelDOC's live output now
    // lives in a per-job %TEMP%\pdfusion-translate-<rand>\ dir that the
    // sidecar wipes on the next translation start, on app exit (via
    // `cleanup_translate_temp_dirs` below), and on sidecar startup (via
    // the FastAPI lifespan orphan sweep).
    let subdirs: &[&str] = &[
        "logs",
        "translated_pdf_cache/files",
        // Chat indexes: one ChromaDB collection per index recorded in
        // `pdfusion.db` (rag/vector_store.py). It replaced `chroma_db_v2`,
        // which the sidecar deletes on startup — so that name must not come
        // back here, or every launch would recreate what the last one removed.
        "vectors",
        "translation_cache",
    ];
    for sub in subdirs {
        let dir = root.join(Path::new(sub));
        if let Err(e) = std::fs::create_dir_all(&dir) {
            log::warn!("Could not pre-create {}: {}", dir.display(), e);
        }
    }
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

/// Whether the sidecar should accept the Vite dev server's origin.
///
/// The sidecar's CORS allowlist has to know whether the webview is being served
/// by Vite (`http://localhost:1420`) or from Tauri's custom protocol
/// (`http://tauri.localhost`). It can't work that out for itself: it used to
/// infer "dev" from `sys.frozen`, but the two are independent. A real
/// PyInstaller sidecar staged into `binaries/` — the state after
/// `build-sidecar.ps1` or any `pnpm tauri build` — wins `resolve_bundled_sidecar`
/// even under `pnpm tauri dev`, so a *frozen* sidecar routinely serves a
/// *Vite-hosted* webview, and every request from it was rejected as a
/// disallowed origin. The shell is the side that knows, so the shell says.
///
/// `debug_assertions`, not `tauri::is_dev()`: the latter is
/// `!cfg!(feature = "custom-protocol")` and this crate declares no `[features]`
/// at all, so it is `true` even in a release bundle — it would hand the shipped
/// app the dev answer. `debug_assertions` is already what `main.rs` uses to tell
/// a dev build from a shipped one.
///
/// Sent explicitly on both spawn paths, including the "0", so that a stray
/// `PDFUSION_DEV_ORIGINS=1` in a developer's environment can't be inherited by
/// an installed app.
fn dev_origins_flag() -> &'static str {
    if cfg!(debug_assertions) {
        "1"
    } else {
        "0"
    }
}

/// Build the spawn `Command` for the sidecar.
///
/// Prefers a bundled exe (production install). Falls back to a Python
/// interpreter running the module (developer machine, `pnpm tauri dev`).
/// Everything both spawn paths need: a writable cwd, unbuffered output, the
/// dev-origins answer, and piped stdio. Kept in one place so the bundled and
/// the dev sidecar can't drift into different environments — they are 20 lines
/// apart, and a one-sided edit compiles fine.
fn base_command(program: impl AsRef<OsStr>, cwd: &Path) -> Command {
    let mut cmd = Command::new(program);
    cmd.current_dir(cwd)
        .env("PYTHONUNBUFFERED", "1")
        // The cwd *is* the data root, and `utils/paths.appdata_dir()` reads
        // this first. Both sides implement the same platform rules, but saying
        // it outright is what makes them agree by construction rather than by
        // two implementations staying in step (#69).
        .env(DATA_DIR_ENV, cwd)
        .env("PDFUSION_DEV_ORIGINS", dev_origins_flag())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(Stdio::null());
    cmd
}

fn build_command(app: &AppHandle) -> Result<Command, SidecarError> {
    if let Some(bundled) = resolve_bundled_sidecar(app) {
        log::info!("Sidecar (bundled): {}", bundled.display());
        let cwd = appdata_dir();
        log::info!("Sidecar cwd: {}", cwd.display());
        return Ok(base_command(bundled, &cwd));
    }

    let python = locate_python()?;
    let root = project_root().ok_or(SidecarError::PythonNotFound)?;
    let src_dir = root.join("src");

    log::info!("Sidecar (dev) python: {}", python.display());
    log::info!("Sidecar (dev) PYTHONPATH: {}", src_dir.display());

    // The dev spawn runs from the repo root, not the data root — `-m` needs
    // the package importable — so `base_command`'s "cwd is the data root"
    // assumption does not hold here and the variable is corrected.
    let mut cmd = base_command(python, &root);
    cmd.arg("-m")
        .arg("desktop_pdf_translator.api.server")
        .env(DATA_DIR_ENV, appdata_dir())
        .env("PYTHONPATH", &src_dir);
    Ok(cmd)
}

/// Ties the child's lifetime to ours: if this process dies for any reason —
/// crash, Task Manager, anything short of the OS itself going down — Windows
/// tears the child down with it instead of orphaning it. Best-effort: a
/// failure here is logged and otherwise ignored, never fatal to startup.
#[cfg(windows)]
fn confine_to_job_object(child: &Child) {
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    let Some(raw) = child.raw_handle() else {
        log::warn!("Sidecar job confinement skipped: no process handle");
        return;
    };

    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            log::warn!("Sidecar job confinement skipped: CreateJobObjectW failed");
            return;
        }

        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;

        let configured = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const _,
            std::mem::size_of_val(&info) as u32,
        );
        if configured == 0 || AssignProcessToJobObject(job, raw) == 0 {
            log::warn!("Sidecar job confinement failed; it may outlive this app if killed abnormally");
        }
        // `job` is deliberately never closed: JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        // fires when the last handle to it closes, so leaking it for the rest
        // of this process's life is what ties the child's life to ours.
    }
}

/// Polls `child` until it exits and reports the exit status, unless
/// `shutting_down` says we're the ones who killed it. `None` covers both
/// "it hasn't exited" — impossible to reach since this only returns once the
/// child is gone — and "we already know why," e.g. `shutdown()` having taken
/// the child out from under this loop.
async fn unexpected_exit(
    child: &Mutex<Option<Child>>,
    shutting_down: &AtomicBool,
    poll_interval: Duration,
) -> Option<ExitStatus> {
    loop {
        sleep(poll_interval).await;
        let status = match child.lock() {
            Ok(mut guard) => match guard.as_mut() {
                Some(c) => c.try_wait().ok().flatten(),
                None => return None,
            },
            Err(_) => return None,
        };
        if let Some(status) = status {
            return (!shutting_down.load(Ordering::SeqCst)).then_some(status);
        }
    }
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

    #[cfg(windows)]
    confine_to_job_object(&child);

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

    let deadline = Instant::now() + READY_TIMEOUT;
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
                        log::info!("[sidecar stdout] {}", redact_ready_line(&text));
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
            log::info!("[sidecar stdout] {}", redact_ready_line(&line));
        }
    });

    health_check(info.port, &info.token).await?;

    let handle = SidecarHandle {
        info: info.clone(),
        child: Mutex::new(Some(child)),
        shutting_down: AtomicBool::new(false),
    };
    SIDECAR
        .set(handle)
        .map_err(|_| SidecarError::Health("sidecar handle already set".into()))?;

    tokio::spawn(async move {
        let handle = SIDECAR.get().expect("just set above");
        let status = unexpected_exit(
            &handle.child,
            &handle.shutting_down,
            SUPERVISOR_POLL_INTERVAL,
        )
        .await;
        if let Some(status) = status {
            log::warn!("Sidecar exited unexpectedly: {status}");
            let _ = app.emit("sidecar://exited", ExitedPayload { code: status.code() });
        }
    });

    Ok(info)
}

async fn health_check(port: u16, token: &str) -> Result<(), SidecarError> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .map_err(|e| SidecarError::Health(e.to_string()))?;

    let deadline = Instant::now() + HEALTH_TIMEOUT;
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

#[cfg(test)]
mod tests {
    use super::{
        check_staged_size, conda_python_candidates, data_dir_candidates,
        ensure_appdata_layout_in, parse_ready_line, redact_ready_line, unexpected_exit,
        Child, Command, PathBuf, STUB_THRESHOLD_BYTES,
    };
    use std::path::Path;
    use std::sync::atomic::AtomicBool;
    use std::sync::Mutex;
    use std::time::Duration;

    /// A child that exits with status 3, spelled for whichever OS is running
    /// the suite. `cmd /C exit 3` is what these two tests used, which made
    /// `cargo test` fail on Linux and macOS for reasons that had nothing to do
    /// with what they assert (#69).
    fn exits_with_code_3() -> Child {
        let mut cmd = if cfg!(windows) {
            let mut c = Command::new("cmd");
            c.args(["/C", "exit", "3"]);
            c
        } else {
            let mut c = Command::new("sh");
            c.args(["-c", "exit 3"]);
            c
        };
        cmd.spawn().expect("spawn a process that exits with 3")
    }

    #[test]
    fn ready_line_is_logged_without_its_token() {
        let line = "READY port=54213 token=Yd7HfSuperSecretG3";
        let redacted = redact_ready_line(line);
        assert_eq!(redacted, "READY port=54213 token=<redacted>");
        assert!(!redacted.contains("Yd7HfSuperSecretG3"));
    }

    #[test]
    fn redaction_does_not_disturb_parsing() {
        // The raw line is still what gets parsed — redaction is for the log
        // only, so the token must survive the path that actually uses it.
        let line = "READY port=54213 token=Yd7HfSuperSecretG3";
        let info = parse_ready_line(line).expect("should parse");
        assert_eq!(info.port, 54213);
        assert_eq!(info.token, "Yd7HfSuperSecretG3");
    }

    #[test]
    fn ordinary_stdout_passes_through_unchanged() {
        let line = "INFO Argos pre-warm: done";
        assert_eq!(redact_ready_line(line), line);
    }

    #[test]
    fn a_malformed_ready_line_does_not_leak_its_token_through_the_error() {
        // `SidecarError` is rendered by `log::error!` and by the
        // `sidecar://error` event, so this is the third sink the token could
        // reach — and the one no `log::info!` call site guards.
        let line = "READY token=Yd7HfSuperSecretG3";
        let err = parse_ready_line(line).expect_err("no port, so it must fail");
        assert!(!format!("{err}").contains("Yd7HfSuperSecretG3"));
    }

    #[tokio::test]
    async fn a_crash_that_was_not_requested_is_reported_with_its_exit_code() {
        let child = Mutex::new(Some(exits_with_code_3()));
        let shutting_down = AtomicBool::new(false);

        let status = unexpected_exit(&child, &shutting_down, Duration::from_millis(10)).await;

        assert_eq!(status.and_then(|s| s.code()), Some(3));
    }

    #[tokio::test]
    async fn a_shutdown_that_already_took_the_child_produces_no_report() {
        let child = Mutex::new(Some(exits_with_code_3()));
        let shutting_down = AtomicBool::new(true);
        // Mirrors what `shutdown()` does before the supervisor's next poll tick.
        child.lock().unwrap().take();

        let status = unexpected_exit(&child, &shutting_down, Duration::from_millis(10)).await;

        assert!(status.is_none());
    }

    #[test]
    fn a_stub_sized_exe_is_rejected_with_its_size() {
        let dir = std::env::temp_dir().join(format!("pdfusion-stub-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("create temp dir");
        let stub = dir.join("pdfusion-sidecar.exe");
        std::fs::write(&stub, b"").expect("write stub");

        assert_eq!(check_staged_size(&stub), Err(Some(0)));

        std::fs::write(&stub, vec![0u8; STUB_THRESHOLD_BYTES as usize]).expect("write real");
        assert_eq!(check_staged_size(&stub), Ok(()));

        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn an_unreadable_path_is_rejected_without_a_size() {
        let missing = std::env::temp_dir().join("pdfusion-does-not-exist.exe");
        assert_eq!(check_staged_size(&missing), Err(None));
    }

    #[test]
    fn the_project_env_is_preferred_over_whatever_python_is_on_path() {
        let home = Path::new("/home/dev");
        let candidates = conda_python_candidates(home);

        // All three distributions, both env names, and nothing else.
        assert_eq!(candidates.len(), 6);
        assert!(candidates.iter().all(|c| c.starts_with(home)));
        for dist in ["anaconda3", "miniconda3", "miniforge3"] {
            assert!(
                candidates.iter().any(|c| c.starts_with(home.join(dist))),
                "{dist} is not looked for"
            );
        }
    }

    #[test]
    fn the_interpreter_is_looked_for_where_this_platform_puts_it() {
        let candidates = conda_python_candidates(Path::new("/home/dev"));
        let first = &candidates[0];

        if cfg!(windows) {
            assert!(first.ends_with("envs/pdfusion/python.exe"));
        } else {
            // The bug: a Windows-shaped `envs/pdfusion/python.exe` never
            // exists on Linux or macOS, so discovery fell through to `$PATH`.
            assert!(first.ends_with("envs/pdfusion/bin/python"));
        }
    }

    #[test]
    fn an_explicit_data_dir_wins_over_every_platform_default() {
        let candidates = data_dir_candidates(
            Some(PathBuf::from("/srv/pdfusion-data")),
            Some(PathBuf::from("/c/Users/dev/AppData/Local")),
            Some(PathBuf::from("/home/dev/.local/share")),
            Some(PathBuf::from("/home/dev")),
        );

        assert_eq!(candidates[0], PathBuf::from("/srv/pdfusion-data"));
    }

    #[test]
    fn the_default_root_follows_this_platforms_convention() {
        let candidates = data_dir_candidates(
            None,
            Some(PathBuf::from("/c/Users/dev/AppData/Local")),
            Some(PathBuf::from("/home/dev/.local/share")),
            Some(PathBuf::from("/home/dev")),
        );

        let expected = if cfg!(windows) {
            "/c/Users/dev/AppData/Local/PDFusion"
        } else if cfg!(target_os = "macos") {
            "/home/dev/Library/Application Support/PDFusion"
        } else {
            "/home/dev/.local/share/PDFusion"
        };
        assert_eq!(candidates[0], PathBuf::from(expected));
    }

    #[cfg(all(not(windows), not(target_os = "macos")))]
    #[test]
    fn a_relative_xdg_data_home_is_ignored_rather_than_resolved_against_the_cwd() {
        // The shell makes the data root the sidecar's cwd, so honouring a
        // relative value would mean resolving the choice against itself.
        let candidates = data_dir_candidates(
            None,
            None,
            Some(PathBuf::from("relative/share")),
            Some(PathBuf::from("/home/dev")),
        );

        assert_eq!(candidates, vec![PathBuf::from("/home/dev/.local/share/PDFusion")]);
    }

    #[test]
    fn the_appdata_layout_holds_the_vector_store_and_no_legacy_one() {
        let root = std::env::temp_dir().join(format!("pdfusion-layout-{}", std::process::id()));
        std::fs::remove_dir_all(&root).ok();

        ensure_appdata_layout_in(&root);

        for sub in ["logs", "translated_pdf_cache/files", "vectors", "translation_cache"] {
            assert!(root.join(sub).is_dir(), "{sub} was not created");
        }
        // The sidecar deletes `chroma_db_v2` on startup (#59); creating it here
        // would put it back on every launch.
        assert!(!root.join("chroma_db_v2").exists());

        std::fs::remove_dir_all(&root).ok();
    }
}
