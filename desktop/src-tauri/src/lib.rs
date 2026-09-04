//! Tauri shell for PDFusion. Spawns + supervises the Python sidecar, then
//! exposes its connection info (port + bearer token) to the React frontend.

mod sidecar;

use std::path::Path;

use serde::Serialize;
use tauri::{Emitter, RunEvent};
use tauri_plugin_opener::OpenerExt;

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
/// than trusting that no injected script (the CSP is still `null`, and the
/// chat panel renders model-authored markdown) ever reaches `invoke`.
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let _ = env_logger::try_init();

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![
            sidecar_info,
            open_path_in_default_app,
            reveal_path_in_file_manager
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
