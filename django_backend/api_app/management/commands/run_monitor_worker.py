import queue
import signal
import threading

from django.core.management.base import BaseCommand

from api_app import views


class Command(BaseCommand):
    help = 'Run the Aviator monitor worker continuously.'

    def handle(self, *args, **options):
        stop_event = threading.Event()
        work_queue = queue.Queue(maxsize=1000)

        def _stop_worker(signum, frame):
            stop_event.set()

        signal.signal(signal.SIGTERM, _stop_worker)
        signal.signal(signal.SIGINT, _stop_worker)

        self.stdout.write(self.style.SUCCESS('Monitor worker started'))
        try:
            views._monitor_runner(work_queue, stop_event)
        finally:
            self.stdout.write(self.style.WARNING('Monitor worker stopped'))