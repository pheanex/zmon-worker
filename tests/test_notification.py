#!/usr/bin/env python
# -*- coding: utf-8 -*-

from zmon_worker_monitor.zmon_worker.notifications.notification import BaseNotification


def test_get_subject_success():
    alert = {'name': '{thing} is {status}'}
    captures = {'thing': 'everything', 'status': 'bröken'}
    entity = {'id': 'everything'}
    ctx = {
        'is_alert': True,
        'changed': True,
        'alert_def': alert,
        'captures': captures,
        'entity': entity,
    }

    assert BaseNotification._get_subject(ctx) == 'NEW ALERT: everything is bröken on everything'


def test_get_subject_success_with_custom_message():
    alert = {'name': '{thing} is {status}'}
    captures = {'thing': 'everything', 'status': 'bröken'}
    entity = {'id': 'everything'}
    ctx = {
        'is_alert': True,
        'changed': True,
        'alert_def': alert,
        'captures': captures,
        'entity': entity,
    }

    assert BaseNotification._get_subject(ctx, custom_message='Älert') == 'NEW ALERT: Älert'


def test_get_subject_success_no_event():
    alert = {'name': '{thing} is {status}'}
    captures = {'thing': 'everything', 'status': 'bröken'}
    entity = {'id': 'everything'}
    ctx = {
        'is_alert': True,
        'changed': True,
        'alert_def': alert,
        'captures': captures,
        'entity': entity,
    }

    assert BaseNotification._get_subject(ctx, include_event=False) == 'everything is bröken on everything'


def test_get_subject_with_bad_capture():
    alert = {'name': '{thingy} is {result}'}
    captures = {'thing': 'everything', 'status': 'bröken'}
    entity = {'id': 'everything'}
    ctx = {
        'is_alert': True,
        'changed': True,
        'alert_def': alert,
        'captures': captures,
        'entity': entity,
    }

    assert BaseNotification._get_subject(ctx) == 'NEW ALERT: {thingy} is {result} on everything'


def test_get_subject_with_bad_formatting():
    alert = {'name': '{thing:w} is {status}'}
    captures = {'thing': 'everything', 'status': 'bröken'}
    entity = {'id': 'everything'}
    ctx = {
        'is_alert': True,
        'changed': True,
        'alert_def': alert,
        'captures': captures,
        'entity': entity,
    }

    assert BaseNotification._get_subject(ctx) == "NEW ALERT: <<< Unformattable name '{thing:w} is {status}': \
Unknown format code 'w' for object of type 'str' >>> on everything"
