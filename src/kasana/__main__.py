"""Top-level Kasana command entry point."""

from kasana.cli import console_main, main

__all__ = ["console_main", "main"]

if __name__ == "__main__":  # pragma: no cover
    console_main()
