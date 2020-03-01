from __future__ import (absolute_import, division,
                        print_function, unicode_literals)

import json

import simplejson
import time

from deepdrive_api.client import get_action
from future.builtins import (dict, input, str)

import zmq
import pyarrow
from gym import spaces

import deepdrive_api.constants as c
import deepdrive_api.methods as m
from deepdrive_api import logs

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

    self.sim is a OpenAI gym environment factory which creates a new gym
    environment on start().

    Simple ZMQ / pyarrow server that runs the deepdrive gym environment locally,
    which communicates with Unreal locally via shared mem and localhost.
    """
    def __init__(self, sim, json_mode: bool = False, sim_args: dict = None):
        """
        :param sim: sim is a module reference to deepdrive.sim, i.e.
            https://github.com/deepdrive/deepdrive/tree/e114f9f053afe20d5a1478167d3f3c1f180fd279/sim
            Yes, this is a circular runtime reference and does not allow
            servers to be written in other languages, but I wanted to
            keep the client and server implementations together so client
            implementations in other languages would have be able to reference
            everything here in one place.
        :param json_mode: Allows sending / receiving all data in json to avoid
            dependency on pyarrow. Sensor data will be omitted in this case.
        :param sim: Sim args configured on the server side. This precludes
            clients from configuring the environment for situations where
            some standardized sim is expected, i.e. leaderboard evals,
            challenges, etc...
        """
        self.sim = sim
        self.sim_args = sim_args
        self.json_mode = json_mode

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
        if self.json_mode:
            msg = self.socket.recv_json()
            if not msg:
                log.error('Received empty message, skipping')
                return
            method, args, kwargs = msg['method'], msg['args'], msg['kwargs']
        else:
            msg = self.socket.recv()
            if not msg:
                log.error('Received empty message, skipping')
                return
            method, args, kwargs = pyarrow.deserialize(msg)

        done = False

        if self.env is None and method != m.START:
            resp = 'No environment started, please send start request'
            log.error('Client sent request with no environment started')
        elif method == m.CLOSE:
            self.env.close()
            resp = dict(closed_sim=True)
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
            if self.json_mode:
                action = get_action(**kwargs)
            else:
                action = args[0]
            resp = self.get_step_response(action)
        elif method == m.RESET:
            resp = dict(reset_response=self.env.reset())
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
        if self.json_mode:
            self.socket.send_string(serialized)
        else:
            self.socket.send(serialized.to_buffer())
        return done

    def get_step_response(self, action):
        resp = self.env.step(action)
        if self.json_mode:
            obs, reward, done, info = resp
            if obs:
                obs = self.get_filtered_observation(obs)
            else:
                obs = None
            resp = dict(
                observation=obs,
                reward=reward,
                done=done,
                info=info,
            )
        return resp

    def handle_start_sim_request(self, kwargs):
        if self.sim_args is not None:
            sim_args = self.sim_args
            server_type = 'locally_configured'
            if 'path_follower' in kwargs and \
                    kwargs['path_follower'] and 'map' in kwargs and \
                    sim_args['map'] != '':
                # Hack to deal with release / request bug in sim on new maps
                sim_args['path_follower'] = kwargs['path_follower']
        else:
            sim_args = kwargs
            server_type = 'remotely_configured'
        self.remove_blacklisted_params(kwargs)
        self.env = self.sim.start(**sim_args)
        ret = dict(server_started=dict(type=server_type))
        return ret

    def serialize(self, resp):
        if self.json_mode:
            ret = simplejson.dumps(resp, ignore_nan=True)
        else:
            ret = self.serialize_pyarrow(resp)
        return ret

    @staticmethod
    def get_filtered_observation(obs):
        coll = obs['last_collision']
        filtered = dict(
            accerlation=obs['acceleration'].tolist(),
            angular_acceleration=obs['angular_acceleration'].tolist(),
            angular_velocity=obs['angular_velocity'].tolist(),
            brake=obs['brake'],
            # Skipping cameras for now (base64??)
            capture_timestamp=obs['capture_timestamp'],
            dimension=obs['dimension'].tolist(),
            distance_along_route=obs['distance_along_route'],
            distance_to_center_of_lane=obs['distance_to_center_of_lane'],
            distance_to_next_agent=obs['distance_to_next_agent'],
            distance_to_next_opposing_agent=obs[
                'distance_to_next_opposing_agent'],
            distance_to_prev_agent=obs['distance_to_prev_agent'],
            episode_return=obs['episode_return'],
            forward_vector=obs['forward_vector'].tolist(),
            handbrake=obs['handbrake'],
            is_game_driving=obs['is_game_driving'],
            is_passing=obs['is_passing'],
            is_resetting=obs['is_resetting'],
            lap_number=obs['lap_number'],
            last_collision=dict(
                collidee_velocity=coll['collidee_velocity'].tolist(),
                collision_location=coll['collision_normal'].tolist(),
                collision_normal=coll['collision_normal'].tolist(),
                time_since_last_collision=coll['time_since_last_collision'],
                time_stamp=coll['time_stamp'],
                time_utc=coll['time_utc'],
            ),
            position=obs['position'].tolist(),
            right_vector=obs['right_vector'].tolist(),
            rotation=obs['rotation'].tolist(),
            route_length=obs['route_length'],
            scenario_finished=obs['scenario_finished'],
            speed=obs['speed'],
            steering=obs['steering'],
            throttle=obs['throttle'],
            up_vector=obs['up_vector'].tolist(),
            velocity=obs['velocity'].tolist(),
            world=obs['world'],
        )
        return filtered

    def serialize_pyarrow(self, resp):
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



def start(sim, json_mode=False, sim_path=None, sim_args: dict = None):
    from deepdrive_api import utils
    if sim_path is not None:
        utils.check_pyarrow_compatibility(sim_path)
    server = Server(sim=sim, json_mode=json_mode, sim_args=sim_args)
    server.run()
