Deepdrive API
=============

Python implementation of Deepdrive API used to run agents within the
 [Deepdrive sim](https://github.com/deepdrive/deepdrive-sim) over the network.

The [server](deepdrive_api/server.py) accepts messages over ZMQ serialized with 
Apache Arrow, allowing agents to be written in any language. 
This will run locally alongside the simulation (Unreal).


A reference client implementation in python can be found in 
[client.py](deepdrive_api/client.py).

To install the package, run `pip3 install deepdrive-api`

## Example usage

https://github.com/crizcraig/forward-agent

## Development

### PyPi upload

```
./pypi_upload.sh
```

## Legal

Copyright &copy; 2019, [Deepdrive](https://deepdrive.io/). 
Licensed under the MIT License, see the file [LICENSE](https://github.com/deepdrive/deepdrive-ci/blob/master/LICENSE) for details.
