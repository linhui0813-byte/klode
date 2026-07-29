"""Enable `python3 -m lodlib …` as an alias for the `lib` console script."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
