"""The one thread that owns the pipeline, and recovering when it stops.

Every change to the GStreamer pipeline goes through a single thread, because
the GPU driver underneath has segfaulted when used from two at once. That makes
the thread a single point of failure: when it stopped making progress, every
later job queued behind nothing and the server never worked again until it was
restarted -- reported as "it worked initially, and after a refresh it hasn't
worked since".
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from fourthplayer.video import PipelineWorker
except ImportError as exc:
    print("SKIPPED: %s -- needs the GStreamer bindings, which live on the host"
          % exc)
    sys.exit(0)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


print("an ordinary worker does its work")
worker = PipelineWorker()
check(worker.submit(lambda: 21 * 2).result(timeout=5) == 42, "it runs a job")
check(worker.alive(), "and reports itself alive")

print("\nwork is serialised, which is the whole point")
order, lock = [], threading.Lock()


def record(n):
    with lock:
        order.append(n)
    time.sleep(0.02)


futures = [worker.submit(record, n) for n in range(5)]
for f in futures:
    f.result(timeout=5)
check(order == [0, 1, 2, 3, 4], "jobs ran one at a time, in order: %r" % order)

print("\na wedged worker is detected rather than waited on")
stuck = threading.Event()
worker.submit(stuck.wait)                 # never returns until we say so
check(not worker.alive(timeout=0.3),
      "a worker busy for ever does not report itself alive")

print("\nand can be replaced, so later work is not queued behind nothing")
worker.reset()
check(worker.alive(timeout=5), "the replacement answers")
check(worker.submit(lambda: "fresh").result(timeout=5) == "fresh",
      "and runs new work")
check(not stuck.is_set(),
      "without waiting for the stuck job, which is still stuck")

print("\nthe old thread is released once it finishes, and changes nothing")
stuck.set()
time.sleep(0.2)
check(worker.alive(timeout=5), "the current worker is unaffected")

print("\nreplacing an idle worker is harmless")
before = worker.submit(lambda: 1).result(timeout=5)
worker.reset()
check(worker.submit(lambda: 1).result(timeout=5) == before,
      "a reset worker behaves exactly like a fresh one")

worker.shutdown(wait=False)
print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
