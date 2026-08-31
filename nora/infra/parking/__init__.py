"""Real-time HK parking vacancy — one provider, behind the `parking_status` tool."""
from .client import ParkingClient, RESOURCE_URL, INDEX_URL

__all__ = ["ParkingClient", "RESOURCE_URL", "INDEX_URL"]