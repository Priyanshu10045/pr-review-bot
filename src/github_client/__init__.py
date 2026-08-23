"""GitHub Client integration package."""

from src.github_client.client import GitHubClient
from src.github_client.models import DiffHunk, InlineComment, PRMetadata, ReviewSummary

__all__ = ["GitHubClient", "PRMetadata", "DiffHunk", "InlineComment", "ReviewSummary"]
