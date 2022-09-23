import json
import boto3
import logging
from botocore.exceptions import ClientError
import paramiko
from . import __version__

logger = logging.getLogger(__name__)


class DataLake:

    def __init__(self, region, aws_access_key=None, aws_secret_key=None):

        if aws_access_key is None:
            self.s3_client = boto3.client('s3', region_name=region)
            self.iam_client = boto3.client('iam', region_name=region)
            self.transfer_client = boto3.client('transfer', region_name=region)
        else:
            self.s3_client = boto3.client(service_name='s3',
                                          region_name=region,
                                          aws_access_key_id=aws_access_key,
                                          aws_secret_access_key=aws_secret_key)

            self.iam_client = boto3.client(service_name='iam',
                                           region_name=region,
                                           aws_access_key_id=aws_access_key,
                                           aws_secret_access_key=aws_secret_key)

            self.transfer_client = boto3.client(service_name='transfer',
                                                region_name=region,
                                                aws_access_key_id=aws_access_key,
                                                aws_secret_access_key=aws_secret_key)

        self.region = region
        self.aws_access_key = aws_access_key
        self.aws_secret_key = aws_secret_key
        self.server_id = None
        self.client = None
        self.sftp = None

    def create_bucket(self, bucket_name):
        """Create an S3 bucket in a specified region

        Args:
           bucket_name (str): Bucket to create

        Returns: True if bucket created, else False
        """
        assert isinstance(bucket_name, str)
        try:
            location = {'LocationConstraint': self.region}
            self.s3_client.create_bucket(Bucket=bucket_name, CreateBucketConfiguration=location)
            print(f"Created bucket {bucket_name}")
        except ClientError as e:
            logger.error(e)
            return False
        return True

    @property
    def list_buckets(self):

        return [name for name in self.s3_client.list_buckets()['Buckets']['Name']]

    def delete_bucket(self, bucket_name):
        assert isinstance(bucket_name, str)
        self.s3_client.delete_bucket(Bucket=bucket_name)

    def create_iam_s3_access_policy(self, bucket_name_list, policy_name):
        primary_resource_list = [f"arn:aws:s3:::{bucket}" for bucket in bucket_name_list]
        secondary_resource_list = [f"arn:aws:s3:::{bucket}/*" for bucket in bucket_name_list]
        policy = {"Version": "2012-10-17", "Statement": [
            {"Sid": "AllowListingOfUserFolder",
             "Effect": "Allow",
             "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
             "Resource": primary_resource_list},
            {"Sid": "HomeDirAccess",
             "Effect": "Allow",
             "Action": [
                 "s3:PutObject",
                 "s3:GetObject",
                 "s3:DeleteObject",
                 "s3:DeleteObjectVersion",
                 "s3:GetObjectVersion",
                 "s3:GetObjectACL",
                 "s3:PutObjectACL"
             ],
             "Resource": secondary_resource_list
             }
        ]}
        try:
            response = self.iam_client.create_policy(PolicyName=policy_name, PolicyDocument=json.dumps(policy))
            response = response['Policy']
        except ClientError as e:
            if e.response['Error']['Code'] == 'EntityAlreadyExists':
                logger.warning(f'Policy {policy_name} already exists')
                policies = self.iam_client.list_policies()['Policies']
                i = 0
                while policies[i]['PolicyName'] != policy_name:
                    i += 1
                    continue
                response = policies[i]
            else:
                raise e

        return response['PolicyName'], response['Arn']

    def create_role_and_attach_policy(self, service, iam_role_name, policies_arn=None):
        trust_relationships = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "Permit",
                    "Effect": "Allow",
                    "Principal": {
                        "Service": f"{service}.amazonaws.com"
                    },
                    "Action": "sts:AssumeRole"
                }
            ]
        }
        try:
            response_role = self.iam_client.create_role(RoleName=iam_role_name,
                                                        AssumeRolePolicyDocument=json.dumps(trust_relationships))
            logger.info('Created role %s.', response_role['Role']['RoleName'])
        except ClientError as e:
            if e.response['Error']['Code'] == 'EntityAlreadyExists':
                logger.warning(f'Role {iam_role_name} already exists')
                response_role = self.iam_client.get_role(RoleName=iam_role_name)
            else:
                logger.exception("Could not create role %s. Here's why: %s.", iam_role_name,
                                 e.response['Error']['Message'])
                raise
        if policies_arn is not None:
            for policy_arn in policies_arn:
                try:
                    self.iam_client.attach_role_policy(RoleName=iam_role_name, PolicyArn=policy_arn)
                except ClientError as e:
                    logger.exception("Could not attach policy%s. Here's why: %s.", policy_arn,
                                     e.response['Error']['Message'])
        else:
            pass

        return response_role['Role']['RoleName'], response_role['Role']['Arn']

    def create_sftp_transfer_server(self, logging_role_arn, custom_config=False, **kwargs):
        try:
            if custom_config:
                response = self.transfer_client.create_server(**kwargs)
            else:
                response = self.transfer_client.create_server(Domain='S3', EndpointType='PUBLIC',
                                                              Protocols=['SFTP'],
                                                              IdentityProviderType='SERVICE_MANAGED',
                                                              SecurityPolicyName='TransferSecurityPolicy-2020-06',
                                                              LoggingRole=logging_role_arn)
        except ClientError as e:
            logger.exception("Could not create server. Here's why: %s", e.response['Error']['Message'])
            raise

        self.server_id = response['ServerId']
        return response['ServerId']

    def add_user(self, user_name, access_role_arn, public_key, directory_mappings, server_id=None):
        try:
            if server_id is None:
                server_id = self.server_id
            response = self.transfer_client.create_user(UserName=user_name,
                                                        HomeDirectoryType='LOGICAL',
                                                        HomeDirectoryMappings=directory_mappings,
                                                        Role=access_role_arn,
                                                        SshPublicKeyBody=public_key,
                                                        ServerId=server_id)
        except ClientError as e:
            logger.error(e)
            raise

        return response

    def AWS(self, service, region=None):

        if region is None:
            region = self.region

        self.client = boto3.client(service_name=service,
                                   aws_access_key_id=self.aws_access_key,
                                   aws_secret_access_key=self.aws_secret_key,
                                   region_name=region)

    def establish_sftp(self, user_name, private_key, server_id=None):

        self.AWS(service='transfer')

        if server_id is None:
            server_id = self.server_id
        elif server_id is not None:
            self.server_id = server_id

        server_status = self.client.describe_server(ServerId=server_id)['Server']['State']

        if server_status == 'STOPPING':

            while server_status != 'OFFLINE':
                server_status = self.client.describe_server(ServerId=server_id)['Server']['State']
                continue

        self.client.start_server(ServerId=server_id)

        while server_status != 'ONLINE':
            server_status = self.client.describe_server(ServerId=server_id)['Server']['State']
            continue
        print('Server is online now')

        host = f'{server_id}.server.transfer.eu-central-1.amazonaws.com'  # copy the AWS transfer endpoint
        ssh_client = paramiko.SSHClient()
        policy = paramiko.AutoAddPolicy()
        ssh_client.set_missing_host_key_policy(policy)
        ssh_client.connect(host, username=user_name, pkey=paramiko.RSAKey.from_private_key_file(private_key))
        self.sftp = ssh_client.open_sftp()

        print('SFTP connection is open now')

        return self

    def put_file_transfer(self, local_path, bucket_name, new_file_name):

        try:
            self.sftp.put(local_path, f'/{bucket_name}/{new_file_name}')
            print(f'Upload successful')
        except ClientError as e:
            logger.exception(e.response['Error']['Code'])
            raise
        return self

    def download_file(self, bucket_name, file_to_download, local_path):

        s3 = boto3.resource(service_name='s3', region_name='eu-central-1', aws_access_key_id=self.aws_access_key, aws_secret_access_key=self.aws_secret_key)
        try:
            s3.meta.client.download_file(bucket_name=f'lumifai-{bucket_name}', Key=file_to_download, Filename=local_path)
        except ClientError as e:
            logger.exception(e)
            raise

    def close_transfer_server(self):
        self.sftp.close()
        self.client.stop_server(ServerId=self.server_id)
        print('Server is offline now')
