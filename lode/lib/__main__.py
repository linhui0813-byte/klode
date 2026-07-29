"""Enable `python3 -m lode.lib …` as an alias for the `lode` console script."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
