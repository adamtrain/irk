"""irk: watch IRC channels and print the links you care about."""


class IrkError(Exception):
    """A problem worth stopping for, with a message fit to show the user as-is."""
