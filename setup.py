from setuptools import setup, find_packages

setup(
    name='awslake',
    version='1.0',
    author='Fahad Ahmed',
    author_email='fahad.ahmed95@live.com',
    description='Communicate with AWS infrastructure',
    packages=[''],
    url='',
    install_requires=[
        'boto3',
        'botocore',
        'paramiko'
    ],
    license='Apache License 2.0',
    classifier = [
        'Development Status :: 4 - Beta',
        'Framework :: AWS CDK',
        'Programming Language :: Python :: 3.10'
    ]
)
