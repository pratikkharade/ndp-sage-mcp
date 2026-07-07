"""Prompt registration for the Sage MCP server."""

from __future__ import annotations


def register(mcp) -> None:
    @mcp.prompt
    def summarize_temperature_anomalies() -> str:
        """Prompt to analyze temperature anomalies."""
        return "Can you summarize any temperature anomalies from the past hour?"

    @mcp.prompt
    def suggest_image_sampler_cron() -> str:
        """Prompt for cron expression help."""
        return (
            "Help me write a scienceRule cron expression for the image-sampler plugin "
            "that samples every 10 minutes between 6am and 6pm."
        )

    @mcp.prompt
    def suggest_environmental_job() -> str:
        """Prompt for environmental monitoring job setup."""
        return (
            "I want to monitor environmental conditions (temperature, humidity, pressure) "
            "across all nodes. What kind of job should I set up?"
        )

    @mcp.prompt
    def getting_started_guide() -> str:
        """Interactive guide for new Sage users."""
        return (
            "I'm new to Sage and want to get started. Can you walk me through:\n"
            "1. How to create an account and get access\n"
            "2. How to explore available data and sensors\n"
            "3. How to access data using the Python client\n"
            "4. How to submit my first job\n"
            "5. How to monitor job status and results\n\n"
            "Please provide step-by-step instructions with examples."
        )

    @mcp.prompt
    def plugin_development_guide() -> str:
        """Comprehensive guide for creating custom Sage plugins."""
        return (
            "I want to create a custom Sage plugin (edge app). Please guide me through:\n"
            "1. Plugin architecture and requirements\n"
            "2. Setting up the development environment\n"
            "3. Using the cookiecutter template\n"
            "4. PyWaggle integration for sensors and data publishing\n"
            "5. Docker container setup and Dockerfile best practices\n"
            "6. Testing with pluginctl on development nodes\n"
            "7. Publishing to the Edge Code Repository (ECR)\n"
            "8. Job submission and scheduling"
        )

    @mcp.prompt
    def data_analysis_guide() -> str:
        """Guide for accessing, querying, and analyzing Sage data."""
        return (
            "I want to work with Sage data for analysis. Please help me understand:\n"
            "1. What types of data are available\n"
            "2. How to use the Python sage-data-client\n"
            "3. Filtering by time, location, and sensor type\n"
            "4. Accessing uploaded files\n"
            "5. Working with protected data and authentication\n"
            "6. Best practices for analysis and visualization\n"
            "7. Setting up triggers and real-time monitoring"
        )

    @mcp.prompt
    def troubleshooting_guide() -> str:
        """Comprehensive troubleshooting guide."""
        return (
            "I'm having issues with Sage and need troubleshooting help. Please cover:\n"
            "1. Plugin/edge app development issues\n"
            "2. Job submission and scheduling problems\n"
            "3. Data access and query issues\n"
            "4. Node access and SSH connection problems\n"
            "5. ECR submission and publication issues\n"
            "6. Common error messages and their solutions"
        )
