//! Tauri shell for PDFusion. Spawns + supervises the Python sidecar, then
//! exposes its connection info (port + bearer token) to the React frontend.

mod sidecar;

use std::path::{Path, PathBuf};

use serde::Serialize;
use tauri::{Emitter, Manager, RunEvent};
use tauri_plugin_log::{RotationStrategy, Target, TargetKind};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_window_state::{AppHandleExt, StateFlags};

/// Emitted when a *second* launch hands this instance a PDF to open, instead
/// of starting a second app (and a second sidecar) of its own.
const OPEN_FILE_EVENT: &str = "pdfusion://open-file";

#[derive(Debug, Clone, Serialize)]
struct SidecarInfoDto {
    port: u16,
    token: String,
}

#[derive(Debug, Clone, Serialize)]
struct SidecarStatus {
    ready: bool,
    info: Option<SidecarInfoDto>,
    error: Option<String>,
}

#[tauri::command]
fn sidecar_info() -> SidecarStatus {
    match sidecar::current() {
        Some(handle) => SidecarStatus {
            ready: true,
            info: Some(SidecarInfoDto {
                port: handle.info.port,
                token: handle.info.token.clone(),
            }),
            error: None,
        },
        None => SidecarStatus {
            ready: false,
            info: None,
            error: Some("Sidecar has not started yet".into()),
        },
    }
}

/// Vet a path before handing it to the shell.
///
/// The extension check is the security boundary, not a convenience: both
/// commands below are reachable from the webview, and `open_path` bottoms out
/// in `ShellExecute`, which will happily launch an `.exe`/`.bat`/`.lnk`. The
/// app only ever needs to open PDFs, so anything else is refused here rather
/// than trusting that no injected script ever reaches `invoke` — the chat
/// panel renders model-authored markdown, and a CSP is a second line of
/// defence, not a substitute for validating what a command is handed.
///
/// The existence check is a UX one: Explorer and the default-app launcher both
/// fail opaquely on a missing path, so we return a message the UI can show.
fn check_pdf_path(path: &str) -> Result<(), String> {
    let p = Path::new(path);
    if !p.extension().is_some_and(|e| e.eq_ignore_ascii_case("pdf")) {
        return Err("Only PDF files can be opened from PDFusion.".to_string());
    }
    if !p.exists() {
        return Err(format!(
            "This file is no longer on disk: {path}. Translate the document again."
        ));
    }
    Ok(())
}

/// Open a file with whatever the OS has registered for its type — for a
/// translated PDF, the user's default PDF reader. See `check_pdf_path` for why
/// this is an app command rather than the `opener` plugin's JS `openPath`.
#[tauri::command]
fn open_path_in_default_app(app: tauri::AppHandle, path: String) -> Result<(), String> {
    check_pdf_path(&path)?;
    app.opener()
        .open_path(path, None::<&str>)
        .map_err(|e| e.to_string())
}

/// Reveal a file in the OS file manager, selecting it — Explorer on Windows,
/// Finder on macOS, whatever owns `org.freedesktop.FileManager1` on Linux.
#[tauri::command]
fn reveal_path_in_file_manager(app: tauri::AppHandle, path: String) -> Result<(), String> {
    check_pdf_path(&path)?;
    app.opener()
        .reveal_item_in_dir(Path::new(&path))
        .map_err(|e| e.to_string())
}

/// The first `.pdf` in this process's own command line, if any.
///
/// The single-instance hook below covers *later* launches; this covers the
/// first one, so `pdfusion.exe paper.pdf` opens the document either way. Only
/// the extension is checked here — the sidecar validates the file itself when
/// the frontend asks it to load one.
#[tauri::command]
fn initial_file_argument() -> Option<String> {
    first_pdf_argument(std::env::args().skip(1))
}

fn first_pdf_argument<I: IntoIterator<Item = String>>(args: I) -> Option<String> {
    args.into_iter().find(|a| {
        !a.starts_with('-')
            && Path::new(a)
                .extension()
                .is_some_and(|e| e.eq_ignore_ascii_case("pdf"))
    })
}

/// Open the folder the sidecar and the shell log into.
///
/// The *folder*, not a specific file: `shell.log` is written unconditionally
/// on every launch (`tauri_plugin_log`, registered below), but whether
/// `app.log` exists yet still depends on the Python side having logged
/// something, and `ensure_appdata_layout` only guarantees the directory.
/// Takes no argument — the path is derived here, so there is nothing for a
/// caller to point somewhere else.
#[tauri::command]
fn open_logs_folder(app: tauri::AppHandle) -> Result<(), String> {
    let dir: PathBuf = sidecar::appdata_dir().join("logs");
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    app.opener()
        .open_path(dir.to_string_lossy().to_string(), None::<&str>)
        .map_err(|e| e.to_string())
}

