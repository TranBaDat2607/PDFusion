//! Tauri shell for PDFusion. Spawns + supervises the Python sidecar, then
//! exposes its connection info (port + bearer token) to the React frontend.

mod sidecar;

use std::path::{Path, PathBuf};

use serde::Serialize;
use tauri::{Emitter, Manager, RunEvent};
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

/// Reveal a file in the OS file manager (Explorer on Windows), selecting it.
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

/// Open the folder the sidecar logs into.
///
/// The *folder*, not `app.log`: whether that file exists depends on how the
/// sidecar was launched (see #26), and `ensure_appdata_layout` guarantees the
/// directory. Takes no argument — the path is derived here, so there is
/// nothing for a caller to point somewhere else.
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
/// Python processes share one `chroma_db`. A relaunch re-runs the existing
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let _ = env_logger::try_init();

    tauri::Builder::default()
        // Must come first: a second launch has to be turned away before the
        // rest of the app builds. Two windows means two sidecars sharing one
        // `chroma_db` and one set of SQLite WAL files.
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
    use super::first_pdf_argument;

    fn args(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
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
