"""Data-access layer (raw SQL over psycopg).

These modules intentionally contain no ORM models. The PostgreSQL schema is the
authoritative definition (see ``database/schema.sql``); this layer issues
parameterized SQL against it and returns plain dictionaries.
"""
