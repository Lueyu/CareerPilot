// 配置 Bucket Policy 拒绝所有 HTTP（非 TLS）请求，强制匿名用户走 HTTPS
import OSS from 'ali-oss';

const REGION = process.env.ALI_REGION || 'oss-cn-hongkong';
const BUCKET = process.env.ALI_BUCKET || 'agent-interview-icu';
const ACCOUNT_ID = process.env.ALI_ACCOUNT_ID || '1359572124865458';

const client = new OSS({
  region: REGION,
  accessKeyId: process.env.ALI_ACCESS_KEY_ID,
  accessKeySecret: process.env.ALI_ACCESS_KEY_SECRET,
  bucket: BUCKET,
  secure: true,
});

const policy = {
  Version: '1',
  Statement: [
    {
      Effect: 'Deny',
      Action: ['oss:*'],
      Principal: ['*'],
      Resource: [
        `acs:oss:*:${ACCOUNT_ID}:${BUCKET}`,
        `acs:oss:*:${ACCOUNT_ID}:${BUCKET}/*`,
      ],
      Condition: {
        Bool: {
          'acs:SecureTransport': ['false'],
        },
      },
    },
  ],
};

const res = await client.putBucketPolicy(BUCKET, policy);
console.log('Bucket Policy 已写入，status =', res.status);
