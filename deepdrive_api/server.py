from __future__ import (absolute_import, division,
                        print_function, unicode_literals)

import json
import time

from future.builtins import (dict, input, str)
import logging as log

import zmq
import pyarrow
from gym import spaces

import deepdrive_api.constants as c
import deepdrive_api.methods as m

log.basicConfig(level=log.INFO)

CONN_STRING = "tcp://*:%s" % c.API_PORT

BLACKLIST_PARAMS = [
    # We are the server, so the sim is always local to us,
    # remote to a client somewhere
    'is_remote_client',

    # Distributed tf sessions are not implemented and probably
    # wouldn't be passed this way anyway. This param is just
    # for sharing local tf sessions on the same GPU.
    'sess',
]

CHALLENGE_BLACKLIST_PARAMS = {
    'env_id': 'Only one gym env',
    'max_steps': 'Evaluation duration is standard across submissions',
    'use_sim_start_command': 'Cannot pass parameters to Unreal',

    # TODO: Step timeout and variable step duration less than threshold
    'fps': 'Step duration is capped',

    'driving_style': 'Modifies reward function',
    'enable_traffic': 'Changes difficulty of scenario',
    'ego_mph': 'Used by in-game throttle PID, '
               'submissions must control their own throttle',
}


class Server(object):
    """Deepdrive server process that runs on same machine as Unreal Engine.

    self.sim is a OpenAI gym environment factory which creates a new gym
    environment on start().

    Simple ZMQ / pyarrow server that runs the deepdrive gym environment locally,
    which communicates with Unreal locally via shared mem and localhost.
    """
    def __init__(self, sim, sim_args: dict = None):
        """
        :param sim: sim is a module reference to deepdrive.sim, i.e.
            https://github.com/deepdrive/deepdrive/tree/e114f9f053afe20d5a1478167d3f3c1f180fd279/sim
            Yes, this is a circular runtime reference and does not allow
            servers to be written in other languages, but I wanted to
            keep the client and server implementations together so client
            implementations in other languages would have be able to reference
            everything here in one place.
        :param sim: Sim args configured on the server side. This precludes
            clients from configuring the environment for situations where
            some standardized sim is expected, i.e. leaderboard evals,
            challenges, etc...
        """
        self.sim = sim
        self.sim_args = sim_args

        self.socket = None
        self.context = None
        self.env = None
        self.serialization_errors = set()

        # Once set, client gets a few seconds to close, then we force close
        self.should_close_time: float = 0

    def create_socket(self):
        if self.socket is not None:
            log.info('Closed server socket')
            self.socket.close()
        if self.context is not None:
            log.info('Destroyed context')
            self.context.destroy()

        self.context = zmq.Context()
        socket = self.context.socket(zmq.PAIR)
        # socket.RCVTIMEO = c.API_TIMEOUT_MS
        # socket.SNDTIMEO = c.API_TIMEOUT_MS
        socket.bind(CONN_STRING)
        self.socket = socket
        return socket

    def run(self):
        self.create_socket()
        log.info('Environment server started at %s', CONN_STRING)
        done = False
        while not done:
            try:
                done = self.dispatch()
            except zmq.error.Again:
                log.info('Waiting for client')
                self.create_socket()

    def dispatch(self):
        """
        Waits for a message from the client, deserializes, routes to the
        appropriate method, and sends a serialized response.
        """
        msg = self.socket.recv()
        if not msg:
            log.error('Received empty message, skipping')
            return
        method, args, kwargs = pyarrow.deserialize(msg)
        done = False
        resp = None

        if self.env is None and method != m.START:
            resp = 'No environment started, please send start request'
            log.error('Client sent request with no environment started')
        elif method == m.CLOSE:
            resp = self.env.close()
            done = True
        elif self.env is not None and self.env.unwrapped.should_close:
            if self.should_close_time == 0:
                self.should_close_time = time.time() + 3
            elif time.time() > self.should_close_time:
                self.env.close()
                done = True
            resp = 'Simulation closing'
        elif method == m.START:
            resp = self.handle_start_sim_request(kwargs)
        elif method == m.STEP:
            resp = self.env.step(args[0])
        elif method == m.RESET:
            resp = self.env.reset()
        elif method == m.ACTION_SPACE or method == m.OBSERVATION_SPACE:
            resp = self.serialize_space(self.env.action_space)
        elif method == m.REWARD_RANGE:
            resp = self.env.reward_range
        elif method == m.METADATA:
            resp = self.env.metadata
        elif method == m.CHANGE_CAMERAS:
            resp = self.env.unwrapped.change_cameras(*args, **kwargs)
        else:
            log.error('Invalid API method')
            resp = 'Invalid API method'
        serialized = self.serialize(resp)
        if serialized is None:
            raise RuntimeError('Could not serialize response. '
                               'Check above for details')
        self.socket.send(serialized.to_buffer())
        return done

    def handle_start_sim_request(self, kwargs):
        if self.sim_args is not None:
            sim_args = self.sim_args
            ret = 'Started locally configured server'
        else:
            sim_args = kwargs
            ret = 'Started remotely configured server'
        self.remove_blacklisted_params(kwargs)
        self.env = self.sim.start(**(sim_args.to_dict()))
        return ret

    def serialize(self, resp):
        serialized = None
        while serialized is None:
            try:
                serialized = pyarrow.serialize(resp)
            except pyarrow.lib.SerializationCallbackError as e:
                msg = str(e)
                self.remove_unserializeables(resp, msg)
        return serialized

    def remove_unserializeables(self, x, msg):
        """
        Make an object serializeable by pyarrow after an error by checking for the type
        in msg. Pyarrow doesn't have a great API for serializable types, so doing this as a
        stop gap for now.
        We should avoid sending unserializable data to pyarrow, but at the same time not
        totally fail when we do. Errors will be printed when unserializable data is first
        encountered, so that we can go back and remove when it's reasonable.
        This will not remove a list or tuple item, but will recursively search through
        lists and tuples for dicts with unserializeable values.

        :param x: Object from which to remove elements that pyarrow cannot serialize
        :param msg: The error message returned by pyarrow during serizialization
        :return:
        """
        if isinstance(x, dict):
            for k, v in x.items():
                value_type = str(type(v))
                if value_type in msg:
                    if value_type not in self.serialization_errors:
                        self.serialization_errors.add(value_type)
                        log.warning('Unserializable type %s Not sending to '
                                    'client!', value_type)
                    x[k] = '[REMOVED!] %s was not serializable on server. ' \
                           'Avoid sending unserializable data for best ' \
                           'performance.' % value_type
                if isinstance(v, dict) or isinstance(v, list) or \
                        isinstance(v, tuple):
                    # No __iter__ as numpy arrays are too big for this
                    self.remove_unserializeables(v, msg)
        elif isinstance(x, tuple) or isinstance(x, list):
            for e in x:
                self.remove_unserializeables(e, msg)

    @staticmethod
    def remove_blacklisted_params(kwargs):
        for key in list(kwargs):
            if key in BLACKLIST_PARAMS:
                log.warning('Removing {key} from sim start args, not'
                            ' relevant to remote clients'.format(key=key))
                del kwargs[key]

    @staticmethod
    def serialize_space(space):
        space_type = type(space)
        if space_type == spaces.Box:
            resp = {'type': str(space_type),
                    'low': space.low,
                    'high': space.high,
                    'dtype': str(space.dtype)
                    }
        else:
            raise RuntimeError('Space of type "%s" value "%r" not supported'
                               % (str(space_type), space))
        return resp


def start(sim, sim_path=None, sim_args=None):
    from deepdrive_api import utils
    if sim_path is not None:
        utils.check_pyarrow_compatibility(sim_path)
    server = Server(sim, sim_args)
    server.run()
