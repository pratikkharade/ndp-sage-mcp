"""Sensor/data-focused MCP tools and resources."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd
import requests
import sage_data_client

from ..data_service import SageDataService
from ..models import DataType, NodeID, TimeRange
from ..utils import parse_time_range, safe_timestamp_format


logger = logging.getLogger(__name__)

SAGE_API_BASE = "https://auth.sagecontinuum.org/api/v-beta"
SAGE_MANIFESTS_URL = "https://auth.sagecontinuum.org/manifests/"
SAGE_SENSORS_URL = "https://auth.sagecontinuum.org/sensors/"


def register(mcp, data_service: SageDataService) -> None:
    """Register sensor-related tools + resources on ``mcp``."""

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------
    @mcp.resource("query://{plugin}")
    def query_plugin_data(plugin: str) -> str:
        """Query Sage data for a specific plugin (last 30 minutes)."""
        try:
            logger.info(f"Querying plugin data for: {plugin}")
            df = data_service.query_plugin_data(plugin)
            return df.to_csv(index=False)
        except Exception as e:
            logger.error(f"Error querying plugin {plugin}: {e}")
            return f"Error querying plugin {plugin}: {e}"

    @mcp.resource("query://plugin-iio")
    def query_plugin_iio() -> str:
        """Pre-bound static version of the plugin-iio resource."""
        return query_plugin_data("plugin-iio")

    @mcp.resource("stats://temperature")
    def temperature_stats() -> str:
        """Temperature stats grouped by node and sensor for the last hour."""
        try:
            start, end = parse_time_range("-1h")
            df = data_service.query_data(start, end, {"name": "env.temperature"})
            if df.empty:
                return "No temperature data found in the last hour"
            stats = df.groupby(["meta.vsn", "meta.sensor"]).value.agg(
                ["size", "min", "max", "mean"]
            )
            return stats.to_csv()
        except Exception as e:
            logger.error(f"Error getting temperature stats: {e}")
            return f"Error getting temperature stats: {e}"

    # ------------------------------------------------------------------
    # Sensor tools
    # ------------------------------------------------------------------
    @mcp.tool
    def get_node_all_data(node_id: str, time_range: str = "-5m", max_records: int = 500) -> str:
        """Get all available sensor data for a specific node (or `*` for all nodes)."""
        try:
            if node_id and node_id != "*":
                node_str = str(NodeID(value=node_id))
            else:
                node_str = "*"
            validated_time = TimeRange(value=time_range)
            df = data_service.query_node_data(node_str, validated_time, max_records=max_records)
            if df.empty:
                return f"No data found for node {node_str} in the last {validated_time}"

            original_size = len(df)
            if len(df) > max_records:
                df = df.sort_values("timestamp", ascending=False).head(max_records)

            time_start = safe_timestamp_format(df.timestamp.min())
            time_end = safe_timestamp_format(df.timestamp.max())

            result = f"All sensor data for node {node_str} ({validated_time}):\n"
            result += f"Total measurements available: {original_size:,}\n"
            if original_size > max_records:
                result += (
                    f"Showing summary of {max_records:,} most recent "
                    "(use max_records parameter to adjust)\n"
                )
            result += f"Time range: {time_start} to {time_end}\n\n"

            numeric_df = df[df["value"].apply(lambda x: isinstance(x, (int, float, np.number)))]
            if not numeric_df.empty:
                summary = (
                    numeric_df.groupby(["name", "meta.sensor"], observed=True)
                    .agg({"value": ["count", "min", "max", "mean"]})
                    .round(2)
                )
                max_measurement_types = 50
                for i, ((name, sensor), group) in enumerate(summary.iterrows()):
                    if i >= max_measurement_types:
                        remaining = len(summary) - max_measurement_types
                        result += f"\n... and {remaining} more measurement types\n"
                        break
                    result += f"{name} ({sensor}):\n"
                    result += f"  Count: {int(group[('value', 'count')]):,}\n"
                    result += f"  Range: {group[('value', 'min')]} to {group[('value', 'max')]}\n"
                    result += f"  Average: {group[('value', 'mean')]}\n\n"

            non_numeric_df = df[
                ~df["value"].apply(lambda x: isinstance(x, (int, float, np.number)))
            ]
            if not non_numeric_df.empty:
                non_numeric_types = non_numeric_df.groupby(
                    ["name", "meta.sensor"], observed=True
                ).size()
                result += "\nNon-numeric measurements:\n"
                for (name, sensor), count in non_numeric_types.head(20).items():
                    result += f"{name} ({sensor}): {count:,} records\n"
                if len(non_numeric_types) > 20:
                    result += f"... and {len(non_numeric_types) - 20} more types\n"

            result += "\nTip: Use search_measurements() or query_job_data() for specific measurement types\n"
            return result
        except Exception as e:
            logger.error(f"Error getting all data for node {node_id}: {e}", exc_info=True)
            return f"Error getting all data for node {node_id}: {e}"

    @mcp.tool
    def export_sage_query_csv(
        output_path: str,
        time_range: str = "-1h",
        node_id: str = "",
        plugin: str = "",
        measurement: str = "",
        aggregate: str = "",
        aggregate_window: str = "",
        max_records: int = 10000,
    ) -> str:
        """Export a filtered Sage query to a local CSV file.

        This is a general-purpose export for scalar measurements and Beehive
        upload records. At least one of node_id, plugin, or measurement is
        required. Set both ``aggregate`` (for example ``mean``) and
        ``aggregate_window`` (for example ``1h``) to request server-side
        time-window aggregation before export.
        """
        try:
            if max_records < 1 or max_records > 1_000_000:
                return "max_records must be between 1 and 1000000."
            if bool(aggregate) != bool(aggregate_window):
                return (
                    "aggregate and aggregate_window must be provided together "
                    "or both left empty."
                )

            filter_params: Dict[str, Any] = {}
            if node_id:
                filter_params["vsn"] = str(NodeID(value=node_id))
            if plugin:
                filter_params["plugin"] = (
                    plugin if ".*" in plugin else f".*{plugin}.*"
                )
            if measurement:
                filter_params["name"] = measurement
            if not filter_params:
                return (
                    "Specify at least one of node_id, plugin, or measurement. "
                    "An unfiltered Sage archive export is not allowed."
                )

            start, end = parse_time_range(TimeRange(value=time_range))
            if aggregate:
                df = sage_data_client.query(
                    start=start,
                    end=end or None,
                    filter=filter_params,
                    experimental_func=aggregate,
                    experimental_window=aggregate_window,
                )
            else:
                df = data_service.query_data(
                    start,
                    end,
                    filter_params,
                    max_records=max_records,
                )
            if df is None or df.empty:
                return (
                    f"Sage query returned no records for {filter_params} over "
                    f"{time_range}; no CSV was written."
                )
            if len(df) > max_records:
                if "timestamp" in df.columns:
                    df = (
                        df.sort_values("timestamp", ascending=False)
                        .head(max_records)
                        .sort_values("timestamp")
                    )
                else:
                    df = df.head(max_records)

            path = Path(output_path).expanduser().resolve()
            if path.suffix.lower() != ".csv":
                return "output_path must end in .csv."
            if not path.parent.exists():
                return f"Output directory does not exist: {path.parent}"
            df.to_csv(path, index=False)

            observed = ""
            if "timestamp" in df.columns and len(df):
                observed = (
                    f"\nObserved: {safe_timestamp_format(df['timestamp'].min())} "
                    f"to {safe_timestamp_format(df['timestamp'].max())}"
                )
            aggregation = (
                f"\nAggregation: {aggregate} per {aggregate_window}"
                if aggregate
                else ""
            )
            return (
                f"Exported {len(df)} Sage record(s) to {path}\n"
                f"Query: {filter_params}\n"
                f"Columns ({len(df.columns)}): {', '.join(map(str, df.columns))}"
                f"{observed}{aggregation}"
            )
        except Exception as e:
            logger.error(f"Error exporting Sage query to CSV: {e}")
            return f"Error exporting Sage query to CSV: {e}"

    @mcp.tool
    def get_node_iio_data(node_id: str, time_range: str = "-30m") -> str:
        """Get IIO (Industrial I/O) sensor data for a node."""
        try:
            validated_node = NodeID(value=node_id)
            validated_time = TimeRange(value=time_range)
            start, end = parse_time_range(validated_time)
            df = data_service.query_data(
                start,
                end,
                {"plugin": ".*plugin-iio.*", "vsn": str(validated_node)},
            )
            if df.empty:
                return f"No IIO data found for node {validated_node} in the last {validated_time}"

            iio_measurements = DataType.iio_types() + [
                DataType.TEMPERATURE.value,
                DataType.HUMIDITY.value,
                DataType.PRESSURE.value,
            ]

            result = f"IIO sensor data for node {validated_node} ({validated_time}):\n"
            result += f"Total IIO measurements: {len(df)}\n\n"

            for measurement in iio_measurements:
                m_df = df[df["name"] == measurement]
                if not m_df.empty:
                    stats = m_df.groupby("meta.sensor").value.agg(["count", "min", "max", "mean"])
                    result += f"{measurement}:\n"
                    for sensor, sensor_stats in stats.iterrows():
                        result += (
                            f"  {sensor}: {sensor_stats['count']} readings, "
                            f"range: {sensor_stats['min']:.2f}-{sensor_stats['max']:.2f}, "
                            f"avg: {sensor_stats['mean']:.2f}\n"
                        )
                    result += "\n"

            other = df[~df["name"].isin(iio_measurements)]["name"].unique()
            if len(other) > 0:
                result += f"Other IIO measurements found: {', '.join(other)}\n"
            return result
        except Exception as e:
            return f"Error getting IIO data for node {node_id}: {e}"

    @mcp.tool
    def get_environmental_summary(node_id: str = "", time_range: str = "-1h") -> str:
        """Environmental data summary (temperature, humidity, pressure) for a node or all."""
        try:
            validated_time = TimeRange(value=time_range)
            validated_node = NodeID(value=node_id) if node_id else None
            df = data_service.query_environmental_data(validated_node, validated_time)
            if df.empty:
                node_text = f"node {validated_node}" if validated_node else "any nodes"
                return f"No environmental data found for {node_text} in the last {validated_time}"

            result = f"Environmental data summary ({validated_node or 'all nodes'}, {validated_time}):\n\n"
            grouped = (
                df.groupby(["meta.vsn", "name", "meta.sensor"])
                .value.agg(["count", "min", "max", "mean"])
                .round(2)
            )
            current_node = None
            for (vsn, name, sensor), stats in grouped.iterrows():
                if current_node != vsn:
                    current_node = vsn
                    result += f"\nNode {vsn}:\n"
                result += (
                    f"  {name} ({sensor}): {stats['count']} readings, "
                    f"{stats['min']}-{stats['max']} (avg: {stats['mean']})\n"
                )
            return result
        except Exception as e:
            return f"Error getting environmental summary: {e}"

    @mcp.tool
    def list_available_nodes(time_range: str = "-1h") -> str:
        """List all Sage nodes that reported environmental data in ``time_range``."""
        try:
            df = data_service.query_environmental_data(time_range=time_range)
            if df.empty:
                return "No active nodes found in the specified time range."

            nodes = df["meta.vsn"].unique().tolist()
            development, other, production = [], [], []
            for node in nodes:
                node_df = df[df["meta.vsn"] == node]
                latest = node_df.sort_values("timestamp").iloc[-1]
                phase = latest.get("meta.phase", "Unknown")
                info = f"- {node}"
                if phase == "Production":
                    production.append(info)
                elif phase == "Development":
                    development.append(info)
                else:
                    other.append(info)

            result = f"Available Sage Nodes ({len(nodes)} total):\n\n"
            if development:
                result += f"Development Nodes ({len(development)}):\n" + "\n".join(development) + "\n"
            if production:
                result += f"Production Nodes ({len(production)}):\n" + "\n".join(production) + "\n"
            if other:
                result += f"Other Nodes ({len(other)}):\n" + "\n".join(other)
            result += "\nTip: For detailed node information, use get_node_info(node_id)"
            return result.strip()
        except Exception as e:
            logger.error(f"Error listing nodes: {e}")
            return f"Error listing nodes: {e}"

    @mcp.tool
    def search_measurements(
        measurement_pattern: str, node_id: str = "", time_range: str = "-30m"
    ) -> str:
        """Search for measurement types by regex-style pattern (supports `|`)."""
        try:
            validated_time = TimeRange(value=time_range)
            validated_node = NodeID(value=node_id) if node_id else None

            def _wrap(part: str) -> str:
                part = part.strip()
                if not part.startswith(".*"):
                    part = f".*{part}"
                if not part.endswith(".*"):
                    part = f"{part}.*"
                return part

            filter_params: Dict[str, Any] = {}
            if "|" in measurement_pattern:
                filter_params["plugin"] = "|".join(_wrap(p) for p in measurement_pattern.split("|"))
            else:
                filter_params["plugin"] = _wrap(measurement_pattern)

            if validated_node:
                filter_params["vsn"] = str(validated_node)

            start, end = parse_time_range(validated_time)
            df = data_service.query_data(start, end, filter_params)
            if df.empty:
                # retry against measurement name
                name_filter = filter_params.copy()
                name_filter["name"] = name_filter.pop("plugin")
                df = data_service.query_data(start, end, name_filter)
            if df.empty:
                node_text = f" for node {validated_node}" if validated_node else ""
                return (
                    f"No measurements matching '{measurement_pattern}'{node_text} "
                    f"found in the last {validated_time}"
                )

            lines = [f"Found {len(df)} records matching '{measurement_pattern}':", f"Time range: {validated_time}"]
            plugins = sorted(df["plugin"].unique()) if "plugin" in df.columns else []
            if plugins:
                lines.append(f"\nPlugins found ({len(plugins)}):")
                for plugin in plugins:
                    p_df = df[df["plugin"] == plugin]
                    lines.append(f"\n{plugin}:")
                    lines.append(f"- Nodes: {', '.join(sorted(p_df['meta.vsn'].unique()))}")
                    lines.append(f"- Measurements: {', '.join(sorted(p_df['name'].unique()))}")
                    lines.append("- Recent data:")
                    for _, row in p_df.sort_values("timestamp", ascending=False).head(3).iterrows():
                        lines.append(_format_sample(row))
            else:
                measurements = sorted(df["name"].unique())
                lines.append(f"\nMeasurements found ({len(measurements)}):")
                for m in measurements:
                    m_df = df[df["name"] == m]
                    lines.append(f"\n{m}:")
                    lines.append(f"- Nodes: {', '.join(sorted(m_df['meta.vsn'].unique()))}")
                    for _, row in m_df.sort_values("timestamp", ascending=False).head(3).iterrows():
                        lines.append(_format_sample(row))
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error searching measurements: {e}")
            return f"Error searching measurements: {e}"

    @mcp.tool
    def get_node_temperature(node_id: str, sensor_type: str = "bme680") -> str:
        """Get recent temperature readings for a node/sensor."""
        try:
            validated_node = NodeID(value=node_id)
            start, end = parse_time_range("-1h")
            df = data_service.query_data(
                start,
                end,
                {"name": DataType.TEMPERATURE.value, "vsn": str(validated_node), "sensor": sensor_type},
            )
            sensor_label = "environment (bme680)" if sensor_type == "bme680" else "internal/hardware (bme280)"
            if df.empty:
                return (
                    f"No {sensor_label} temperature data found for node {validated_node} "
                    "in the last hour"
                )
            latest = df.iloc[-1]
            last_updated = safe_timestamp_format(latest.timestamp)
            return (
                f"{sensor_label.capitalize()} temperature data for node {validated_node}:\n"
                f"- Latest reading: {latest.value:.2f}°C (sensor: {latest['meta.sensor']})\n"
                f"- Average over last hour: {df.value.mean():.2f}°C\n"
                f"- Min/Max: {df.value.min():.2f}°C / {df.value.max():.2f}°C\n"
                f"- Total readings: {len(df)}\n"
                f"- Sensors active: {', '.join(df['meta.sensor'].unique())}\n"
                f"- Last updated: {last_updated}"
            )
        except Exception as e:
            return f"Error getting temperature for node {node_id}: {e}"

    @mcp.tool
    def get_temperature_summary(time_range: str = "-1h", sensor_type: str = "bme680") -> str:
        """Summary of temperature readings across all sensors of a given type."""
        try:
            validated_time = TimeRange(value=time_range)
            start, end = parse_time_range(validated_time)
            df = data_service.query_data(
                start, end, {"name": DataType.TEMPERATURE.value, "sensor": sensor_type}
            )
            sensor_label = "environment (bme680)" if sensor_type == "bme680" else "internal/hardware (bme280)"
            if df.empty:
                return f"No {sensor_label} temperature data available in the last {validated_time}"
            return (
                f"{sensor_label.capitalize()} Temperature Summary (Last {validated_time}):\n"
                f"- Total readings: {len(df)}\n"
                f"- Unique sensors: {df['meta.vsn'].nunique()}\n"
                f"- Average temperature: {df.value.mean():.2f}°C\n"
                f"- Min temperature: {df.value.min():.2f}°C\n"
                f"- Max temperature: {df.value.max():.2f}°C"
            )
        except Exception as e:
            return f"Error getting temperature summary: {e}"

    @mcp.tool
    def get_node_info(node_id: str) -> str:
        """Detailed info about a Sage node (sensors, location, hardware)."""
        try:
            validated_node = NodeID(value=node_id)
            response = requests.get(f"{SAGE_API_BASE}/nodes/{validated_node}/", timeout=15)
            if response.status_code != 200:
                return f"Error: Could not retrieve node information. Status code: {response.status_code}"
            node_info = response.json()
            out = [f"Node {validated_node} Information:"]
            for key in ("name", "project", "type", "focus", "phase", "location", "address"):
                out.append(f"- {key.capitalize()}: {node_info.get(key, 'Unknown')}")
            if node_info.get("gps_lat") and node_info.get("gps_lon"):
                out.append(f"- GPS: {node_info['gps_lat']}, {node_info['gps_lon']}")
            for section, label in [("sensors", "Sensors"), ("computes", "Compute Resources")]:
                items = node_info.get(section, []) or []
                if items:
                    out.append(f"\n{label} ({len(items)}):")
                    for it in items:
                        status = "Active" if it.get("is_active") else "Inactive"
                        out.append(
                            f"- {it.get('name', 'Unknown')}: {it.get('hw_model', 'Unknown')} "
                            f"({it.get('manufacturer', 'Unknown')}) - {status}"
                        )
                        if it.get("capabilities"):
                            out.append(f"  Capabilities: {', '.join(it['capabilities'])}")
            return "\n".join(out)
        except Exception as e:
            logger.error(f"Error in get_node_info: {e}")
            return f"Error getting detailed information for node {node_id}: {e}"

    @mcp.tool
    def list_all_nodes() -> str:
        """List all Sage nodes with their basic information."""
        try:
            response = requests.get(SAGE_MANIFESTS_URL, timeout=15)
            if response.status_code != 200:
                return f"Error: Could not retrieve node list. Status code: {response.status_code}"
            nodes = response.json()
            deployed, other = [], []
            for node in nodes:
                info = f"- {node.get('vsn', 'Unknown')} ({node.get('name', 'Unknown')})"
                if node.get("phase") == "Deployed":
                    if node.get("address"):
                        info += f": {node['address']}"
                    deployed.append(info)
                else:
                    info += f": {node.get('phase', 'Unknown phase')}"
                    other.append(info)
            parts = [f"Available Sage Nodes ({len(nodes)}):"]
            if deployed:
                parts.append("\n\nDeployed Nodes:\n" + "\n".join(deployed))
            if other:
                parts.append("\n\nOther Nodes:\n" + "\n".join(other))
            parts.append("\n\nFor detailed information about a specific node, use get_node_info.")
            return "".join(parts)
        except Exception as e:
            logger.error(f"Error in list_all_nodes: {e}")
            return f"Error listing all nodes: {e}"

    @mcp.tool
    def get_sensor_details(sensor_type: str) -> str:
        """Detailed information about a specific sensor type."""
        try:
            if not sensor_type:
                return "Error: No sensor type provided"
            response = requests.get(SAGE_SENSORS_URL, timeout=15)
            if response.status_code != 200:
                return f"Error: Could not retrieve sensor information. Status code: {response.status_code}"
            all_sensors = response.json()
            needle = sensor_type.lower()
            matches = [
                s
                for s in all_sensors
                if needle in (s.get("hardware", "") or "").lower()
                or needle in (s.get("hw_model", "") or "").lower()
                or any(needle in (cap or "").lower() for cap in s.get("capabilities", []))
            ]
            if not matches:
                return f"No sensors found matching '{sensor_type}'."
            out = [f"Sensor Information for '{sensor_type}' (Found {len(matches)} matches):"]
            for i, sensor in enumerate(matches, 1):
                out.append(f"\n--- Sensor {i}: {sensor.get('hw_model', 'Unknown')} ---")
                out.append(f"- Hardware ID: {sensor.get('hardware', 'Unknown')}")
                out.append(f"- Manufacturer: {sensor.get('manufacturer', 'Unknown')}")
                if sensor.get("capabilities"):
                    out.append(f"- Capabilities: {', '.join(sensor['capabilities'])}")
                if sensor.get("datasheet"):
                    out.append(f"- Datasheet: {sensor['datasheet']}")
                if sensor.get("vsns"):
                    out.append(f"- Used in nodes: {', '.join(sensor['vsns'])}")
                if sensor.get("description"):
                    desc = (
                        sensor["description"]
                        .replace("# ", "")
                        .replace("\r\n\r\n", "\n")
                        .replace("\r\n", " ")
                    )
                    if len(desc) > 300:
                        desc = desc[:297] + "..."
                    out.append(f"- Description: {desc}")
            return "\n".join(out)
        except Exception as e:
            logger.error(f"Error in get_sensor_details: {e}")
            return f"Error getting sensor details for '{sensor_type}': {e}"


def _format_sample(row: pd.Series) -> str:
    timestamp = safe_timestamp_format(row.get("timestamp", "N/A"))
    node = row.get("meta.vsn", "N/A")
    name = row.get("name", "N/A")
    value = row.get("value", "N/A")
    part = f"  {timestamp} | Node {node} | {name}"
    if isinstance(value, (int, float, np.number)):
        part += f" | Value: {value:.2f}"
    elif value != "N/A":
        part += f" | Value: {value}"
    return part
