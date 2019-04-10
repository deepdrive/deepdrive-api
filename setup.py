from os.path import abspath, dirname, join
from setuptools import setup

# Read the README markdown data from README.md
with open(abspath(join(dirname(__file__), 'README.md')), 'rb') as readme_file:
    __readme__ = readme_file.read().decode('utf-8')

setup(
    name='deepdrive-api',
    version='3.0.20190410042042',
    description='Deepdrive API used to run agents over the network',
    long_description=__readme__,
    long_description_content_type='text/markdown',
    classifiers=[
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Environment :: Console'
    ],
    keywords='deepdrive api',
    url='http://github.com/deepdrive/deepdrive-api',
    author='Deepdrive',
    author_email='craig@deepdrive.io',
    license='MIT',
    packages=['deepdrive_api'],
    zip_safe=True,
    python_requires='>=3.5',
    install_requires=[
        'setuptools>=38.6.0',
        'twine>=1.11.0',
        'pyarrow==0.12.1',
        'pyzmq',
        'future',
        'gym==0.10.0',
        'numpy>=1.16.1',
        'wheel>=0.31.0'
    ]
)
