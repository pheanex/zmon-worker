#!/usr/bin/env python
# -*- coding: utf-8 -*-

import base64
import json
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from operator import itemgetter
from random import random
from traceback import format_exc

import opentracing
import requests
import setproctitle
import snappy
import tokens

import settings
from redis_context_manager import RedisConnHandler
from rpc_client import get_rpc_client
from tasks import check_and_notify, cleanup, configure_tasks, trial_run
from zmon_worker_monitor import eventloghttp
from zmon_worker_monitor.zmon_worker.common.tracing import extract_tracing_span
from zmon_worker_monitor.zmon_worker.common.utils import get_process_cmdline

logger = logging.getLogger(__name__)

TASK_POP_TIMEOUT = 5
OPENTRACING_TAG_QUEUE_RESULT = 'worker_task_result'
OPENTRACING_QUEUE_OPERATION = 'worker_task_processing'
OPENTRACING_TASK_EXPIRATION = 'worker_task_expire_time'

SAMPLING_RATE_UPDATE_DURATION = 60
SAMPLING_RATE_ENTITY_ID = 'zmon-sampling-rate'


__config = None


# We manage uid tokens once when we import
tokens.manage('uid', ['uid'])


def get_sampling_rate_config(config, current_span):
    """
    Get sampling rate config from a ZMON entity or config vars.
    Entity:
        {
            "id": "zmon-sampling-rate",
            "type": "zmon_config",
            "default_sampling": 100,
            "critical_checks": [13, 14, 19],
            "worker_sampling": {
                "account-1": 50,
                "account-2": 60,
                "account-3": 0
            }
        }
    """
    default_sampling = int(config.get('zmon.sampling.rate', 100))
    critical_checks = config.get('zmon.critical.checks')
    if type(critical_checks) is not list:
        critical_checks = critical_checks.replace(' ', '').split(',')

    sampling_config = {
        'default_sampling': default_sampling,
        'critical_checks': critical_checks
    }

    # We try to get sampling rate entity!
    zmon_url = config.get('zmon.url')
    if not zmon_url:
        current_span.set_tag('sampling_entity_used', False)
    else:
        current_span.set_tag('sampling_entity_used', True)

        try:
            url = '{}/api/v1/entities/{}'.format(zmon_url, SAMPLING_RATE_ENTITY_ID)
            headers = {'Authorization': 'Bearer {}'.format(tokens.get('uid'))}
            resp = requests.get(url, headers=headers, timeout=2)

            resp.raise_for_status()

            entity = resp.json()
            sampling_config.update(entity)
        except Exception:
            current_span.set_tag('sampling_entity_used', False)
            current_span.log_kv({'exception': format_exc()})

    return sampling_config


def get_config():
    global __config
    if __config is None:
        __config = settings.get_external_config()
    return __config


