-- Create schemas (namespaces)
create schema if not exists raw;
create schema if not exists analytics;

-- Raw table: store one JSON payload per city per day
create table if not exists raw.open_meteo_current (
  load_date date not null,
  city_name text not null,
  lat double precision not null,
  lon double precision not null,
  payload jsonb not null,
  ingested_at timestamptz not null default now(),
  primary key (load_date, city_name)
);

-- Analytics table: typed columns for reporting
create table if not exists analytics.weather_daily (
  day date not null,
  city_name text not null,
  temp_c double precision,
  wind_speed double precision,
  weathercode int,
  primary key (day, city_name)
);

-- Weekly rollup table
create table if not exists analytics.weather_weekly (
  week_start date not null,
  city_name text not null,
  avg_temp_c double precision,
  avg_wind_speed double precision,
  primary key (week_start, city_name)
);