/// Relaunch the app.
///
/// This is what the boot screen's Retry does. The sidecar handle is a
/// `OnceCell` set exactly once per process, so "spawn it again" would mean
/// making that lifecycle re-entrant — two spawn paths, a window where two
/// Python processes share one `pdfusion.db` and `vectors` store. A relaunch re-runs the existing
/// startup path unchanged, which is the whole point of retrying.
///
/// Everything an ordinary exit does has to be done *here*, explicitly.
/// `AppHandle::restart` only routes through `RunEvent::ExitRequested` when
/// it is called off the main thread; a synchronous command handler runs on
/// it, so restart takes the `cleanup_before_exit` branch instead — which
/// clears resource tables and hides windows, and nothing else. Neither the
/// `ExitRequested` arm at the bottom of this file nor the window-state
/// plugin's own `RunEvent::Exit` hook fires. Without the two calls below, a
/// Retry would leak the job temp dir and silently discard any resize the
/// user made before clicking it.
#[tauri::command]
fn restart_app(app: tauri::AppHandle) {
    if let Some(handle) = sidecar::current() {
        handle.shutdown();
    }
    // `StateFlags::default()` is what `Builder::default()` above registers
    // with, so this writes exactly the state an exit would have written.
    let _ = app.save_window_state(StateFlags::default());
    sidecar::cleanup_translate_temp_dirs();
    app.restart();
}

/// The JSC option name. Exposed to us as an environment variable because that
/// is the only way to reach JavaScriptCore's options from outside the engine —
/// WebKitGTK's `WebKitSettings` covers web-facing features, not JIT internals.
const RELAXED_SIMD_ENV: &str = "JSC_useWasmRelaxedSIMD";

/// Whether the shell should supply the option itself.
///
/// Split out from the `set_var` below only so an explicit setting can be
/// shown to win: `JSC_useWasmRelaxedSIMD=0 PDFusion` has to keep disabling the
/// workaround, or someone hitting a WebKit bug in this code path would have no
/// way out short of rebuilding.
fn relaxed_simd_needs_default(existing: Option<&std::ffi::OsStr>) -> bool {
    existing.is_none()
}

/// Let pdf.js decode JPEG 2000 with its WASM decoder rather than a JS fallback.
///
/// pdf.js ships an `openjpeg.wasm` built with the WebAssembly Relaxed SIMD
/// proposal. WebKitGTK carries the feature but defaults it off, so the module
/// fails to *parse* — and pdf.js reacts by quietly loading
/// `openjpeg_nowasm_fallback.js` instead. Nothing surfaces: pages still render,
/// roughly 6x slower (2553 ms against 432 ms per page, measured), which reads
/// as "this document is heavy" rather than as a defect (#74).
///
/// Only WebKitGTK needs this. Relaxed SIMD is on by default in Chrome 114+, so
/// WebView2 already has it, and in Safari 18.4+, so WKWebView should too. The
/// cfg spelling matches how `sidecar.rs` names this platform, and is right for
/// the same reason: WebKitGTK is the engine on every non-macOS unix.
///
/// Delete all of this once WebKitGTK enables Relaxed SIMD by default — at which
/// point it is already a no-op, so there is no hurry and no way to notice.
#[cfg(all(not(windows), not(target_os = "macos")))]
fn enable_wasm_relaxed_simd() {
    if relaxed_simd_needs_default(std::env::var_os(RELAXED_SIMD_ENV).as_deref()) {
        // Safe here and nowhere later: `set_var` races any thread reading the
        // environment, and this runs before Tauri has built anything, on the
        // one thread that exists. The web process reads it when WebKitGTK
        // forks it, which is why setting our own environment reaches it at all.
        std::env::set_var(RELAXED_SIMD_ENV, "1");
    }
}

