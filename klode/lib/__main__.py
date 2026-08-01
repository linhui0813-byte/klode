"""Enable `python3 -m klode.lib …` as an alias for the `klode` console script."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
