"""Run database schema changes before starting the web application."""

from . import db


def main() -> None:
    db.init_db()


if __name__ == "__main__":
    main()
