# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4

import messaging
import database
from twisted.python import log, components
from feat.interface.agent import IAgencyAgent, IAgentFactory
from feat.interface.agency import IAgency
from feat.interface.protocols import IInitiatorFactory,\
                                     IAgencyInitiatorFactory,\
                                     IListener
from feat.interface.requester import IAgencyRequester, IRequesterFactory
from zope.interface import implements, classProvides
from feat.common import log

import uuid

class Agency(object):
    implements(IAgency)

    def __init__(self):
        self._agents = []
        # shard -> [ agents ]
        self._shards = {}

        self._messaging = messaging.Messaging()
        self._database = database.Database()

    def start_agent(self, factory, descriptor):
        factory = IAgentFactory(factory)
        medium = AgencyAgent(self, factory, descriptor)
        self._agents.append(medium)
        return medium

    def unregisterAgent(self, agent):
        self._agents.remove(agent)
        agent._messaging.disconnect()

    def joinedShard(self, agent, shard):
        shard_list = self._shards.get(shard, [])
        shard_list.append(agent)
        self._shards[shard] = shard_list

    def leftShard(self, agent, shard):
        shard_list = self._shards.get(shard, [])
        if agent in shard_list:
            shard_list.remove(agent)
        else:
            log.err('Was supposed to leave shard %r, but it was not there!' %\
                        shard)
        self._shards[shard] = shard_list

    # FOR TESTS
    def callbackOnMessage(self, shard, key):
        m = self._messaging
        queue = m.defineQueue(name=uuid.uuid1())
        exchange = m._getExchange(shard)
        exchange.bind(key, queue)
        return queue.consume()


class AgencyAgent(log.FluLogKeeper, log.Logger):
    implements(IAgencyAgent)

    log_category = "agency-agent"

    def __init__(self, agency, factory, descriptor):

        log.FluLogKeeper.__init__(self)
        log.Logger.__init__(self, self)

        self.agency = IAgency(agency)
        self.descriptor = descriptor
        self.agent = factory(self)

        self._messaging = agency._messaging.createConnection(self)
        self._database = agency._database

        # instance_id -> IListener
        self._listeners = {}
        # contract_type -> IListenerFactory
        self._listener_factories = []

        self.joinShard()
        self.agent.initiate()

    def joinShard(self):
        self.log("Join shard called")
        shard = self.descriptor.shard
        self._messaging.createPersonalBinding(self.descriptor.uuid, shard)
        self.agency.joinedShard(self, shard)

    def leaveShard(self):
        bindings = self._messaging.getBindingsForShard(self.descriptor.shard)
        map(lambda binding: binding.revoke(), bindings)
        self.agency.leftShard(self, self.descriptor.shard)
        self.descriptor.shard = None

    def on_message(self, message):
        if message.session_id in self._listeners:
            listener = self._listeners[session_id]
            return listener.on_message(message)

        if message.protocol_id in self._listener_factoriers:
            factory = self._listener_factories[message.protocol_id]
            self.create_listener_instance(factory, message.instance_id)

    def initiate_protocol(self, factory, recipients, *args, **kwargs):
        factory = IInitiatorFactory(factory)
        medium_factory = IAgencyInitiatorFactory(factory)
        medium = medium_factory(self, recipients, *args, **kwargs)

        initiator = factory(self.agent, medium, *args, **kwargs)
        self.register_listener(initiator)
        initiator.initiate()

    def register_listener(self, listener):
        listener = IListener(listener)
        session_id = listener.get_session_id()
        assert session_id not in self._listeners

        self._listeners[session_id] = listener


class AgencyRequesterFactory(object):
    implements(IAgencyInitiatorFactory)

    def __init__(self, factory):
        self._factory = factory

    def __call__(self, agent, recipients, *args, **kwargs):
        return AgencyRequester(agent, recipients, *args, **kwargs)


class AgencyRequester(log.LogProxy, log.Logger):
    implements(IAgencyRequester)

    log_category = 'agency-requester'

    def __init__(self, agent, recipients, *args, **kwargs):
        log.Logger.__init__(self, agent)
        log.LogProxy.__init__(self, agent)

        self.agent = agent
        self.recipients = recipients
        self.session_id = str(uuid.uuid1())
        self.log_name = self.session_id

    def request(self, request):
        self.debug("Sending request")
        request.session_id = self.session_id
        request.reply_to_shard = self.agent.descriptor.shard
        request.reply_to_key = self.agent.descriptor.uuid

        self.agent._messaging.publish(self.recipients.key,\
                                      self.recipients.shard, request)


components.registerAdapter(AgencyRequesterFactory,
                           IRequesterFactory, IAgencyInitiatorFactory)
