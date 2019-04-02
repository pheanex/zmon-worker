#!/usr/bin/env python
# -*- coding: utf-8 -*-

import whois

from zmon_worker_monitor.zmon_worker.errors import ConfigurationError
from zmon_worker_monitor.adapters.ifunctionfactory_plugin import IFunctionFactoryPlugin, propartial


class WhoisFactory(IFunctionFactoryPlugin):
    def __init__(self):
        super(WhoisFactory, self).__init__()

    def configure(self, conf):
        """
        Called after plugin is loaded to pass the [configuration] section in their plugin info file
        :param conf: configuration dictionary
        """
        return

    def create(self, factory_ctx):
        """
        Automatically called to create the check function's object
        :param factory_ctx: (dict) names available for Function instantiation
        :return: an object that implements a check function
        """
        return propartial(WhoisWrapper, host=factory_ctx['host'])


class WhoisWrapper(object):
    def __init__(self, host, timeout=10):
        if not host:
            raise ConfigurationError('Whois wrapper improperly configured. Valid host is required!')

        self.host = host

    def check(self):
        return whois.whois(self.host)
