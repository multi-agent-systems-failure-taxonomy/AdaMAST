"""Allow ``python -m adamast`` to work as the CLI."""

from adamast.learning.vendor.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
