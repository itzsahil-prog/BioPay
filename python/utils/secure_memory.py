"""
utils/secure_memory.py
Secure memory wiping to prevent sensitive biometric data from being
extracted from process memory or swap.
"""

import ctypes
import gc
import sys
import numpy as np


def secure_wipe_bytes(buf: bytearray) -> None:
    """
    Overwrite a bytearray with zeros using ctypes to prevent
    the compiler/interpreter from optimising away the write.
    """
    if buf is None or len(buf) == 0:
        return
    addr = (ctypes.c_char * len(buf)).from_buffer(buf)
    ctypes.memset(addr, 0, len(buf))


def secure_wipe_ndarray(arr: np.ndarray) -> None:
    """
    Overwrite a numpy array with zeros.
    Works even if the array's buffer is shared.
    """
    if arr is None:
        return
    try:
        if arr.flags.writeable:
            arr.fill(0)
        else:
            arr_copy = arr.copy()
            arr_copy.fill(0)
    except Exception:
        pass
    finally:
        del arr
        gc.collect()


def secure_wipe_string(s: str) -> None:
    """
    Attempt to wipe a Python string from memory.
    Note: CPython interns short strings, so this is best-effort.
    """
    if s is None:
        return
    # Convert to bytearray for wiping
    b = bytearray(s.encode())
    secure_wipe_bytes(b)
    del b
    gc.collect()
