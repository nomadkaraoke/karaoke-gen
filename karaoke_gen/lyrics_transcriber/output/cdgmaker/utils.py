from collections.abc import Iterable, Iterator, Sequence
import itertools as it
import operator
from typing import Any, TypeVar, overload


_T = TypeVar("_T")


@overload
def ceildiv(a: int, b: int) -> int: ...
@overload
def ceildiv(a: float, b: float) -> float: ...
def ceildiv(a, b):
    """
    Return the ceiling of `a / b`.

    Parameters
    ----------
    a : int or float
        Dividend.
    b : int or float
        Divisor.

    Returns
    -------
    int or float
        The ceiling of the quotient of `a` and `b`.
    """
    return -(a // -b)


def distribute(
        sequence: Sequence[_T],
        start: float = 0,
        stop: float = 1,
) -> Iterator[tuple[float, _T]]:
    """
    Enumerate the sequence evenly over the interval (`start`, `stop`).

    Based on https://stackoverflow.com/a/59594546 .

    Parameters
    ----------
    sequence : array-like
        Sequence to enumerate.
    start : float, default 0
        Start of interval (exclusive).
    stop : float, default 1
        End of interval (exclusive).

    Yields
    ------
    position : float
        Position of sequence item in interval.
    item
        Sequence item.

    Examples
    --------
    >>> list(distribute("abc"))
    [(0.25, 'a'), (0.5, 'b'), (0.75, 'c')]
    >>> list(distribute("abc", 1, 4))
    [(1.75, 'a'), (2.5, 'b'), (3.25, 'c')]
    """
    m = len(sequence) + 1
    for i, v in enumerate(sequence, 1):
        yield start + (stop - start) * i / m, v


def intersperse(*sequences: Sequence[_T]) -> Iterator[_T]:
    """
    Evenly intersperse the sequences.

    Based on https://stackoverflow.com/a/59594546 .

    Parameters
    ----------
    *sequences
        Sequences to intersperse.

    Yields
    ------
    item
        Sequence item.

    Examples
    --------
    >>> list(intersperse(range(10), "abc"))
    [0, 1, 'a', 2, 3, 4, 'b', 5, 6, 7, 'c', 8, 9]
    >>> list(intersperse("XY", range(10), "abc"))
    [0, 1, 'a', 2, 'X', 3, 4, 'b', 5, 6, 'Y', 7, 'c', 8, 9]
    >>> "".join(intersperse("hlwl", "eood", "l r!"))
    'hello world!'
    """
    distributions = map(distribute, sequences)
    for _, v in sorted(it.chain(*distributions), key=operator.itemgetter(0)):
        yield v


def pad(
        iterable: Iterable[_T],
        size: int,
        padvalue: Any = None,
) -> Iterable[_T]:
    """
    Pad an iterable to a specified size.

    If the iterable is longer than the specified size, it is truncated.
    If it is shorter, `padvalue` is appended until the specified size is
    reached.

    Parameters
    ----------
    iterable : iterable
        Iterable to pad.
    size : int
        Size to pad iterable to.
    padvalue : any, default None
        Value to pad iterable with.

    Returns
    -------
    iterable
        Padded iterable.
    """
    return it.islice(it.chain(iterable, it.repeat(padvalue)), size)


__all__ = [
    "ceildiv", "distribute", "intersperse", "pad",
]
