"""Configuration management for AI Company Agent."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class AgentConfig:
    """Configuration for an agent adapter."""
    name: str
    enabled: bool = True
    timeout_seconds: int = 300
    max_retries: int = 2


@dataclass
class ClaudeAgentConfig(AgentConfig):
    """Claude agent-specific configuration."""
    name: str = "claude"
    model: Optional[str] = None  # auto-detect if None
    temperature: float = 0.7
    max_tokens: int = 8192
    project_path: Optional[str] = None


@dataclass
class KiloCodeAgentConfig(AgentConfig):
    """KiloCode agent-specific configuration."""
    name: str = "kilocode"
    review_pass_keywords: list[str] = field(
        default_factory=lambda: ["approved", "lgtm", "looks good", "no issues", "pass"]
    )
    review_fail_keywords: list[str] = field(
        default_factory=lambda: ["issue", "problem", "warning", "error", "bug", "security"]
    )


@dataclass
class PytestAgentConfig(AgentConfig):
    """Pytest agent-specific configuration."""
    name: str = "pytest"
    test_paths: list[str] = field(default_factory=lambda: ["tests/"])
    strict_types: bool = True


@dataclass
class ApproveGateConfig:
    """Approval gate rules."""
    auto_approve: bool = True
    min_test_pass_rate: float = 1.0  # 100%
    min_review_score: float = 7.0    # out of 10
    require_tests: bool = True
    require_review: bool = True


@dataclass
class TelegramConfig:
    """Telegram bot configuration."""
    bot_token: Optional[str] = None
    allowed_usernames: list[str] = field(default_factory=list)  # empty = allow all
    poll_interval_seconds: float = 1.0
    chunk_size: int = 30
    message_chunk_size: int = 3500


@dataclass
class Config:
    """Root configuration for the AI Company Agent."""
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    claude: ClaudeAgentConfig = field(default_factory=ClaudeAgentConfig)
    kilocode: KiloCodeAgentConfig = field(default_factory=KiloCodeAgentConfig)
    pytest: PytestAgentConfig = field(default_factory=PytestAgentConfig)
    approve_gate: ApproveGateConfig = field(default_factory=ApproveGateConfig)

    # Internal paths
    data_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent.parent / "data")
    tasks_file: Path = field(init=False)

    def __post_init__(self):
        self.tasks_file = self.data_dir / "company_tasks.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_yaml(cls, path: Path) -> "Config":
        """Load configuration from YAML file."""
        if not path.exists():
            return cls()

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        telegram_data = data.get("telegram", {})
        claude_data = data.get("claude", {})
        kilocode_data = data.get("kilocode", {})
        pytest_data = data.get("pytest", {})
        approve_gate_data = data.get("approve_gate", {})

        return cls(
            telegram=TelegramConfig(
                bot_token=telegram_data.get("bot_token") or os.getenv("TELEGRAM_BOT_TOKEN"),
                allowed_usernames=telegram_data.get("allowed_usernames", []),
                poll_interval_seconds=telegram_data.get("poll_interval_seconds", 1.0),
                chunk_size=telegram_data.get("chunk_size", 30),
                message_chunk_size=telegram_data.get("message_chunk_size", 3500),
            ),
            claude=ClaudeAgentConfig(
                name="claude",
                project_path=claude_data.get("project_path") or os.getenv("CLAUDE_CODE_PROJECT_PATH"),
            ),
            kilocode=KiloCodeAgentConfig(
                name="kilocode",
            ),
            pytest=PytestAgentConfig(
                name="pytest",
            ),
            approve_gate=ApproveGateConfig(
                auto_approve=approve_gate_data.get("auto_approve", True),
                min_test_pass_rate=approve_gate_data.get("min_test_pass_rate", 1.0),
                min_review_score=approve_gate_data.get("min_review_score", 7.0),
                require_tests=approve_gate_data.get("require_tests", True),
                require_review=approve_gate_data.get("require_review", True),
            ),
        )

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            telegram=TelegramConfig(
                bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
                allowed_usernames=os.getenv("ALLOWED_TELEGRAM_USERNERS", "").split(",") if os.getenv("ALLOWED_TELEGRAM_USERNERS") else [],
            ),
            claude=ClaudeAgentConfig(
                project_path=os.getenv("CLAUDE_CODE_PROJECT_PATH"),
            ),
        )


# Global config instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        # Try YAML first, fall back to env
        config_path = Path(__file__).parent.parent.parent / "config.yaml"
        if config_path.exists():
            _config = Config.from_yaml(config_path)
        else:
            _config = Config.from_env()
    return _config


def reload_config(config_path: Optional[Path] = None) -> Config:
    """Reload configuration from file or environment."""
    global _config
    if config_path:
        _config = Config.from_yaml(config_path)
    else:
        _config = Config.from_env()
    return _config
