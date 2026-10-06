"""python -m seed : wipe the database contents and load the dummy data (Discover NA and Acme Insurance)."""

import sys

from app.core.config import get_settings
from app.core.db import new_session
from seed.load import load


def main() -> None:
    if get_settings().env == "production":
        sys.exit("Refusing to seed a production database.")
    with new_session() as db:
        load(db)
    print(
        "Seeded Discover NA: 4 BUs, 13 users, 18 demands, 6 open escalations, 2 interviews,"
        " 64 rate card rows."
    )
    print(
        "Seeded Acme Insurance: 3 BUs, 7 people (Sanjay shared with Discover),"
        " 6 demands, 1 interview, 15 rate card rows. Administrators: Anil (Discover), Rosa (Acme)."
    )


if __name__ == "__main__":
    main()
