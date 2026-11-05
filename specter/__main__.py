"""``python -m specter`` keeps working; it is ``python -m docintel`` under the old name."""

from docintel.__main__ import main

if __name__ == "__main__":
    main(prog="specter")
