Because of the **volume mount** in Docker Compose + Airflow’s **DAG parsing loop**.

Here’s what’s happening, end-to-end, in plain terms:

---

## 1) Your file is shared into the Airflow containers (volume mount)

In your `docker-compose.yml` you have:

```yaml
volumes:
  - ./dags:/opt/airflow/dags
```

That means:

* On your laptop: `airflow-open-meteo/dags/open_meteo_ingest_daily.py`
* Inside the Airflow containers: `/opt/airflow/dags/open_meteo_ingest_daily.py`

So when you “paste code and save the file” on your machine, **the container sees it instantly** (it’s the same folder via the mount).

✅ That’s the key reason it shows up.

---

## 2) Airflow continuously scans the DAG folder

The Airflow **scheduler** (and webserver too) regularly:

* looks inside `/opt/airflow/dags`
* imports every `.py` file
* tries to find DAG objects

This is called **DAG parsing**.

So as soon as your file exists:

* scheduler imports it
* executes the Python module
* the module calls `open_meteo_ingest_daily()` at the bottom
* that registers the DAG with Airflow

Then it appears in the UI.

---

## 3) Why “Broken DAG” happens in UI

When Airflow parses/imports the file, if Python raises an error (syntax error, import error, TaskFlow signature issue), Airflow:

* cannot register the DAG
* shows **“Broken DAG”** and the traceback in the UI

So the UI is basically telling you:

> “I tried to import your file but it crashed.”

---

## 4) Why you sometimes need to wait ~30 seconds / refresh

Airflow doesn’t re-import constantly every millisecond. It parses periodically (and also on refresh).

So usually:

* save the file
* wait a moment
* refresh UI → DAG appears (or shows broken error)

---

## 5) In one sentence (mental model)

✅ **Your local `dags/` folder is mounted into the containers, and the scheduler imports Python files from that folder to register DAGs.**

---

## Quick verification you can do (optional, super useful)

Run this to confirm the file exists **inside** the scheduler container:

```bash
docker compose exec airflow-scheduler ls -l /opt/airflow/dags
```

You should see your `.py` file listed.

And to check parsing errors quickly:

```bash
docker compose logs airflow-scheduler --tail 50
```

---

If you want, next we can do a quick “sanity check” step: you run the `ls` command above and paste the output, and I’ll tell you exactly what Airflow is seeing.

