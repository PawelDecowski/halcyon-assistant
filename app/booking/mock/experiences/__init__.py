"""The mock experience catalogue: six categories of invented experiences beside the restaurants.

EXPERIENCES is every item from every category. See common.py for the shape of
an item and the three ways an experience is booked.
"""
from __future__ import annotations

from app.booking.mock.experiences.concerts import CONCERTS
from app.booking.mock.experiences.nature import NATURE
from app.booking.mock.experiences.stars import STARS
from app.booking.mock.experiences.stays import STAYS
from app.booking.mock.experiences.theatre import THEATRE
from app.booking.mock.experiences.watchmaking import WATCHMAKING

EXPERIENCES: list[dict] = CONCERTS + NATURE + STARS + THEATRE + WATCHMAKING + STAYS
