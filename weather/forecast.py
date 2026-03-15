"""
Fetches probabilistic ensemble weather forecasts from Open-Meteo.

Uses the ECMWF IFS ensemble model (51 members) to get temperature
and precipitation distributions for a specific city and date.

Free API, no key required.
"""
import time
from datetime import date, timedelta
from functools import lru_cache
from typing import Optional
import requests
from loguru import logger

GEOCODING_API = "https://geocoding-api.open-meteo.com/v1/search"
ENSEMBLE_API = "https://ensemble-api.open-meteo.com/v1/ensemble"

# Cache geocoding results to avoid repeated API calls
_geocode_cache: dict[str, Optional[tuple[float, float]]] = {}


class WeatherForecaster:
    """Fetches ensemble weather forecasts from Open-Meteo for specific locations and dates."""

    def __init__(
        self,
        models: list[str] = None,
        session: Optional[requests.Session] = None,
    ):
        self.models = models or ["ecmwf_ifs04"]
        self.session = session or requests.Session()

    def get_ensemble(
        self,
        city: str,
        target_date: date,
        variable: str = "temperature_max",
    ) -> Optional[list[float]]:
        """
        Returns a list of ensemble member values for the given city, date, and variable.

        Args:
            city: City name (e.g., "Shanghai")
            target_date: Target date
            variable: "temperature_max" | "temperature_min" | "precipitation" | "snowfall"

        Returns:
            List of float values (one per ensemble member), or None if unavailable.
        """
        coords = self._geocode(city)
        if not coords:
            logger.warning(f"Could not geocode city: {city!r}")
            return None

        lat, lon = coords
        logger.debug(f"Geocoded {city!r} → lat={lat}, lon={lon}")

        # Map variable to Open-Meteo API variable name
        api_var = {
            "temperature_max": "temperature_2m_max",
            "temperature_min": "temperature_2m_min",
            "precipitation": "precipitation_sum",
            "snowfall": "snowfall_sum",
        }.get(variable, "temperature_2m_max")

        members = self._fetch_ensemble(lat, lon, target_date, api_var)
        if members is None:
            return None

        logger.debug(f"Got {len(members)} ensemble members for {city} on {target_date}")
        return members

    def _geocode(self, city: str) -> Optional[tuple[float, float]]:
        """Convert city name to (latitude, longitude) using Open-Meteo geocoding."""
        if city in _geocode_cache:
            return _geocode_cache[city]

        try:
            resp = self.session.get(
                GEOCODING_API,
                params={"name": city, "count": 1, "language": "en", "format": "json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                _geocode_cache[city] = None
                return None

            result = results[0]
            coords = (float(result["latitude"]), float(result["longitude"]))
            _geocode_cache[city] = coords
            time.sleep(0.1)
            return coords

        except Exception as e:
            logger.error(f"Geocoding error for {city!r}: {e}")
            _geocode_cache[city] = None
            return None

    def _fetch_ensemble(
        self,
        lat: float,
        lon: float,
        target_date: date,
        api_variable: str,
    ) -> Optional[list[float]]:
        """Fetch ensemble forecast values for a specific location and date."""
        # Request a range around the target date to ensure we hit it
        start = target_date
        end = target_date

        for model in self.models:
            members = self._fetch_model_ensemble(lat, lon, start, end, api_variable, model)
            if members:
                return members

        return None

    def _fetch_model_ensemble(
        self,
        lat: float,
        lon: float,
        start: date,
        end: date,
        api_variable: str,
        model: str,
    ) -> Optional[list[float]]:
        """Fetch ensemble data from Open-Meteo for a specific model."""
        try:
            resp = self.session.get(
                ENSEMBLE_API,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": api_variable,
                    "models": model,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "timezone": "UTC",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            daily = data.get("daily", {})
            times = daily.get("time", [])

            if not times:
                logger.warning(f"No data returned for {model} at {lat},{lon}")
                return None

            # Find the index for our target date
            target_str = start.isoformat()
            if target_str not in times:
                logger.warning(f"Target date {target_str} not in forecast: {times}")
                return None

            date_idx = times.index(target_str)

            # Collect all ensemble member values for this date
            member_values = []
            for key, values in daily.items():
                if key == "time":
                    continue
                # Keys look like "temperature_2m_max_member01", "temperature_2m_max_member02", etc.
                if api_variable in key and isinstance(values, list) and len(values) > date_idx:
                    val = values[date_idx]
                    if val is not None:
                        member_values.append(float(val))

            if not member_values:
                # Some models return all members as a single flat response
                # Try the base variable key
                base_vals = daily.get(api_variable, [])
                if base_vals and len(base_vals) > date_idx:
                    val = base_vals[date_idx]
                    if val is not None:
                        member_values = [float(val)]

            if not member_values:
                logger.warning(f"No member values found for {api_variable} on {start}")
                return None

            time.sleep(0.3)  # be nice to the free API
            return member_values

        except Exception as e:
            logger.error(f"Open-Meteo ensemble error for model={model}: {e}")
            return None
