# -*- coding: utf-8 -*-
# -*- Mode: Python -*-
# vi:si:et:sw=4:sts=4:ts=4
import time
import uuid

from feat.agencies.contracts import ContractorState
from feat.agents.base import (descriptor, contractor, replay,
                              message, manager, recipient)
from feat.interface import contracts, protocols
from feat.common import delay, defer

from . import common


class DummyContractor(contractor.BaseContractor, common.Mock):

    protocol_id = 'dummy-contract'
    interest_type = protocols.InterestType.public

    def __init__(self, medium, *args, **kwargs):
        contractor.BaseContractor.__init__(self, medium, *args, **kwargs)
        common.Mock.__init__(self)

    @replay.immutable
    def _get_medium(self, state):
        return state.medium

    @common.Mock.stub
    def announced(announce):
        pass

    @common.Mock.stub
    def announce_expired():
        pass

    @common.Mock.stub
    def bid_expired():
        pass

    @common.Mock.stub
    def rejected(rejection):
        pass

    @common.Mock.stub
    def granted(grant):
        pass

    @common.Mock.stub
    def cancelled(grant):
        pass

    @common.Mock.stub
    def acknowledged(grant):
        pass

    @common.Mock.stub
    def aborted():
        pass


class DummyManager(manager.BaseManager, common.Mock):

    protocol_id = 'dummy-contract'

    initiate_timeout = 10
    grant_timeout = 10

    def __init__(self, *args, **kwargs):
        manager.BaseManager.__init__(self, *args, **kwargs)
        common.Mock.__init__(self)

    @replay.immutable
    def _get_medium(self, state):
        return state.medium

    @common.Mock.stub
    def initiate(self):
        pass

    @common.Mock.stub
    def bid(self, bid):
        pass

    @common.Mock.stub
    def closed(self):
        pass

    @common.Mock.stub
    def expired(self):
        pass

    @common.Mock.stub
    def cancelled(self, cancellation):
        pass

    @common.Mock.stub
    def completed(self, reports):
        pass

    @common.Mock.stub
    def aborted(self):
        pass


