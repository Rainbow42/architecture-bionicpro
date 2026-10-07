import os
import time

import boto3
from botocore.exceptions import ClientError, EndpointConnectionError

s3 = boto3.client('s3', endpoint_url='http://minio:9000', region_name='us-east-1',
                  aws_access_key_id=os.environ['S3_ACCESS_KEY'], aws_secret_access_key=os.environ['S3_SECRET_KEY'])
for attempt in range(60):
    try:
        s3.create_bucket(Bucket='reports')
        break
    except ClientError as error:
        if error.response['Error']['Code'] == 'BucketAlreadyOwnedByYou':
            break
        raise
    except EndpointConnectionError:
        time.sleep(2)
else:
    raise RuntimeError('MinIO did not start')
print('Private reports bucket is ready')
