import type {
  City,
  DataStatus,
  QueryPayload,
  ReachabilityResponse,
  RefreshJob,
  Verification,
  Station,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((item) => item.msg).join("；")
          : `请求失败（${response.status}）`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  cities: () => request<City[]>("/api/cities"),
  stations: (cityId: string) =>
    request<Station[]>(`/api/cities/${encodeURIComponent(cityId)}/stations`),
  dataStatus: () => request<DataStatus>("/api/data-status"),
  refresh: () =>
    request<RefreshJob>("/api/data-status/refresh", { method: "POST" }),
  refreshProgress: () => request<RefreshJob>("/api/data-status/refresh"),
  result: (id: string) => request<ReachabilityResponse>(`/api/results/${encodeURIComponent(id)}`),
  verify: (id: string, code: string, day: string) =>
    request<Verification>(`/api/results/${encodeURIComponent(id)}/verify?${new URLSearchParams({
      train_code: code, service_date: day,
    })}`, { method: "POST" }),
  reachability: (payload: QueryPayload) =>
    request<ReachabilityResponse>("/api/reachability", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  compare: (payloads: QueryPayload[]) =>
    request<ReachabilityResponse[]>("/api/reachability/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payloads),
    }),
};
