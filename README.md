Deepdrive API
=============

Python implementation of Deepdrive API used to run agents within the [Deepdrive sim](https://github.com/deepdrive/deepdrive-sim) over the network.

The Deepdrive sim server accepts messages over ZMQ serialized with Apache Arrow, allowing agents to be written in any language that also supports these libs.

To install the package, run: `pip3 install deepdrive-api`


## PyPi upload

```
# Build
python setup.py sdist bdist_wheel

# Check files
tar tzf dist/deepdrive-api-*.gz

# Upload to test PyPi [optional]
twine upload --repository-url https://test.pypi.org/legacy/ dist/*

# Upload to PyPi
twine upload dist/*


```

## Legal

Copyright &copy; 2019, [Deepdrive](https://deepdrive.io/). Licensed under the MIT License, see the file [LICENSE](https://github.com/deepdrive/deepdrive-ci/blob/master/LICENSE) for details.
