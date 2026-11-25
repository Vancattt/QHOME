import type { ApiContext, ConfigSaveResult, DashboardSnapshot, QHomeConfig, QHomeMeta, SerialPortInfo } from "../types/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    let detail = body;
    try {
      const parsed: unknown = JSON.parse(body);
      if (parsed && typeof parsed === "object" && "errors" in parsed && Array.isArray(parsed.errors)) {
        detail = parsed.errors.map(String).join("; ");
      } else if (parsed && typeof parsed === "object" && "error" in parsed && typeof parsed.error === "string") {
        detail = parsed.error;
      }
    } catch { /* the server can return a plain-text error body */ }
    throw new Error(`API ${response.status} ${response.statusText}${detail ? `: ${detail}` : ""}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  getMeta: () => request<QHomeMeta>("/api/meta"),
  getConfig: () => request<QHomeConfig>("/api/config"),
  getState: () => request<DashboardSnapshot>("/api/state"),
  getPorts: () => request<SerialPortInfo[]>("/api/ports"),
  saveConfig: (config: QHomeConfig) => request<ConfigSaveResult>("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  }),
  async getDashboard(): Promise<ApiContext> {
    const [meta, config, snapshot] = await Promise.all([this.getMeta(), this.getConfig(), this.getState()]);
    return { meta, config, snapshot };
  },
  command(id: string, action: string, value?: unknown) {
    return request<{ ok: boolean }>("/api/cmd", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, action, value }),
    });
  },
  simulateInput(board: string, pin: string | number | undefined, value: number) {
    return request<{ ok: boolean }>("/api/sim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ board, pin, value }),
    });
  },
};
