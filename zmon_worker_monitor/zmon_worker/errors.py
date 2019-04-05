#!/usr/bin/env python
# -*- coding: utf-8 -*-


class CheckError(Exception):
    def __init__(self, message=''):
        self.message = message
        super().__init__(message)


class AlertError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


class NotificationError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


class SecurityError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


class ConfigurationError(CheckError):
    def __init__(self, message):
        message = 'Configuration error: {}'.format(message)

        super().__init__(message)


class InsufficientPermissionsError(CheckError):
    def __init__(self, user, entity):
        self.user = user
        self.entity = entity

    def __str__(self):
        return 'Insufficient permisions for user {} to access {}'.format(self.user, self.entity)


class JmxQueryError(CheckError):
    def __init__(self, message):
        self.message = message
        super().__init__(message)

    def __str__(self):
        return 'JMX Query failed: {}'.format(self.message)


class HttpError(CheckError):
    def __init__(self, message, url=None):
        self.message = message
        self.url = url
        super().__init__(message)

    def __str__(self):
        return 'HTTP request failed for {}: {}'.format(self.url, self.message)


class DbError(CheckError):
    def __init__(self, message, operation=None):
        self.message = message
        self.operation = operation
        super().__init__(message)

    def __str__(self):
        return 'DB operation {} failed: {}'.format(self.operation, self.message)


class ResultSizeError(CheckError):
    def __init__(self, message):
        message = 'Result size error: {}'.format(message)
        super().__init__(message)


class NagiosError(CheckError):
    def __init__(self, output):
        self.output = output
        super().__init__(output)

    def __str__(self):
        return 'NagiosError. Command output: {}'.format(self.output)
