import time


def wait_for(predicate, timeout=3.0, step=0.01):
    """Ждать, пока predicate() станет истинным; вернуть его итоговое значение."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return bool(predicate())
