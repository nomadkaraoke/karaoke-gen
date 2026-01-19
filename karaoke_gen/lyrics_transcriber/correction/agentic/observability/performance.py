from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def timer() -> Iterator[float]:
    start = time.time()
    try:
        yield start
    finally:
        pass

def elapsed_ms(start: float) -> int:
    return int((time.time() - start) * 1000)


