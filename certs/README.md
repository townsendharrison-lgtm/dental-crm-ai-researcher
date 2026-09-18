# Supabase database CA

`prod-ca-2021.crt` is a public CA certificate, downloaded over verified HTTPS on
2026-09-17 from:

https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt

The URL was verified against Supabase's official Studio configuration:
https://github.com/supabase/supabase/blob/master/apps/studio/hooks/custom-content/custom-content.json

SHA-256 of the downloaded file:
`700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7`

When running from the service directory, set
`DATABASE_CA_FILE=certs/prod-ca-2021.crt` and keep `DATABASE_SSL=true`.
Certificate and hostname verification remain enabled. For another project,
confirm the CA using its Dashboard → Database Settings → SSL Configuration.
See https://supabase.com/docs/guides/platform/ssl-enforcement.
