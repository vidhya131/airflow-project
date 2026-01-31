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

“**Poke mode**” usually means **sensor mode** in **Apache Airflow**.
It’s one of the two ways a **Sensor** checks whether something is ready.

Let’s break it down cleanly, with a concrete example 👇

---

## What is a Sensor?

A **Sensor** is a special Airflow task that **waits for a condition**:

* File exists
* API data is available
* Table/partition is ready
* Another DAG finished

Example conditions:

> “Wait until today’s weather data is published”

---

## Poke Mode (default)

In **poke mode**, the sensor:

1. **Runs**
2. Checks the condition
3. If ❌ not ready → **sleeps**
4. Wakes up → checks again
5. Repeats until success or timeout

💡 **Important:**
While sleeping, the task **keeps the worker slot occupied**.

---

## Simple example: FileSensor (poke mode)

```python
from airflow.sensors.filesystem import FileSensor

wait_for_file = FileSensor(
    task_id="wait_for_weather_file",
    filepath="/data/weather_2024_01_30.csv",
    poke_interval=60,   # check every 60 seconds
    timeout=3600,       # give up after 1 hour
    mode="poke",        # default
)
```

### What happens at runtime

* Minute 0 → file not found ❌
* Minute 1 → file not found ❌
* Minute 2 → file not found ❌
* Minute 5 → file appears ✅ → task succeeds

⚠️ Worker is **busy the whole time**.

---

## When poke mode is OK

✅ Fast checks
✅ Short waits
✅ Small Airflow clusters

Examples:

* File appears in a few minutes
* API responds quickly
* One or two sensors total

---

## The downside

🚫 **Wastes worker resources**
🚫 Doesn’t scale well
🚫 Can block other tasks

If 10 sensors wait for hours → 10 workers are stuck doing nothing.

---

## Alternative: Reschedule Mode (better most of the time)

Just for contrast:

```python
FileSensor(
    task_id="wait_for_weather_file",
    filepath="/data/weather_2024_01_30.csv",
    poke_interval=60,
    timeout=3600,
    mode="reschedule",  # 👈 key difference
)
```

### Difference

| Mode       | Uses Worker While Waiting? |
| ---------- | -------------------------- |
| poke       | ❌ Yes                      |
| reschedule | ✅ No                       |

Reschedule mode:

* Checks once
* Releases worker
* Gets scheduled again later

---

## Real-world API example (Open-Meteo)

Wait until API publishes daily data:

```python
from airflow.sensors.python import PythonSensor

def weather_ready():
    response = requests.get(url)
    return response.status_code == 200

wait_for_api = PythonSensor(
    task_id="wait_for_open_meteo",
    python_callable=weather_ready,
    poke_interval=300,  # every 5 min
    timeout=7200,       # 2 hours
    mode="poke",
)
```

---

## Mental model 🧠

* **Poke mode** = “Are we there yet?” every X seconds 🧍‍♂️
* **Reschedule mode** = “Call me back later” 📞

---

## TL;DR

* **Poke mode** keeps checking while holding a worker
* Simple but inefficient
* Use for **short waits**
* Prefer **reschedule mode** for long waits

If you want, I can also explain:

* sensors vs normal tasks
* deferrable sensors (even better!)
* poke vs reschedule vs deferrable (comparison table)

Just say the word 🚀

Perfect — let’s zoom in and **understand every single line** in this block and how Airflow actually uses it at runtime.

```python
DEFAULT_ARGS = {
    "owner": "you",
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=15),
}
```

This dictionary defines **default behavior for tasks in the DAG**.

---

## 1️⃣ What `DEFAULT_ARGS` is (concept first)

In Airflow:

* Every **task** has parameters like retries, owner, timeouts, etc.
* Instead of repeating them for every task, you define them once in `default_args`
* Airflow applies them to **all tasks in the DAG** unless overridden

You passed this here:

```python
@dag(
    default_args=DEFAULT_ARGS,
)
```

So these settings apply to:

* `get_config`
* `fetch_weather`
* `write_raw_file`
* `upsert_raw_to_postgres`
* etc.

---

## 2️⃣ `"owner": "you"`

```python
"owner": "you",
```

### What it does

* Sets the task owner metadata
* Shows up in the Airflow UI

### What it **does NOT** do

* Does not affect permissions
* Does not affect execution

### Why it exists

* Historical feature for notifications
* Mostly informational now

---

## 3️⃣ `"retries": 3`

```python
"retries": 3,
```

### Meaning

* If a task **fails**, Airflow will try again
* Total attempts = `retries + 1`

  * initial attempt
  * +3 retries
  * = **4 total attempts**

### When retries happen

* Only for task failures (exceptions, non-zero exit)
* Not for skipped tasks
* Not for upstream failures

---

## 4️⃣ `"retry_delay": timedelta(minutes=2)`

```python
"retry_delay": timedelta(minutes=2),
```

### Meaning

* After a failure, Airflow waits **2 minutes** before retrying

Timeline example:

```
Attempt 1 fails → wait 2 min → retry 1
```

---

## 5️⃣ `"retry_exponential_backoff": True`

```python
"retry_exponential_backoff": True,
```

### This is important.

Without it:

```
Retry delays are constant:
2 min → 2 min → 2 min
```

With it:

```
Retry delays grow exponentially:
2 min → 4 min → 8 min → ...
```

### Why this matters

* Prevents hammering:

  * APIs
  * databases
  * unstable services

This is especially good for:

* HTTP APIs (like Open-Meteo)
* transient network failures

---

## 6️⃣ `"max_retry_delay": timedelta(minutes=15)`

```python
"max_retry_delay": timedelta(minutes=15),
```

### Why this exists

Exponential backoff could grow forever:

```
2 → 4 → 8 → 16 → 32 → ...
```

This setting **caps** the delay.

So your real retry delays look like:

```
Attempt 1 fails → wait 2 min
Attempt 2 fails → wait 4 min
Attempt 3 fails → wait 8 min
Attempt 4 fails → wait 15 min (capped)
```

---

## 7️⃣ How Airflow applies this to tasks

Let’s take one task as an example:

```python
@task
def fetch_weather(...):
```

At runtime, Airflow internally creates a `PythonOperator` with:

* retries = 3
* retry_delay = 2 min
* retry_exponential_backoff = True
* max_retry_delay = 15 min

You didn’t write this explicitly — Airflow injects it.

---

## 8️⃣ Where you see this in the UI

If a task fails:

* Task Instance page shows:

  * “Try 1 of 4”
  * Retry countdown timer
* Graph view shows:

  * yellow (up for retry)
  * red (failed after all retries)

---

## 9️⃣ Common beginner misunderstanding (important)

> “Retries mean the DAG reruns?”

❌ No.

* **Retries are per-task**
* Only the failed task retries
* Downstream tasks wait

DAG run stays the same.

---

## 10️⃣ Why these defaults are GOOD for your project

For your pipeline:

* API calls can fail transiently → retries help
* DB can have momentary hiccups → retries help
* Backfills shouldn’t fail permanently due to a temporary issue

This setup is **production-grade**, not toy config.

---

## 11️⃣ Quick mental summary

| Setting                     | Purpose                      |
| --------------------------- | ---------------------------- |
| `owner`                     | metadata only                |
| `retries`                   | how many times to retry      |
| `retry_delay`               | initial wait between retries |
| `retry_exponential_backoff` | increase wait time           |
| `max_retry_delay`           | cap wait time                |

---

If you want, next we can:

* intentionally break the API call and watch retries in action
* or move to explaining **`schedule`, `start_date`, and `ds`** together (another critical Airflow concept cluster)

Just tell me 👍
