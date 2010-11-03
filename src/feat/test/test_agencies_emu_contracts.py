# -*- coding: utf-8 -*-
# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
import uuid, time

from zope.interface import classProvides, implements
from twisted.internet import reactor, defer

from feat.agencies.emu import agency
from feat.agents import agent, descriptor, contractor, message, manager
from feat.interface import recipient, contracts
from feat.interface.contractor import IContractorFactory

from . import common


class DummyContractor(contractor.BaseContractor, common.Mock):
    classProvides(IContractorFactory)
    
    protocol_id = 'dummy-contract'

    def __init__(self, *args, **kwargs):
        contractor.BaseContractor.__init__(self, *args, **kwargs)
        common.Mock.__init__(self)

    @common.stub
    def announced(announce):
        pass

    @common.stub
    def rejected(rejection):
        pass

    @common.stub
    def granted(grant):
        pass

    @common.stub
    def canceled(grant):
        pass

    @common.stub
    def acknowledged(grant):
        pass

    @common.stub
    def aborted():
        pass

class TestContractor(common.TestCase):

    timeout = 3

    def setUp(self):
        self.agency = agency.Agency()
        desc = descriptor.Descriptor()
        self.agent = self.agency.start_agent(agent.BaseAgent, desc)
        self.agent.register_interest(DummyContractor)

        self.endpoint = recipient.Agent(str(uuid.uuid1()), 'lobby')
        self.queue = self.agency._messaging.defineQueue(self.endpoint.key)
        exchange = self.agency._messaging._getExchange(self.endpoint.shard)
        exchange.bind(self.endpoint.key, self.queue)
        self.contractor = None
        self.session_id = None

    def tearDown(self):
        self._cancel_expiration_call_if_necessary()

    def testRecivingAnnouncement(self):
        d = self._recv_announce()
        
        def asserts(rpl):
            self.assertEqual(True, rpl)
            self.assertEqual(1, len(self.agent._listeners))

        d.addCallback(asserts)
        d.addCallback(self._get_contractor)

        def asserts_on_contractor(contractor):
            self.assertEqual(DummyContractor, contractor.__class__)
            self.assertCalled(contractor, 'announced', times=1)
            args = contractor.find_calls('announced')[0].args
            self.assertEqual(1, len(args))
            announce = args[0]
            self.assertEqual(contracts.ContractState.announced,
                             contractor.state)
            self.assertNotEqual(None, contractor.medium.announce)
            self.assertEqual(announce, contractor.medium.announce)
            self.assertTrue(isinstance(contractor.medium.announce,\
                                       message.Announcement))

        d.addCallback(asserts_on_contractor)
        
        return d

    def testContractorExpireExpirationTime(self):
        self.agency.time_scale = 0.01

        d = self._recv_announce()
        d.addCallback(self.cb_after, obj=self.agent,\
                          method='unregister_listener')

        return d

    def testPuttingBid(self):
        d = self._recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self._send_bid)

        def asserts(contractor):
            self.assertEqual(contracts.ContractState.bid, contractor.state)

        d.addCallback(asserts)
        d.addCallback(self.queue.consume)
        
        def asserts_on_bid(msg):
            self.assertEqual(message.Bid, msg.__class__)
            self.assertEqual(self.contractor.medium.bid, msg)

        d.addCallback(asserts_on_bid)

        return d            

    def testRefusing(self):
        d = self._recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self._send_refusal)

        d.addCallback(self.assertUnregistered)
        d.addCallback(self.queue.consume)
        
        def asserts_on_refusal(msg):
            self.assertEqual(message.Refusal, msg.__class__)
            self.assertEqual(self.contractor.medium.session_id, msg.session_id)

        d.addCallback(asserts_on_refusal)
        
        return d

    def testCorrectGrant(self):
        d = self._recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self._send_bid, 1)
        d.addCallback(self._recv_grant, 1)

        def asserts(_):
            self.assertEqual(contracts.ContractState.granted,\
                                 self.contractor.state)
            self.assertCalled(self.contractor, 'granted')

        d.addCallback(asserts)

        return d

    def assertUnregistered(self, *_):
        self.assertFalse(self.contractor.medium.session_id in\
                             self.agent._listeners)
        self.assertEqual(contracts.ContractState.closed, self.contractor.state)

    def _cancel_expiration_call_if_necessary(self):
        if self.contractor and self.contractor.medium._expiration_call and\
                not (self.contractor.medium._expiration_call.called or
                     self.contractor.medium._expiration_call.cancelled):
            self.warning("Canceling contractor expiration call in tearDown")
            self.contractor.medium._expiration_call.cancel()    

    def _get_contractor(self, _):
        self.contractor = self.agent._listeners.values()[0].contractor
        return self.contractor

    def _send_bid(self, contractor, bid=1):
        msg = message.Bid()
        msg.bids = [ bid ]
        contractor.medium.bid(msg)
        return contractor

    def _send_refusal(self, contractor):
        msg = message.Refusal()
        contractor.medium.refuse(msg)
        return contractor

    def _recv_announce(self, *_):
        msg = message.Announcement()
        msg.session_id = str(uuid.uuid1())
        self.session_id = msg.session_id
        return self._recv_msg(msg)

    def _recv_grant(self, _, bid, update_report=None):
        msg = message.Grant()
        msg.bid = bid
        msg.update_report = update_report
        msg.session_id = self.session_id
        return self._recv_msg(msg)

    def _recv_msg(self, msg):
        d = self.cb_after(arg=None, obj=self.agent, method='on_message')
        msg.reply_to_shard = self.endpoint.shard
        msg.reply_to_key = self.endpoint.key
        msg.expiration_time = time.time() + 10
        msg.protocol_type = "Contract"
        msg.protocol_id = "dummy-contract"
        msg.message_id = str(uuid.uuid1())

        key = self.agent.descriptor.uuid
        shard = self.agent.descriptor.shard
        self.agent._messaging.publish(key, shard, msg)
        return d






