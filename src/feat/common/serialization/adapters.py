from twisted.python.failure import Failure
from zope.interface import implements, classProvides

from feat.common import serialization, adapter
from feat.common.serialization import base

from feat.interface.serialization import *


class AdaptedMarker(object):
    pass


class BaseAdapter(object):

    adapter_mixin = None

    _adapters = {} # {EXCEPTION_TYPE: ADAPTER_TYPE}

    @classmethod
    def get_adapter(cls, base_type):
        adapter = cls._adapters.get(base_type)
        if adapter is None:
            adapter_name = base_type.__name__ + "Adapter"
            bases = (base_type, AdaptedMarker)
            if cls.adapter_mixin is not None:
                bases += (cls.adapter_mixin, )
            adapter = type(adapter_name, bases, {})
            cls._adapters[base_type] = adapter
        return adapter

    @classmethod
    def get_type(cls, value):
        vtype = type(value)
        if issubclass(vtype, AdaptedMarker):
            return vtype.__bases__[0]
        return vtype


class AdaptedExceptionMixin(object):

    def __eq__(self, other):
        if not isinstance(self, type(other)):
            return NotImplemented
        return (self.args == other.args
                and self.__dict__ == other.__dict__)

    def __ne__(self, other):
        eq = self.__eq__(other)
        return not eq if eq is not NotImplemented else eq


@adapter.register(Exception, ISerializable)
@serialization.register
class ExceptionAdapter(BaseAdapter):

    classProvides(IRestorator)
    implements(ISerializable)

    type_name = "exception"
    adapter_mixin = AdaptedExceptionMixin

    def __init__(self, exception):
        self._args = exception.args
        self._attrs = exception.__dict__
        self._type = self.get_type(exception)

    ### ISerializable Methods ###

    def snapshot(self):
        return self._type, self._args, self._attrs

    @classmethod
    def prepare(self):
        return None

    @classmethod
    def restore(cls, snapshot):
        extype, args, attrs = snapshot
        adapter = cls.get_adapter(extype)
        ex = adapter.__new__(adapter)
        ex.args = args
        ex.__dict__.update(attrs)
        return ex


@adapter.register(Failure, ISerializable)
@serialization.register
class FailureAdapter(Failure, BaseAdapter, base.Serializable):

    type_name = "failure"

    def __init__(self, failure):
        self.__dict__.update(failure.__dict__)
        self.cleanFailure()

    def __eq__(self, other):
        if not isinstance(other, Failure):
            return NotImplemented
        return (self.value == other.value
                and self.type == self.type)

    def __ne__(self, other):
        eq = self.__eq__(other)
        return not eq if eq is not NotImplemented else eq