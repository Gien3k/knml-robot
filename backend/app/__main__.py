import logging

import uvicorn

from .config import settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-14s %(message)s",
        datefmt="%H:%M:%S",
    )
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        log_level="warning",
        ws_ping_interval=20,
    )


if __name__ == "__main__":
    main()
