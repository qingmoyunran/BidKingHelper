use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader};
use std::os::windows::io::{FromRawHandle, IntoRawHandle};
use tauri::{AppHandle, Emitter, Runtime, plugin::Plugin};
use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::Storage::FileSystem::PIPE_ACCESS_DUPLEX;
use windows::Win32::System::Pipes::{
    ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe, PIPE_READMODE_BYTE, PIPE_TYPE_BYTE,
    PIPE_UNLIMITED_INSTANCES, PIPE_WAIT,
};

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct PipeMessage {
    #[serde(rename = "type")]
    pub event_type: String,
    pub room_id: String,
    pub timestamp: String,
    pub data: serde_json::Value,
}

pub struct PipeListenerPlugin;

impl<R: Runtime> Plugin<R> for PipeListenerPlugin {
    fn name(&self) -> &'static str {
        "pipe-listener"
    }

    fn initialize(
        &mut self,
        app: &AppHandle<R>,
        _config: serde_json::Value,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let app_handle = app.clone();
        std::thread::spawn(move || {
            pipe_server_loop(app_handle);
        });
        Ok(())
    }
}

fn pipe_server_loop<R: Runtime>(app: AppHandle<R>) {
    let pipe_name = wide_str(r"\\.\pipe\bidking_log");

    loop {
        let pipe = unsafe {
            CreateNamedPipeW(
                PCWSTR(pipe_name.as_ptr()),
                PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                PIPE_UNLIMITED_INSTANCES,
                65536,
                65536,
                0,
                None,
            )
        };

        if pipe.is_invalid() {
            std::thread::sleep(std::time::Duration::from_secs(1));
            continue;
        }

        let _ = unsafe { ConnectNamedPipe(pipe, None) };

        let file = unsafe { std::fs::File::from_raw_handle(pipe.0 as *mut _) };
        let mut reader = BufReader::new(file);

        loop {
            let mut line = String::new();
            match reader.read_line(&mut line) {
                Ok(0) | Err(_) => break,
                Ok(_) => {
                    let trimmed = line.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    if let Ok(msg) = serde_json::from_str::<PipeMessage>(trimmed) {
                        let _ = app.emit("game-event", &msg);
                    }
                }
            }
        }

        let file = reader.into_inner();
        let raw = file.into_raw_handle();
        let handle = HANDLE(raw as _);
        let _ = unsafe { DisconnectNamedPipe(handle) };
        let _ = unsafe { CloseHandle(handle) };
    }
}

fn wide_str(s: &str) -> Vec<u16> {
    s.encode_utf16().chain(std::iter::once(0)).collect()
}
