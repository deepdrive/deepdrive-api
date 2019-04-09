from __future__ import (absolute_import, division,
                        print_function, unicode_literals)

import pkg_resources
from future.builtins import (str)

import zmq
import pyarrow
from gym import spaces

import logs

import config as c
import api.methods as m
import sim
import util.ensure_sim
import utils

log = logs.get_log(__name__)


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

    Simple ZMQ / pyarrow server that runs the deepdrive project, communicates
    with Unreal locally via shared mem and localhost.
    """
    def __init__(self):
        self.socket = None
        self.context = None
        self.env = None
        self.serialization_errors = set()

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
        Waits for a message from the client, deserializes, routes to the appropriate method,
        and sends a serialized response.
        """
        msg = self.socket.recv()
        if not msg:
            log.error('Received empty message, skipping')
            return
        method, args, kwargs = pyarrow.deserialize(msg)
        resp = None
        done = False
        if self.env is None and method != m.START:
            resp = 'No environment started, please send start request'
            log.error('Client sent request with no environment started')
        elif method == m.START:
            self.remove_blacklisted_params(kwargs)
            self.env = sim.start(**kwargs)
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
        elif method == m.CLOSE:
            resp = self.env.close()
            done = True
        else:
            log.error('Invalid API method')
        serialized = self.serialize(resp)
        if serialized is None:
            raise RuntimeError('Could not serialize response. Check above for details')
        self.socket.send(serialized.to_buffer())
        return done

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

        :param obj: Object from which to remove elements that pyarrow cannot serialize
        :param msg: The error message returned by pyarrow during serizialization
        :return:
        """
        if isinstance(x, dict):
            for k, v in x.items():
                value_type = str(type(v))
                if value_type in msg:
                    if value_type not in self.serialization_errors:
                        self.serialization_errors.add(value_type)
                        log.warn('Unserializable type %s Not sending to client!', value_type)
                    x[k] = '[REMOVED!] %s was not serializable on server. ' \
                           'Avoid sending unserializable data for best performance.' % value_type
                if isinstance(v, dict) or isinstance(v, list) or isinstance(v, tuple):
                    # No __iter__ as numpy arrays are too big for this
                    self.remove_unserializeables(v, msg)
        elif isinstance(x, tuple) or isinstance(x, list):
            for e in x:
                self.remove_unserializeables(e, msg)

    @staticmethod
    def remove_blacklisted_params(kwargs):
        for key in list(kwargs):
            if key in BLACKLIST_PARAMS:
                log.warning('Removing {key} from sim start args, not relevant to remote clients'
                            .format(key=key))
                del kwargs[key]
            if c.IS_CHALLENGE and key in CHALLENGE_BLACKLIST_PARAMS:
                log.warning('Removing {key} from sim start args, '
                            'blacklisted for challenges. Reason: {reason}.'
                            .format(key=key, reason=CHALLENGE_BLACKLIST_PARAMS[key]))
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


def check_pyarrow_compatibility():
    """
    Different versions of pyarrow on serialization and deserialization ends
    can cause segmentation faults in Unreal
    :return: bool indicating whether the versions are the same
    """
    uepy_pyarrow_version = util.ensure_sim.get_uepy_pyarrow_version()
    local_pyarrow_version = pkg_resources.get_distribution('pyarrow').version
    versions_equal = uepy_pyarrow_version == local_pyarrow_version
    if not versions_equal:
        log.warn(r"""
        
                                 \\\///
                                / _  _ \
                              (| (.)(.) |)
.---------------------------.OOOo--()--oOOO.---------------------------.
|                                                                      |
| Pyarrow version mismatch!                                            |
|                                                                      |
| UEPy version: %s                                   |
| Local version: %s                                  |
|                                                                      |
| You may want to correct this if you see segfaults in UnrealEngine or |
| other unexplained failures.                                          |
|                                                                      |
'---------------------------.oooO--------------------------------------'
                             (   )   Oooo.
                              \ (    (   )
                               \_)    ) /
                                     (_/

""" % (uepy_pyarrow_version.ljust(20), local_pyarrow_version.ljust(20)))
    # sudo apt-get install boxes
    # gen cat <msg-file> | boxes -d ian_jones
    return versions_equal


def start():
    check_pyarrow_compatibility()
    server = Server()
    server.run()


if __name__ == '__main__':
    start()
