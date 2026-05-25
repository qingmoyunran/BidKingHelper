mod pipe_listener;

pub fn run() {
    tauri::Builder::default()
        .plugin(pipe_listener::PipeListenerPlugin)
        .plugin(tauri_plugin_opener::init())
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
