#!/usr/bin/env python
# -*- coding: utf-8 -*-

from collections.abc import Callable
from zmon_worker_monitor.zmon_worker.tasks.main import Try


def test_single_exception():
    t = Try(lambda: 1 / 0, lambda e: 2)
    assert isinstance(t, Callable)
    result = t()
    assert result == 2
    result = Try(lambda: 1 / 0, lambda e: e)()
    assert isinstance(result, Exception)


def test_nested_exception():
    t = Try(lambda: 1 / 0, Try(lambda: 2 / 0, lambda e: 4))
    assert isinstance(t, Callable)
    result = t()
    assert result == 4