def flow_simple_queue_processor(queue='', **execution_context):
    '''
    Simple logic to connect to a redis queue, listen to messages, decode them and execute the tasks

    :param queue: (str) queue to connect to
    :param execution_context: (dict) other kwargs that may have been passed when worker was spawn
    :return:

    Some info to understand celery messages:

    1. An example of a celery message as first received (base64-encoded body shortened):
    ('zmon:queue:default',
    '{
        "body": "eyJleHBpcm...t9fQ==", "headers": {}, "content-type": "application/json",
        "properties": {
            "body_encoding": "base64",
            "correlation_id": "check-277-de_zalando:access-control-kit-1409826332.92",
            "reply_to": "abc5c87f-74eb-3570-a1cf-e426eaf91ca7",
            "delivery_info": {
                "priority": 0,
                "routing_key": "default",
                "exchange": "zmon"
            },
            "delivery_mode": 2,
            "delivery_tag": "94288433-cb4e-4d33-be29-c63e2bbce39a"
        },
        "content-encoding": "utf-8"}'
    )

    2. An example of the message['body'] after being base64-decoded (args list shortened):
    {
        u'utc': True,
        u'chord': None,
        u'args': [{u'check_id': 277, u'interval': 60, u'entity': {u'instance_type': u'zomcat', ...}, u'condition': u'>100', ...}],  # noqa
        u'retries': 0,
        u'expires': u'2014-09-04T10:27:32.919152+00:00',
        u'task': u'check_and_notify',
        u'callbacks': None,
        u'errbacks': None,
        u'timelimit': [90, 60],
        u'taskset': None,
        u'kwargs': {},
        u'eta': None,
        u'id': u'check-277-de_zalando:access-control-kit-1409826332.92'
    }

    '''

    known_tasks = {'check_and_notify': check_and_notify, 'trial_run': trial_run, 'cleanup': cleanup}

    # get configuration and configure tasks
    config = get_config()
    configure_tasks(config)

    logger.info('Connecting simple_queue_consumer to queue=%s, execution_context=%s', queue, execution_context)

    RedisConnHandler.configure(**dict(config))

    eventloghttp.set_target_host(config.get('eventlog.host', 'localhost'), config.get('eventlog.port', 8081))
    eventloghttp.enable_http(config.get('eventlog.http', False))

    reactor = FlowControlReactor.get_instance()

    conn_handler = RedisConnHandler.get_instance()

    expired_count = 0
    count = 0

    sampling_rate_last_updated = datetime.utcnow()
    sampling_config = None
    sampling_update_rate = int(config.get('zmon.sampling.update.rate', SAMPLING_RATE_UPDATE_DURATION))

    while True:
        try:

            with conn_handler as ch:

                r_conn = ch.get_healthy_conn()

                encoded_task = r_conn.blpop(queue, TASK_POP_TIMEOUT)

                if encoded_task is None:
                    raise ch.IdleLoopException('No task received')

                queue, msg = encoded_task

                if msg[:1] != '{':
                    msg = snappy.decompress(msg)

                msg_obj = json.loads(msg)

                # OpenTracing: picking up trace from scheduler
                trace = msg_obj.get('properties', {}).get('trace', {})

                span = extract_tracing_span(trace)
                span.set_operation_name(OPENTRACING_QUEUE_OPERATION)

                # Get sampling rates. We update every minute.
                if sampling_config is None or (
                        (datetime.utcnow() - sampling_rate_last_updated).seconds > sampling_update_rate):
                    try:
                        sampling_rate_last_updated = datetime.utcnow()
                        sampling_config = get_sampling_rate_config(config, span)
                        span.log_kv({'sampling_config': sampling_config})
                        span.set_tag('sampling_rate_updated', True)
                    except Exception:
                        span.set_tag('sampling_rate_updated', False)
                        span.log_kv({'exception': format_exc()})

                with span:
                    try:
                        is_processed = process_message(
                            queue, known_tasks, reactor, msg_obj, current_span=span, sampling_config=sampling_config)
                        if is_processed:
                            span.set_tag(OPENTRACING_TAG_QUEUE_RESULT, 'success')
                        else:
                            span.set_tag(OPENTRACING_TAG_QUEUE_RESULT, 'expired')
                            expired_count += 1
                            if expired_count % 500 == 0:
                                logger.warning('expired tasks count: %s', expired_count)
                    except Exception:
                        span.set_tag(OPENTRACING_TAG_QUEUE_RESULT, 'error')
                        span.set_tag('error', True)
                        span.log_kv({'exception': format_exc()})

                count += 1

        except Exception:
            logger.exception('Exception in redis loop. Details: ')
            try:
                span = opentracing.tracer.start_span(operation_name='worker_redis_loop')
                with span:
                    span.set_tag('error', True)
                    span.log_kv({'exception': format_exc()})
            except Exception:
                pass

            time.sleep(5)  # avoid heavy log spam here
            # TODO: some exit condition on failure: maybe when number of consecutive failures > n ?


