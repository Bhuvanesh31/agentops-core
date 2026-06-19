"""Summary + pending/local-only repository reporting for a capture run."""

from dataclasses import dataclass, field


@dataclass
class RunReport:
    files_processed: int = 0
    files_skipped: int = 0
    events_created: int = 0
    events_duplicate: int = 0
    events_error: int = 0
    pending_repos: dict[str, int] = field(default_factory=dict)
    local_only: dict[str, int] = field(default_factory=dict)

    def add_pending(self, canonical_remote: str) -> None:
        count = self.pending_repos.get(canonical_remote, 0)
        self.pending_repos[canonical_remote] = count + 1

    def add_local_only(self, label: str) -> None:
        self.local_only[label] = self.local_only.get(label, 0) + 1

    def render(self) -> str:
        lines = [
            "AgentOps Claude Code capture",
            "----------------------------",
            f"files: processed={self.files_processed} skipped={self.files_skipped}",
            (
                f"events: created={self.events_created} "
                f"duplicate={self.events_duplicate} error={self.events_error}"
            ),
        ]
        if self.pending_repos:
            lines.append("")
            lines.append("Pending repositories (register, then re-run):")
            for remote, count in sorted(self.pending_repos.items()):
                lines.append(f"  - {remote}  ({count} sessions)")
        if self.local_only:
            lines.append("")
            lines.append("Local-only folders skipped (no git remote):")
            for label, count in sorted(self.local_only.items()):
                lines.append(f"  - {label}  ({count} sessions)")
        return "\n".join(lines)
