"""Plugin discovery, data querying, and creation tools."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from ..data_service import SageDataService
from ..models import NodeID, TimeRange
from ..plugin_generator import PluginGenerator, PluginRequirements, PluginTemplate
from ..plugin_metadata import plugin_registry
from ..plugin_query_service import plugin_query_service
from ..utils import parse_time_range, safe_timestamp_format


logger = logging.getLogger(__name__)


def find_plugins_for_task_impl(task_description: str) -> str:
    """Shared implementation — usable outside the MCP tool wrapper."""
    task = (task_description or "").strip()
    if not task:
        return "Please provide a task description to find relevant plugins."
    matching = plugin_registry.search_plugins(task.lower(), max_results=10)
    if not matching:
        return (
            f"No plugins found matching '{task_description}'.\n\n"
            "Try different keywords or check categories:\n"
            "- Camera/Vision: camera, image, video, ptz, detection\n"
            "- Audio: sound, audio, microphone, bird, noise\n"
            "- Environmental: temperature, humidity, pressure, weather\n"
            "- AI/Detection: yolo, object detection, recognition\n"
            "- Movement: motion, tracking, pan, tilt, zoom"
        )
    parts = [f"Found {len(matching)} plugins matching your task '{task_description}':"]
    for i, plugin in enumerate(matching, 1):
        parts.append(f"\n{i}. {plugin.name} (v{plugin.version}):")
        parts.append(f"   Image: {plugin.id}")
        if plugin.description:
            parts.append(f"   Description: {plugin.description}")
        if plugin.keywords:
            parts.append(f"   Keywords: {plugin.keywords}")
        if plugin.authors:
            parts.append(f"   Authors: {plugin.authors}")
        if plugin.inputs:
            input_params = ", ".join(f"{inp.id} ({inp.type})" for inp in plugin.inputs)
            parts.append(f"   Parameters: {input_params}")
        if plugin.homepage:
            parts.append(f"   Homepage: {plugin.homepage}")
        if plugin.science_description_content:
            snippet = plugin.science_description_content[:200].strip()
            if len(plugin.science_description_content) > 200:
                snippet += "..."
            parts.append(f"   Science Description: {snippet}")
        parts.append("")
    return "\n".join(parts)


def register(mcp, *, data_service: SageDataService):
    @mcp.tool
    def find_plugins_for_task(task_description: str) -> str:
        """Find plugins whose description/keywords match a task."""
        try:
            return find_plugins_for_task_impl(task_description)
        except Exception as e:
            logger.error(f"Error finding plugins: {e}")
            return f"Error searching for plugins: {e}"

    @mcp.tool
    def get_plugin_data(plugin_id: str, nodes: str = "", time_range: str = "-1h") -> str:
        """Query and format data from a specific plugin."""
        try:
            node_list: Optional[List[str]] = (
                [n.strip() for n in nodes.split(",") if n.strip()] if nodes else None
            )
            plugin = plugin_registry.get_plugin_by_id(plugin_id)
            if not plugin:
                return f"Plugin not found: {plugin_id}"
            df = plugin_query_service.query_plugin_data(
                plugin_id=plugin_id, nodes=node_list, time_range=time_range
            )
            return plugin_query_service.format_plugin_data(df, plugin)
        except Exception as e:
            logger.error(f"Error getting plugin data: {e}")
            return f"Error getting plugin data: {e}"

    @mcp.tool
    def get_cloud_images(time_range: str = "-1h", node_id: str = "") -> str:
        """Recent cloud/sky imagery from Sage nodes."""
        try:
            validated_time = TimeRange(value=time_range)
            validated_node = NodeID(value=node_id) if node_id else None
            filter_params: Dict[str, Any] = {
                "plugin": "|".join([".*cloud-cover.*", ".*cloud-motion.*", ".*imagesampler.*"])
            }
            if validated_node:
                filter_params["vsn"] = str(validated_node)
            start, end = parse_time_range(validated_time)
            df = data_service.query_data(start, end, filter_params)
            if df.empty:
                node_text = f" for node {validated_node}" if validated_node else ""
                return f"No cloud images found{node_text} in the last {validated_time}"
            return _summarize_by_plugin(df, header=f"Cloud images found (last {validated_time}):\n\n")
        except Exception as e:
            logger.error(f"Error getting cloud images: {e}")
            return f"Error getting cloud images: {e}"

    @mcp.tool
    def get_image_data(
        time_range: str = "-1h",
        node_id: str = "",
        plugin_pattern: str = ".*imagesampler.*|.*camera.*|.*cloud-cover.*",
    ) -> str:
        """Recent image data. Supports node and plugin-pattern filtering."""
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
            if "|" in plugin_pattern:
                filter_params["plugin"] = "|".join(_wrap(p) for p in plugin_pattern.split("|"))
            else:
                filter_params["plugin"] = _wrap(plugin_pattern)
            if validated_node:
                filter_params["vsn"] = str(validated_node)

            start, end = parse_time_range(validated_time)
            df = data_service.query_data(start, end, filter_params)
            if df.empty:
                node_text = f" for node {validated_node}" if validated_node else ""
                pattern_text = f" matching pattern '{plugin_pattern}'" if plugin_pattern != ".*" else ""
                return f"No image data found{node_text}{pattern_text} in the last {validated_time}"
            return _summarize_by_plugin(df, header=f"Image data found (last {validated_time}):\n\n")
        except Exception as e:
            logger.error(f"Error getting image data: {e}")
            return f"Error getting image data: {e}"

    @mcp.tool
    def query_plugin_data_nl(query: str) -> str:
        """Query plugin data using natural language."""
        try:
            return plugin_query_service.query_by_natural_language(query)
        except Exception as e:
            logger.error(f"Error processing natural language query: {e}")
            return f"Error processing your query: {e}"

    @mcp.tool
    def create_plugin(
        description: str,
        name: str,
        use_gpu: bool = False,
        use_camera: bool = False,
        use_env_sensors: bool = False,
        use_audio: bool = False,
        packages: str = "",
        system_deps: str = "",
    ) -> str:
        """Generate a new Sage plugin scaffold from a description."""
        try:
            template = PluginTemplate(
                name=name,
                description=description,
                requirements=PluginRequirements(
                    gpu=use_gpu,
                    camera=use_camera,
                    environmental_sensors=use_env_sensors,
                    audio=use_audio,
                    python_packages=[p.strip() for p in packages.split(",") if p.strip()],
                    system_packages=[p.strip() for p in system_deps.split(",") if p.strip()],
                ),
            )
            generator = PluginGenerator()
            plugin_path = generator.generate_plugin(template)
            return (
                f"Plugin '{name}' created successfully at {plugin_path}\n\n"
                "Deployment steps:\n"
                f"1. tar -czf {name}.tar.gz {name}/\n"
                "2. scp <archive> waggle-dev-node-WXXX:~\n"
                "3. ssh waggle-dev-node-WXXX\n"
                f"4. mkdir -p {name} && cd {name} && tar -xzf ../{name}.tar.gz --strip-components=1\n"
                "5. sudo pluginctl build . && sudo pluginctl run ."
            )
        except Exception as e:
            logger.error(f"Error creating plugin: {e}")
            return f"Error creating plugin: {e}"


def _summarize_by_plugin(df, *, header: str) -> str:
    result = header
    result += f"Total records: {len(df)}\n"
    if "meta.vsn" in df.columns:
        result += f"Nodes reporting: {df['meta.vsn'].nunique()}\n"
    if "plugin" in df.columns:
        result += f"Plugins active: {df['plugin'].nunique()}\n"
    if "name" in df.columns:
        result += f"Measurement types: {df['name'].nunique()}\n"

    if "plugin" in df.columns:
        for plugin in sorted(df["plugin"].unique()):
            p_df = df[df["plugin"] == plugin]
            result += f"\nPlugin: {plugin}\n"
            if "meta.vsn" in p_df.columns:
                result += f"- Nodes: {', '.join(sorted(p_df['meta.vsn'].unique()))}\n"
            if "name" in p_df.columns:
                result += f"- Measurements: {', '.join(sorted(p_df['name'].unique()))}\n"
            result += "- Recent data:\n"
            for _, row in p_df.sort_values("timestamp", ascending=False).head(3).iterrows():
                value = row.get("value", "N/A")
                line = (
                    f"  {safe_timestamp_format(row.get('timestamp', 'N/A'))} | "
                    f"Node {row.get('meta.vsn', 'N/A')} | {row.get('name', 'N/A')}"
                )
                if isinstance(value, (int, float, np.number)):
                    line += f" | Value: {value:.2f}"
                elif value != "N/A":
                    line += f" | Value: {value}"
                result += line + "\n"
    return result
