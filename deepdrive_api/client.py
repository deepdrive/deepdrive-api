from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
import numpy as np
import zmq
import pyarrow
from gym import spaces

import deepdrive_api.methods as m
import deepdrive_api.constants as c
from deepdrive_api import logs


log = logs.get_log(__name__)


def deserialize_space(resp):
    if resp['type'] == "<class 'gym.spaces.box.Box'>":
        ret = spaces.Box(resp['low'], resp['high'], dtype=resp['dtype'])
    else:
        raise RuntimeError('Unsupported action space type')
    return ret


class Client(object):
    """
    A Client object acts as a remote proxy to the deepdrive gym environment.
    Methods that you would call on the env, like step() are also called on
    this object, with communication over the network -
    rather than over shared memory (for observations) and network
    (for transactions like reset) as is the case with the locally run
    sim/gym_env.py.
    This allows the agent and environment to run on separate machines, but
    with the same API as a local agent, namely the gym API.

    The local gym environment is then run by api/server.py which proxies
    RPC's from this client to the local environment.

    All network communication happens over ZMQ to take advantage of their
    highly optimized cross-language / cross-OS sockets.

    NOTE: This will obviously run more slowly than a local agent which
    communicates sensor data over shared memory.
    """
    def __init__(self, **kwargs):
        """

        :param kwargs['client_render'] (bool): Whether to render on this
            side of the client server connection.
            Passing kwargs['render'] = True will cause the server to render
            an MJPG stream at http://localhost:5000
        """
        self.socket = None
        self.last_obz = None
        self.create_socket()
        self.should_render = kwargs.get('client_render', False)
        self.is_open = True
        kwargs['cameras'] = kwargs.get('cameras', [c.DEFAULT_CAM])
        log.info('Waiting for sim to start on server...')
        # TODO: Fix connecting to an open sim
        self._send(m.START, kwargs=kwargs)
        self.is_open = True
        log.info('===========> Deepdrive sim started')

    def _send(self, method, args=None, kwargs=None):
        if method != m.START and not self.is_open:
            log.warning('Not sending, env is closed')
            return None
        args = args or []
        kwargs = kwargs or {}
        try:
            msg = pyarrow.serialize([method, args, kwargs]).to_buffer()
            self.socket.send(msg)
            return pyarrow.deserialize(self.socket.recv())
        except zmq.error.Again:
            log.info('Waiting for Deepdrive API server...')
            self.create_socket()
            return None

    def create_socket(self):
        if self.socket:
            self.socket.close()
        context = zmq.Context()
        socket = context.socket(zmq.PAIR)

        # Creating a new socket on timeout is not working when other ZMQ
        # connections are present in the process.
        # socket.RCVTIMEO = c.API_TIMEOUT_MS
        # socket.SNDTIMEO = c.API_TIMEOUT_MS

        connection_str = 'tcp://%s:%s' % (c.SIM_HOST, c.API_PORT)
        log.info('Deepdrive API client connecting to %s' % connection_str)

        socket.connect(connection_str)
        self.socket = socket
        return socket

    def step(self, action):
        if hasattr(action, 'as_gym'):
            # Legacy support for original agents written within deepdrive repo
            action = action.as_gym()
        ret = self._send(m.STEP, args=[action])
        obz, reward, done, info = ret
        if info.get('closed', False):
            self.handle_closed()
        if not obz:
            obz = None
        self.last_obz = obz
        if self.should_render:
            self.render()
        return obz, reward, done, info

    def reset(self):
        if self.is_open:
            return self._send(m.RESET)
        else:
            log.warning('Env closed, not resetting')
            return None

    def render(self):
        """
        We pass the obz through an instance variable to comply with
        the gym api where render() takes 0 arguments
        """
        if self.last_obz is not None:
            self.renderer.render(self.last_obz)

    def change_cameras(self, cameras):
        return self._send(m.CHANGE_CAMERAS, args=[cameras])

    def close(self):
        self._send(m.CLOSE)
        self.handle_closed()

    def handle_closed(self):
        self.is_open = False
        try:
            self.socket.close()
        except Exception as e:
            log.debug('Caught exception closing socket')

    @property
    def action_space(self):
        resp = self._send(m.ACTION_SPACE)
        ret = deserialize_space(resp)
        return ret

    @property
    def observation_space(self):
        resp = self._send(m.OBSERVATION_SPACE)
        ret = deserialize_space(resp)
        return ret

    @property
    def metadata(self):
        return self._send(m.METADATA)

    @property
    def reward_range(self):
        return self._send(m.REWARD_RANGE)


def get_action(steering=0, throttle=0, brake=0, handbrake=0, has_control=True):
    ret = [np.array([steering]),
           np.array([throttle]),
           np.array([brake]),
           np.array([handbrake]),
           has_control]
    return ret
