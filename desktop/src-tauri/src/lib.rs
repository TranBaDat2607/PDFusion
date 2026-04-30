//! Tauri shell for PDFusion. Spawns + supervises the Python sidecar, then
//! exposes its connection info (port + bearer token) to the React frontend.

mod sidecar;

use serde::Serialize;
use tauri::{Emitter, RunEvent};

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let _ = env_logger::try_init();

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_fs::init())
        .invoke_handler(tauri::generate_handler![sidecar_info])
        .setup(|app| {
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                match sidecar::spawn().await {
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
            }
        });
}
