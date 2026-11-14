"""`python -m findex ...` — same as the installed `findex` command."""

from findex.cli import app

# The guard matters for the `spawn` start method: each worker process re-imports
# the main module (as `__mp_main__`); without it every worker would start the CLI.
if __name__ == "__main__":
    app(prog_name="findex")
