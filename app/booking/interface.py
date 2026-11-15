"""The booking provider interface.

The assistant only ever talks to a `BookingProvider`. Today the only
implementation is the mock in `app/booking/mock/`; a real restaurant-booking
API would be another implementation of this class, with nothing else in the
app changing.

Everything is async, because a real provider is a network call. Results are
plain JSON-ready dicts, because they are handed to the language model as-is.
Failures raise `BookingError` with a stable `code` the assistant can reason
about and a `retryable` flag.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BookingError(Exception):
    """A failure the caller should handle, not a bug.

    Codes used: unknown_venue, unknown_booking, invalid_date, invalid_time,
    invalid_party_size, date_in_past, too_far_ahead, too_late, invalid_slot,
    party_too_large, slot_unavailable, policy_acknowledgement_required,
    fee_acknowledgement_required, not_modifiable, upstream_timeout.
    """

    def __init__(self, code: str, message: str, retryable: bool = False, **extra):
        super().__init__(message)
        self.code, self.message, self.retryable, self.extra = code, message, retryable, extra

    def as_dict(self) -> dict:
        return {"error": self.code, "message": self.message, "retryable": self.retryable, **self.extra}


class BookingProvider(ABC):
    """Dates are YYYY-MM-DD and times HH:MM (24h), local to London."""

    @abstractmethod
    async def search(self, *, date: str, party_size: int, time: str | None = None, area: str | None = None,
                     cuisine: str | None = None, keywords: str | None = None, dietary: str | None = None,
                     max_price: str | None = None, step_free: bool | None = None, limit: int = 6) -> dict:
        """Restaurants matching the filters, each with available times near `time`."""

    @abstractmethod
    async def venue(self, venue_id: str) -> dict:
        """Full details: opening hours, dietary options, policies, notes."""

    @abstractmethod
    async def availability(self, venue_id: str, date: str, party_size: int, time: str | None = None) -> dict:
        """Available seating times on a date, closest to `time` if given."""

    @abstractmethod
    async def book(self, *, venue_id: str, date: str, time: str, party_size: int, member_id: str,
                   guest_name: str, idempotency_key: str, special_requests: str | None = None,
                   policy_acknowledged: bool = False) -> dict:
        """Create a reservation. Repeating a call with the same idempotency_key returns the
        original booking (with "replayed": true) instead of creating another, so a retry
        after a timeout is always safe."""

    @abstractmethod
    async def bookings_for_member(self, member_id: str) -> list[dict]:
        """All of a member's bookings, any status, by date and time."""

    @abstractmethod
    async def modify(self, reference: str, *, date: str | None = None, time: str | None = None,
                     party_size: int | None = None, special_requests: str | None = None) -> dict:
        """Change a booking in place. If the new slot isn't available, the original is unchanged."""

    @abstractmethod
    async def cancel(self, reference: str, accept_fee: bool = False) -> dict:
        """Cancel a booking. Raises fee_acknowledgement_required, with the amount, if a
        late-cancellation charge applies and accept_fee is false."""
