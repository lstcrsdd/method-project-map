import { invoke } from "@tauri-apps/api/core";

/** The desktop UI asks the Rust side of the app to connect. */
export async function connect(server: string): Promise<boolean> {
  return invoke<boolean>("connect_stream", { server });
}

export const status = () => invoke<string>("player_status");
