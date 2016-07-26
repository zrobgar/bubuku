#!/usr/bin/env python3
import functools
import logging
import os
import re
from time import sleep

from kazoo.client import NoNodeError

from bubuku.amazon import Amazon
from bubuku.config import KafkaProperties
from bubuku.zookeeper import Exhibitor

_LOG = logging.getLogger('bubuku.id_generator')


class BrokerIdGenerator(object):
    def __init__(self, zk: Exhibitor):
        self.zk = zk

    def get_broker_id(self):
        raise NotImplementedError('Not implemented')

    def wait_for_broker_id_absence(self):
        while self.is_registered():
            sleep(1)

    def _is_registered_in_zk(self, id_):
        try:
            return self.zk.get('/brokers/ids/{}'.format(id_)) is not None
        except NoNodeError:
            return False

    def wait_for_broker_id_presence(self):
        while not self.is_registered():
            sleep(1)
        return True

    def is_registered(self):
        raise NotImplementedError('Not implemented')


def _create_rfc1918_address_hash(ip: str) -> int:
    address = [int(v) for v in ip.split('.')]
    # the goal of this hashing is to get positive 4-bytes int which can not be changed during restarts
    if address[0] == 10:
        address[0] = 1
    elif address[0] == 192 and address[1] == 168:
        address[0] = 2
    elif address[0] == 172 and (address[1] & 0xF0) == 16:
        address[0] = 3
    else:
        return None
    return str(functools.reduce(lambda o, v: o * 256 + v, address, 0))


class BrokerIDByIp(BrokerIdGenerator):
    def __init__(self, zk: Exhibitor, ip: str):
        super().__init__(zk)
        self.broker_id = _create_rfc1918_address_hash(ip)
        _LOG.info('Built broker id {} from ip: {}'.format(self.broker_id, ip))
        if self.broker_id is None:
            raise NotImplementedError('Broker id from ip address supported only for rfc1918 private addresses')

    def get_broker_id(self):
        return self.broker_id

    def is_registered(self):
        return self._is_registered_in_zk(self.broker_id)


class BrokerIdAutoAssign(BrokerIdGenerator):
    def __init__(self, zk: Exhibitor, kafka_properties: KafkaProperties):
        super().__init__(zk)
        self.kafka_properties = kafka_properties
        self.broker_id = None

    def get_broker_id(self):
        return None

    def is_registered(self):
        meta_path = '{}/meta.properties'.format(self.kafka_properties.get_property('log.dirs'))
        while not os.path.isfile(meta_path):
            return False
        with open(meta_path) as f:
            lines = f.readlines()
            for line in lines:
                match = re.search('broker\.id=(\d+)', line)
                if match:
                    return self._is_registered_in_zk(match.group(1))
        return False


def get_broker_id_policy(policy: str, zk: Exhibitor, kafka_props: KafkaProperties, amazon: Amazon) -> BrokerIdGenerator:
    if policy == 'ip':
        return BrokerIDByIp(zk, amazon.get_own_ip())
    elif policy == 'auto':
        return BrokerIdAutoAssign(zk, kafka_props)
    else:
        raise Exception('Unsupported id generator policy')