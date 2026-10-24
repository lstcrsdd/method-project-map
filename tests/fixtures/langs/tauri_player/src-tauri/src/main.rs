//! The Rust side of the desktop app: commands the web UI invokes.

#[tauri::command]
fn connect_stream(server: String) -> bool {
    !server.is_empty()
}

#[tauri::command]
async fn player_status() -> String {
    "disconnected".to_string()
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![connect_stream, player_status])
        .run(tauri::generate_context!())
        .expect("error while running the app");
}
