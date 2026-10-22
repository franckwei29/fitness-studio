"""Run deterministic in-app reminders independently of model task execution."""

import argparse
import logging
import signal
from threading import Event

from app.notifications import generate_reminders

logger = logging.getLogger("fitness_studio.scheduler")


def scheduled_maintenance():
    from app.canary_monitor import monitor_canaries
    from app.evaluation_lifecycle import close_incomplete_evaluations
    from app.expiry import expire_waits
    from app.work_queue import recover_expired

    created = 0
    for operation in (
        recover_expired,
        expire_waits,
        close_incomplete_evaluations,
        generate_reminders,
        monitor_canaries,
    ):
        try:
            count = operation()
            if operation is generate_reminders:
                created = count
        except Exception as exc:
            logger.error("Scheduled operation %s failed (%s).", operation.__name__, type(exc).__name__)
    return created


def run_scheduler(stop, interval=60, generate=scheduled_maintenance):
    if interval < 1:
        raise ValueError("The scheduling interval must be positive.")
    while not stop.is_set():
        try:
            created = generate()
            if created:
                logger.info("Created %d in-app notifications.", created)
        except Exception as exc:
            # Database unavailability must not terminate the scheduler or expose data.
            logger.error("Reminder generation failed (%s); retrying next interval.", type(exc).__name__)
        stop.wait(interval)


def main():
    parser = argparse.ArgumentParser(description="Generate scheduled in-app reminders without model calls.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between due-notification checks.")
    args = parser.parse_args()
    if args.interval < 1:
        parser.error("--interval must be at least 1 second")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if args.once:
        logger.info("Created %d in-app notifications.", scheduled_maintenance())
        return
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    run_scheduler(stop, args.interval)


if __name__ == "__main__":
    main()
