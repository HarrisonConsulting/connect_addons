-- Neutralize S3 recording storage so a copied database cannot reach
-- production object storage. Clears credentials, endpoint, and bucket;
-- reverts storage backend to the base default; disables migration cron.

UPDATE connect_settings
   SET s3_access_key = NULL,
       s3_secret_key = NULL,
       s3_endpoint = NULL,
       s3_bucket = NULL,
       recording_storage = 'twilio'
 WHERE recording_storage = 's3'
    OR s3_access_key IS NOT NULL
    OR s3_secret_key IS NOT NULL
    OR s3_bucket IS NOT NULL
    OR s3_endpoint IS NOT NULL;

UPDATE ir_cron
   SET active = FALSE
  FROM ir_model_data d
 WHERE d.model = 'ir.cron'
   AND d.module = 'connect_s3'
   AND d.name = 'ir_cron_s3_migration'
   AND d.res_id = ir_cron.id;