class TestManager(common.TestCase, common.AgencyTestHelper):

    protocol_type = 'Contract'
    protocol_id = 'dummy-contract'

    timeout = 3

    @defer.inlineCallbacks
    def setUp(self):
        common.AgencyTestHelper.setUp(self)
        desc = yield self.doc_factory(descriptor.Descriptor)
        self.log("Descriptor: %r", desc)
        self.agent = yield self.agency.start_agent(desc)

        self.contractors = []
        for x in range(3):
            endpoint, queue = self.setup_endpoint()
            self.contractors.append({'endpoint': endpoint, 'queue': queue})
        self.recipients = map(lambda x: x['endpoint'], self.contractors)
        self.queues = map(lambda x: x['queue'], self.contractors)

    def start_manager(self):
        self.manager =\
                self.agent.initiate_protocol(DummyManager, self.recipients)
        self.finished = self.manager.notify_finish()
        self.medium = self.manager._get_medium()
        self.session_id = self.medium.session_id
        return self.medium.wait_for_state(contracts.ContractState.initiated)

    def assertUnregistered(self, _, state):
        self.assertFalse(self.manager._get_medium().session_id in\
                             self.agent._listeners)
        self.assertEqual(state, self.manager._get_medium().state)
        self.assertTrue(self.finished.called)
        if state not in (contracts.ContractState.completed,
                         contracts.ContractState.terminated, ):
            self.assertFailure(self.finished, protocols.InitiatorFailed)
        return self.manager

    def _consume_all(self, *_):
        return defer.DeferredList(map(lambda x: x.get(), self.queues))

    def _put_bids(self, results, costs):
        '''
        Put "refuse" as a cost to send Refusal.
        Put "skip" to ignore
        '''

        defers = []
        for result, sender, cost in zip(results, self.recipients, costs):
            called, msg = result
            assert cost is not None
            if cost and cost == "skip":
                continue
            elif cost and cost == "refuse":
                bid = message.Refusal()
            else:
                bid = message.Bid()
                bid.payload['cost'] = cost

            self.log('Puting bid')
            defers.append(self.reply(bid, sender, msg))

        return defer.DeferredList(defers)

    def testInitiateTimeout(self):
        delay.time_scale = 0.01
        d = self.start_manager()
        d.addCallback(defer.drop_result, self.cb_after,
                      arg=None, obj=self.agent,
                      method='unregister_listener')
        d.addCallback(self.assertUnregistered, contracts.ContractState.wtf)

        return d

    def testSendAnnouncementAndWaitForExpired(self):
        delay.time_scale = 0.01
        d = self.start_manager()

        d.addCallback(defer.drop_result, self.send_announce, self.manager)
        d.addCallback(defer.drop_result, self._consume_all)

        def asserts_on_msgs(results):
            for result in results:
                called, arg = result
                self.assertTrue(called)
                self.assertTrue(isinstance(arg, message.Announcement))
                self.assertFalse(arg.traversal_id is None)

        d.addCallback(asserts_on_msgs)
        d.addCallback(lambda x: self.manager)
        d.addCallback(self.cb_after, obj=self.agent,
                      method='unregister_listener')
        d.addCallback(self.assertCalled, 'closed', times=0)
        d.addCallback(self.assertCalled, 'expired')
        d.addCallback(self.assertUnregistered, contracts.ContractState.expired)

        return d

    def testSendAnnouncementRecvBidsAndGoToClosed(self):
        delay.time_scale = 0.01
        d = self.start_manager()

        closed = self.cb_after(None, self.medium, '_on_announce_expire')

        d.addCallback(defer.drop_result, self.send_announce, self.manager)
        d = self._consume_all()
        d.addCallback(self._put_bids, (1, 1, "skip", ))
        d.addCallback(lambda _: closed)

        def asserts_on_manager(_):
            self.assertEqual(contracts.ContractState.closed, self.medium.state)
            self.assertEqual(2, len(self.medium.contractors))
            for bid in self.medium.contractors:
                self.assertTrue(isinstance(bid, message.Bid))

            return self.manager

        d.addCallback(asserts_on_manager)
        d.addCallback(self.assertCalled, 'bid', times=2)

        d.addCallback(self.cb_after, obj=self.agent,
                       method='unregister_listener')
        d.addCallback(self.assertUnregistered, contracts.ContractState.expired)

        return d

    def testRefuseAndGrantFromBidHandler(self):

        @replay.immutable
        def bid_handler(s, state, bid):
            s.log('Received bid: %r', bid.payload['cost'])
            if bid.payload['cost'] == 3:
                state.medium.reject(bid)
            elif bid.payload['cost'] == 2:
                pass
            elif bid.payload['cost'] == 1:
                grant = message.Grant()
                state.medium.grant((bid, grant, ))

        d = self.start_manager()
        self.stub_method(self.manager, 'bid', bid_handler)

        d.addCallback(defer.drop_result, self.send_announce, self.manager)
        d.addCallback(defer.drop_result, self._consume_all)
        d.addCallback(self._put_bids, (3, 2, 1, ))

        d = self.queues[0].get()
        d.addCallback(self.assertIsInstance, message.Rejection)
        d.addCallback(lambda _: self.queues[1].get())
        d.addCallback(self.assertIsInstance, message.Rejection)
        d.addCallback(lambda _: self.queues[2].get())
        d.addCallback(self.assertIsInstance, message.Grant)

        def asserts_on_manager(_):
            self.assertEqual(3, len(self.medium.contractors))
            self.assertEqual(1, len(self.medium.contractors.with_state(\
                        ContractorState.granted)))
            self.assertEqual(2, len(self.medium.contractors.with_state(\
                        ContractorState.rejected)))

        d.addCallback(asserts_on_manager)
        ex = protocols.InitiatorExpired('timeout')
        d.addCallback(lambda _: self.medium._terminate(ex))
        d.addCallback(self.assertUnregistered, contracts.ContractState.closed)

        return d

    def testGrantingFromClosedState(self):
        delay.time_scale = 0.01

        @replay.immutable
        def closed_handler(s, state):
            s.log('Contracts closed, sending grants')
            to_grant = filter(lambda x: x.payload['cost'] < 3,
                              state.medium.contractors)
            params = map(lambda bid: (bid, message.Grant(), ),
                         to_grant)
            state.medium.grant(params)

        d = self.start_manager()
        self.stub_method(self.manager, 'closed', closed_handler)

        d.addCallback(defer.drop_result, self.send_announce, self.manager)
        d.addCallback(defer.drop_result, self._consume_all)
        d.addCallback(self._put_bids, (3, 2, 1, ))

        d.addCallback(self.cb_after, obj=self.manager, method='closed')
        d.addCallback(lambda _: self.queues[0].get())
        d.addCallback(self.assertIsInstance, message.Rejection)
        d.addCallback(lambda _: self.queues[1].get())
        d.addCallback(self.assertIsInstance, message.Grant)
        d.addCallback(lambda _: self.queues[2].get())
        d.addCallback(self.assertIsInstance, message.Grant)

        def asserts_on_manager(_):
            self.assertEqual(3, len(self.medium.contractors))
            self.assertEqual(2, len(self.medium.contractors.with_state(\
                        ContractorState.granted)))
            self.assertEqual(1, len(self.medium.contractors.with_state(\
                        ContractorState.rejected)))

        d.addCallback(asserts_on_manager)

        d.addCallback(lambda _: self._terminate_manager())
        d.addCallback(self.assertUnregistered,
                      contracts.ContractState.aborted)

        return d

    def testTerminatingFromClosedState(self):
        delay.time_scale = 0.01

        @replay.immutable
        def closed_handler(s, state):
            s.log('Contracts closed, terminating.')
            to_elect = filter(lambda x: x.payload['cost'] == 3,
                              state.medium.contractors)[0]
            state.medium.elect(to_elect)
            state.medium.terminate()

        d = self.start_manager()
        self.stub_method(self.manager, 'closed', closed_handler)

        d.addCallback(defer.drop_result, self.send_announce, self.manager)
        d.addCallback(defer.drop_result, self._consume_all)
        d.addCallback(self._put_bids, (3, 2, 1, ))

        d.addCallback(self.cb_after, obj=self.manager, method='closed')
        d.addCallback(lambda _: self.assert_queue_empty(self.queues[0]))
        d.addCallback(lambda _: self.queues[1].get())
        d.addCallback(self.assertIsInstance, message.Rejection)
        d.addCallback(lambda _: self.queues[2].get())
        d.addCallback(self.assertIsInstance, message.Rejection)

        def asserts_on_manager(_):
            self.assertEqual(3, len(self.medium.contractors))
            self.assertEqual(2, len(self.medium.contractors.with_state(\
                        ContractorState.rejected)))
            self.assertEqual(1, len(self.medium.contractors.with_state(\
                        ContractorState.elected)))

        d.addCallback(asserts_on_manager)

        d.addCallback(self.assertUnregistered,
                      contracts.ContractState.terminated)

        return d

    def testRefusingContractors(self):
        delay.time_scale = 0.01
        d = self.start_manager()

        closed = self.cb_after(None, self.medium, '_on_announce_expire')

        d.addCallback(defer.drop_result, self.send_announce, self.manager)
        d.addCallback(defer.drop_result, self._consume_all)
        # None stands for Refusal
        d.addCallback(self._put_bids, ("refuse", "refuse", "refuse", ))
        d.addCallback(lambda _: closed)

        def asserts_on_manager(_):
            self.assertEqual(contracts.ContractState.expired,
                             self.medium.state)
            self.assertEqual(3, len(self.medium.contractors))
            for contractor in self.medium.contractors.values():
                self.assertEqual(ContractorState.refused, contractor.state)

            return self.manager

        d.addCallback(asserts_on_manager)
        d.addCallback(self.assertCalled, 'bid', times=0)
        d.addCallback(self.assertCalled, 'closed', times=0)
        d.addCallback(self.assertCalled, 'expired', times=1, params=[])

        d.addCallback(self.assertUnregistered, contracts.ContractState.expired)

        return d

    def testTimeoutAfterGrant(self):
        delay.time_scale = 0.01

        @replay.immutable
        def bid_handler(s, state, bid):
            state.medium.grant((bid, message.Grant(), ))

        d = self.start_manager()
        self.stub_method(self.manager, 'bid', bid_handler)

        d.addCallback(defer.drop_result, self.send_announce, self.manager)
        d.addCallback(defer.drop_result, self._consume_all)
        # None stands for Refusal
        d.addCallback(self._put_bids, (1, 2, 3, ))

        d.addCallback(self.cb_after, obj=self.agent,
                      method='unregister_listener')
        d.addCallback(self.assertUnregistered, contracts.ContractState.aborted)
        d.addCallback(lambda _: self.manager)
        d.addCallback(self.assertCalled, 'aborted', params=[])

        return d

    def testRecvCancellation(self):
        delay.time_scale = 0.01

        @replay.immutable
        def closed_handler(s, state):
            s.log('Contracts closed, granting everybody')
            params = map(lambda bid: (bid, message.Grant(), ),
                         state.medium.contractors.keys())
            state.medium.grant(params)

        d = self.start_manager()
        self.stub_method(self.manager, 'closed', closed_handler)

        d.addCallback(defer.drop_result, self.send_announce, self.manager)
        d.addCallback(defer.drop_result, self._consume_all)
        d.addCallback(self._put_bids, (3, 2, 1, ))
        d.addCallback(lambda _: self.queues[2].get()) #just swallow
        d.addCallback(lambda _: self.queues[0].get())

        def complete_one(grant):
            msg = message.FinalReport()
            endpoint = self.recipients[0]
            return self.reply(msg, endpoint, grant)

        d.addCallback(complete_one)

        def asserts_on_manager(_):
            self.assertEqual(1, len(
                self.medium.contractors.with_state(ContractorState.completed)))
            self.assertEqual(2, len(
                self.medium.contractors.with_state(ContractorState.granted)))

        d.addCallback(lambda _: self.queues[1].get())

        def cancel_one(grant):
            msg = message.Cancellation(reason='Ad majorem dei gloriam!')
            endpoint = self.recipients[1]
            return self.reply(msg, endpoint, grant)

        d.addCallback(cancel_one)

        d.addCallback(lambda _: self.queues[0].get())
        d.addCallback(self.assertIsInstance, message.Cancellation)
        d.addCallback(lambda _: self.queues[2].get())
        d.addCallback(self.assertIsInstance, message.Cancellation)

        def asserts_on_manager2(_):
            self.assertEqual(3, len(
                self.medium.contractors.with_state(ContractorState.cancelled)))
            self.assertCalled(self.manager, 'cancelled', params=[])

        d.addCallback(asserts_on_manager2)
        d.addCallback(self.assertUnregistered,
                      contracts.ContractState.cancelled)

        return d

    def testContactorsFinishAckSent(self):
        delay.time_scale = 0.01

        @replay.immutable
        def closed_handler(s, state):
            s.log('Contracts closed, granting everybody')
            params = map(lambda bid: (bid, message.Grant(), ),
                         state.medium.contractors.keys())
            state.medium.grant(params)

        d = self.start_manager()
        self.stub_method(self.manager, 'closed', closed_handler)

        d.addCallback(defer.drop_result, self.send_announce, self.manager)
        d.addCallback(defer.drop_result, self._consume_all)
        d.addCallback(self._put_bids, (3, 2, 1, ))
        d.addCallback(self._consume_all)

        def finish_all(results):
            for (called, grant), recipient in zip(results, self.recipients):
                msg = message.FinalReport()
                self.reply(msg, recipient, grant)

        d.addCallback(finish_all)

        d.addCallback(self._consume_all)

        def assert_acked(results):
            for called, msg in results:
                self.assertIsInstance(msg, message.Acknowledgement)

        d.addCallback(assert_acked)

        d.addCallback(lambda _: self.manager)
        d.addCallback(self.assertCalled, 'completed', params=[list])
        d.addCallback(self.assertUnregistered,
                      contracts.ContractState.completed)

        return d

    @defer.inlineCallbacks
    def testCountingExpectedBids(self):
        yield self.start_manager()

        self.assertEqual(len(self.recipients),
            self.manager._get_medium()._count_expected_bids(self.recipients))
        broadcast = recipient.Broadcast('some protocol')
        self.assertEqual(None,
               self.manager._get_medium()._count_expected_bids(broadcast))
        self.assertEqual(None,
               self.manager._get_medium()._count_expected_bids(
                             self.recipients + [broadcast]))
        yield self._terminate_manager()
        self.assertUnregistered(None, contracts.ContractState.wtf)

    def _terminate_manager(self):
        d = self.manager._get_medium().expire_now()
        self.assertFailure(d, protocols.InitiatorExpired)
        return d

    def testGettingAllBidsGetsToClosed(self):
        d = self.start_manager()

        closed = self.cb_after(None, self.medium, '_close_announce_period')

        d.addCallback(defer.drop_result, self.send_announce, self.manager)
        d.addCallback(defer.drop_result, self._consume_all)
        d.addCallback(self._put_bids, (1, 1, 1, ))
        d.addCallback(lambda _: closed)

        def asserts_on_manager(_):
            self.assertEqual(3, self.medium.expected_bids)
            self.assertEqual(contracts.ContractState.closed, self.medium.state)
            self.assertEqual(3, len(self.medium.contractors))
            for bid in self.medium.contractors:
                self.assertTrue(isinstance(bid, message.Bid))

            return self.manager

        d.addCallback(asserts_on_manager)
        d.addCallback(self.assertCalled, 'bid', times=3)

        d.addCallback(lambda _: self._terminate_manager())
        d.addCallback(self.assertUnregistered, contracts.ContractState.expired)

        return d