def process_message(queue, known_tasks, reactor, msg_obj, current_span, sampling_config=None):
    """
    Proccess and execute a task.

    :param queue: Name of the queue the task comes from
    :type queue: str

    :param known_tasks: Dictionary of tasks that processor knows how to work with
    :type known_tasks: dict

    :param reactor: Instance of FlowControlReactor
    :type reactor: FlowControlReactor

    :param msg_obj: Dictionary that contains the task to process
    :type msg_obj: dict

    :param current_span: Current OpenTracing span.
    :type current_span: opentracing.Span

    :param sampling_config: Dict holding all sampling rate info.
    :type sampling_config: dict

    :return: Return True if the message was processed successfully
    :rtype: bool
    """
    msg_body = None
    body_encoding = msg_obj.get('properties', {}).get('body_encoding')
    if body_encoding == "nested":
        msg_body = msg_obj["body"]
    elif body_encoding == "base64":
        msg_body = json.loads(base64.b64decode(msg_obj['body']))
    elif body_encoding == "snappy":
        msg_body = json.loads(snappy.decompress(base64.b64decode(msg_obj['body'])))

    taskname = msg_body['task']
    func_args = msg_body['args']
    func_kwargs = msg_body['kwargs']
    timelimit = msg_body.get('timelimit')  # [90, 60]
    t_hard, t_soft = timelimit

    current_span.set_tag('taskname', taskname)

    # we pass task metadata as a kwargs right now, later will be put in the function context by decorator
    task_context = {
        'queue': queue,
        'taskname': taskname,

        'delivery_info': msg_obj.get('properties', {}).get('delivery_info', {}),
        'task_properties': {
            'task': taskname,
            'id': msg_body.get('id', ''),
            'expires': msg_body.get('expires'),  # '2014-09-04T10:27:32.91915200:00'
            'timelimit': timelimit,  # [90, 60]
            'utc': msg_body.get('utc', True),
        },
    }

    # discard tasks that are expired if expire metadata comes with the message
    cur_time = datetime.utcnow() if task_context['task_properties']['utc'] else datetime.now()
    expire_time = datetime.strptime(
        msg_body.get('expires').replace('Z', '').rsplit('+', 1)[0], '%Y-%m-%dT%H:%M:%S.%f') \
        if msg_body.get('expires') else cur_time + timedelta(seconds=10)

    check_id = (msg_body['args'][0].get('check_id', 'xx') if len(msg_body['args']) > 0 and isinstance(
        msg_body['args'][0], dict) else 'XX')

    current_span.set_tag('check_id', check_id)

    if cur_time >= expire_time:
        current_span.set_tag(OPENTRACING_TASK_EXPIRATION, str(expire_time))
        logger.warn(
            'Discarding task due to time expiration. cur_time: %s , expire_time: %s, check_id: %s, '
            'msg_body["expires"]=%s  ----  msg_body=%s',
            cur_time, expire_time, check_id, msg_body.get('expires'), msg_body)
        return False

    with reactor.enter_task_context(taskname, t_hard, t_soft):
        known_tasks[taskname](*func_args, task_context=task_context, sampling_config=sampling_config, **func_kwargs)
    return True


