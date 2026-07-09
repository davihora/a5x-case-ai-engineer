-- Carrega o dataset de observabilidade A5X (gerado; nao edite a mao). Engine: DuckDB.
CREATE OR REPLACE TABLE service_catalog AS SELECT * FROM read_parquet('{data_dir}/service_catalog.parquet');
CREATE OR REPLACE TABLE otel_signal AS SELECT * FROM read_parquet('{data_dir}/otel_signal.parquet');
CREATE OR REPLACE TABLE action_catalog AS SELECT * FROM read_parquet('{data_dir}/action_catalog.parquet');
CREATE OR REPLACE TABLE incident_log AS SELECT * FROM read_parquet('{data_dir}/incident_log.parquet');
CREATE OR REPLACE TABLE eval_golden AS SELECT * FROM read_parquet('{data_dir}/eval_golden.parquet');
