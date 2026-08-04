{#
    DDL for the tables the forecasting job owns.

    dbt reads `analytics_ml.forecast_predictions` as a source but never writes
    it, so the table has to exist before the first training run or the graph
    cannot compile. This macro is the guarantee.

    **Columns must match forecasting/warehouse.py exactly, and neither side
    declares constraints.** Whichever runs first creates the table, so a
    PRIMARY KEY declared on one side and not the other is a build-order
    dependency: the writer's upsert then works on a fresh warehouse and raises
    on a dbt-first one. The writer deletes and inserts instead, which needs
    only the column list to agree.
#}

{% macro ensure_forecast_predictions() %}
    create table if not exists analytics_ml.forecast_predictions (
        run_id varchar not null,
        target varchar not null,
        series_key varchar not null,
        model_name varchar not null,
        model_class varchar not null,
        origin_date date not null,
        business_date date not null,
        horizon integer not null,
        yhat double,
        yhat_lower double,
        yhat_upper double
    )
{% endmacro %}


{% macro ensure_forecast_runs() %}
    create table if not exists analytics_ml.forecast_runs (
        run_id varchar,
        target varchar not null,
        model_name varchar not null,
        model_class varchar not null,
        version varchar not null,
        promoted boolean not null,
        promotion_reason varchar,
        horizon integer not null,
        training_start date,
        training_end date,
        data_snapshot_id varchar,
        wape double,
        mase double,
        bias double,
        interval_coverage double,
        evaluation_points integer,
        created_at timestamp default current_timestamp
    )
{% endmacro %}


{% macro ensure_forecast_explanations() %}
    create table if not exists analytics_ml.forecast_explanations (
        run_id varchar not null,
        target varchar not null,
        series_key varchar not null,
        business_date date not null,
        horizon integer not null,
        feature varchar not null,
        feature_value double,
        effect double,
        baseline double
    )
{% endmacro %}
