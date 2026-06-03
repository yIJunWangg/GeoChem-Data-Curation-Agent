"""Mapping memory store: read/write confirmed mapping rules."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import yaml

from .logging_config import get_logger
from .models import MappingRule, MappingType, ReviewStatus

logger = get_logger("memory")


class MemoryStore:
    """Store and retrieve confirmed mapping rules.

    V1 Phase: read-only (loaded from YAML file).
    V1 Phase 4+: read-write (supports adding/editing rules).
    """

    def __init__(self, rules_path: str | Path | None = None):
        self.rules_path = Path(rules_path) if rules_path else None
        self._rules: dict[str, MappingRule] = {}  # rule_id -> MappingRule
        self._source_index: dict[str, list[str]] = {}  # normalized_source_field -> [rule_id]

    def load(self) -> None:
        """Load rules from YAML file."""
        if not self.rules_path or not self.rules_path.exists():
            return

        with open(self.rules_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or "rules" not in data:
            return

        for rule_data in data["rules"]:
            rule = MappingRule(**rule_data)
            self._rules[rule.rule_id] = rule
            src_key = rule.source_field.lower().strip()
            if src_key not in self._source_index:
                self._source_index[src_key] = []
            self._source_index[src_key].append(rule.rule_id)

        logger.info(f"Loaded {len(self._rules)} mapping rules from {self.rules_path}")

    def save(self) -> None:
        """Save all rules to YAML file."""
        if not self.rules_path:
            return

        self.rules_path.parent.mkdir(parents=True, exist_ok=True)
        rules_data = [rule.model_dump(mode="json") for rule in self._rules.values()]
        with open(self.rules_path, "w", encoding="utf-8") as f:
            yaml.dump({"rules": rules_data}, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        logger.info(f"Saved {len(self._rules)} mapping rules to {self.rules_path}")

    def query_by_source(self, source_field: str) -> list[MappingRule]:
        """Find rules matching a source field name."""
        src_key = source_field.lower().strip()
        rule_ids = self._source_index.get(src_key, [])
        return [self._rules[rid] for rid in rule_ids if rid in self._rules]

    def query_by_source_target(self, source_field: str, target_field: str) -> MappingRule | None:
        """Find a specific source->target mapping rule."""
        rules = self.query_by_source(source_field)
        for rule in rules:
            if rule.target_field == target_field:
                return rule
        return None

    def add_rule(self, rule: MappingRule) -> None:
        """Add a new rule."""
        self._rules[rule.rule_id] = rule
        src_key = rule.source_field.lower().strip()
        if src_key not in self._source_index:
            self._source_index[src_key] = []
        if rule.rule_id not in self._source_index[src_key]:
            self._source_index[src_key].append(rule.rule_id)
        logger.info(f"Added rule: {rule.rule_id} ({rule.source_field} -> {rule.target_field})")

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID."""
        if rule_id not in self._rules:
            return False
        rule = self._rules.pop(rule_id)
        src_key = rule.source_field.lower().strip()
        if src_key in self._source_index:
            self._source_index[src_key] = [
                rid for rid in self._source_index[src_key] if rid != rule_id
            ]
        logger.info(f"Removed rule: {rule_id}")
        return True

    def get_all_rules(self) -> list[MappingRule]:
        """Get all stored rules."""
        return list(self._rules.values())

    def get_rule_count(self) -> int:
        """Get the number of stored rules."""
        return len(self._rules)
