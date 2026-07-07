"""Geographic query tools."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from ..data_service import SageDataService
from ..models import TimeRange
from ..utils import parse_time_range, safe_timestamp_format
from .sensor_tools import SAGE_MANIFESTS_URL


logger = logging.getLogger(__name__)


REGION_STATE_MAP: Dict[str, List[str]] = {
    "east coast": [
        "new york", "massachusetts", "rhode island", "connecticut", "new jersey",
        "delaware", "maryland", "virginia", "north carolina", "south carolina",
        "georgia", "florida", "pennsylvania", "washington dc", "washington d.c.",
        "maine", "new hampshire", "vermont", "ny", "ma", "ri", "ct", "nj", "de",
        "md", "va", "nc", "sc", "ga", "fl", "pa", "dc", "me", "nh", "vt", "eastern",
    ],
    "west coast": ["california", "oregon", "washington", "ca", "or", "wa", "western"],
    "midwest": [
        "illinois", "indiana", "michigan", "ohio", "wisconsin", "minnesota",
        "iowa", "missouri", "kansas", "nebraska", "south dakota", "north dakota",
        "il", "in", "mi", "oh", "wi", "mn", "ia", "mo", "ks", "ne", "sd", "nd", "chicago",
    ],
    "southwest": ["arizona", "new mexico", "texas", "oklahoma", "nevada", "az", "nm", "tx", "ok", "nv"],
    "southeast": ["alabama", "mississippi", "louisiana", "tennessee", "kentucky", "al", "ms", "la", "tn", "ky"],
}

RAIN_META = {
    "env.raingauge.rint": {
        "unit": "mm/hr",
        "desc": "Hydreon RG-15 rain gauge rain intensity (past minute, extrapolated to hour)",
    },
    "env.raingauge.event_acc": {
        "unit": "mm",
        "desc": "Hydreon RG-15 rain gauge event precipitation accumulation (resets 60 min after last drop)",
    },
    "env.raingauge.total_acc": {
        "unit": "mm",
        "desc": "Hydreon RG-15 rain gauge total precipitation accumulation (since last reset)",
    },
}


def _get_nodes_by_location(location: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    try:
        response = requests.get(SAGE_MANIFESTS_URL, timeout=15)
        if response.status_code != 200:
            return None, f"Error: Could not retrieve node list. Status code: {response.status_code}"
        nodes = response.json() or []
        if not nodes:
            return None, "No nodes found in the database."

        location_lower = location.lower().strip()
        target_states = REGION_STATE_MAP.get(location_lower, [])
        is_region = bool(target_states)

        matches: List[Dict[str, Any]] = []
        for node in nodes:
            address = (node.get("address", "") or "").lower()
            node_location = (node.get("location", "") or "").lower()
            name = (node.get("name", "") or "").lower()

            if is_region:
                if any(s in address or s in node_location or s in name for s in target_states):
                    matches.append(node)
            else:
                if location_lower in address or location_lower in node_location:
                    matches.append(node)

        if not matches:
            return None, f"No Sage nodes found in {location}."
        matches.sort(key=lambda x: x.get("vsn", ""))
        return matches, None
    except Exception as e:
        logger.error(f"Error looking up nodes by location: {e}")
        return None, f"Error finding nodes in {location}: {e}"


def register(mcp, *, data_service: SageDataService) -> None:
    @mcp.tool
    def get_nodes_by_location(location: str) -> str:
        """Find Sage nodes in a city, state, or region."""
        matches, error = _get_nodes_by_location(location)
        if error:
            return error
        assert matches is not None
        out = [f"Found {len(matches)} nodes in or near {location}:\n"]
        for node in matches:
            out.append(f"- Node {node.get('vsn', 'Unknown')}: {node.get('name', 'Unknown')}")
            out.append(f"  Location: {node.get('address', 'Unknown location')}")
            out.append(f"  Status: {node.get('phase', 'Unknown phase')}")
            sensors = node.get("sensors", []) or []
            if sensors:
                names = [s.get("name", "Unknown") for s in sensors]
                extra = f" and {len(names) - 5} more" if len(names) > 5 else ""
                out.append(f"  Sensors: {', '.join(names[:5])}{extra}")
            out.append("")
        return "\n".join(out)

    @mcp.tool
    def get_measurement_stat_by_location(
        location: str,
        measurement_type: str = "env.temperature",
        stat: str = "max",
        time_range: str = "-1h",
        sensor_type: str = "bme680",
        filter_expr: str = "",
    ) -> str:
        """Compute min/max/avg for a measurement across all nodes in a location."""
        try:
            if stat not in {"min", "max", "avg"}:
                return f"Error: Unknown stat '{stat}'. Use 'min', 'max', or 'avg'."
            validated_time = TimeRange(value=time_range)
            matches, error = _get_nodes_by_location(location)
            if error:
                return error
            assert matches is not None
            node_ids = [n.get("vsn", "") for n in matches if n.get("vsn")]
            if not node_ids:
                return f"Found nodes in {location}, but couldn't extract node IDs."
            if len(node_ids) > 20:
                node_ids = node_ids[:20]

            is_raingauge = measurement_type.startswith("env.raingauge")
            all_data = []
            start, end = parse_time_range(validated_time)

            for node_id in node_ids:
                if is_raingauge:
                    filter_params = {"plugin": ".*plugin-raingauge.*", "vsn": node_id}
                    df = data_service.query_data(start, end, filter_params)
                    if not df.empty:
                        df2 = df[df["name"] == measurement_type].copy()
                        if not df2.empty:
                            df2["node_id"] = node_id
                            all_data.append(df2)
                    if df.empty:
                        fallback = {"name": measurement_type, "vsn": node_id}
                        df_fb = data_service.query_data(start, end, fallback)
                        if not df_fb.empty:
                            df_fb = df_fb.copy()
                            df_fb["node_id"] = node_id
                            all_data.append(df_fb)
                else:
                    filter_params = {"name": measurement_type, "vsn": node_id}
                    if sensor_type:
                        filter_params["sensor"] = sensor_type
                    df = data_service.query_data(start, end, filter_params)
                    if not df.empty:
                        df = df.copy()
                        df["node_id"] = node_id
                        all_data.append(df)

            if not all_data:
                return f"No {measurement_type} data found for nodes in {location} during the last {validated_time}"

            combined = pd.concat(all_data, ignore_index=True)
            if filter_expr:
                try:
                    combined = combined.query(filter_expr)
                except Exception as e:
                    return f"Error: Invalid filter expression '{filter_expr}': {e}"
                if combined.empty:
                    return (
                        f"No {measurement_type} data matched the filter '{filter_expr}' "
                        f"for nodes in {location} during the last {validated_time}"
                    )

            unit = ""
            desc = ""
            if is_raingauge and measurement_type in RAIN_META:
                unit = RAIN_META[measurement_type]["unit"]
                desc = RAIN_META[measurement_type]["desc"]

            unit_suffix = f" {unit}" if unit else ""
            if stat in {"min", "max"}:
                idx = combined["value"].idxmin() if stat == "min" else combined["value"].idxmax()
                row = combined.loc[idx]
                stat_val = row["value"]
                out = (
                    f"{'Minimum' if stat == 'min' else 'Maximum'} {measurement_type} "
                    f"in {location} (last {validated_time}, filter: '{filter_expr}'):\n\n"
                    f"{stat.capitalize()}: {stat_val:.2f}{unit_suffix} measured at node "
                    f"{row['node_id']}\n  Time: {safe_timestamp_format(row['timestamp'])}\n"
                    f"  Data from {len(combined)} filtered readings\n"
                )
            else:  # avg
                out = (
                    f"Average {measurement_type} in {location} "
                    f"(last {validated_time}, filter: '{filter_expr}'):\n\n"
                    f"Avg: {combined['value'].mean():.2f}{unit_suffix} "
                    f"(from {len(combined)} filtered readings)\n"
                )
            if desc:
                out += f"Description: {desc}\n"
            return out
        except Exception as e:
            logger.error(f"Error in get_measurement_stat_by_location: {e}")
            return f"Error getting {stat} of {measurement_type} for {location}: {e}"
