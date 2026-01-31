from __future__ import annotations # Makes type hints lazy

import json
from datetime import datetime, timedelta
from pathlib import Path

from airflow import Dataset
from airflow.decorators import dag, task
from airflow.models import Variable
from airflow.providers.http.hooks.http import HttpHook
from airflow.providers.http.sensors.http import HttpSensor
from airflow.providers.postgres.hooks.postgres import PostgresHook

# Dataset name: downstream DAGs can "subscribe" to this
RAW_DATASET = Dataset("dataset://open-meteo/raw_loaded")

# Default task settings (retries, etc.)
DEFAULT_ARGS = {
    "owner": "you",
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=15),
}

@dag(
    dag_id="open_meteo_ingest_daily",
    start_date=datetime(2025, 1, 1),
    schedule="@daily",
    catchup=True,
    default_args=DEFAULT_ARGS,
    tags=["open-meteo", "ingest"],
    max_active_runs=1,
)
def open_meteo_ingest_daily():
    # 1) Sensor: wait until the API endpoint is reachable
    api_up = HttpSensor(
        task_id="check_api_up",
        http_conn_id="open_meteo_http",
        endpoint="/v1/forecast",
        request_params={"latitude": "0", "longitude": "0", "current_weather": "true"},
        response_check=lambda r: r.status_code == 200,
        poke_interval=30,
        timeout=5 * 60,
        mode="poke",
    )

    # 2) Read config from Airflow Variables (cities, base folder)
    @task
    def get_config() -> dict:
        cities = json.loads(Variable.get("OPEN_METEO_CITIES"))
        raw_base = Variable.get("RAW_BASE_PATH", default_var="/opt/airflow/data/raw/open_meteo")
        return {"cities": cities, "raw_base": raw_base}

    # 3) Build a list of per-city request dicts (drives dynamic mapping)
    @task
    def build_requests(cfg: dict, ds: str) -> list[dict]:
        return [{"city_name": c["name"], "lat": c["lat"], "lon": c["lon"], "ds": ds} for c in cfg["cities"]]

    # 4) Fetch weather for one city (mapped task: one instance per city)
    @task(pool="open_meteo_pool")
    def fetch_weather(req: dict) -> dict:
        hook = HttpHook(method="GET", http_conn_id="open_meteo_http")
        params = {
            "latitude": req["lat"],
            "longitude": req["lon"],
            "current_weather": "true",
            "timezone": "UTC",
        }
        r = hook.run(endpoint="/v1/forecast", extra_options={"params": params})
        payload = r.json()
        return {**req, "payload": payload}

    # 5) Write raw payload to a partitioned file path (data lake style)
    @task
    def write_raw_file(item: dict, cfg: dict) -> dict:
        load_date = item["ds"]
        city = item["city_name"].replace(" ", "_")

        base = Path(cfg["raw_base"]) / f"load_date={load_date}"
        base.mkdir(parents=True, exist_ok=True)

        fp = base / f"city={city}.json"
        fp.write_text(json.dumps(item["payload"]))

        return {**item, "raw_path": str(fp)}

    # 6) Upsert all city payloads into Postgres raw table
    @task
    def upsert_raw_to_postgres(items: list[dict]) -> int:
        pg = PostgresHook(postgres_conn_id="warehouse_postgres")
        sql = """
        insert into raw.open_meteo_current (load_date, city_name, lat, lon, payload)
        values (%s, %s, %s, %s, %s::jsonb)
        on conflict (load_date, city_name) do update
        set lat = excluded.lat,
            lon = excluded.lon,
            payload = excluded.payload,
            ingested_at = now();
        """
        rows = [(i["ds"], i["city_name"], i["lat"], i["lon"], json.dumps(i["payload"])) for i in items]
        pg.run(sql, parameters=rows, autocommit=True)
        return len(rows)

    # 7) Data quality check: confirm at least 1 row for the day
    @task
    def dq_rowcount(ds: str) -> bool:
        pg = PostgresHook(postgres_conn_id="warehouse_postgres")
        n = pg.get_first(
            "select count(*) from raw.open_meteo_current where load_date=%s",
            parameters=(ds,),
        )[0]
        return n > 0

    # 8) Branch based on DQ result
    @task.branch
    def branch_on_dq(dq_ok: bool) -> str:
        return "publish_dataset" if dq_ok else "quarantine_and_fail"

    # 9) If DQ fails: move the day's partition to quarantine and fail the run
    @task
    def quarantine_and_fail(cfg: dict, ds: str) -> None:
        raw_base = Path(cfg["raw_base"])
        src = raw_base / f"load_date={ds}"
        dst = raw_base / "quarantine" / f"load_date={ds}"

        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)

        raise ValueError(f"DQ failed for {ds}; partition moved to quarantine")

    # 10) If DQ passes: publish dataset update (triggers downstream DAGs)
    @task(outlets=[RAW_DATASET])
    def publish_dataset(n_loaded: int) -> str:
        return f"Loaded {n_loaded} city payloads"

    # ----- DAG wiring (dependencies) -----
    cfg = get_config()
    reqs = build_requests(cfg, ds="{{ ds }}")

    fetched = fetch_weather.expand(req=reqs)
    written = write_raw_file.expand(item=fetched, cfg=cfg)

    loaded = upsert_raw_to_postgres(written)

    dq_ok = dq_rowcount(ds="{{ ds }}")
    decision = branch_on_dq(dq_ok)

    api_up >> cfg >> reqs
    loaded.set_upstream(written)

    decision >> publish_dataset(loaded)
    decision >> quarantine_and_fail(ds="{{ ds }}", cfg=cfg)

open_meteo_ingest_daily()