class TestContractor(common.TestCase, common.AgencyTestHelper):

    protocol_type = 'Contract'
    protocol_id = 'dummy-contract'

    timeout = 3

    @defer.inlineCallbacks
    def setUp(self):
        common.AgencyTestHelper.setUp(self)
        desc = yield self.doc_factory(descriptor.Descriptor)
        self.agent = yield self.agency.start_agent(desc)
        self.agent.register_interest(DummyContractor)

        self.contractor = None
        self.session_id = None
        self.endpoint, self.queue = self.setup_endpoint()

    def tearDown(self):
        self._cancel_expiration_call_if_necessary()

    def testRecivingAnnouncement(self):
        d = self.recv_announce()

        def asserts(_):
            self.assertEqual(1, len(self.agent._listeners))

        d.addCallback(asserts)
        d.addCallback(self._get_contractor)

        def asserts_on_contractor(contractor):
            self.assertEqual(DummyContractor, contractor.__class__)
            self.assertCalled(contractor, 'announced', times=1)
            args = contractor.find_calls('announced')[0].args
            self.assertEqual(1, len(args))
            medium = contractor._get_medium()
            self.assertEqual(contracts.ContractState.announced,
                             medium.state)

        d.addCallback(asserts_on_contractor)

        return d

    @defer.inlineCallbacks
    def testRecivingAnnouncementTwoTimes(self):
        '''
        This test checks that mechanics of storing traversal ids works
        correctly. Second announcement with same traversal id
        should be ignored.
        '''
        delay.time_scale = 1
        expiration_time = time.time() + 1
        yield self.recv_announce(expiration_time, traversal_id='first')

        self.assertEqual(1, self._get_number_of_listeners())
        yield self._expire_contractor()
        self.assertEqual(0, self._get_number_of_listeners())

        yield self.recv_announce(expiration_time, traversal_id='first')
        self.assertEqual(0, self._get_number_of_listeners())

        yield self.recv_announce(expiration_time, traversal_id='other')
        self.assertEqual(1, self._get_number_of_listeners())
        yield self._expire_contractor()

        yield common.delay(None, 1)
        # now receive expired message
        yield self.recv_announce(expiration_time, traversal_id='first')
        self.assertEqual(0, self._get_number_of_listeners())

        yield self.recv_announce(expiration_time + 2, traversal_id='first')
        self.assertEqual(1, self._get_number_of_listeners())
        yield self._expire_contractor()

    def _expire_contractor(self):
        return self.agent._listeners.values()[0].expire_now()

    def _get_number_of_listeners(self):
        return len(self.agent._listeners.values())

    def testAnnounceExpiration(self):
        delay.time_scale = 0.01

        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.cb_after, obj=self.agent,\
                          method='unregister_listener')
        d.addCallback(self.assertCalled, 'announce_expired')

        return d

    def testPuttingBid(self):
        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.send_bid)

        def asserts(contractor):
            self.assertEqual(contracts.ContractState.bid,
                             contractor._get_medium().state)

        d.addCallback(asserts)
        d.addCallback(self.queue.get)

        def asserts_on_bid(msg):
            self.assertEqual(message.Bid, msg.__class__)
            self.assertEqual(self.contractor._get_medium().own_bid, msg)

        d.addCallback(asserts_on_bid)

        return d

    def testHandingOverTheBid(self):
        wait = self.cb_after(None, self.agent, 'unregister_listener')

        d = self.recv_announce()
        d.addCallback(self._get_contractor)

        def send_delegated_bid(contractor):
            msg = message.Bid()
            msg.reply_to = self.endpoint
            msg.expiration_time = time.time() + 10
            msg.protocol_type = self.protocol_type
            msg.protocol_id = self.protocol_id
            msg.message_id = str(uuid.uuid1())

            contractor._get_medium().handover(msg)
            return contractor

        d.addCallback(send_delegated_bid)

        d.addCallback(self.queue.get)

        def asserts_on_bid(msg):
            self.assertEqual(message.Bid, msg.__class__)
            self.assertEqual(self.contractor._get_medium().bid, msg)
            self.assertEqual(self.endpoint, msg.reply_to)

        d.addCallback(asserts_on_bid)
        d.addCallback(lambda _: wait)
        d.addCallback(self.assertUnregistered,
                      contracts.ContractState.delegated)

        return d

    def testPuttingBidAndReachingTimeout(self):
        delay.time_scale = 0.01

        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.send_bid)
        d.addCallback(self.cb_after, obj=self.agent,\
                          method='unregister_listener')
        d.addCallback(self.assertCalled, 'bid_expired')
        d.addCallback(self.assertUnregistered, contracts.ContractState.expired)

        return d

    def testRefusing(self):
        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.send_refusal)

        d.addCallback(self.assertUnregistered, contracts.ContractState.refused)
        d.addCallback(self.queue.get)

        def asserts_on_refusal(msg):
            self.assertEqual(message.Refusal, msg.__class__)
            self.assertEqual(self.contractor._get_medium().session_id,
                             msg.sender_id)

        d.addCallback(asserts_on_refusal)

        return d

    def testCorrectGrant(self):
        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.send_bid, 1)
        d.addCallback(self.recv_grant)

        def asserts(_):
            self.assertEqual(contracts.ContractState.granted,\
                                 self.medium.state)
            self.assertCalled(self.contractor, 'granted')
            call = self.contractor.find_calls('granted')[0]
            self.assertEqual(1, len(call.args))
            self.assertEqual(message.Grant, call.args[0].__class__)

        d.addCallback(asserts)

        return d

    def testGrantWithUpdater(self):
        delay.time_scale = 0.01

        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.send_bid, 1)
        d.addCallback(self.recv_grant, update_report=1)

        d.addCallback(self.queue.get) # this is a bid

        def assert_msg_is_report(msg):
            self.assertEqual(message.UpdateReport, msg.__class__)
            self.log("Received report message")

        for x in range(3):
            d.addCallback(self.queue.get)
            d.addCallback(assert_msg_is_report)

        d.addCallback(self._get_contractor)
        d.addCallback(
            lambda contractor: contractor._get_medium()._terminate(None))

        return d

    def testBidRejected(self):
        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.send_bid)
        d.addCallback(self.recv_rejection)

        d.addCallback(self.assertCalled, 'rejected',
                      params=[message.Rejection])
        d.addCallback(self.assertUnregistered,
                      contracts.ContractState.rejected)

        return d

    def testCancelingGrant(self):
        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.send_bid)
        d.addCallback(self.recv_grant)
        d.addCallback(self.send_cancel)

        d.addCallback(self.assertUnregistered,
                      contracts.ContractState.defected)

        return d

    def testCancellingByManager(self):
        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.send_bid)
        d.addCallback(self.recv_grant)
        d.addCallback(self.recv_cancel)

        d.addCallback(self.assertCalled, 'cancelled',
                      params=[message.Cancellation])
        d.addCallback(self.assertUnregistered,
                      contracts.ContractState.cancelled)

        return d

    def testSendingReportThanExpiring(self):
        delay.time_scale = 0.01

        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.send_bid)
        d.addCallback(self.recv_grant)
        d.addCallback(self.send_final_report)

        def asserts(contractor):
            self.assertEqual(contracts.ContractState.completed,
                             contractor._get_medium().state)

        d.addCallback(asserts)

        d.addCallback(self.cb_after, obj=self.agent,
                      method='unregister_listener')
        d.addCallback(self.assertUnregistered, contracts.ContractState.aborted)
        d.addCallback(self.assertCalled, 'aborted')

        return d

    def testSendingReportAndReceivingCancellation(self):
        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.send_bid)
        d.addCallback(self.recv_grant)
        d.addCallback(self.send_final_report)
        d.addCallback(self.recv_cancel)

        d.addCallback(self.assertCalled, 'aborted',
                      params=[])
        d.addCallback(self.assertUnregistered,
                      contracts.ContractState.aborted)

        return d

    def testCompletedAndAcked(self):
        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.send_bid)
        d.addCallback(self.recv_grant)
        d.addCallback(self.send_final_report)
        d.addCallback(self.recv_ack)

        d.addCallback(self.assertCalled, 'acknowledged',
                      params=[message.Acknowledgement])
        d.addCallback(self.assertUnregistered,
                      contracts.ContractState.acknowledged)

        return d

    def testReceivingFromIncorrectState(self):
        delay.time_scale = 0.01

        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.recv_grant)
        # this will be ignored, we follow the path to expiration

        d.addCallback(self.cb_after, obj=self.agent,\
                          method='unregister_listener')
        d.addCallback(self.assertCalled, 'announce_expired')
        d.addCallback(self.assertUnregistered, contracts.ContractState.closed)
        return d

    def testReceivingUnknownMessage(self):
        delay.time_scale = 0.01

        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(lambda contractor:
                 message.BaseMessage(receiver_id=\
                                     contractor._get_medium().session_id))
        d.addCallback(self.recv_msg)
        # this will be ignored, we follow the path to expiration

        d.addCallback(lambda _: self.contractor)
        d.addCallback(self.cb_after, obj=self.agent,
                      method='unregister_listener')
        d.addCallback(self.assertCalled, 'announce_expired')
        d.addCallback(self.assertUnregistered, contracts.ContractState.closed)
        return d

    def testSendingMessageFromIncorrectState(self):

        @replay.immutable
        def custom_handler(s, state, msg):
            s.log("Sending refusal from incorrect state")
            msg = message.Refusal()
            msg.session_id = state.medium.session_id
            state.medium.refuse(msg)

        d = self.recv_announce()
        d.addCallback(self._get_contractor)
        d.addCallback(self.send_bid)
        d.addCallback(self.stub_method, 'granted', custom_handler)
        d.addCallback(self.recv_grant)

        d.addCallback(self.assertUnregistered, contracts.ContractState.wtf)

        return d

    def assertUnregistered(self, _, state):
        self.assertFalse(self.medium.session_id in\
                             self.agent._listeners)
        self.assertEqual(state, self.medium.state)
        return self.contractor

    def _cancel_expiration_call_if_necessary(self):
        if self.contractor and self.medium._expiration_call and\
                not (self.medium._expiration_call.called or
                     self.medium._expiration_call.cancelled):
            self.warning("Canceling contractor expiration call in tearDown")
            self.medium._expiration_call.cancel()

    def _get_contractor(self, _):
        self.contractor = self.agent._listeners.values()[0].contractor
        self.medium = self.contractor._get_medium()
        self.remote_id = self.medium.session_id
        return self.contractor