#[cfg(any(windows, target_os = "macos"))]
fn enable_wasm_relaxed_simd() {}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Before the builder, not inside `setup`: by then the webview exists and
    // the option has already been read. See the function's own note on why
    // that ordering is also what makes the `set_var` sound.
    enable_wasm_relaxed_simd();

    tauri::Builder::default()
        // Must come first: a second launch has to be turned away before the
        // rest of the app builds. Two windows means two sidecars sharing one
        // `vectors` store and one set of SQLite WAL files.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
            // Hand over a document the second launch was asked to open, so
            // "open with PDFusion" on a running app still does something.
            if let Some(path) = first_pdf_argument(argv.into_iter().skip(1)) {
                let _ = app.emit(OPEN_FILE_EVENT, path);
            }
        }))
        // Replaces the old bare `env_logger::try_init()`, which only ever
        // wrote to a terminal a release build doesn't have
        // (`windows_subsystem = "windows"`) — nothing reached a file (#26).
        // Stdout keeps `pnpm tauri dev`'s terminal output unchanged; Folder
        // writes `shell.log` next to the Python sidecar's `app.log`, in the
        // same directory `open_logs_folder` below already opens. The plugin
        // creates that directory itself if it doesn't exist yet.
        .plugin(
            tauri_plugin_log::Builder::new()
                .target(Target::new(TargetKind::Stdout))
                .target(Target::new(TargetKind::Folder {
                    path: sidecar::appdata_dir().join("logs"),
                    file_name: Some("shell".into()),
                }))
                .level(log::LevelFilter::Info)
                .max_file_size(5 * 1024 * 1024) // 5 MB, mirrors app.log's cap
                .rotation_strategy(RotationStrategy::KeepSome(5)) // + 5 backups
                .build(),
        )
        // Restores size/position/maximized state from the previous run, and
        // saves them on exit. Replaces the `GUISettings.window_width/height`
        // fields, which nothing ever read.
        .plugin(tauri_plugin_window_state::Builder::default().build())
        // `shell` and `fs` were registered but never imported by the webview:
        // Save/Open/Reveal go through the app commands below, and PDFs are
        // streamed from the sidecar over HTTP rather than read from disk by
        // the frontend. Registering them only widened what an injected script
        // could reach.
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            sidecar_info,
            open_path_in_default_app,
            reveal_path_in_file_manager,
            initial_file_argument,
            open_logs_folder,
            restart_app
        ])
        .setup(|app| {
            // Pre-create %LOCALAPPDATA%\PDFusion\ and the subdirs every
            // sidecar subsystem writes to (logs, translated_pdfs, caches,
            // chroma). Done synchronously before the sidecar spawn so the
            // Python side never races on first-run mkdirs across subsystems.
            sidecar::ensure_appdata_layout();

            let handle = app.handle().clone();
            let spawn_handle = handle.clone();
            tauri::async_runtime::spawn(async move {
                match sidecar::spawn(spawn_handle).await {
                    Ok(info) => {
                        log::info!("Sidecar ready on port {}", info.port);
                        let _ = handle.emit("sidecar://ready", SidecarInfoDto {
                            port: info.port,
                            token: info.token,
                        });
                    }
                    Err(e) => {
                        log::error!("Sidecar failed to start: {e}");
                        let _ = handle.emit("sidecar://error", e.to_string());
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app_handle, event| {
            if let RunEvent::ExitRequested { .. } = event {
                if let Some(handle) = sidecar::current() {
                    handle.shutdown();
                }
                // Wipe any %TEMP%\pdfusion-translate-* dirs left behind by
                // this session. The sidecar's per-job cleanup handles the
                // common case (previous dir wiped when the next translation
                // starts), but the *last* run's dir survives until exit —
                // this is where it gets removed.
                sidecar::cleanup_translate_temp_dirs();
            }
        });
}

#[cfg(test)]
mod tests {
    use super::{first_pdf_argument, relaxed_simd_needs_default};
    use std::ffi::OsStr;

    fn args(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    // Exercised through the pure half rather than the real environment: a test
    // that called `set_var` would leak the option into every other test in this
    // binary, and they all share one process.
    #[test]
    fn relaxed_simd_is_supplied_only_when_the_environment_is_silent() {
        assert!(relaxed_simd_needs_default(None));
    }

    #[test]
    fn an_explicit_relaxed_simd_setting_is_left_alone() {
        // Including "0". Someone turning the workaround off is the case that
        // matters — overwriting it would strand them with no way back (#74).
        assert!(!relaxed_simd_needs_default(Some(OsStr::new("0"))));
        assert!(!relaxed_simd_needs_default(Some(OsStr::new("1"))));
    }

    #[test]
    fn finds_the_document_a_launch_was_given() {
        assert_eq!(
            first_pdf_argument(args(&["D:\\Papers\\attention.pdf"])),
            Some("D:\\Papers\\attention.pdf".to_string())
        );
    }

    #[test]
    fn matches_the_extension_case_insensitively() {
        assert_eq!(
            first_pdf_argument(args(&["paper.PDF"])),
            Some("paper.PDF".to_string())
        );
    }

    #[test]
    fn ignores_flags_and_non_pdf_arguments() {
        assert_eq!(first_pdf_argument(args(&["--debug", "notes.txt"])), None);
        assert_eq!(first_pdf_argument(args(&[])), None);
    }

    #[test]
    fn takes_the_first_document_when_several_are_passed() {
        assert_eq!(
            first_pdf_argument(args(&["--debug", "a.pdf", "b.pdf"])),
            Some("a.pdf".to_string())
        );
    }
}
