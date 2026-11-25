import type { ApiContext, DashboardSnapshot, QHomeConfig, QHomeMeta } from "../types/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(`API ${response.status} ${response.statusText}${detail ? `: ${detail}` : ""}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  getMeta: () => request<QHomeMeta>("/api/meta"),
  getConfig: () => request<QHomeConfig>("/api/config"),
  getState: () => request<DashboardSnapshot>("/api/state"),
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
