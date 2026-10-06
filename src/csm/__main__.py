import sys

from .hooks import dispatch

if (code := dispatch(sys.argv[1:])) is not None:
    sys.exit(code)

from .app import main

main()
