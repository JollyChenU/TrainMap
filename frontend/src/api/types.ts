export interface City {
  city_id: string;
  name: string;
  longitude: number;
  latitude: number;
  station_count: number;
}

export interface Station {
  station_id: string;
  city_id: string;
  name: string;
  longitude: number;
  latitude: number;
}

export interface DataStatus {
  source_kind: "demo" | "external";
  source_name: string;
  fetched_at: string;
  coverage_status: "complete_demo" | "partial" | "complete";
  note: string;
  version: string;
  valid_from: string | null;
  valid_until: string | null;
  trip_count: number;
  station_count: number;
  rejected_count: number;
  unknown_city_count: number;
  source_url: string;
  license: string;
}

export interface RefreshJob {
  state: "idle" | "running" | "completed" | "failed";
  message: string;
}

export interface QueryPayload {
  origin_city_id: string;
  origin_station_ids: string[];
  start_at: string;
  end_at: string;
  train_types: string[];
  transfers_enabled: boolean;
  max_transfers: number;
  min_transfer_minutes: number;
}

export interface LegResult {
  train_code: string;
  train_type: string;
  from_station_id: string;
  from_station_name: string;
  to_station_id: string;
  to_station_name: string;
  departure_at: string;
  arrival_at: string;
  service_date: string | null;
}

export interface Verification {
  train_code: string;
  queried_at: string;
  service_date: string;
  note: string;
  stops: { station_name: string; arrive_time: string; start_time: string; arrive_day_diff: string }[];
}

export interface StationResult {
  station_id: string;
  station_name: string;
  city_id: string;
  city_name: string;
  longitude: number;
  latitude: number;
  arrival_at: string;
  transfers: number;
  direct_available: boolean;
  legs: LegResult[];
}

export interface CityResult {
  city_id: string;
  city_name: string;
  longitude: number;
  latitude: number;
  earliest_arrival_at: string;
  direct_available: boolean;
  stations: StationResult[];
}

export interface ReachabilityResponse {
  query_id: string;
  generated_at: string;
  origin_city_id: string;
  origin_city_name: string;
  origin_longitude: number;
  origin_latitude: number;
  origin_station_ids: string[];
  data_status: DataStatus;
  request: QueryPayload;
  cities: CityResult[];
  reachable_city_count: number;
  reachable_station_count: number;
}
