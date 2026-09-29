"""Canonical event topic names shared between publishers and subscribers.

Keeping these in one place prevents the tickets-service (publisher) and the
notifications-service (subscriber) from drifting apart.
"""

TICKET_CREATED = "ticket.created"
TICKET_UPDATED = "ticket.updated"
TICKET_COMMENTED = "ticket.commented"

ALL_TICKET_TOPICS = (TICKET_CREATED, TICKET_UPDATED, TICKET_COMMENTED)
