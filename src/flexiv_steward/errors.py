"""Shared application errors."""


class ProjectError(RuntimeError):
    """An operation could not be completed safely for the selected project."""
