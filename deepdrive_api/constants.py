import os

DEFAULT_CAM = dict(name='My cam!',
                   field_of_view=60,
                   capture_width=227,
                   capture_height=227,

                   # forward, right, up in cm
                   relative_position=[150, 1.0, 200],

                   # roll, pitch, yaw in degrees
                   relative_rotation=[0.0, 0.0, 0.0])

API_PORT = 5557
SIM_HOST = os.environ.get('DEEPDRIVE_SIM_HOST', 'localhost')
