"""Allow ``python -m qsltracker`` as an alias for the console script."""

from qsltracker.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
