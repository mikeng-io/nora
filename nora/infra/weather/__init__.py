"""Current-weather grounding — a `WeatherProvider` port, HK Observatory today."""

from .hko import HkoWeatherProvider

__all__ = ["HkoWeatherProvider"]
