from __future__ import (absolute_import, division,
                        print_function, unicode_literals)

import logging as log
import os
import stat

import pkg_resources
from deepdrive_api.run_command import run_command

log.basicConfig(level=log.INFO)


def get_uepy_path(sim_path):
    ret = os.path.join(
        sim_path,
        'Engine/Plugins/UnrealEnginePython/EmbeddedPython/Linux/bin/python3')
    return ret


def ensure_uepy_executable(sim_path):
    """
    Ensure the UEPY python binary is executable
    :param path: Path to UEPy python binary
    :return:
    """
    uepy = get_uepy_path(sim_path)
    st = os.stat(uepy)
    os.chmod(uepy, st.st_mode | stat.S_IEXEC)
    return uepy


def get_uepy_pyarrow_version(sim_path):
    uepy = ensure_uepy_executable(sim_path)
    show, ret_code = run_command(
        '{uepy} -m pip show pyarrow'.format(uepy=uepy))
    version_line = [x for x in show.split('\n') if x.startswith('Version')][0]
    version = version_line.replace('Version: ', '').strip()
    return version


def check_pyarrow_compatibility(sim_path):
    """
    Different versions of pyarrow on serialization and deserialization ends
    can cause segmentation faults in Unreal
    :param get_py_arrow_version_fn:  Function that gets the pyarrow
        version running within Unreal Engine.
        See here for a reference implementation: https://github.com/deepdrive/deepdrive/blob/c38c9f71099f308c15d45750e653863af01f182d/util/ensure_sim.py#L139-L148

    :return: bool indicating whether the versions are the same
    """
    uepy_pyarrow_version = get_uepy_pyarrow_version(sim_path)
    local_pyarrow_version = pkg_resources.get_distribution('pyarrow').version
    versions_equal = uepy_pyarrow_version == local_pyarrow_version
    if not versions_equal:
        log.warning(r"""

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
    # https://boxes.thomasjensen.com
    # boxes -l #  list options
    # sudo apt-get install boxes
    # cat <msg-file> | boxes -d ian_jones
    return versions_equal
