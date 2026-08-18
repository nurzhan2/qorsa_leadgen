"""Telegram order-channel monitor.

Reads the public message *stream* of channels/chats the account already
belongs to, looks for order-style posts ("нужен сайт", "ищу разработчика",
...), and forwards matching leads to the qorsa_leadgen Java core via
POST /api/v1/companies/ingest.

Explicitly out of scope: listing channel participants/subscribers, or
scraping comment threads. Only the post stream itself is read. See
README.md for the full rationale.
"""
