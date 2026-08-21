"""CLI entrypoint for recorded-livestream offline benchmarking."""

import sys

from src.benchmark import main

if __name__ == "__main__":
    sys.exit(main())
