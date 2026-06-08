from __future__ import annotations

import sys

from ykm.cli import main as ykm_main


def main() -> None:
    sys.argv.insert(1, "curator-dry-run")
    ykm_main()


if __name__ == "__main__":
    main()
