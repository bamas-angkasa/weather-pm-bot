"""
Parses Polymarket weather market questions into structured data.

Examples handled:
  "Will the highest temperature in Shanghai be >= 16°C on March 14?"
  "Will the high temp in New York City exceed 20°C on March 15, 2026?"
  "Will Tel Aviv reach 26°C on March 16?"
  "Will it snow more than 1 inch in Chicago on March 17?"
  "Will precipitation in London exceed 10mm on March 18?"
"""
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from loguru import logger


@dataclass
class ParsedMarket:
    city: str
    target_date: date
    threshold: float
    condition: str      # ">=" | ">" | "<=" | "<" | "=="
    variable: str       # "temperature_max" | "temperature_min" | "precipitation" | "snowfall"
    unit: str           # "celsius" | "fahrenheit" | "mm" | "inches" | "cm"


# Month name → number mapping
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Condition word/symbol → canonical operator
CONDITIONS = {
    ">=": ">=", "≥": ">=", "be at least": ">=", "at least": ">=",
    "exceed": ">", "exceeds": ">", "above": ">", "over": ">", ">": ">",
    "<=": "<=", "≤": "<=", "at most": "<=", "no more than": "<=",
    "below": "<", "under": "<", "<": "<",
    "be": ">=",  # "Will the highest temperature be 16°C" → interpret as >=
    "reach": ">=",
    "=": "==", "equal": "==",
}


class MarketParser:
    """Parses weather market questions into structured ParsedMarket objects."""

    # Regex patterns
    DATE_PATTERN = re.compile(
        r"(?:on\s+)?(?P<month>january|february|march|april|may|june|july|august|"
        r"september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
        r"\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(?P<year>\d{4}))?",
        re.IGNORECASE,
    )

    TEMP_PATTERN = re.compile(
        r"(?P<threshold>-?\d+(?:\.\d+)?)\s*°?\s*(?P<unit>[CF]|celsius|fahrenheit)?",
        re.IGNORECASE,
    )

    PRECIP_PATTERN = re.compile(
        r"(?P<threshold>\d+(?:\.\d+)?)\s*(?P<unit>mm|cm|inches?|in\.?)",
        re.IGNORECASE,
    )

    # City extraction: "in CITY" or "for CITY" patterns
    CITY_PATTERN = re.compile(
        r"(?:in|for)\s+([A-Z][a-zA-Z\s\-]{1,40}?)(?=\s+(?:be|reach|exceed|on|above|below|>=|<=|>|<|≥|≤|will|the)|\s*\?|,|\s+\d)",
        re.IGNORECASE,
    )

    def parse(self, question: str) -> Optional[ParsedMarket]:
        """Parse a market question string into structured data. Returns None if unparseable."""
        question = question.strip()

        # Must be a weather/temperature/precipitation question
        if not self._is_weather_question(question):
            return None

        target_date = self._extract_date(question)
        if not target_date:
            logger.debug(f"No date found in: {question!r}")
            return None

        city = self._extract_city(question)
        if not city:
            logger.debug(f"No city found in: {question!r}")
            return None

        variable, unit, threshold, condition = self._extract_variable_and_threshold(question)
        if threshold is None:
            logger.debug(f"No threshold found in: {question!r}")
            return None

        return ParsedMarket(
            city=city.strip(),
            target_date=target_date,
            threshold=threshold,
            condition=condition,
            variable=variable,
            unit=unit,
        )

    def _is_weather_question(self, question: str) -> bool:
        """Check if this looks like a weather market question."""
        weather_words = [
            "temperature", "temp", "celsius", "fahrenheit", "°c", "°f",
            "snow", "snowfall", "precipitation", "rain", "rainfall",
            "high", "highest", "low", "lowest", "warm", "cold",
        ]
        q = question.lower()
        return any(w in q for w in weather_words)

    def _extract_date(self, question: str) -> Optional[date]:
        """Extract target date from question text."""
        m = self.DATE_PATTERN.search(question)
        if not m:
            return None

        month_str = m.group("month").lower()
        day = int(m.group("day"))
        year_str = m.group("year")

        month = MONTHS.get(month_str)
        if not month:
            return None

        # Determine year
        if year_str:
            year = int(year_str)
        else:
            # Infer year: use current year, but if date already passed, use next year
            today = date.today()
            year = today.year
            candidate = date(year, month, day)
            if candidate < today:
                year += 1

        try:
            return date(year, month, day)
        except ValueError:
            return None

    def _extract_city(self, question: str) -> Optional[str]:
        """Extract city name from question text."""
        # Try "in CITY" or "for CITY" pattern
        m = self.CITY_PATTERN.search(question)
        if m:
            city = m.group(1).strip()
            # Filter out noise words
            if len(city) > 1 and city.lower() not in ("the", "a", "an"):
                return city

        # Fallback: look for capitalized multi-word phrases after "in"
        fallback = re.search(r"\bin\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)", question)
        if fallback:
            return fallback.group(1).strip()

        return None

    def _extract_variable_and_threshold(self, question: str) -> tuple:
        """Returns (variable, unit, threshold, condition)."""
        q = question.lower()

        # Determine variable type
        if any(w in q for w in ["snow", "snowfall"]):
            variable = "snowfall"
        elif any(w in q for w in ["precipitation", "rain", "rainfall"]):
            variable = "precipitation"
        elif "lowest" in q or "minimum" in q or "min temp" in q or "low temp" in q:
            variable = "temperature_min"
        else:
            variable = "temperature_max"  # default: highest/max temperature

        # Determine condition
        condition = ">="  # default
        for pattern, op in CONDITIONS.items():
            if pattern in q:
                condition = op
                break

        # Extract threshold and unit
        if variable in ("snowfall", "precipitation"):
            m = self.PRECIP_PATTERN.search(question)
            if m:
                threshold = float(m.group("threshold"))
                raw_unit = m.group("unit").lower().rstrip(".")
                if raw_unit in ("inch", "inches", "in"):
                    unit = "inches"
                elif raw_unit == "cm":
                    unit = "cm"
                else:
                    unit = "mm"
                return variable, unit, threshold, condition
            return variable, "mm", None, condition
        else:
            m = self.TEMP_PATTERN.search(question)
            if m:
                threshold = float(m.group("threshold"))
                raw_unit = (m.group("unit") or "").upper()
                if raw_unit in ("F", "FAHRENHEIT"):
                    unit = "fahrenheit"
                    threshold = (threshold - 32) * 5 / 9  # convert to celsius for API
                else:
                    unit = "celsius"
                return variable, unit, threshold, condition
            return variable, "celsius", None, condition
