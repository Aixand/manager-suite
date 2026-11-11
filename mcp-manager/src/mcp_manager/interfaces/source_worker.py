"""Detached source and installation worker entry point."""

import sys
from ..jobs.sources import run


def main(argv=None):
    # Import the facade only in the executable boundary, not the job service.
    from ..manager import Manager

    home, identifier = sys.argv[1:] if argv is None else argv
    run(Manager(home)._source_service.backend(), identifier)


if __name__ == "__main__":
    main()
