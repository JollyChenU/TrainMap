import type { CityResult, ReachabilityResponse, StationResult } from "./api/types";

export type DisplayMode = "city" | "station";
export type ResultView = "single" | "intersection" | "union";

export interface OriginOption {
  id: string;
  name: string;
  selected: boolean;
}

interface OriginRoute {
  origin: OriginOption;
  query: ReachabilityResponse;
  station: StationResult;
}

export interface ComparedPlace {
  id: string;
  name: string;
  city: CityResult;
  stationId?: string;
  arrival: string;
  routes: OriginRoute[];
}

export function comparePlaces(
  sources: { origin: OriginOption; query: ReachabilityResponse }[],
  display: DisplayMode,
  intersection: boolean,
): ComparedPlace[] {
  const places = new Map<string, ComparedPlace>();
  for (const { origin, query } of sources) {
    for (const city of query.cities) {
      for (const station of city.stations) {
        const stationId = display === "station" ? station.station_id : undefined;
        const id = stationId ?? city.city_id;
        let place = places.get(id);
        if (!place) {
          place = {
            id, name: stationId ? station.station_name : city.city_name, stationId,
            city: { ...city, stations: [], direct_available: false },
            arrival: station.arrival_at, routes: [],
          };
          places.set(id, place);
        }
        const previous = place.routes.at(-1);
        if (previous?.origin.id !== origin.id) {
          place.routes.push({ origin, query, station });
        } else if (station.arrival_at < previous.station.arrival_at) {
          previous.station = station;
        }
        place.city.direct_available ||= station.direct_available;
        if (!place.city.stations.some(item => item.station_id === station.station_id)) {
          place.city.stations.push(station);
        }
      }
    }
  }
  return [...places.values()]
    .filter(place => !intersection || place.routes.length === sources.length)
    .map(place => {
      const arrivals = place.routes.map(route => route.station.arrival_at).sort();
      place.arrival = (intersection ? arrivals.at(-1) : arrivals[0])!;
      return place;
    })
    .sort((left, right) => left.arrival.localeCompare(right.arrival));
}
