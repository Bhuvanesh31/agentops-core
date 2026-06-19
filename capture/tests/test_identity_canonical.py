import pytest

from capture.claude_code.identity import canonicalize_remote


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "https://github.com/Bhuvanesh31/agentops-core.git",
            "github.com/Bhuvanesh31/agentops-core",
        ),
        ("git@github.com:Bhuvanesh31/agentops-core.git", "github.com/Bhuvanesh31/agentops-core"),
        (
            "ssh://git@github.com/Bhuvanesh31/agentops-core.git",
            "github.com/Bhuvanesh31/agentops-core",
        ),
        ("https://github.com/Bhuvanesh31/agentops-core", "github.com/Bhuvanesh31/agentops-core"),
        (
            "https://GitHub.com/Bhuvanesh31/agentops-core.git",
            "github.com/Bhuvanesh31/agentops-core",
        ),
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_canonicalize_remote(raw, expected):
    assert canonicalize_remote(raw) == expected
