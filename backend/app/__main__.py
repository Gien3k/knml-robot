"""Start serwera: python -m app (w kontenerze port 8000)."""
import logging

import uvicorn


class _Server(uvicorn.Server):
    def handle_exit(self, sig, frame) -> None:
        # najpierw koła, potem reszta zamykania (SIGTERM z `docker stop`, Ctrl+C)
        try:
            from .main import emergency_stop
            emergency_stop()
        finally:
            super().handle_exit(sig, frame)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)-11s %(message)s",
                        datefmt="%H:%M:%S")
    config = uvicorn.Config("app.main:app", host="0.0.0.0", port=8000, log_level="warning",
                            ws_ping_interval=20, timeout_graceful_shutdown=2)
    _Server(config).run()


if __name__ == "__main__":
    main()
