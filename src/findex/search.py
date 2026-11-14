"""`python -m findex.search INDEX QUERY [options]` — kept for the commands in the
Lab 2–3 README; it is now `findex search` (see `findex.cli`)."""

import sys

from findex.cli import app

if __name__ == "__main__":
    app(["search", *sys.argv[1:]], prog_name="findex")
