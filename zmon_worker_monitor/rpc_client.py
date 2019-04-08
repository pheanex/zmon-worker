#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
This module have functions to instantiate RPC clients, to execute RPC methods exposed by a remote server.
You can get a default all-compatible RPC client: client = get_rpc_client(uri)
You can also get an extended rpc client: client = get_rpc_client_plus(uri)

Although RPC specification does not allow calling methods with Python's kwargs, our extended
RPC client supports it, which means you can pass keyword arguments to exposed ZMON RPC methods.
One important limitation: kwargs values must be built-in types and json serializable.
In practice this means: nested lists, dicts, tuples and primitive types (int, float, string)

Example of use from code:
  from rpc_client import get_rpc_client_plus
  client = get_rpc_client_plus('http://localhost:8000/rpc_path')
  result = client.call_rpc_method('my_method', args=[300, 1.1], kwargs={"age": 12, "name": "Peter Pan"})

You can also call the remote method as if it was a member of client:
  client = get_rpc_client_plus('http://localhost:8000/rpc_path')
  result = client.my_method(300, 1.1, age=12, name="Peter Pan")

To execute the same example from the command line:
  python rpc_client.py http://localhost:8000/rpc_path my_method int:300 float:1.1 'js:{"age": 12, "name": "Peter Pan"}'

"""
import json
import xmlrpc.client
from functools import partial


DEBUG = True

_cmd_struct = {
    'endpoint': None,
    'method_name': None,
    'args': []
}


class RpcClientPlus:
    """
    A thin wrapper around Python lib rpc client: xmlrpclib.ServerProxy
    It can call RPC methods with keyword arguments (only for ZMON's RPC server).
    Also call_rpc_method(name) is handy for dynamic method name resolution.
    """

    def __init__(self, uri_endpoint, **kwargs):
        self._client = xmlrpc.client.ServerProxy(uri_endpoint, **kwargs)

    def _call_rpc_method(self, method, *args, **kwargs):
        rpc_args = list(args)
        if kwargs:
            rpc_args.append(self._serialize_kwargs(kwargs))
        return getattr(self._client, method)(*rpc_args)

    def call_rpc_method(self, method, args=(), kwargs=None):
        """
        Executes RPC method and returns result.

        :param str method: remote method name
        :param list args: positional arguments to passed
        :param dict kwargs: keyword arguments to passed. See module docstring for limitations.
        :return: remote result
        """
        return self._call_rpc_method(method, *(args if args else ()), **(kwargs if kwargs else {}))

    def __getattr__(self, item):
        # you can call remote functions directly like in the original client
        return partial(self._call_rpc_method, item)

    @classmethod
    def _serialize_kwargs(cls, kwargs):
        return 'js:{}'.format(json.dumps(kwargs)) if kwargs else ''


def get_rpc_client(endpoint):
    """
    Returns a standard rpc client that connects to the remote server listening at endpoint

    :param str endpoint: RPC url, example http://host:port/rpc_path
    :return: rpc_client
    """
    return xmlrpc.client.ServerProxy(endpoint)


def get_rpc_client_plus(endpoint):
    """
    Returns a extended rpc client that connects to the remote server listening at endpoint

    :param str endpoint: RPC url, example http://host:port/rpc_path
    :return: rpc_client
    """
    return RpcClientPlus(endpoint)