class FlowControlReactor(object):
    """
    Implements a singleton object with a permanently running action loop, that can communicate with the
    parent process (ProcessController) to request certain actions or submit information about the health
    of this worker.
    Only implemented capability till now is a "Hard Kill" functionality that kicks in when a task is
    taking too long to complete. We use a context manager to signal when we enter or leave this mode of operations.
    Future capabilities may include periodical reports to the parent process about number of processed tasks,
    mean time spent by the N slowest running tasks. Also a soft kill feature.
    """

    _initialized = False
    _can_init = False
    _instance = None

    t_wait = 0.2
    ping_timedelta = 30  # send ping data every X seconds

    _ping_template = {
        'timestamp': None,
        'timedelta': None,
        'tasks_done': 0,
        'percent_idle': 0,
        'task_duration': 0.0,
    }

    _event_template = {
        'origin': '',
        'type': '',
        'body': '',
        'timestamp': 0,
        'repeats': 0,
    }

    _max_keep_events = 5000
    events_timedelta = 60  # send events every X seconds

    def __init__(self):
        # self.task_agg_info = {}  # we could aggregate some info about how tasks are running in this worker
        assert not self._initialized and self._can_init, 'Call get_instance() to instantiate'
        self._initialized = True
        self._pid = os.getpid()
        self._rpc_client = get_rpc_client('http://{}:{}{}'.format(settings.RPC_SERVER_CONF['HOST'],
                                                                  settings.RPC_SERVER_CONF['PORT'],
                                                                  settings.RPC_SERVER_CONF['RPC_PATH']))
        self._current_task_by_thread = {}  # {thread_id: (taskname, t_hard, t_soft, tstart)}
        self.action_on = False
        self._thread = threading.Thread(target=self.action_loop)
        self._thread.daemon = True
        self._actions = (self.action_hard_kill, self.action_send_ping, self.action_send_events)

        self._ping_data = deepcopy(self._ping_template)
        self._ping_lock = threading.RLock()
        self._ping_idle_points = [0, 0]  # [num_idle_points, num_total_points]
        self._t_last_ping = time.time() - self.ping_timedelta * random()  # randomize ping start
        self._num_ping_sent = -1

        self._event_list = []
        self._event_lock = threading.RLock()
        self._t_last_events = time.time() + self.events_timedelta * random()  # randomize event start

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._can_init = True
            cls._instance = cls()
        return cls._instance

    @contextmanager
    def enter_task_context(self, taskname, t_hard, t_soft):
        self.task_received(taskname, t_hard, t_soft)
        try:
            yield self
        except Exception:
            self.task_ended(exc=format_exc())  # self.task_ended(exc=e)
            raise
        else:
            self.task_ended()

    def action_hard_kill(self):
        """ hard kill logic """
        for th_name, (taskname, t_hard, t_soft, ts) in self._current_task_by_thread.copy().items():
            if time.time() > ts + t_hard:
                msg = 'Hard kill request received for worker pid={}, task={}, t_hard={}, cmdline={}'.format(
                    self._pid, taskname, t_hard, get_process_cmdline(self._pid)
                )
                logger.warn(msg)
                self.add_event('FlowControlReactor.action_hard_kill', 'ACTION', msg)
                self._rpc_client.mark_for_termination(self._pid)  # rpc call to parent asking for a kill
                self._current_task_by_thread.pop(th_name, {})

    def action_send_ping(self):

        t_now = time.time()

        if t_now - self._t_last_ping >= self.ping_timedelta:

            with self._ping_lock:
                data = self._ping_data
                self._ping_data = deepcopy(self._ping_template)
                idle, total = tuple(self._ping_idle_points)
                self._ping_idle_points = [0, 0]

            data['timestamp'] = t_now
            data['timedelta'] = t_now - self._t_last_ping
            data['percent_idle'] = (idle * 100.0) / total if total > 0 else 0

            # send ping data
            if self._num_ping_sent >= 0:
                self._rpc_client.ping(self._pid, data)  # rpc call to send ping data to parent

            self._num_ping_sent += 1
            self._t_last_ping = t_now
        else:
            # update idle info
            with self._ping_lock:
                self._ping_idle_points[0] += 1 if not self._current_task_by_thread else 0  # idle
                self._ping_idle_points[1] += 1  # total

    def action_send_events(self):
        t_now = time.time()

        if t_now - self._t_last_events >= self.events_timedelta:
            with self._event_lock:
                events = self._event_list
                self._event_list = []

            # eliminate repeated events, keep last timestamp
            event_dict = {}
            for e in events[::-1]:
                key = (e['origin'], e['type'], e['body'])
                if key in event_dict:
                    event_dict[key]['repeats'] += e['repeats']
                else:
                    event_dict[key] = e

            events = sorted(event_dict.values(), key=itemgetter('timestamp'))
            if events:
                self._rpc_client.add_events(self._pid, events)  # rpc call to send events to parent
            self._t_last_events = t_now

    def add_event(self, origin, type, body, repeats=1):
        with self._event_lock:
            self._event_list.append(dict(origin=origin, type=type, body=body, repeats=repeats, timestamp=time.time()))
            if len(self._event_list) > self._max_keep_events:
                self._event_list = self._event_list[-self._max_keep_events:]

    def action_loop(self):

        while self.action_on:
            for action in self._actions:
                try:
                    action()
                except Exception:
                    self.add_event('FlowControlReactor.action_loop', 'ERROR', format_exc())
                    logger.exception('Scary Error in FlowControlReactor.action_loop(): ')

            time.sleep(self.t_wait)

    def start(self):
        self.action_on = True
        self._thread.start()

    def stop(self):
        self.action_on = False

    def task_received(self, taskname, t_hard, t_soft):
        # this sets a timer for this task, there is only one task per thread, and right now only main thread produce
        self._current_task_by_thread[threading.currentThread().getName()] = (taskname, t_hard, t_soft, time.time())

    def task_ended(self, exc=None):
        # delete the task from the list
        task_detail = self._current_task_by_thread.pop(threading.currentThread().getName(), ())
        if not exc:
            # update ping data
            with self._ping_lock:
                self._ping_data['tasks_done'] += 1
                if len(list(task_detail)) >= 4:
                    self._ping_data['task_duration'] += time.time() - list(task_detail)[3]
        else:
            # register error event
            self.add_event('FlowControlReactor.task_ended', 'ERROR', str(exc))


def start_worker_for_queue(flow='simple_queue_processor', queue='zmon:queue:default', **execution_context):
    """
    Starting execution point to the workflows
    """

    known_flows = {'simple_queue_processor': flow_simple_queue_processor}

    if flow not in known_flows:
        logger.exception('Bad role: %s' % flow)
        sys.exit(1)

    logger.info('Starting worker with pid=%s, flow type: %s, queue: %s, execution_context: %s', os.getpid(), flow,
                queue, execution_context)
    setproctitle.setproctitle('zmon-worker {} {}'.format(flow, queue))

    # start Flow Reactor here
    FlowControlReactor.get_instance().start()

    exit_code = 0
    try:

        known_flows[flow](queue=queue, **execution_context)

    except (KeyboardInterrupt, SystemExit):
        logger.warning('Caught user signal to stop consumer: finishing!')
    except Exception:
        logger.exception('Exception in start_worker(). Details: ')
        exit_code = 2
    finally:
        FlowControlReactor.get_instance().stop()
        sys.exit(exit_code)
